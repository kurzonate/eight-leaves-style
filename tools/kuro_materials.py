"""Read / rewrite the material section (type 0) of Kuro-engine MDL files (Sky 2nd, MDL v2+).

Layout follows eArmada8's kuro_mdl_tool (obtain_material_data). Each material:
  pascal material_name, pascal shader_name, pascal str3,
  u32 n_textures, n x {pascal image, i32 slot, i32 unk0, i32 wrapS, i32 wrapT, i32 unk3},
  u32 n_params,   n x {pascal name, u32 type, value (size depends on type)},
  u32 n_switches, n x {pascal name, i32 value},
  u32 n_uv, n bytes, u32 n_unk1, n bytes, 20 bytes unknown2.
"""
import struct

PARAM_SIZE = {0: 4, 1: 4, 2: 8, 3: 12, 4: 4, 5: 8, 6: 12, 7: 16, 8: 64, 0xFFFFFFFF: 0}


def _pascal(b, q):
    n = b[q]
    return b[q + 1:q + 1 + n].decode('ascii'), q + 1 + n


def _mk(s):
    e = s.encode('ascii')
    return bytes([len(e)]) + e


def parse_materials(sec):
    n = struct.unpack_from('<I', sec, 0)[0]
    q, mats = 4, []
    for _ in range(n):
        m = {}
        m['name'], q = _pascal(sec, q)
        m['shader'], q = _pascal(sec, q)
        m['str3'], q = _pascal(sec, q)
        nt = struct.unpack_from('<I', sec, q)[0]; q += 4
        m['textures'] = []
        for _ in range(nt):
            img, q = _pascal(sec, q)
            vals = list(struct.unpack_from('<5i', sec, q)); q += 20
            m['textures'].append({'image': img, 'vals': vals})
        npar = struct.unpack_from('<I', sec, q)[0]; q += 4
        m['params'] = []
        for _ in range(npar):
            name, q = _pascal(sec, q)
            t = struct.unpack_from('<I', sec, q)[0]; q += 4
            size = PARAM_SIZE[t]
            m['params'].append({'name': name, 'type': t, 'raw': bytes(sec[q:q + size])}); q += size
        nsw = struct.unpack_from('<I', sec, q)[0]; q += 4
        sw_start = q
        m['switches'] = []
        for _ in range(nsw):
            name, q = _pascal(sec, q)
            m['switches'].append([name, struct.unpack_from('<i', sec, q)[0]]); q += 4
        m['switch_block'] = bytes(sec[sw_start:q])
        nuv = struct.unpack_from('<I', sec, q)[0]; q += 4
        m['uv'] = bytes(sec[q:q + nuv]); q += nuv
        nu1 = struct.unpack_from('<I', sec, q)[0]; q += 4
        m['unk1'] = bytes(sec[q:q + nu1]); q += nu1
        m['unk2'] = bytes(sec[q:q + 20]); q += 20
        mats.append(m)
    return mats, bytes(sec[q:])


def build_materials(mats, tail=b''):
    out = bytearray(struct.pack('<I', len(mats)))
    for m in mats:
        out += _mk(m['name']) + _mk(m['shader']) + _mk(m['str3'])
        out += struct.pack('<I', len(m['textures']))
        for t in m['textures']:
            out += _mk(t['image']) + struct.pack('<5i', *t['vals'])
        out += struct.pack('<I', len(m['params']))
        for p in m['params']:
            out += _mk(p['name']) + struct.pack('<I', p['type']) + p['raw']
        out += struct.pack('<I', len(m['switches']))
        for name, v in m['switches']:
            out += _mk(name) + struct.pack('<i', v)
        out += struct.pack('<I', len(m['uv'])) + m['uv']
        out += struct.pack('<I', len(m['unk1'])) + m['unk1']
        out += m['unk2']
    return bytes(out) + tail


def switch_block(m):
    return b''.join(_mk(n) + struct.pack('<i', v) for n, v in m['switches'])
