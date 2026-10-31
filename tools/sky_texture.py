"""Convert textures between Trails beyond the Horizon (raw .dds) and Trails in the Sky 2nd Chapter (LZ4 frame).

Sky stores every .dds in image.pac as one LZ4 frame: independent blocks, content size and content checksum,
no block checksums (FLG 0x6c), block size = the smallest LZ4 block size that holds the whole texture
(64 KB / 256 KB / 1 MB, 4 MB above that). Re-encoding a Sky texture this way reproduces it byte for byte.

Usage:
  python sky_texture.py encode <raw.dds> <sky.dds>
  python sky_texture.py check <sky .dds files or folder>   (decode + encode must reproduce each file)
"""
import os
import sys

import lz4.frame

SIZES = [(64 << 10, lz4.frame.BLOCKSIZE_MAX64KB), (256 << 10, lz4.frame.BLOCKSIZE_MAX256KB),
         (1 << 20, lz4.frame.BLOCKSIZE_MAX1MB)]


def encode(raw):
    assert raw[:4] == b'DDS ', 'not a DDS file'
    block = next((b for limit, b in SIZES if len(raw) <= limit), lz4.frame.BLOCKSIZE_MAX4MB)
    return lz4.frame.compress(raw, block_size=block, block_linked=False, content_checksum=True, store_size=True,
                              compression_level=0)


def decode(data):
    return lz4.frame.decompress(data)


def main(argv):
    if len(argv) == 4 and argv[1] == 'encode':
        open(argv[3], 'wb').write(encode(open(argv[2], 'rb').read()))
        return 0
    if len(argv) >= 3 and argv[1] == 'check':
        files = []
        for p in argv[2:]:
            files += [os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs if f.endswith('.dds')] \
                if os.path.isdir(p) else [p]
        bad = [f for f in files if encode(decode(open(f, 'rb').read())) != open(f, 'rb').read()]
        print(f'{len(files) - len(bad)} / {len(files)} re-encode byte-identically', bad[:5])
        return 0 if not bad else 1
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
