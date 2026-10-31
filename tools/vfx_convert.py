"""Convert Trails beyond the Horizon .vfx (text JSON) to Trails in the Sky 2nd Chapter .vfx (binary JSON).

Kai's effect schema is a subset of Sky's: Sky only adds fields. Comparing the effects Sky ships under the
same name as Kai's (the 20 that are real conversions, same emitters and modules) shows how Falcom filled them:
  Spawn            max_count = count, delay = 0
  Event            flag 0, target_node_name "", emit_rotation_limit 0
  *Renderer        is_billboard_y false, no_cull false, texture[].anime_info {start_random 0, start_random_snap 0}
  Light, Collider  constant defaults
  root             ambient_color_ratio 0 (aabb and play_speed are optional: absent in some Sky files)
So a Kai effect gets every field that (almost) every Sky object of the same kind has, with Sky's most common
value as the default, except max_count, which copies count. Keys are ordered the way Sky orders them.

Schema (field presence and defaults per path, learned from Sky's own effects) is cached as JSON:
  python vfx_convert.py schema <sky vfx folder> <schema.json>
  python vfx_convert.py convert <schema.json> <kai.vfx> <out.vfx>
  python vfx_convert.py check <schema.json> <kai folder> <sky folder>   (compare conversions with Sky's own)
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bjson  # noqa: E402

REQUIRED = 0.98  # a field is filled in when at least this share of Sky's objects of that kind have it
# Optional fields (texture anime_info, Light radius_band, Emitter is_scaling_for_widescreen, root play_speed) are
# left out: Falcom's conversions mostly omit them too, and older Sky effects ship without them.
# aabb is a bounding box computed per effect, so no default is valid; Sky ships effects without one.
NEVER_FILL = {'aabb'}


def child_path(path, key, value=None):
    return f'{path}/{key}'


def element_path(path, v):
    return path + (f'[{v["type"]}]' if isinstance(v, dict) and isinstance(v.get('type'), str) else '[]')


def build_schema(folder):
    count = collections.Counter()                       # path -> objects seen
    present = collections.defaultdict(collections.Counter)  # path -> key -> objects having it
    values = collections.defaultdict(collections.Counter)   # path/key -> json value -> count
    order = collections.defaultdict(collections.Counter)    # path -> key order tuple -> count

    def walk(v, path):
        if isinstance(v, dict):
            count[path] += 1
            order[path][tuple(v)] += 1
            for k, x in v.items():
                present[path][k] += 1
                if not isinstance(x, (dict, list)) or len(json.dumps(x)) < 400:
                    values[path + '/' + k][json.dumps(x, sort_keys=False)] += 1
                walk(x, path + '/' + k)
        elif isinstance(v, list):
            for x in v:
                walk(x, element_path(path, x))
    files = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if f.endswith('.vfx')]
    for f in files:
        walk(bjson.decode(open(f, 'rb').read()), '')
    schema = {}
    for path, n in count.items():
        keys = [k for k, c in present[path].items() if c / n >= REQUIRED]
        best = order[path].most_common(1)[0][0]
        rank = {k: i for i, k in enumerate(best)}
        schema[path] = {
            'objects': n,
            'required': sorted(keys, key=lambda k: rank.get(k, 1e9)),
            'order': list(best) + [k for k in present[path] if k not in rank],
            'defaults': {k: json.loads(values[path + '/' + k].most_common(1)[0][0])
                         for k in keys if values[path + '/' + k]},
        }
    return {'files': len(files), 'paths': schema}


def convert(obj, schema):
    paths = schema['paths']
    filled = collections.Counter()

    def fix(v, path):
        if isinstance(v, dict):
            s = paths.get(path)
            out = {k: fix(x, path + '/' + k) for k, x in v.items()}
            if s:
                for k in s['required']:
                    if k in out or k in NEVER_FILL:
                        continue
                    if k == 'max_count' and 'count' in out:
                        out[k] = out['count']
                    elif k in s['defaults']:
                        out[k] = s['defaults'][k]
                    else:
                        continue
                    filled[path + '/' + k] += 1
                rank = {k: i for i, k in enumerate(s['order'])}
                out = dict(sorted(out.items(), key=lambda kv: rank.get(kv[0], 1e9)))
            return out
        if isinstance(v, list):
            return [fix(x, element_path(path, x)) for x in v]
        return v
    return fix(obj, ''), filled


def main(argv):
    if len(argv) == 4 and argv[1] == 'schema':
        json.dump(build_schema(argv[2]), open(argv[3], 'w', encoding='utf8'), ensure_ascii=False, indent=0)
        return 0
    if len(argv) == 5 and argv[1] == 'convert':
        schema = json.load(open(argv[2], encoding='utf8'))
        out, _ = convert(json.load(open(argv[3], encoding='utf8')), schema)
        open(argv[4], 'wb').write(bjson.encode(out))
        return 0
    if len(argv) == 5 and argv[1] == 'check':
        schema = json.load(open(argv[2], encoding='utf8'))
        missing, extra, n = collections.Counter(), collections.Counter(), 0
        for r, _, fs in os.walk(argv[3]):
            for f in fs:
                rel = os.path.relpath(os.path.join(r, f), argv[3])
                sky_file = os.path.join(argv[4], rel)
                if not f.endswith('.vfx') or not os.path.exists(sky_file):
                    continue
                kai = json.load(open(os.path.join(r, f), encoding='utf8'))
                sky = bjson.decode(open(sky_file, 'rb').read())
                if [[m.get('type') for m in e.get('Module', [])] for e in kai['VFX'].get('Emitter', [])] != \
                        [[m.get('type') for m in e.get('Module', [])] for e in sky['VFX'].get('Emitter', [])]:
                    continue
                n += 1
                conv, _ = convert(kai, schema)

                def keys(a, b, path):
                    if isinstance(a, dict) and isinstance(b, dict):
                        for k in b:
                            if k not in a:
                                missing[path + '/' + k] += 1
                        for k in a:
                            if k not in b:
                                extra[path + '/' + k] += 1
                            else:
                                keys(a[k], b[k], path + '/' + k)
                    elif isinstance(a, list) and isinstance(b, list):
                        for x, y in zip(a, b):
                            keys(x, y, element_path(path, x))
                keys(conv, sky, '')
        print(f'{n} real conversions compared')
        print('fields Sky has that the conversion lacks:', missing.most_common(20))
        print('fields the conversion added that Sky lacks:', extra.most_common(20))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
