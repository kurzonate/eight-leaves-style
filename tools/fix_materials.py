"""Make a foreign (e.g. Trails beyond the Horizon) model's materials use shader-switch combinations
that Trails in the Sky 2nd Chapter actually ships, so the engine has a compiled shader for them.

Sky precompiles one shader per (shader, switch combination). A material asking for a combination
Sky never shipped is skipped in the main pass (the model only shows up in the shadow pass).

Usage:
  python fix_materials.py catalog <sky asset_common_model.pac> <catalog.json>
  python fix_materials.py fix <in.mdl> <catalog.json> <out.mdl> [--drop SWITCH_X ...] [--prefer MAT=model/material ...]

"fix" removes the --drop switches (default: SWITCH_GRENDEL), then replaces each material's switch
list with the closest combination in the catalog for the same shader (exact match if one exists).
Textures and parameters are kept. --prefer forces a specific catalogued combination for a material.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kuro_materials as KM  # noqa: E402
import kuro_mdl as K  # noqa: E402


def build_catalog(pac, out):
    from fpac import read_index
    catalog = {}
    with open(pac, 'rb') as f:
        for e in read_index(pac)[2]:
            if '_m_' in e['name']:
                continue
            f.seek(e['off'])
            d = f.read(e['size'])
            if d[:4] != b'MDL ':
                continue
            secs = dict(K.read_sections(d)[1])
            if 0 not in secs:
                continue
            for m in KM.parse_materials(secs[0])[0]:
                key = json.dumps([m['shader'], sorted(m['switches'])])
                catalog.setdefault(key, []).append(e['name'].rsplit('/', 1)[1] + '/' + m['name'])
    rows = [[json.loads(k)[0], json.loads(k)[1], v[:5]] for k, v in catalog.items()]
    json.dump(rows, open(out, 'w'), indent=0)
    print(f'{len(rows)} distinct shader/switch combinations -> {out}')


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['catalog', 'fix'])
    ap.add_argument('src')
    ap.add_argument('catalog')
    ap.add_argument('out', nargs='?')
    ap.add_argument('--drop', nargs='*', default=['SWITCH_GRENDEL'])
    ap.add_argument('--prefer', nargs='*', default=[], help='MATERIAL=model.mdl/material')
    a = ap.parse_args(argv[1:])
    if a.cmd == 'catalog':
        build_catalog(a.src, a.catalog)
        return 0

    data = open(a.src, 'rb').read()
    out = fix(data, load_catalog(a.catalog), a.drop, dict(p.split('=', 1) for p in a.prefer), print)
    open(a.out, 'wb').write(out)
    print(f'wrote {a.out} ({len(data)} -> {len(out)} bytes)')
    return 0


def load_catalog(path):
    return [(sh, [tuple(x) for x in sw], ex) for sh, sw, ex in json.load(open(path))]


def fix(data, rows, drop=('SWITCH_GRENDEL',), prefer=None, report=lambda s: None):
    """The model with each material's switches replaced by the closest catalogued combination (see above)."""
    prefer = prefer or {}
    secs = dict(K.read_sections(data)[1])
    mats, tail = KM.parse_materials(secs[0])
    for m in mats:
        want = {(n, v) for n, v in m['switches'] if n not in drop}
        cands = [r for r in rows if r[0] == m['shader']]
        if m['name'] in prefer:
            pick = next(r for r in cands if prefer[m['name']] in r[2])
        else:
            pick = min(cands, key=lambda r: (len(want - set(r[1])) + len(set(r[1]) - want), -len(r[2])))
        lost, added = sorted(want - set(pick[1])), sorted(set(pick[1]) - want)
        m['switches'] = [list(x) for x in pick[1]]
        report(f"[{m['name']}] {m['shader']}: using {pick[2][0]}"
               + ('  (exact)' if not lost and not added else f'  lost {lost} added {added}'))
    return K.replace_section(data, 0, KM.build_materials(mats, tail))


if __name__ == '__main__':
    sys.exit(main(sys.argv))
