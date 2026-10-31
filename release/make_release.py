"""Assemble the NexusMods release of Eight Leaves Style from a finished build (port/out/shizuna).

  python release/make_release.py data      release/data: manifest (with the rebuilt files' checksums, the files the
                                           mod replaces and their original checksums), the mod's own files, the
                                           build's data tables
  python release/make_release.py exe       release/dist/EightLeavesStyleInstaller.exe (PyInstaller)
  python release/make_release.py notices   release/THIRD_PARTY_NOTICES.txt: licenses of what the .exe bundles
  python release/make_release.py zip       release/dist/<name>.zip: the .exe, the readme and the notices

No file taken from Trails beyond the Horizon goes into the release: the manifest names them, the installer rebuilds
them from the player's copy.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
sys.path[:0] = [HERE, os.path.join(ROOT, 'port'), os.path.join(ROOT, 'tools')]
import sky_patch as SP  # noqa: E402

BUILD = os.path.join(ROOT, 'port', 'out', 'shizuna')
DATA = os.path.join(HERE, 'data')
DIST = os.path.join(HERE, 'dist')
ORIGINAL = os.path.join(ROOT, 'original')
NAME = 'EightLeavesStyleInstaller'
FROM_HORIZON = ('asset_common_model.pac', 'asset_common_model_info.pac', 'image.pac', 'vfx.pac')


def walk(base):
    out = {}
    for root, _, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            out[os.path.relpath(full, base).replace(os.sep, '/')] = full
    return out


def data():
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA)
    m = json.load(open(os.path.join(BUILD, 'assets', 'manifest.json'), encoding='utf8'))
    m['sha1'] = {}
    for archive in FROM_HORIZON:
        for name, full in walk(os.path.join(BUILD, 'assets', 'override', archive)).items():
            m['sha1'][f'{archive}/{name}'] = hashlib.sha1(open(full, 'rb').read()).hexdigest()
    assert len(m['sha1']) == len(m['files']), (len(m['sha1']), len(m['files']))
    m['mod_files'], m['replaces'] = {}, {}
    for stage in ('scripts', 'tables'):
        base = os.path.join(BUILD, stage, 'override')
        for archive in sorted(os.listdir(base)):
            for name, full in walk(os.path.join(base, archive)).items():
                assert archive not in FROM_HORIZON, archive
                m['mod_files'].setdefault(archive, []).append(name)
                dst = os.path.join(DATA, 'files', archive, *name.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(full, dst)
                orig = SP.read_entry(os.path.join(ORIGINAL, archive), name)
                assert orig is not None, f'{archive}/{name} is not in the original archive'
                m['replaces'][f'{archive}/{name}'] = hashlib.sha1(orig).hexdigest()
    del m['files']
    json.dump(m, open(os.path.join(DATA, 'manifest.json'), 'w', encoding='utf8'), indent=1, ensure_ascii=False)
    shutil.copyfile(os.path.join(ROOT, 'grendel_test', 'sky_shader_combos.json'), os.path.join(DATA, 'sky_shader_combos.json'))
    shutil.copyfile(os.path.join(ROOT, 'vfx_port', 'sky_vfx_schema.json'), os.path.join(DATA, 'sky_vfx_schema.json'))
    print(f"data: {len(m['sha1'])} files to rebuild from Horizon, mod files {m['mod_files']}")


def exe():
    work = os.path.join(HERE, 'build')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--name', NAME,
                    '--distpath', DIST, '--workpath', work, '--specpath', work,
                    '--paths', HERE, '--paths', os.path.join(ROOT, 'port'), '--paths', os.path.join(ROOT, 'tools'),
                    '--add-data', f'{DATA}{os.pathsep}data',
                    '--hidden-import', 'assets', '--hidden-import', 'gui',
                    '--collect-data', 'archspec',   # etcpak reads archspec's CPU tables on import (round 1: missing)
                    os.path.join(HERE, 'extract.py')], check=True)
    print('exe:', os.path.join(DIST, NAME + '.exe'))


# what the .exe bundles besides our code: (name, distribution or None, URL, license, license files); their
# licenses ask for these notices to travel with it
BUNDLED = [
    ('Python', None, 'https://www.python.org', 'PSF-2.0', [os.path.join(sys.base_prefix, 'pkgs', 'python-3.13.5-h286a616_100_cp313',
                                                                    'LICENSE_PYTHON.txt')]),
    ('Tcl/Tk (the installer window)', None, 'https://www.tcl-lang.org', 'Tcl/Tk license',
     [os.path.join(sys.base_prefix, 'Library', 'lib', 'tk8.6', 'license.terms')]),
    ('NumPy', 'numpy', 'https://numpy.org', 'BSD-3-Clause', ['numpy-*.dist-info/LICENSE.txt']),
    ('python-zstandard', 'zstandard', 'https://github.com/indygreg/python-zstandard', 'BSD-3-Clause',
     ['zstandard-*.dist-info/LICENSE']),
    ('python-lz4', 'lz4', 'https://github.com/python-lz4/python-lz4', 'BSD-3-Clause', ['lz4-*.dist-info/licenses/LICENSE']),
    ('etcpak (Python bindings by K0lb3, of etcpak by Bartosz Taudul)', 'etcpak', 'https://github.com/K0lb3/etcpak', 'MIT',
     ['etcpak-*.dist-info/LICENSE']),
    ('texture2ddecoder', 'texture2ddecoder', 'https://github.com/K0lb3/texture2ddecoder', 'MIT',
     ['texture2ddecoder-*.dist-info/LICENSE']),
    ('archspec (used by etcpak)', 'archspec', 'https://github.com/archspec/archspec', 'Apache-2.0 OR MIT (MIT chosen)',
     ['archspec/json/LICENSE-MIT', 'archspec/json/NOTICE']),
    ('PyInstaller bootloader', 'pyinstaller', 'https://pyinstaller.org', 'GPL-2.0-or-later with the bootloader exception',
     ['pyinstaller-*.dist-info/licenses/COPYING.txt']),
]
# code inside those packages' compiled modules that their own license files don't cover (texts in release/licenses,
# from each project's GitHub repository)
UPSTREAM = [
    ('etcpak by Bartosz Taudul (inside etcpak)', 'https://github.com/wolfpld/etcpak', 'wolfpld_etcpak.txt'),
    ('AssetStudio Texture2DDecoder by Perfare: BCn and ATC codecs (inside texture2ddecoder)',
     'https://github.com/Perfare/AssetStudio', 'Perfare_AssetStudio.txt'),
    ('mikunyan by Ishotihadus: ASTC, ETC and PVRTC codecs (inside texture2ddecoder)',
     'https://github.com/Ishotihadus/mikunyan', 'Ishotihadus_mikunyan.txt'),
    ('FP16 by Marat Dukhan (inside texture2ddecoder)', 'https://github.com/Maratyszcza/FP16', 'Maratyszcza_FP16.txt'),
    ('crunch by Binomial LLC (inside texture2ddecoder)', 'https://github.com/BinomialLLC/crunch', 'BinomialLLC_crunch.txt'),
    ('crunch, Unity version (inside texture2ddecoder)', 'https://github.com/Unity-Technologies/crunch',
     'Unity-Technologies_crunch.txt'),
]


def notices():
    """release/THIRD_PARTY_NOTICES.txt: the license of everything the .exe bundles besides the mod's own code."""
    import glob
    import importlib.metadata as md
    site = os.path.dirname(os.path.dirname(md.distribution('numpy').locate_file('numpy/__init__.py')))
    out = ['Third-party software bundled in EightLeavesStyleInstaller.exe', '=' * 62, '',
           'The installer is a Python program packed with PyInstaller. Besides its own code (GPL-3.0, see the',
           'source at https://github.com/kurzonate/eight-leaves-style), it contains:', '']
    for name, dist, url, lic, files in BUNDLED:
        version = md.version(dist) if dist else ('3.13.5' if name == 'Python' else '8.6')
        out.append(f'  - {name} {version}: {lic}, {url}')
    for name, dist, url, lic, files in BUNDLED:
        out += ['', '', '-' * 78, f'{name} ({url})', f'License: {lic}', '-' * 78, '']
        for pattern in files:
            paths = glob.glob(pattern) if os.path.isabs(pattern) else glob.glob(os.path.join(site, pattern))
            assert len(paths) == 1, (name, pattern, paths)
            out.append(open(paths[0], encoding='utf8', errors='replace').read().strip())
            out.append('')
    out += ['', '', '=' * 78, 'Code inside the packages above, under its own license', '=' * 78]
    for name, url, file in UPSTREAM:
        out += ['', '-' * 78, f'{name} ({url})', '-' * 78, '',
                open(os.path.join(HERE, 'licenses', file), encoding='utf8').read().strip(), '']
    text = '\n'.join(out) + '\n'
    open(os.path.join(HERE, 'THIRD_PARTY_NOTICES.txt'), 'w', encoding='utf8').write(text)
    print(f'notices: {len(BUNDLED)} components, {len(text)} characters')


VERSION = '1.0.2'   # 1.0.1: Zeroth / Nilmoon Gleam put everyone back in place (S33); 1.0.2: Estelle learns
                    # Eight Leaves Style at the next battle's start, at any level


def zip_release():
    z = os.path.join(DIST, f'EightLeavesStyle_v{VERSION}.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as f:
        f.write(os.path.join(DIST, NAME + '.exe'), NAME + '.exe')
        f.write(os.path.join(HERE, 'README.txt'), 'README.txt')
        f.write(os.path.join(HERE, 'THIRD_PARTY_NOTICES.txt'), 'THIRD_PARTY_NOTICES.txt')
    print('zip:', z)


if __name__ == '__main__':
    {'data': data, 'exe': exe, 'notices': notices, 'zip': zip_release}[sys.argv[1]]()
