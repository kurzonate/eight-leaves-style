"""Asset stage: everything the ported code loads, taken from the source game and made Sky-compatible.

  model      the form's model: materials switched to shader combinations Sky ships (fix_materials), its
             textures (LZ4 frames), and a model-info file copied from one of the target's outfits; or (a style's
             skin) one of the target's own models under the form's name
  equipment  a style's weapon models under new names (their textures renamed with them), with their
             model-info files converted (the attachment's own clips, dynamic bones)
  motions    every motion / camera-track file the built script names that Sky doesn't have; for a style,
             retargeted to the target's rig (kuro_mdl.retarget), camera heights scaled to the target's head
  effects    every effect file the built script loads that Sky doesn't have, converted to Sky's binary JSON,
             plus the textures and effect models they use (models fixed like the form's)
Writes fpac override folders: <out>/<archive>/<path inside the archive>, and manifest.json: what the build took from
the source game (motions, effects) and the config values it used, so the release's extractor (release/extract.py)
can redo it on a player's own installs without the scripts (build(..., plan=manifest)).
"""
import json
import os
import re
import struct
import sys

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import bjson  # noqa: E402
import fix_materials  # noqa: E402
import fpac  # noqa: E402
import kuro_materials as KM  # noqa: E402
import kuro_mdl as K  # noqa: E402
import p3a  # noqa: E402
import sky_texture  # noqa: E402
import vfx_convert  # noqa: E402

MODEL, IMAGE, VFX, MODEL_INFO = 'asset/common/model/', 'asset/dx11/image/', 'vfx/', 'asset/common/model_info/'
# data files the build reads (the extractor points these at its own copies)
CATALOG = os.path.join(ROOT, 'grendel_test', 'sky_shader_combos.json')   # tools/fix_materials.py catalog output
VFX_SCHEMA = os.path.join(ROOT, 'vfx_port', 'sky_vfx_schema.json')       # tools/vfx_convert.py schema output
ORIGINAL = os.path.join(ROOT, 'original')                                 # untouched Sky archives
# the config values the asset build uses (all of them: the manifest carries these for the extractor)
CONFIG_KEYS = ('EQUIP_MODELS', 'EQUIP_ANCHOR', 'RETARGET', 'RETARGET_RENAME', 'FORM_MODEL', 'ROW', 'SKIRT_POSE',
               'PELVIS_UPRIGHT', 'HEAD_CLEARANCE', 'RECOLOURED_FACE')


class Kai:
    """A source-game .p3a archive."""
    def __init__(self, p):
        self.idx = p3a.read_index(p)
        self.path = p
        self.entries = {e['name']: e for e in self.idx['entries']}

    def read(self, name):
        with open(self.path, 'rb') as f:
            return p3a.read_entry(f, self.idx, self.entries[name])


class Sky:
    """A Sky .pac archive (names only, plus single reads)."""
    def __init__(self, p):
        self.path = p
        self.entries = {e['name']: e for e in fpac.read_index(p)[2]}

    def read(self, name):
        e = self.entries[name]
        with open(self.path, 'rb') as f:
            f.seek(e['off'])
            return f.read(e['size'])


_catalog = {}


def fix_model(data, work, name, prefer=()):
    """tools/fix_materials.py fix (a no-op for materials that already use a Sky combination)."""
    if CATALOG not in _catalog:
        _catalog[CATALOG] = fix_materials.load_catalog(CATALOG)
    return fix_materials.fix(data, _catalog[CATALOG], prefer=dict(p.split('=', 1) for p in prefer))


def model_textures(data):
    secs = dict(K.read_sections(data)[1])
    return {t['image'] for m in KM.parse_materials(secs[0])[0] for t in m['textures']} if 0 in secs else set()


def rename_textures(data, old, new):
    """The model with every texture whose name contains `old` renamed (`old` -> `new`); returns (data, names)."""
    secs = dict(K.read_sections(data)[1])
    mats, tail = KM.parse_materials(secs[0])
    renamed = {}
    for m in mats:
        for t in m['textures']:
            if old in t['image']:
                renamed[t['image'].replace(old, new)] = t['image']
                t['image'] = t['image'].replace(old, new)
    return K.replace_section(data, 0, KM.build_materials(mats, tail)), renamed


def kai_model_info(text):
    """A Horizon model-info file (JSON text) in Sky's form (binary JSON): Sky's sections in Sky's order; dynamic
    bones get Sky's extra fields at their defaults. Only what weapon models use is converted."""
    k = json.loads(text)
    assert not k['Occluder']['is_valid']
    for key in ('Collider', 'TwoBoneIK', 'LookIK', 'DynamicBoneCollider', 'LOD', 'Lights', 'Locators'):
        assert not k.get(key), key
    bones = [{'Joint': [dict(j, driven_infl=j.get('driven_infl', 1)) for j in d['Joint']],
              'ignore_collision': d.get('ignore_collision', False), 'SpecificCollider': d.get('SpecificCollider', []),
              'NeighborBones': d.get('NeighborBones', [])} for d in k.get('DynamicBone', [])]
    return bjson.encode({'Bounding': {'type': 0}, 'Collider': [], 'Animation': k.get('Animation', []),
                         'Occluder': {'is_valid': False}, 'TwoBoneIK': [], 'LookIK': [], 'DynamicBone': bones,
                         'DynamicBoneCollider': [], 'LOD': [], 'Lights': [], 'Locators': [], 'DrivenKeys': [],
                         'Extra': k.get('Extra', {'door_type': 0})})


def add_anchor(data, spec, source_rig, pose=None, target_rig=None):
    """An equipment model with a node that hangs it where the source character wears it, relative to another of
    the source's bones (spec: name, point, bone, pos_scale, model_scale; pose: the source's motion whose first frame
    shows it, else the bind pose). Attached by that node to the target's bone of the same name, it needs no rotation
    offset: chr_attach_set_offset's rotation convention isn't the nodes' (S1: the node's own angles hung the sheath
    straight down). Where the model's 'hold' node meets `point` in the source, the new node meets `bone`: anchor =
    hold * (bone -> point)^-1, the offset's position scaled by pos_scale (the rigs) and divided by model_scale (the
    attachment's own scale, applied about the anchor). align (a direction in the model, e.g. a sheath's length):
    turned about the vertical through the hold point until it points straight back from the target's torso (S10:
    the source's pose angled it out to the side)."""
    w = K.pose_matrices(source_rig, pose[0], pose[1][0]) if pose else K.world_matrices(source_rig)
    rel = np.linalg.inv(w[spec['bone']]) @ w[spec['point']]
    rel[:3, 3] *= spec['pos_scale']
    hold = K.world_matrices(skeleton(data))['hold']
    if spec.get('align'):
        bone = K.world_matrices(target_rig)[spec['bone']]
        place = bone @ rel                                          # the hold point's frame on the target, at rest
        d = place[:3, :3] @ hold[:3, :3].T @ np.array(spec['align'], dtype=float)
        a = np.arctan2(-d[0], d[2]) + np.pi                         # turn so the direction has no sideways part, backwards (-z)
        turn = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        place[:3, :3] = turn @ place[:3, :3]
        rel = np.linalg.inv(bone) @ place
    rel[:3, 3] /= spec['model_scale']
    a = hold @ np.linalg.inv(rel)
    return K.add_node(data, spec['name'], 'world', a[:3, 3], K.matrix_to_rpy(a[:3, :3]), 'hold')


def _axis_rotation(axis, angle):
    x, y, z = axis / np.linalg.norm(axis)
    c, s, C = np.cos(angle), np.sin(angle), 1 - np.cos(angle)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def _quat(R):
    """(x, y, z, w) of a rotation matrix."""
    w = np.sqrt(max(1e-12, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    return np.array([(R[2, 1] - R[1, 2]) / (4 * w), (R[0, 2] - R[2, 0]) / (4 * w), (R[1, 0] - R[0, 1]) / (4 * w), w])


def keep_clear(data, rig, hold, spec, fps=30):
    """A motion whose held weapon keeps clear of the target's head: at each frame the weapon (model hold node on
    spec['bone'], blade along spec['axis'] for spec['length'] m at spec['scale']) is measured against the head bone,
    and where it passes closer than spec['clearance'] the bone turns about the hold point, away from the head, just
    enough; the turn is smoothed over time (no jumps where it starts or ends). The bone's rotation channel is
    resampled at fps with the turn applied in its own frame. Source rigs differ in head size (Estelle's is bigger for
    her height: Shizuna's stance put the katana through it, S19-S20). Returns (data, largest turn in degrees)."""
    version, sections, _ = K.read_sections(data)
    blocks, (t0, t1) = K.parse_animation(dict(sections)[3])
    rot = next((b for b in blocks if b['bone'] == spec['bone'] and b['type'] == 10), None)
    if rot is None:
        return data, 0.0
    times = _frames(t0, t1, fps)
    model = np.diag([spec['scale']] * 3 + [1]) @ np.linalg.inv(hold)
    axis_m = np.append(np.asarray(spec['axis'], float), 0)
    length = spec['length'] * spec['scale']
    turns, frames = [], []
    for t in times:
        w = K.pose_matrices(rig, blocks, t)
        node = w[spec['bone']]
        place = node @ model
        grip, d = place[:3, 3], (place @ axis_m)[:3]
        d /= np.linalg.norm(d)
        v = w[spec['head']][:3, 3] - grip

        def dist(dd):
            s = np.clip(v @ dd, 0, length)
            return np.linalg.norm(v - s * dd)
        ax = np.cross(v, d)
        if np.linalg.norm(ax) < 1e-6:
            ax = np.cross(d, [0, 1, 0])
        a = 0.0
        if dist(d) < spec['clearance']:
            lo, hi = 0.0, np.pi / 2
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if dist(_axis_rotation(ax, mid) @ d) < spec['clearance'] else (lo, mid)
            a = hi
        turns.append(a)
        frames.append((node[:3, :3] / np.linalg.norm(node[:3, :3], axis=0), ax))
    turns = np.array(turns)
    if not turns.any():
        return data, 0.0
    eased = _ease(turns)
    values = []
    for t, a, (Rn, ax) in zip(times, eased, frames):
        key = np.array([np.interp(t, rot['times'], rot['values'][:, c]) for c in range(4)])
        key /= np.linalg.norm(key)
        delta = _quat(Rn.T @ _axis_rotation(ax, a) @ Rn)
        x, y, z, w_ = key
        r = K._qmul(np.array([w_, x, y, z]), np.array([delta[3], delta[0], delta[1], delta[2]]))
        values.append([r[1], r[2], r[3], r[0]])
    sec = K.replace_channel(dict(sections)[3], spec['bone'], 10, times, np.array(values))
    return K.replace_section(data, 3, sec), float(np.degrees(eased.max()))


def pose_unkeyed(data, keys):
    """A motion with constant channels added for the bones of `keys` (a target motion's blocks, by bone) it doesn't
    key itself, held at that motion's first key: Estelle's skirt bones, which her battle motions all key (her stance
    swings the front panels out over her thighs) and Shizuna's don't (S22: left at rest, the front tucked in behind
    her forward thigh). Returns (data, channels added)."""
    version, sections, _ = K.read_sections(data)
    sec = dict(sections)[3]
    blocks, (t0, t1) = K.parse_animation(sec)
    have = {(b['bone'], b['type']) for b in blocks}
    n = 0
    for bone, b in keys.items():
        if (bone, b['type']) not in have:
            sec = K.add_channel(sec, bone, b['type'], [t0, t1], np.repeat(b['values'][:1], 2, axis=0), b['name'])
            n += 1
    return (K.replace_section(data, 3, sec), n) if n else (data, 0)


def _frames(t0, t1, fps):
    """Frame times from t0 to t1 inclusive (the clip's own float32 ends: an arange to t1 lost the last frame)."""
    return np.linspace(t0, t1, int(round((t1 - t0) * fps)) + 1)


def _ease(turns, k=5):
    """Per-frame angles never less than needed (a running maximum), then eased (a moving average over the same
    window): no jumps where a correction starts or ends."""
    padded = np.pad(turns, k, mode='edge')
    held = np.array([padded[i:i + 2 * k + 1].max() for i in range(len(turns))])
    padded = np.pad(held, k, mode='edge')
    return np.maximum(np.array([padded[i:i + 2 * k + 1].mean() for i in range(len(turns))]), turns)


def _wxyz(key):
    x, y, z, w = key / np.linalg.norm(key)
    return np.array([w, x, y, z])


def _inv(q):
    return q * np.array([1, -1, -1, -1])


def upright_pelvis(data, rig, spec, fps=30):
    """A motion whose pelvis doesn't tip far from the thighs: at each frame, where a thigh points more than
    spec['behind'] degrees behind the pelvis' down axis, the pelvis turns about the line through the hip joints
    until it doesn't (but never leans forward more than spec['lean'] degrees), smoothed over time. The thighs turn
    back by as much, so legs and upper body (not the pelvis' child) are where they were; only the pelvis and what
    hangs on it (skirt, hip rolls) move. Estelle's skirt is skinned to her thighs as well: Shizuna's stance (pelvis
    tipped 21 degrees, a thigh 37 behind it; Estelle's own never behind) dragged its front in (S19-S21). The pelvis
    gets a translation channel (the hip joints are off its origin). Returns (data, largest turn in degrees)."""
    version, sections, _ = K.read_sections(data)
    blocks, (t0, t1) = K.parse_animation(dict(sections)[3])
    nodes = {n['name']: n for n in rig}
    pelvis, legs, knees = spec['bone'], spec['legs'], spec['knees']
    rot = {b['bone']: b for b in blocks if b['type'] == 10 and b['bone'] in (pelvis, *legs)}
    move = next((b for b in blocks if b['type'] == 9 and b['bone'] == pelvis), None)
    if len(rot) < 3:
        return data, 0.0
    times = _frames(t0, t1, fps)
    up = np.array([0.0, 1.0, 0.0])
    turns = []
    for t in times:
        w = K.pose_matrices(rig, blocks, t)
        inv = np.linalg.inv(w[pelvis])
        behind = []
        for leg, knee in zip(legs, knees):
            d = (inv @ w[knee][:, 3])[:3] - (inv @ w[leg][:, 3])[:3]
            behind.append(np.degrees(np.arctan2(d[1], d[2])))   # the pelvis' front is -y, its down axis z
        H = w[pelvis][:3, :3] / np.linalg.norm(w[pelvis][:3, :3], axis=0)
        front = -H[:, 1] - (-H[:, 1] @ up) * up
        tip = np.degrees(np.arctan2(H[:, 2] @ front / np.linalg.norm(front), -H[:, 2] @ up))   # + = down axis forward
        turns.append(np.clip(max(behind) - spec['behind'], 0, max(0.0, tip + spec['lean'])))
    turns = np.radians(np.array(turns))
    if not turns.any():
        return data, 0.0
    eased = _ease(turns)
    pivot = np.mean([nodes[leg]['pos'] for leg in legs], axis=0)   # on the pelvis' x axis through both hip joints

    def at(b, t):
        return np.array([np.interp(t, b['times'], b['values'][:, c]) for c in range(b['values'].shape[1])])
    out = {pelvis: [], 'move': [], **{leg: [] for leg in legs}}
    for t, a in zip(times, eased):
        R = np.array([np.cos(-a / 2), np.sin(-a / 2), 0, 0])   # about the pelvis' own x: its down axis toward the thighs
        key = _wxyz(at(rot[pelvis], t))
        local = K._qmul(nodes[pelvis]['rot'], key)
        out[pelvis].append(K._qmul(key, R))
        pos = at(move, t) if move is not None else nodes[pelvis]['pos']
        out['move'].append(pos + K.quat_to_matrix(local) @ (pivot - K.quat_to_matrix(R) @ pivot))
        for leg in legs:
            rest = nodes[leg]['rot']
            out[leg].append(K._qmul(K._qmul(K._qmul(_inv(rest), _inv(R)), rest), _wxyz(at(rot[leg], t))))
    sec = dict(sections)[3]
    for bone in (pelvis, *legs):
        sec = K.replace_channel(sec, bone, 10, times, np.array([[q[1], q[2], q[3], q[0]] for q in out[bone]]))
    sec = (K.replace_channel if move is not None else K.add_channel)(sec, pelvis, 9, times, np.array(out['move']))
    return K.replace_section(data, 3, sec), float(np.degrees(eased.max()))


def recoloured_face(spec, sky, kai):
    """(model, texture) of a replacement eye material (chr_load_replace_material) for the target: a face-material
    file (spec['container'], with exactly those materials) holding the target's own eye materials (spec['materials']
    of spec['model'], their look exactly), drawn with a recoloured copy of its eye texture (spec['texture']: old, new
    name). The recolour is a gradient map: each texel keeps its brightness, its colour comes from the source's
    texture (spec['colours']) at that brightness, sampled where its hue lies in spec['hue'] (and saturation above
    spec['saturation']; below its darkest sample, the same colour darker). Spirit Unification turns Shizuna's eyes
    gold (Horizon's fc_chr5117_50, on her own face: not Estelle's)."""
    import colorsys
    import etcpak
    import texture2ddecoder

    def texels(raw, w, h, offset):
        px = texture2ddecoder.decode_bc7(bytes(raw[offset:offset + max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16]),
                                         max(w, 4), max(h, 4))
        return np.frombuffer(px, np.uint8).reshape(max(h, 4), max(w, 4), 4)[:h, :w, [2, 1, 0, 3]]
    weights = np.array([0.299, 0.587, 0.114])
    src = kai['asset_image'].read(IMAGE + spec['colours'] + '.dds')
    sh, sw = struct.unpack_from('<II', src, 12)
    colours = texels(src, sw, sh, 148)[..., :3].reshape(-1, 3)[::7].astype(float)
    hsv = np.array([colorsys.rgb_to_hsv(*(c / 255)) for c in colours])
    lo, hi = spec['hue']
    colours = colours[(hsv[:, 1] > spec['saturation']) & (hsv[:, 0] > lo) & (hsv[:, 0] < hi)]
    edges = np.linspace(0, 255, 65)
    level = colours @ weights
    lut = np.array([colours[(level >= a) & (level < b)].mean(0) if ((level >= a) & (level < b)).sum() > 20
                    else [np.nan] * 3 for a, b in zip(edges[:-1], edges[1:])])
    idx = np.arange(64)
    ok = ~np.isnan(lut[:, 0])
    lut = np.stack([np.interp(idx, idx[ok], lut[ok, c]) for c in range(3)], 1)
    first = idx[ok][0]
    lut[:first] = lut[first] * (idx[:first, None] + 0.5) / (first + 0.5)
    centres = (edges[:-1] + edges[1:]) / 2

    raw = sky_texture.decode(sky['image'].read(IMAGE + spec['texture'][0] + '.dds'))
    assert raw[84:88] == b'DX10' and struct.unpack_from('<I', raw, 128)[0] == 98, 'BC7 expected'
    h, w = struct.unpack_from('<II', raw, 12)
    mips = struct.unpack_from('<I', raw, 28)[0]
    out, q = bytearray(raw[:148]), 148
    for m in range(mips):
        mw, mh = max(1, w >> m), max(1, h >> m)
        size = max(1, (mw + 3) // 4) * max(1, (mh + 3) // 4) * 16
        t = texels(raw, mw, mh, q)
        q += size
        b = t[..., :3].astype(float) @ weights
        rgb = np.stack([np.interp(b, centres, lut[:, c]) for c in range(3)], -1).clip(0, 255).astype(np.uint8)
        pad = np.zeros((max(mh, 4), max(mw, 4), 4), np.uint8)
        pad[:mh, :mw] = np.concatenate([rgb, t[..., 3:]], -1)
        enc = etcpak.compress_bc7(pad.tobytes(), pad.shape[1], pad.shape[0])
        assert len(enc) == size, (m, len(enc), size)
        out += enc
    assert q == len(raw), (q, len(raw))
    texture = sky_texture.encode(bytes(out))

    container = sky['asset_common_model'].read(MODEL + spec['container'] + '.mdl')
    version, secs, _ = K.read_sections(container)
    own, tail = KM.parse_materials(dict(secs)[0])
    version, msecs, _ = K.read_sections(sky['asset_common_model'].read(MODEL + spec['model'] + '.mdl'))
    mats = {m['name']: m for m in KM.parse_materials(dict(msecs)[0])[0]}
    # the container's meshes (placeholder cubes) use its materials by index: same materials, same order (S30 cut
    # fc_chr5000_00's four to two and left cubes on materials that were gone: a crash when the aura loaded it)
    assert [m['name'] for m in own] == list(spec['materials']), [m['name'] for m in own]
    mats = [mats[n] for n in spec['materials']]
    for m in mats:
        for t in m['textures']:
            if t['image'] == spec['texture'][0]:
                t['image'] = spec['texture'][1]
    return K.replace_section(container, 0, KM.build_materials(mats, tail)), texture


def skeleton(data):
    version, secs, _ = K.read_sections(data)
    return K.parse_skeleton(dict(secs)[2], version)


def reachable(cfg, built):
    """Functions the battle can run: the target's own, the craft-row functions, what btlcom calls into the script,
    and everything they name (calls, threads, end hooks; any quoted function name, to be safe)."""
    P = cfg.PREFIX
    todo = [n for n in built.functions if not n.startswith(P)]
    todo += [c[1] for c in cfg.FORM_CRAFTS] + [P + cfg.REVERT_FN, P + 'CraftSwapOut', P + 'BattleEndRevertFx']
    seen = set()
    while todo:
        n = todo.pop()
        if n in seen or n not in built.functions:
            continue
        seen.add(n)
        todo += [s for s in re.findall(r'"(\w+)"', built.functions[n].body) if s in built.functions]
    return seen


def script_strings(cfg, built_py):
    """String constants of every function the port added or changed (ASSETS_FROM_REACHABLE: and the battle can
    reach; a source script also holds field, event and minigame code)."""
    import common as C   # the script tools (dev only: the extractor has a manifest instead)
    built, orig = C.read_script(built_py), C.read_script(cfg.TARGET_PY)
    keep = reachable(cfg, built) if getattr(cfg, 'ASSETS_FROM_REACHABLE', False) else set(built.functions)
    out = set()
    for f in built.functions.values():
        if f.name in orig.functions and f.body == orig.functions[f.name].body or f.name not in keep:
            continue
        for line in f.lines():
            if 'check_asset_name' not in line:   # identity checks of other characters, not loads
                out |= set(re.findall(r'"([A-Za-z0-9_/]+)"', line))
    return out


def effect_deps(tree):
    textures, models = set(), set()

    def walk(n, k=None):
        if isinstance(n, dict):
            for kk, v in n.items():
                walk(v, kk)
        elif isinstance(n, list):
            for v in n:
                walk(v, k)
        elif isinstance(n, str) and n:
            if k == 'filename':
                textures.add(n)
            elif k == 'model_name':
                models.add(n)
    walk(tree)
    return textures, models


def build(cfg, built_py, out, log, plan=None, sky_dir=None, progress=lambda done, total, what: None):
    """plan: a manifest (the extractor): its motions and effects instead of those the built script names."""
    kai = {n: Kai(os.path.join(cfg.SOURCE_GAME, n + '.p3a'))
           for n in ('asset_common_model', 'asset_image', 'vfx', 'asset_common_model_info')}
    sky = {n: Sky(os.path.join(sky_dir or ORIGINAL, n + '.pac'))
           for n in ('asset_common_model', 'image', 'vfx', 'asset_common_model_info')}
    files = {}   # (archive, path) -> bytes
    work = os.path.join(out, 'work')

    def add(pac, name, data):
        files[(pac + '.pac', name)] = data

    textures, models = set(), set()
    texture_source = {}   # texture name in Sky -> its name in the source game (renamed ones)
    # equipment (a style's weapon) under new names: Sky has other models (and their textures) under the source's
    for name, source in getattr(cfg, 'EQUIP_MODELS', {}).items():
        assert MODEL + name + '.mdl' not in sky['asset_common_model'].entries, name
        data = fix_model(kai['asset_common_model'].read(MODEL + source + '.mdl'), work, name)
        base, new_base = source.split('_')[0], name.split('_')[0]
        data, renamed = rename_textures(data, base, new_base)
        texture_source.update(renamed)
        anchor = getattr(cfg, 'EQUIP_ANCHOR', None)
        if anchor:
            pose = anchor.get('pose') and K.parse_animation(dict(K.read_sections(
                kai['asset_common_model'].read(MODEL + anchor['pose'] + '.mdl'))[1])[3])
            data = add_anchor(data, anchor, skeleton(kai['asset_common_model'].read(MODEL + cfg.RETARGET[0] + '.mdl')), pose,
                              skeleton(sky['asset_common_model'].read(MODEL + cfg.RETARGET[1] + '.mdl')))
        add('asset_common_model', MODEL + name + '.mdl', data)
        textures |= model_textures(data)
        if MODEL_INFO + source + '.mi' in kai['asset_common_model_info'].entries:
            add('asset_common_model_info', MODEL_INFO + name + '.mi',
                kai_model_info(kai['asset_common_model_info'].read(MODEL_INFO + source + '.mi')))
    # the form's model
    if getattr(cfg, 'FORM_MODEL', None):
        m = cfg.FORM_MODEL
        if 'sky_source' in m:   # one of the target game's own models under the form's name (a style's skin)
            data = sky['asset_common_model'].read(MODEL + m['sky_source'] + '.mdl')
        else:
            data = fix_model(kai['asset_common_model'].read(MODEL + m['source'] + '.mdl'), work, cfg.ROW, m.get('prefer', ()))
        add('asset_common_model', MODEL + cfg.ROW + '.mdl', data)
        textures |= model_textures(data)
        add('asset_common_model_info', MODEL_INFO + cfg.ROW + '.mi',
            sky['asset_common_model_info'].read(MODEL_INFO + m['model_info_from'] + '.mi'))
    # motions and camera tracks
    strings = set() if plan else script_strings(cfg, built_py)
    motions = plan['motions'] if plan else sorted(s for s in strings if MODEL + s + '.mdl' in kai['asset_common_model'].entries
                                                  and MODEL + s + '.mdl' not in sky['asset_common_model'].entries)
    rig = getattr(cfg, 'RETARGET', None)   # (source model, target model): a style plays the motions on the target
    if rig:
        rigs = (skeleton(kai['asset_common_model'].read(MODEL + rig[0] + '.mdl')),
                skeleton(sky['asset_common_model'].read(MODEL + rig[1] + '.mdl')))
        # camera tracks frame the source's head: scaled to the target's (S2: close-ups aimed above Estelle)
        head = K.world_positions(rigs[1])['Head'][1] / K.world_positions(rigs[0])['Head'][1]
        # the source's face expressions are keyed for its own face rig: on the target's they turned Estelle's eyes
        # white (S5); without them the target's face does what it does in its own crafts
        face = {n['name'] for n in skeleton(sky['asset_common_model'].read(MODEL + rig[1] + '_face.mdl'))}
    cleared, straightened, posed = [], [], []
    skirt = getattr(cfg, 'SKIRT_POSE', None)   # the target's own pose for bones the source doesn't key
    if skirt:
        version, secs, _ = K.read_sections(sky['asset_common_model'].read(MODEL + skirt['source'] + '.mdl'))
        skirt_keys = {b['bone']: b for b in K.parse_animation(dict(secs)[3])[0] if b['bone'] in skirt['bones']}
    for i, s in enumerate(motions):
        progress(i, len(motions), 'motions')
        data = kai['asset_common_model'].read(MODEL + s + '.mdl')
        if rig:
            data = (K.scale_camera_height(data, head) if s.endswith('_gs') else
                    K.retarget(data, *rigs, drop=face, rename=getattr(cfg, 'RETARGET_RENAME', None)))
            if skirt and re.fullmatch(skirt['clips'], s):
                data, n = pose_unkeyed(data, skirt_keys)
                if n:
                    posed.append(s)
            upright = getattr(cfg, 'PELVIS_UPRIGHT', None)   # the target's skirt kept out of its thighs
            if upright and re.fullmatch(upright['clips'], s):
                data, turn = upright_pelvis(data, rigs[1], upright)
                if turn:
                    straightened.append(f'{s} {turn:.0f}')
            clear = getattr(cfg, 'HEAD_CLEARANCE', None)   # a held weapon kept out of the target's head
            if clear and re.fullmatch(clear['clips'], s):
                hold = K.world_matrices(skeleton(files[('asset_common_model.pac', MODEL + clear['model'] + '.mdl')]))['hold']
                data, turn = keep_clear(data, rigs[1], hold, clear)
                if turn:
                    cleared.append(f'{s} {turn:.0f}')
        add('asset_common_model', MODEL + s + '.mdl', data)
    # effects
    schema = json.load(open(VFX_SCHEMA, encoding='utf8'))
    effects = plan['effects'] if plan else sorted(s for s in strings if VFX + s + '.vfx' in kai['vfx'].entries
                                                  and VFX + s + '.vfx' not in sky['vfx'].entries)
    for i, s in enumerate(effects):
        progress(i, len(effects), 'effects')
        tree, _ = vfx_convert.convert(json.loads(kai['vfx'].read(VFX + s + '.vfx')), schema)
        add('vfx', VFX + s + '.vfx', bjson.encode(tree))
        t, m = effect_deps(tree)
        textures |= t
        models |= m
    for s in sorted(models):
        if MODEL + s + '.mdl' not in sky['asset_common_model'].entries:
            data = fix_model(kai['asset_common_model'].read(MODEL + s + '.mdl'), work, s)
            add('asset_common_model', MODEL + s + '.mdl', data)
            textures |= model_textures(data)
    face = getattr(cfg, 'RECOLOURED_FACE', None)   # a replacement eye material of the target's, recoloured
    if face:
        model, texture = recoloured_face(face, sky, kai)
        add('asset_common_model', MODEL + face['name'] + '.mdl', model)
        add('image', IMAGE + face['texture'][1] + '.dds', texture)
        log.append(f"face {face['name']}: {face['texture'][0]} recoloured as {face['colours']} ({face['texture'][1]})")
    new_textures = sorted(t for t in textures if IMAGE + t + '.dds' not in sky['image'].entries)
    for i, t in enumerate(new_textures):
        progress(i, len(new_textures), 'textures')
        add('image', IMAGE + t + '.dds', sky_texture.encode(kai['asset_image'].read(IMAGE + texture_source.get(t, t) + '.dds')))
    files.update(listed(cfg))
    write(files, out)
    if not plan:
        manifest = dict(motions=motions, effects=effects, config={k: getattr(cfg, k) for k in CONFIG_KEYS if hasattr(cfg, k)},
                        files=sorted(f'{pac}/{name}' for pac, name in files))
        json.dump(manifest, open(os.path.join(out, 'manifest.json'), 'w', encoding='utf8'), indent=1, ensure_ascii=False)
    if posed:
        log.append(f"unkeyed bones posed as in the target's {skirt['source']}: {len(posed)} motions")
    if straightened:
        log.append(f'pelvis kept upright over the thighs (largest turn, degrees): {", ".join(straightened)}')
    if cleared:
        log.append(f'held weapon kept clear of the head (largest turn, degrees): {", ".join(cleared)}')
    log.append(f'assets: {len(motions)} motions, {len(effects)} effects, {len(models)} effect models referenced, '
               f'{len(new_textures)} new textures; {len(files)} files')
    return files


def listed(cfg):
    """Hand-picked files: ASSETS = {archive: [(folder, [paths inside the archive])]}."""
    files = {}
    for pac, sources in getattr(cfg, 'ASSETS', {}).items():
        for folder, names in sources:
            for n in names:
                files[(pac, n)] = open(os.path.join(folder, *n.split('/')), 'rb').read()
    return files


def write(files, out):
    for (pac, name), data in files.items():
        p = os.path.join(out, 'override', pac, *name.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(data)
