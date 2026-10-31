"""FPAC archive tool for Trails in the Sky 2nd Chapter (Kuro-engine .pac).

Format: "FPAC", u32 count, u32 data_start, u32 version, then `count` 32-byte
entries {u64 hash, u64 name_offset, u64 size, u64 data_offset} sorted by hash,
where hash = ~crc32(path). Names are NUL-terminated strings; file data is stored
uncompressed.

Usage:
  python fpac.py list    <in.pac> [glob]
  python fpac.py extract <in.pac> <out_dir> [glob ...]
  python fpac.py diff    <a.pac> <b.pac> [glob]
      Lists entries only in a, only in b, and in both with different contents.
  python fpac.py repack  <in.pac> <out.pac> <override_dir> [--allow-add]
      Rebuilds <in.pac>, replacing any entry whose path exists under
      <override_dir> (e.g. override_dir/table_en/t_skill.tbl). Files under
      <override_dir> that are not in the archive are an error unless
      --allow-add is given, in which case they are added as new entries.
"""
import fnmatch
import os
import struct
import sys
import zlib

HDR = struct.Struct('<4sIII')
ENT = struct.Struct('<QQQQ')


def path_hash(name):
    return (~zlib.crc32(name.encode('utf-8'))) & 0xffffffff


def read_index(path):
    with open(path, 'rb') as f:
        magic, count, data_start, ver = HDR.unpack(f.read(HDR.size))
        if magic != b'FPAC':
            raise ValueError(f'{path}: not an FPAC archive')
        f.seek(0)
        head = f.read(data_start)
    entries = []
    for i in range(count):
        h, name_off, size, data_off = ENT.unpack_from(head, HDR.size + i * ENT.size)
        name = head[name_off:head.index(b'\0', name_off)].decode('utf-8')
        entries.append({'name': name, 'hash': h, 'size': size, 'off': data_off, 'src': data_off})
    return head, ver, entries


def collect_overrides(override_dir):
    out = {}
    for root, _, files in os.walk(override_dir):
        for fn in files:
            full = os.path.join(root, fn)
            out[os.path.relpath(full, override_dir).replace(os.sep, '/')] = full
    return out


def repack(src, dst, override_dir, allow_add=False):
    head, ver, entries = read_index(src)
    overrides = collect_overrides(override_dir)
    known = {e['name'] for e in entries}
    added = sorted(set(overrides) - known)
    if added and not allow_add:
        raise ValueError(f'not in {os.path.basename(src)} (use --allow-add to add): ' + ', '.join(added))
    replaced = sorted(set(overrides) & known)

    # Original data order is preserved so an unmodified repack is byte-identical.
    order = sorted(entries, key=lambda e: e['off'])
    for name in added:
        order.append({'name': name, 'hash': path_hash(name), 'size': 0, 'off': None, 'src': None})
    by_hash = sorted(order, key=lambda e: e['hash'])
    if len({e['hash'] for e in by_hash}) != len(by_hash):
        raise ValueError('hash collision between archive paths')

    if added:
        # Rebuild index and name table from scratch.
        names = bytearray()
        base = HDR.size + len(by_hash) * ENT.size
        for e in by_hash:
            e['name_off'] = base + len(names)
            names += e['name'].encode('utf-8') + b'\0'
        head = bytearray(HDR.size + len(by_hash) * ENT.size) + names
    else:
        head = bytearray(head)
        for i, e in enumerate(by_hash):
            e['name_off'] = ENT.unpack_from(head, HDR.size + i * ENT.size)[1]
    data_start = len(head)

    tmp = dst + '.tmp'
    with open(src, 'rb') as fin, open(tmp, 'wb') as fout:
        fout.write(b'\0' * data_start)
        for e in order:
            e['off'] = fout.tell()
            if e['name'] in overrides:
                with open(overrides[e['name']], 'rb') as f:
                    blob = f.read()
                fout.write(blob)
                e['size'] = len(blob)
            else:
                fin.seek(e['src'])
                remaining = e['size']
                while remaining:
                    chunk = fin.read(min(remaining, 1 << 24))
                    fout.write(chunk)
                    remaining -= len(chunk)
        HDR.pack_into(head, 0, b'FPAC', len(by_hash), data_start, ver)
        for i, e in enumerate(by_hash):
            ENT.pack_into(head, HDR.size + i * ENT.size, e['hash'], e['name_off'], e['size'], e['off'])
        fout.seek(0)
        fout.write(head)
    os.replace(tmp, dst)
    return replaced, added


def diff(a, b, pat='*'):
    """(only in a, only in b, changed) entry names; contents compared in chunks."""
    ea = {e['name']: e for e in read_index(a)[2] if fnmatch.fnmatch(e['name'], pat)}
    eb = {e['name']: e for e in read_index(b)[2] if fnmatch.fnmatch(e['name'], pat)}
    changed = []
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        for name in sorted(set(ea) & set(eb)):
            x, y = ea[name], eb[name]
            if x['size'] != y['size']:
                changed.append(name)
                continue
            fa.seek(x['off'])
            fb.seek(y['off'])
            remaining = x['size']
            while remaining:
                n = min(remaining, 1 << 24)
                if fa.read(n) != fb.read(n):
                    changed.append(name)
                    break
                remaining -= n
    return sorted(set(ea) - set(eb)), sorted(set(eb) - set(ea)), changed


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, pac = argv[1], argv[2]
    if cmd == 'list':
        pat = argv[3] if len(argv) > 3 else '*'
        for e in read_index(pac)[2]:
            if fnmatch.fnmatch(e['name'], pat):
                print(e['size'], e['name'])
    elif cmd == 'extract':
        out_dir = argv[3]
        pats = argv[4:] or ['*']
        with open(pac, 'rb') as f:
            for e in read_index(pac)[2]:
                if not any(fnmatch.fnmatch(e['name'], p) for p in pats):
                    continue
                dest = os.path.join(out_dir, *e['name'].split('/'))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                f.seek(e['off'])
                with open(dest, 'wb') as o:
                    o.write(f.read(e['size']))
                print('extracted', e['name'])
    elif cmd == 'diff':
        only_a, only_b, changed = diff(pac, argv[3], argv[4] if len(argv) > 4 else '*')
        for n in only_a:
            print('only in a', n)
        for n in only_b:
            print('only in b', n)
        for n in changed:
            print('changed', n)
    elif cmd == 'repack':
        replaced, added = repack(pac, argv[3], argv[4], '--allow-add' in argv[5:])
        for n in replaced:
            print('replaced', n)
        for n in added:
            print('added', n)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
