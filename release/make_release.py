"""Assemble the NexusMods release of Eight Leaves Style from a finished build (port/out/shizuna).

  python release/make_release.py data      release/data: manifest (with the rebuilt files' checksums, the files the
                                           mod replaces and their original checksums), the mod's own files, the
                                           build's data tables
  python release/make_release.py exe       release/dist/EightLeavesStyleInstaller.exe (PyInstaller)
  python release/make_release.py zip       release/dist/<name>.zip: the .exe and the readme

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


VERSION = '1.0.1'   # 1.0.1: Zeroth / Nilmoon Gleam put everyone back in place (S33)


def zip_release():
    z = os.path.join(DIST, f'EightLeavesStyle_v{VERSION}.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as f:
        f.write(os.path.join(DIST, NAME + '.exe'), NAME + '.exe')
        f.write(os.path.join(HERE, 'README.txt'), 'README.txt')
    print('zip:', z)


if __name__ == '__main__':
    {'data': data, 'exe': exe, 'zip': zip_release}[sys.argv[1]]()
