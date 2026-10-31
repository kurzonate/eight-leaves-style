"""In-place, reversible patching of Trails in the Sky 2nd Chapter .pac archives (FPAC, see tools/fpac.py).

An FPAC file is its index (header, entries sorted by path hash with absolute data offsets, path names) followed by
the file data. Patching never rewrites the archive: new and replaced files are appended after its end, the files
whose data sits where the larger index goes are copied there too, and the new index is written over the start.
Before anything is written, an undo file keeps the archive's original size and the bytes the new index covers;
unpatching writes them back and cuts the archive to its original size, which gives back the very same file.

The archive stays valid at every step: until the new index is written, the old one only points at old data.
"""
import hashlib
import json
import os
import struct
import zlib

HDR = struct.Struct('<4sIII')       # "FPAC", count, data_start (= index length), version
ENT = struct.Struct('<QQQQ')        # path hash, name offset, size, data offset
UNDO_MAGIC = b'SKYPATCH-UNDO-1\n'


def path_hash(name):
    return (~zlib.crc32(name.encode('utf-8'))) & 0xffffffff


def read_index(f):
    f.seek(0)
    magic, count, data_start, ver = HDR.unpack(f.read(HDR.size))
    if magic != b'FPAC':
        raise ValueError('not an FPAC archive')
    f.seek(0)
    head = f.read(data_start)
    entries = []
    for i in range(count):
        h, name_off, size, off = ENT.unpack_from(head, HDR.size + i * ENT.size)
        entries.append({'name': head[name_off:head.index(b'\0', name_off)].decode('utf-8'), 'hash': h,
                        'size': size, 'off': off})
    return ver, entries


def read_entry(path, name):
    with open(path, 'rb') as f:
        _, entries = read_index(f)
        e = next((e for e in entries if e['name'] == name), None)
        if e is None:
            return None
        f.seek(e['off'])
        return f.read(e['size'])


def _copy(f, src, size, dst):
    """Copy size bytes from offset src to offset dst (dst at or after the end: no overlap)."""
    done = 0
    while done < size:
        n = min(size - done, 1 << 24)
        f.seek(src + done)
        chunk = f.read(n)
        f.seek(dst + done)
        f.write(chunk)
        done += n


def patch(path, files, undo_path):
    """Put files ({path inside the archive: bytes}) into the archive at path; the undo file goes to undo_path."""
    if os.path.exists(undo_path):
        raise RuntimeError(f'{os.path.basename(path)} is already patched (undo file {undo_path})')
    with open(path, 'r+b') as f:
        ver, entries = read_index(f)
        size = f.seek(0, 2)
        names = {e['name'] for e in entries}
        entries += [{'name': n, 'hash': path_hash(n), 'size': 0, 'off': 0} for n in sorted(set(files) - names)]
        by_hash = sorted(entries, key=lambda e: e['hash'])
        if len({e['hash'] for e in by_hash}) != len(by_hash):
            raise ValueError('path hash collision')
        index_len = HDR.size + len(by_hash) * ENT.size + sum(len(e['name'].encode('utf-8')) + 1 for e in by_hash)

        # the undo file first: the original size and every byte the new index will cover
        f.seek(0)
        prefix = f.read(index_len)
        meta = {'size': size, 'prefix': len(prefix), 'archive': os.path.basename(path)}
        end = size
        moves = []
        for e in sorted(entries, key=lambda e: e['off']):
            if e['name'] in files:
                continue
            if e['off'] < index_len:          # data where the larger index goes: copied after the end
                if e['size']:
                    moves.append((e['off'], e['size'], end))
                e['off'] = end
                end += e['size']
        for name in sorted(files):
            e = next(x for x in entries if x['name'] == name)
            e['off'], e['size'] = end, len(files[name])
            end += len(files[name])

        index = bytearray(index_len)
        HDR.pack_into(index, 0, b'FPAC', len(by_hash), index_len, ver)
        q = HDR.size + len(by_hash) * ENT.size
        for i, e in enumerate(by_hash):
            n = e['name'].encode('utf-8') + b'\0'
            ENT.pack_into(index, HDR.size + i * ENT.size, e['hash'], q, e['size'], e['off'])
            index[q:q + len(n)] = n
            q += len(n)
        meta['patched_size'] = end
        meta['patched_index_sha1'] = hashlib.sha1(index).hexdigest()
        tmp = undo_path + '.tmp'
        with open(tmp, 'wb') as u:
            u.write(UNDO_MAGIC + json.dumps(meta).encode('utf-8') + b'\n' + prefix)
            u.flush()
            os.fsync(u.fileno())
        os.replace(tmp, undo_path)

        # data after the old end (the old index still describes the old file), then the new index
        for src, n, dst in moves:
            _copy(f, src, n, dst)
        for name in sorted(files):
            e = next(x for x in entries if x['name'] == name)
            f.seek(e['off'])
            f.write(files[name])
        f.flush()
        os.fsync(f.fileno())
        f.seek(0)
        f.write(index)
        f.flush()
        os.fsync(f.fileno())


def read_undo(undo_path):
    with open(undo_path, 'rb') as u:
        data = u.read()
    if not data.startswith(UNDO_MAGIC):
        raise ValueError(f'{undo_path}: not an undo file')
    line_end = data.index(b'\n', len(UNDO_MAGIC))
    meta = json.loads(data[len(UNDO_MAGIC):line_end])
    return meta, data[line_end + 1:]


def is_patched_as_recorded(path, undo_path):
    meta, _ = read_undo(undo_path)
    with open(path, 'rb') as f:
        size = f.seek(0, 2)
        f.seek(0)
        _, count, index_len, _ = HDR.unpack(f.read(HDR.size))
        f.seek(0)
        index = f.read(index_len)
    return size == meta['patched_size'] and hashlib.sha1(index).hexdigest() == meta['patched_index_sha1']


def unpatch(path, undo_path, force=False):
    """Give back the original archive. Refuses if it isn't the archive this undo file was made for, as patched
    (a game update replaced it, say), unless force."""
    meta, prefix = read_undo(undo_path)
    if not force and not is_patched_as_recorded(path, undo_path):
        raise RuntimeError(f'{os.path.basename(path)} changed since it was patched; not restoring it')
    with open(path, 'r+b') as f:
        f.seek(0)
        f.write(prefix)
        f.truncate(meta['size'])
        f.flush()
        os.fsync(f.fileno())
    os.remove(undo_path)
