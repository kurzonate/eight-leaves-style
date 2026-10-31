"""Falcom binary JSON (Trails in the Sky 2nd Chapter .vfx and .mi files) <-> Python / text JSON.

Layout: "JSON", u32 0, u32 root offset, u32 0, then a key dictionary from 0x10 to the root: entries
{u32 ~crc32(key), key, NUL} in order of first use (the root's key "" first). Then the value tree, written
depth-first in source order, each node:
    u8 type, [u32 offset of its key's dictionary entry, unless type has bit 0x10 (array elements)], payload
    type 2 string   NUL-terminated UTF-8
    type 3 number   f64
    type 4 object   u32 count, count x u32 child node offsets, sorted by the children's key hashes
    type 5 array    u32 count, count x u32 element node offsets, in order
    type 6 bool     u8 (0 / 1)
The root is an object with key "" holding one member (e.g. {"VFX": {...}}).
Text JSON (Trails beyond the Horizon) is the same tree, so text -> binary is decode(json) + encode.

Usage:
  python bjson.py decode <in.bin> <out.json>
  python bjson.py encode <in.json> <out.bin>
  python bjson.py roundtrip <file or folder> ...     (decode + encode must reproduce every file byte for byte)
"""
import json
import os
import struct
import sys
import zlib

STR, NUM, OBJ, ARR, BOOL, NOKEY = 2, 3, 4, 5, 6, 0x10


def khash(key):
    return ~zlib.crc32(key.encode('utf8')) & 0xffffffff


def decode(data):
    if data[:4] != b'JSON':
        raise ValueError('not a binary JSON file')

    def key(p):
        off = struct.unpack_from('<I', data, p)[0]
        return data[off + 4:data.index(b'\0', off + 4)].decode('utf8')

    def node(p):
        t = data[p]
        base, keyed = t & 0xf, not t & NOKEY
        k = key(p + 1) if keyed else None
        q = p + 1 + (4 if keyed else 0)
        if base == STR:
            return k, data[q:data.index(b'\0', q)].decode('utf8')
        if base == NUM:
            v = struct.unpack_from('<d', data, q)[0]
            return k, int(v) if v.is_integer() and abs(v) < 2 ** 53 and not (v == 0 and str(v).startswith('-')) else v
        if base == BOOL:
            return k, data[q] != 0
        if base in (OBJ, ARR):
            n = struct.unpack_from('<I', data, q)[0]
            offs = sorted(struct.unpack_from(f'<{n}I', data, q + 4)) if base == OBJ else \
                struct.unpack_from(f'<{n}I', data, q + 4)
            kids = [node(o) for o in offs]
            return k, (dict(kids) if base == OBJ else [v for _, v in kids])
        raise ValueError(f'unknown node type {t:#x} at {p:#x}')

    k, v = node(struct.unpack_from('<I', data, 8)[0])
    assert k == '', k
    return v


def encode(obj):
    keys = {}
    order = []

    def collect(k, v):
        if k is not None and k not in keys:
            keys[k] = None
            order.append(k)
        if isinstance(v, dict):
            for kk, vv in v.items():
                collect(kk, vv)
        elif isinstance(v, list):
            for vv in v:
                collect(None, vv)
    collect('', obj)
    dic = bytearray()
    for k in order:
        keys[k] = 0x10 + len(dic)
        dic += struct.pack('<I', khash(k)) + k.encode('utf8') + b'\0'
    out = bytearray(b'JSON' + struct.pack('<III', 0, 0x10 + len(dic), 0)) + dic

    def write(k, v):
        here = len(out)
        head = (lambda t: bytes([t]) if k is None else bytes([t]) + struct.pack('<I', keys[k]))
        tag = NOKEY if k is None else 0
        if isinstance(v, bool):
            out.extend(head(BOOL | tag) + bytes([1 if v else 0]))
        elif isinstance(v, (int, float)):
            out.extend(head(NUM | tag) + struct.pack('<d', float(v)))
        elif isinstance(v, str):
            out.extend(head(STR | tag) + v.encode('utf8') + b'\0')
        elif isinstance(v, (dict, list)):
            items = list(v.items()) if isinstance(v, dict) else [(None, x) for x in v]
            out.extend(head((OBJ if isinstance(v, dict) else ARR) | tag) + struct.pack('<I', len(items)))
            table = len(out)
            out.extend(b'\0' * 4 * len(items))
            offs = [write(kk, vv) for kk, vv in items]
            if isinstance(v, dict):
                offs = [o for _, o in sorted(zip([khash(kk) for kk, _ in items], offs))]
            struct.pack_into(f'<{len(offs)}I', out, table, *offs)
        elif v is None:
            raise ValueError('null has no known encoding')
        else:
            raise TypeError(type(v))
        return here
    write('', obj)
    return bytes(out)


def roundtrip(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += [os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs if f.endswith(('.vfx', '.mi'))]
        else:
            files.append(p)
    bad = 0
    for f in files:
        d = open(f, 'rb').read()
        if d[:4] != b'JSON':
            continue
        if encode(decode(d)) != d:
            bad += 1
            if bad <= 5:
                print('MISMATCH', f)
    print(f'{len(files) - bad} / {len(files)} files round-trip byte-identically')
    return bad == 0


def main(argv):
    if len(argv) >= 3 and argv[1] == 'roundtrip':
        return 0 if roundtrip(argv[2:]) else 1
    if len(argv) == 4 and argv[1] == 'decode':
        json.dump(decode(open(argv[2], 'rb').read()), open(argv[3], 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        return 0
    if len(argv) == 4 and argv[1] == 'encode':
        open(argv[3], 'wb').write(encode(json.load(open(argv[2], encoding='utf8'))))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
