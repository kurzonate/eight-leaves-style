"""Eight Leaves Style (Shizuna's moveset for Estelle) for Trails in the Sky 2nd Chapter: installer.

The mod's own files (Estelle's battle script, btlcom and the craft tables) ship with it. Everything taken from Trails
beyond the Horizon (motions, the katana and sheath, effects, textures) is rebuilt here from the player's own copy
of Horizon, by the same code that built the mod (port/assets.py), following data/manifest.json. Each rebuilt file is
checked against the mod's own build. The files then go into the player's Sky archives in place (sky_patch), with
undo data that gives the original archives back on uninstall.

  python extract.py install <Horizon folder> <Sky folder>
  python extract.py uninstall <Sky folder>
  python extract.py status <Sky folder>
"""
import hashlib
import json
import os
import sys
import tempfile
import types

FROZEN = getattr(sys, 'frozen', False)
HERE = sys._MEIPASS if FROZEN else os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
if not FROZEN:   # the mod's source tree: release/, port/, tools/
    sys.path[:0] = [HERE, os.path.join(HERE, '..', 'port'), os.path.join(HERE, '..', 'tools')]
import sky_patch as SP  # noqa: E402

UNDO_DIR = 'eight_leaves_style_undo'      # next to the archives
HORIZON_FILES = ('asset_common_model.p3a', 'asset_common_model_info.p3a', 'asset_image.p3a', 'vfx.p3a')
SKY_ARCHIVES = ('asset_common_model.pac', 'asset_common_model_info.pac', 'image.pac', 'vfx.pac',
                'script.pac', 'script_en.pac', 'table.pac', 'table_en.pac')


class Problem(Exception):
    """Something the player has to fix (a wrong folder, a changed game version, ...): shown as is."""


def manifest():
    return json.load(open(os.path.join(DATA, 'manifest.json'), encoding='utf8'))


# ---- finding the games ------------------------------------------------------------------------------------------
def sky_archives(folder):
    """The folder holding Sky's archives, from the game's folder (or that folder itself)."""
    for d in (os.path.join(folder, 'pac', 'steam'), os.path.join(folder, 'pac'), folder):
        if all(os.path.isfile(os.path.join(d, a)) for a in SKY_ARCHIVES):
            return d
    return None


def horizon_archives(folder):
    if folder and all(os.path.isfile(os.path.join(folder, a)) for a in HORIZON_FILES):
        return folder
    return None


def steam_libraries():
    roots = []
    for steam in (r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam'):
        vdf = os.path.join(steam, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            import re
            roots += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf8', errors='ignore').read())]
            roots.append(steam)
    return [os.path.join(r, 'steamapps', 'common') for r in dict.fromkeys(roots)]


def guess(kind):
    """A likely install folder of 'sky' or 'horizon' in the Steam libraries, else ''."""
    words = {'sky': ('sky', '2nd'), 'horizon': ('horizon',)}[kind]
    for common in steam_libraries():
        if not os.path.isdir(common):
            continue
        for name in os.listdir(common):
            if all(w in name.lower() for w in words):
                d = os.path.join(common, name)
                if (sky_archives(d) if kind == 'sky' else horizon_archives(d)):
                    return d
    return ''


# ---- install state ----------------------------------------------------------------------------------------------
def undo_path(pac_dir, archive):
    return os.path.join(pac_dir, UNDO_DIR, archive + '.undo')


def installed(pac_dir):
    """The archives this mod has patched (their undo files)."""
    return [a for a in SKY_ARCHIVES if os.path.isfile(undo_path(pac_dir, a))]


def sha1(data):
    return hashlib.sha1(data).hexdigest()


# ---- install / uninstall ----------------------------------------------------------------------------------------
def check_versions(m, pac_dir):
    """The files the mod replaces must be the game's own, as the mod was built against (an update or another mod
    changed them otherwise: the mod's versions would undo those changes)."""
    changed = []
    for key, want in m['replaces'].items():
        archive, name = key.split('/', 1)
        data = SP.read_entry(os.path.join(pac_dir, archive), name)
        if data is None or sha1(data) != want:
            changed.append(key)
    return changed


def build_assets(m, horizon, pac_dir, log, progress):
    import assets as A
    A.CATALOG = os.path.join(DATA, 'sky_shader_combos.json')
    A.VFX_SCHEMA = os.path.join(DATA, 'sky_vfx_schema.json')
    cfg = types.SimpleNamespace(SOURCE_GAME=horizon, **m['config'])
    with tempfile.TemporaryDirectory() as tmp:
        lines = []
        files = A.build(cfg, None, tmp, lines, plan=m, sky_dir=pac_dir, progress=progress)
    for l in lines:
        log(l)
    return files


def install(horizon_folder, sky_folder, log=print, progress=lambda done, total, what: None, accept_differences=None):
    """accept_differences(list) -> bool: asked when rebuilt files differ from the mod's build (other game versions);
    None: refuse."""
    m = manifest()
    horizon = horizon_archives(horizon_folder)
    if not horizon:
        raise Problem(f'Trails beyond the Horizon not found in {horizon_folder}\n(the folder with asset_common_model.p3a)')
    pac_dir = sky_archives(sky_folder)
    if not pac_dir:
        raise Problem(f'Trails in the Sky 2nd Chapter not found in {sky_folder}\n(the game folder, with pac\\steam)')
    if installed(pac_dir):
        raise Problem('The mod is already installed. Uninstall it first.')
    changed = check_versions(m, pac_dir)
    if changed:
        raise Problem('These game files are not the versions the mod was made for (a game update, or another mod):\n  '
                      + '\n  '.join(changed) + '\nRestore the original game files (Steam: Properties > Installed Files > '
                      'Verify integrity) and try again.')

    log('Rebuilding the mod\'s files from Trails beyond the Horizon...')
    built = build_assets(m, horizon, pac_dir, log, progress)
    files = {}
    for (archive, name), data in built.items():
        files.setdefault(archive, {})[name] = data
    differ = sorted(f'{a}/{n}' for (a, n), d in built.items() if m['sha1'].get(f'{a}/{n}') != sha1(d))
    missing = sorted(set(m['sha1']) - {f'{a}/{n}' for a, n in built})
    if missing:
        raise Problem('Some of the mod\'s files could not be rebuilt:\n  ' + '\n  '.join(missing[:20]))
    if differ:
        log(f'{len(differ)} rebuilt files differ from the mod\'s own build (another game version?)')
        if not (accept_differences and accept_differences(differ)):
            raise Problem('Stopped: rebuilt files differ from the mod\'s build.')
    else:
        log(f'All {len(built)} rebuilt files match the mod\'s build.')
    for archive in m['mod_files']:
        for name in m['mod_files'][archive]:
            files.setdefault(archive, {})[name] = open(os.path.join(DATA, 'files', archive, *name.split('/')), 'rb').read()

    os.makedirs(os.path.join(pac_dir, UNDO_DIR), exist_ok=True)
    done = []
    try:
        for i, archive in enumerate(sorted(files)):
            progress(i, len(files), 'archives')
            log(f'Patching {archive} ({len(files[archive])} files)...')
            SP.patch(os.path.join(pac_dir, archive), files[archive], undo_path(pac_dir, archive))
            done.append(archive)
    except Exception:
        log('Patching failed: restoring the archives already patched.')
        for archive in reversed(done):
            SP.unpatch(os.path.join(pac_dir, archive), undo_path(pac_dir, archive))
        raise
    log('Installed.')


def uninstall(sky_folder, log=print):
    pac_dir = sky_archives(sky_folder)
    if not pac_dir:
        raise Problem(f'Trails in the Sky 2nd Chapter not found in {sky_folder}')
    archives = installed(pac_dir)
    if not archives:
        raise Problem('The mod is not installed.')
    changed = [a for a in archives if not SP.is_patched_as_recorded(os.path.join(pac_dir, a), undo_path(pac_dir, a))]
    if changed:
        raise Problem('These archives changed since the mod was installed (a game update?), so they were left as they '
                      'are:\n  ' + '\n  '.join(changed) + '\nUse Steam\'s Verify integrity to restore them, then delete '
                      f'the folder {os.path.join(pac_dir, UNDO_DIR)}.')
    for a in archives:
        log(f'Restoring {a}...')
        SP.unpatch(os.path.join(pac_dir, a), undo_path(pac_dir, a))
    try:
        os.rmdir(os.path.join(pac_dir, UNDO_DIR))
    except OSError:
        pass
    log('Uninstalled: the original archives are back.')


def selftest():
    """Imports everything the build needs (a frozen build missing a module or its data fails here)."""
    import assets  # noqa: F401
    import etcpak  # noqa: F401
    import lz4.frame  # noqa: F401
    import numpy  # noqa: F401
    import texture2ddecoder  # noqa: F401
    import zstandard  # noqa: F401
    manifest()
    for f in ('sky_shader_combos.json', 'sky_vfx_schema.json'):
        assert os.path.isfile(os.path.join(DATA, f)), f


def main(argv):
    try:
        if argv[1:2] == ['selftest']:
            selftest()
        elif argv[1:2] == ['install'] and len(argv) == 4:
            install(argv[2], argv[3], accept_differences=lambda d: input(f'{len(d)} files differ; install anyway? [y/N] ') == 'y')
        elif argv[1:2] == ['uninstall'] and len(argv) == 3:
            uninstall(argv[2])
        elif argv[1:2] == ['status'] and len(argv) == 3:
            d = sky_archives(argv[2])
            print('installed' if d and installed(d) else 'not installed')
        else:
            print(__doc__)
            return 2
    except Problem as e:
        print(e)
        return 1
    except Exception:   # the command line never shows a dialog: the error goes to a log beside the installer
        import traceback
        text = traceback.format_exc()
        print(text)
        log = os.path.join(os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__)), 'EightLeavesStyleInstaller.log')
        open(log, 'w', encoding='utf8').write(text)
        return 1
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 1 and FROZEN:
        import gui
        gui.main()
    else:
        sys.exit(main(sys.argv))
