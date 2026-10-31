"""Read-only extractor for NIS America .p3a archives (Trails beyond the Horizon PC).

Usage:
  python p3a.py list    <in.p3a> [glob]
  python p3a.py extract <in.p3a> <out_dir> <glob> [<glob> ...]

Layout: "PH3ARCV\\0", u32 flags (bit 0 = zstd dictionary present), u32 version, u64 count,
8-byte hash, then an extended header {8 bytes, u32 header size, u32 entry size (0x130)}.
Entries: name[256], u64 compression (0 none, 1 lz4, 2 zstd, 3 zstd+dictionary),
u64 compressed size, u64 size, u64 offset, u64 hash, u64 hash.
With flag bit 0, "P3ADICT\\0" + u64 size + dictionary follow the entry table.
"""
import fnmatch
import os
import struct
import sys

import zstandard


def read_index(path):
    with open(path, 'rb') as f:
        head = f.read(0x30)
        if head[:8] != b'PH3ARCV\0':
            raise ValueError(f'{path}: not a p3a archive')
        flags, version, count = struct.unpack_from('<IIQ', head, 8)
        ext_size, entry_size = struct.unpack_from('<II', head, 0x28)
        table = f.read(count * entry_size)
        entries = []
        for i in range(count):
            o = i * entry_size
            name = table[o:o + 256].split(b'\0')[0].decode('utf-8')
            comp, csize, usize, off = struct.unpack_from('<QQQQ', table, o + 256)
            entries.append({'name': name, 'comp': comp, 'csize': csize, 'size': usize, 'off': off})
        dictionary = None
        if flags & 1:
            magic = f.read(8)
            if magic != b'P3ADICT\0':
                raise ValueError(f'expected P3ADICT, got {magic!r}')
            size, = struct.unpack('<Q', f.read(8))
            dictionary = zstandard.ZstdCompressionDict(f.read(size))
    return {'flags': flags, 'version': version, 'entries': entries, 'dict': dictionary}


def read_entry(f, idx, e):
    f.seek(e['off'])
    raw = f.read(e['csize'])
    if e['comp'] == 0:
        return raw
    if e['comp'] == 2:
        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=e['size'])
    if e['comp'] == 3:
        return zstandard.ZstdDecompressor(dict_data=idx['dict']).decompress(raw, max_output_size=e['size'])
    if e['comp'] == 1:
        import lz4.block
        return lz4.block.decompress(raw, uncompressed_size=e['size'])
    raise ValueError(f"unknown compression {e['comp']} for {e['name']}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    idx = read_index(path)
    if cmd == 'list':
        pat = argv[3] if len(argv) > 3 else '*'
        for e in idx['entries']:
            if fnmatch.fnmatch(e['name'], pat):
                print(e['size'], e['name'])
    elif cmd == 'extract' and len(argv) >= 5:
        out_dir, pats = argv[3], argv[4:]
        with open(path, 'rb') as f:
            for e in idx['entries']:
                if any(fnmatch.fnmatch(e['name'], p) for p in pats):
                    data = read_entry(f, idx, e)
                    if len(data) != e['size']:
                        raise ValueError(f"size mismatch for {e['name']}")
                    dest = os.path.join(out_dir, *e['name'].split('/'))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as o:
                        o.write(data)
                    print('extracted', e['name'])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
