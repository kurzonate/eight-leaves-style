"""Minimal reader/writer for Kuro-engine MDL skeleton and animation sections
(Trails in the Sky 2nd Chapter, MDL v4/v5).

MDL: "MDL ", u32 version, u32 size-ish, then sections {u32 type, u32 size, data}.
Type 2 = node hierarchy, type 3 = animation. Layouts follow eArmada8's kuro_mdl_tool.
Rotation keys are stored relative to the node's rest rotation (final = rest * key).
"""
import struct

import numpy as np

KEY_WIDTH = {9: 3, 10: 4, 11: 3, 12: 1, 13: 2}  # floats per value, by channel type
TYPE_NAME = {9: 'translate', 10: 'rotate', 11: 'scale'}


def read_sections(data):
    if data[:4] != b'MDL ':
        raise ValueError('not an MDL file')
    version = struct.unpack_from('<I', data, 4)[0]
    sections, o = [], 12
    while o + 8 <= len(data):
        t, size = struct.unpack_from('<II', data, o)
        if o + 8 + size > len(data):
            break
        sections.append((t, data[o + 8:o + 8 + size]))
        o += 8 + size
    return version, sections, data[o:]


def _pascal(buf, q):
    n = buf[q]
    return buf[q + 1:q + 1 + n].decode('ascii'), q + 1 + n


def _make_pascal(s):
    b = s.encode('ascii')
    return bytes([len(b)]) + b


def rpy_to_quat(r):
    """Kuro euler (roll, pitch, yaw) -> quaternion (w, x, y, z)."""
    cr, sr = np.cos(r[0] / 2), np.sin(r[0] / 2)
    cp, sp = np.cos(r[1] / 2), np.sin(r[1] / 2)
    cy, sy = np.cos(r[2] / 2), np.sin(r[2] / 2)
    return np.array([cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy,
                     cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy])


def parse_skeleton(sec, version):
    n = struct.unpack_from('<I', sec, 0)[0]
    q, nodes = 4, []
    for _ in range(n):
        name, q = _pascal(sec, q)
        ntype, mesh = struct.unpack_from('<Ii', sec, q); q += 8
        pos = struct.unpack_from('<3f', sec, q); q += 12
        q += 16  # unknown quaternion
        q += 4   # skin mesh
        rpy = struct.unpack_from('<3f', sec, q); q += 12
        scale = struct.unpack_from('<3f', sec, q); q += 12
        q += 12  # unknown
        if version > 4:
            q += 4
        nchild = struct.unpack_from('<I', sec, q)[0]; q += 4
        children = list(struct.unpack_from('<%dI' % nchild, sec, q)); q += 4 * nchild
        nodes.append({'name': name, 'pos': np.array(pos), 'rot': rpy_to_quat(rpy),
                      'scale': np.array(scale), 'children': children, 'parent': -1})
    for i, nd in enumerate(nodes):
        for c in nd['children']:
            nodes[c]['parent'] = i
    return nodes


def _infer_width(sec, q, nkeys, last):
    """Key width of a channel type not in KEY_WIDTH: the one after which the next block (or, for the last block,
    the closing time range) parses."""
    for w in range(1, 9):
        end = q + nkeys * (1 + w + 5) * 4
        if last:
            if end + 8 == len(sec):
                return w
        elif end < len(sec) and sec[end] and all(32 <= c < 127 for c in sec[end + 1:end + 1 + sec[end]]):
            return w
    raise ValueError('cannot infer key width')


def matrix_to_rpy(R):
    """Inverse of rpy_to_quat (R = Rz(yaw) Ry(pitch) Rx(roll)), radians."""
    pitch = np.arcsin(np.clip(-R[2, 0], -1.0, 1.0))
    return np.array([np.arctan2(R[2, 1], R[2, 2]), pitch, np.arctan2(R[1, 0], R[0, 0])])


def add_node(data, name, parent, pos, rpy, like):
    """The model with a new node `name` (a copy of node `like`: its type, mesh and unknown fields) under `parent`,
    at local pos / rpy (radians); appended last, so no existing node index moves."""
    version, sections, _ = read_sections(data)
    sec = dict(sections)[2]
    n = struct.unpack_from('<I', sec, 0)[0]
    q, recs = 4, []
    for _ in range(n):
        start = q
        nm, q = _pascal(sec, q)
        fixed = 8 + 12 + 16 + 4 + 12 + 12 + 12 + (4 if version > 4 else 0)
        body = sec[q:q + fixed]
        q += fixed
        nchild = struct.unpack_from('<I', sec, q)[0]
        children = list(struct.unpack_from('<%dI' % nchild, sec, q + 4))
        q += 4 + 4 * nchild
        recs.append([nm, bytearray(body), children])
    names = [r[0] for r in recs]
    assert name not in names, name
    body = bytearray(recs[names.index(like)][1])
    struct.pack_into('<3f', body, 8, *pos)                 # after type, mesh
    struct.pack_into('<3f', body, 8 + 12 + 16 + 4, *rpy)   # after pos, unknown quat, skin mesh
    recs.append([name, body, []])
    recs[names.index(parent)][2].append(n)
    out = bytearray(struct.pack('<I', n + 1))
    for nm, b, ch in recs:
        out += _make_pascal(nm) + b + struct.pack('<I', len(ch)) + struct.pack('<%dI' % len(ch), *ch)
    return replace_section(data, 2, bytes(out) + sec[q:])


def parse_animation(sec):
    n = struct.unpack_from('<I', sec, 0)[0]
    q, blocks = 4, []
    for i in range(n):
        name, q = _pascal(sec, q)
        bone, q = _pascal(sec, q)
        ctype, u0, u1, nkeys = struct.unpack_from('<4I', sec, q); q += 16
        w = KEY_WIDTH.get(ctype) or _infer_width(sec, q, nkeys, i == n - 1)
        keys = np.frombuffer(sec, dtype='<f4', count=nkeys * (1 + w + 5), offset=q).reshape(nkeys, 1 + w + 5)
        q += keys.nbytes
        blocks.append({'name': name, 'bone': bone, 'type': ctype, 'unk0': u0, 'unk1': u1,
                       'times': keys[:, 0].copy(), 'values': keys[:, 1:1 + w].copy(), 'extra': keys[:, 1 + w:].copy()})
    tmin, tmax = struct.unpack_from('<2f', sec, q)
    return blocks, (tmin, tmax)


def edit_translations(sec, edit):
    """The animation section with every translate channel's values replaced by edit(bone, values) (an n x 3
    array); every other byte is kept."""
    out = bytearray(sec)
    n = struct.unpack_from('<I', sec, 0)[0]
    q = 4
    for i in range(n):
        name, q = _pascal(sec, q)
        bone, q = _pascal(sec, q)
        ctype, u0, u1, nkeys = struct.unpack_from('<4I', sec, q); q += 16
        w = KEY_WIDTH.get(ctype) or _infer_width(sec, q, nkeys, i == n - 1)
        keys = np.frombuffer(sec, dtype='<f4', count=nkeys * (1 + w + 5), offset=q).reshape(nkeys, 1 + w + 5).copy()
        if ctype == 9:
            keys[:, 1:4] = edit(bone, keys[:, 1:4].astype(np.float64))
            out[q:q + keys.nbytes] = keys.astype('<f4').tobytes()
        q += keys.nbytes
    return bytes(out)


def edit_rotations(sec, edit):
    """The animation section with every rotation channel's keys (x, y, z, w) replaced by edit(bone, keys) (an n x 4
    array); every other byte is kept."""
    out = bytearray(sec)
    n = struct.unpack_from('<I', sec, 0)[0]
    q = 4
    for i in range(n):
        name, q = _pascal(sec, q)
        bone, q = _pascal(sec, q)
        ctype, u0, u1, nkeys = struct.unpack_from('<4I', sec, q); q += 16
        w = KEY_WIDTH.get(ctype) or _infer_width(sec, q, nkeys, i == n - 1)
        keys = np.frombuffer(sec, dtype='<f4', count=nkeys * (1 + w + 5), offset=q).reshape(nkeys, 1 + w + 5).copy()
        if ctype == 10:
            keys[:, 1:5] = edit(bone, keys[:, 1:5].astype(np.float64))
            out[q:q + keys.nbytes] = keys.astype('<f4').tobytes()
        q += keys.nbytes
    return bytes(out)


def post_rotate(data, bone, delta):
    """A motion with one bone's rotation keys followed by a constant rotation in the bone's own frame (delta: x, y,
    z, w): final = rest * key * delta. The bone must have a rotation channel."""
    dx, dy, dz, dw = delta
    hit = []

    def edit(b, keys):
        if b != bone:
            return keys
        hit.append(b)
        out = []
        for x, y, z, w in keys:
            r = _qmul(np.array([w, x, y, z]), np.array([dw, dx, dy, dz]))
            out.append([r[1], r[2], r[3], r[0]])
        return np.array(out)
    version, sections, _ = read_sections(data)
    sec = edit_rotations(dict(sections)[3], edit)
    assert hit, f'{bone} has no rotation channel'
    return replace_section(data, 3, sec)


def replace_channel(sec, bone, ctype, times, values):
    """The animation section with one existing channel's keys replaced (times; values n x width; extras zero)."""
    n = struct.unpack_from('<I', sec, 0)[0]
    q, out, hit = 4, bytearray(struct.pack('<I', n)), False
    for i in range(n):
        start = q
        name, q = _pascal(sec, q)
        b, q = _pascal(sec, q)
        t, u0, u1, nkeys = struct.unpack_from('<4I', sec, q)
        w = KEY_WIDTH.get(t) or _infer_width(sec, q + 16, nkeys, i == n - 1)
        end = q + 16 + nkeys * (1 + w + 5) * 4
        if b == bone and t == ctype:
            keys = np.hstack([np.asarray(times, '<f4')[:, None], np.asarray(values, '<f4'),
                              np.zeros((len(times), 5), '<f4')]).astype('<f4')
            out += _make_pascal(name) + _make_pascal(b) + struct.pack('<4I', t, u0, u1, len(times)) + keys.tobytes()
            hit = True
        else:
            out += sec[start:end]
        q = end
    assert hit, f'{bone}: no channel of type {ctype}'
    return bytes(out) + sec[q:]


def add_channel(sec, bone, ctype, times, values, name=None):
    """The animation section with a new channel for `bone` (times; values n x width; extras zero), named as the
    source's own are (Hips_translate)."""
    n = struct.unpack_from('<I', sec, 0)[0]
    q = 4
    for i in range(n):
        _, q = _pascal(sec, q)
        _, q = _pascal(sec, q)
        t, u0, u1, nkeys = struct.unpack_from('<4I', sec, q); q += 16
        w = KEY_WIDTH.get(t) or _infer_width(sec, q, nkeys, i == n - 1)
        q += nkeys * (1 + w + 5) * 4
    name = name or bone + {9: '_translate', 10: '_rotate'}[ctype]
    keys = np.hstack([np.asarray(times, '<f4')[:, None], np.asarray(values, '<f4'),
                      np.zeros((len(times), 5), '<f4')]).astype('<f4')
    new = _make_pascal(name) + _make_pascal(bone) + struct.pack('<4I', ctype, 0, 0, len(times)) + keys.tobytes()
    return struct.pack('<I', n + 1) + sec[4:q] + new + sec[q:]


def rename_channels(sec, names):
    """The animation section with the channels of the bones in `names` (old -> new) moved to the new bones."""
    n = struct.unpack_from('<I', sec, 0)[0]
    q, out = 4, bytearray(struct.pack('<I', n))
    for i in range(n):
        name, q = _pascal(sec, q)
        bone, q = _pascal(sec, q)
        start = q
        ctype, u0, u1, nkeys = struct.unpack_from('<4I', sec, q)
        w = KEY_WIDTH.get(ctype) or _infer_width(sec, q + 16, nkeys, i == n - 1)
        q += 16 + nkeys * (1 + w + 5) * 4
        if bone in names:
            name, bone = name.replace(bone, names[bone], 1), names[bone]
        out += _make_pascal(name) + _make_pascal(bone) + sec[start:q]
    return bytes(out) + sec[q:]


def drop_channels(sec, bones):
    """The animation section without the channels of `bones`; every other byte is kept."""
    n = struct.unpack_from('<I', sec, 0)[0]
    q, kept = 4, []
    for i in range(n):
        start = q
        _, q = _pascal(sec, q)
        bone, q = _pascal(sec, q)
        ctype, u0, u1, nkeys = struct.unpack_from('<4I', sec, q); q += 16
        w = KEY_WIDTH.get(ctype) or _infer_width(sec, q, nkeys, i == n - 1)
        q += nkeys * (1 + w + 5) * 4
        if bone not in bones:
            kept.append(sec[start:q])
    return struct.pack('<I', len(kept)) + b''.join(kept) + sec[q:]


def retarget(data, src_nodes, dst_nodes, scaled=('Root', 'Up_Point'), drop=(), rename=None):
    """A motion made for one rig (src_nodes, parse_skeleton) played by another of the same bone names: each
    translated bone keeps its motion relative to its rest position, moved onto the target's rest position. The
    bones in `scaled` (the body's own movement) also scale that motion by the rigs' height ratio (Root heights).
    Bones the target lacks, and rotations (stored relative to the rest rotation), are left as they are. The
    channels of `drop` are removed (a face rigged differently: its expressions don't carry over); those of `rename`
    (source bone -> target bone) drive the target's bone of another name."""
    rename = rename or {}
    src, dst = {n['name']: n for n in src_nodes}, {n['name']: n for n in dst_nodes}
    ratio = dst['Root']['pos'][1] / src['Root']['pos'][1]

    def edit(bone, v):
        target = rename.get(bone, bone)
        if bone not in src or target not in dst:
            return v
        return dst[target]['pos'] + (v - src[bone]['pos']) * (ratio if bone in scaled else 1.0)
    version, sections, _ = read_sections(data)
    sec = edit_translations(dict(sections)[3], edit)
    if rename:
        sec = rename_channels(sec, rename)
    return replace_section(data, 3, drop_channels(sec, set(drop)) if drop else sec)


def quat_to_matrix(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def world_matrices(nodes):
    """Rest-pose world transform (4x4) of every node (parse_skeleton), by name."""
    out = {}

    def walk(i, parent):
        n = nodes[i]
        local = np.eye(4)
        local[:3, :3] = quat_to_matrix(n['rot']) * n['scale']
        local[:3, 3] = n['pos']
        m = parent @ local
        out[n['name']] = m
        for c in n['children']:
            walk(c, m)
    for i, n in enumerate(nodes):
        if n['parent'] == -1:
            walk(i, np.eye(4))
    return out


def _qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def pose_matrices(nodes, blocks, t):
    """World transform (4x4) of every node with a motion (parse_animation blocks) applied at time t: translation
    keys replace the rest position, rotation keys (x, y, z, w) multiply the rest rotation (final = rest * key)."""
    names = {n['name'] for n in nodes}
    keys = {}
    for b in blocks:
        if b['bone'] in names and b['type'] in (9, 10):
            keys.setdefault(b['bone'], {})[b['type']] = np.array(
                [np.interp(t, b['times'], b['values'][:, k]) for k in range(b['values'].shape[1])])
    out = {}

    def walk(i, parent):
        n = nodes[i]
        k = keys.get(n['name'], {})
        rot = n['rot']
        if 10 in k:
            x, y, z, w = k[10]
            rot = _qmul(rot, np.array([w, x, y, z]) / np.linalg.norm(k[10]))
        local = np.eye(4)
        local[:3, :3] = quat_to_matrix(rot) * n['scale']
        local[:3, 3] = k[9] if 9 in k else n['pos']
        m = parent @ local
        out[n['name']] = m
        for c in n['children']:
            walk(c, m)
    for i, n in enumerate(nodes):
        if n['parent'] == -1:
            walk(i, np.eye(4))
    return out


def world_positions(nodes):
    """Rest-pose world position of every node (parse_skeleton), by name."""
    return {k: m[:3, 3] for k, m in world_matrices(nodes).items()}


CAMERA_NODES = ('gameCamera', 'gameCamera_aim')   # eye and aim point of a camera track (*_gs.mdl), from the scene origin


def scale_camera_height(data, ratio, nodes=CAMERA_NODES):
    """A camera track made for one character's size framing another's: the eye and aim heights scaled by `ratio`
    (the characters' head heights); horizontal distances, and so the framing of the targets, are kept."""
    def edit(bone, v):
        if bone in nodes:
            v[:, 1] *= ratio
        return v
    version, sections, _ = read_sections(data)
    return replace_section(data, 3, edit_translations(dict(sections)[3], edit))


def build_animation(blocks):
    out = bytearray(struct.pack('<I', len(blocks)))
    tmin, tmax = float('inf'), float('-inf')
    for b in blocks:
        times = np.asarray(b['times'], dtype='<f4')
        values = np.asarray(b['values'], dtype='<f4')
        extra = np.zeros((len(times), 5), dtype='<f4')
        out += _make_pascal(b['name']) + _make_pascal(b['bone'])
        out += struct.pack('<4I', b['type'], b.get('unk0', 0), b.get('unk1', 0), len(times))
        out += np.hstack([times[:, None], values, extra]).astype('<f4').tobytes()
        tmin, tmax = min(tmin, float(times.min())), max(tmax, float(times.max()))
    out += struct.pack('<2f', tmin, tmax)
    return bytes(out)


def replace_animation(data, new_anim_section):
    """Return MDL bytes with the type-3 section replaced; header/version/skeleton kept."""
    return replace_section(data, 3, new_anim_section)


def replace_section(data, section_type, new_section):
    """Return MDL bytes with one section replaced; everything else kept."""
    version, sections, tail = read_sections(data)
    body = bytearray()
    for t, sec in sections:
        if t == section_type:
            sec = new_section
        body += struct.pack('<II', t, len(sec)) + sec
    size_field = struct.unpack_from('<I', data, 8)[0]
    # The header size field equals file size minus 16 in the shipped files; keep that relation.
    delta = len(data) - size_field
    total = 12 + len(body) + len(tail)
    return data[:8] + struct.pack('<I', total - delta) + bytes(body) + tail
