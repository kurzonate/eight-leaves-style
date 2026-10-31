# Eight Leaves Style: installer source

Source code of **EightLeavesStyleInstaller.exe**, the installer of *Eight Leaves Style*, a mod for
*Trails in the Sky 2nd Chapter* that gives Estelle Shizuna's moveset from *Trails beyond the Horizon*
(see [release/README.txt](release/README.txt) for what the mod does and how to use the installer).

The mod can't ship anything taken from *Trails beyond the Horizon*. Instead, the installer rebuilds those files
(motions, the katana and sheath, effects, textures) on your machine from your own copy of Horizon, using the same
code that built the mod. This repository is that code, published so you can see exactly what the `.exe` does.

## What the installer does

`release/extract.py` (the window is `release/gui.py`) runs these steps when you press **Install**:

1. **Finds the games.** Horizon's folder must hold `asset_common_model.p3a`, `asset_common_model_info.p3a`,
   `asset_image.p3a` and `vfx.p3a`. Sky's folder must hold its archives in `pac\steam`. Both folders are guessed
   from Steam's `libraryfolders.vdf` and can be changed.
2. **Checks the game version.** The mod replaces eight files inside Sky's archives (Estelle's battle script and
   `btlcom` in Japanese and English, plus the craft and status tables in both). The checksum of each original file
   must match `release/data/manifest.json`, which the mod was built against. If it doesn't, the installer stops
   (a game update or another mod changed them).
3. **Rebuilds the Horizon files** (`port/assets.py`, function `build`). The manifest lists every file to rebuild
   and the settings used. Horizon's archives are only read (`tools/p3a.py`). Sky's archives are read for Estelle's
   skeleton, her eye texture and a few model files. The conversions:
   - **motions**: retargeted to Estelle's skeleton (`tools/kuro_mdl.py`), with the per-frame fixes described in
     `port/assets.py` (katana kept clear of her head, skirt pose, pelvis);
   - **effects**: Horizon's binary JSON converted to Sky's (`tools/vfx_convert.py`, `tools/bjson.py`);
   - **models**: material shader switches set to combinations Sky ships (`tools/fix_materials.py`);
   - **textures**: re-wrapped as Sky's LZ4 frames (`tools/sky_texture.py`);
   - **Spirit Unification's gold eyes**: Estelle's eye texture recoloured (`recoloured_face`).

   Work files go to a temporary folder that is deleted afterwards.
4. **Verifies the result.** Every rebuilt file's SHA-1 must equal the mod's own build (`sha1` in the manifest).
   If any differ (another Horizon version), the installer asks before going on.
5. **Patches Sky's archives in place** (`release/sky_patch.py`). New and replaced files are appended at the end
   of the eight archives (`asset_common_model`, `asset_common_model_info`, `image`, `vfx`, `script`, `script_en`,
   `table`, `table_en`), and each archive's index is rewritten.
   - **Undo data first:** before an archive changes, its original size and the bytes the new index will cover are
     saved in `pac\steam\eight_leaves_style_undo`.
   - **Crash-safe:** the archive stays valid at every step, and if any archive fails, the ones already patched
     are restored.

**Uninstall** writes the saved bytes back and cuts each archive to its original size, so you get the very same
files back. It refuses if an archive changed since the install (for example, a game update replaced it).

What it never does:
- connect to the internet;
- run anything else;
- write anywhere except Sky's `pac\steam` folder, its undo folder, a temporary folder, and (on a command-line
  error) `EightLeavesStyleInstaller.log` next to the `.exe`.

## Files

| Path | What it is |
|---|---|
| `release/extract.py` | Install / uninstall logic; command line: `install <Horizon> <Sky>`, `uninstall <Sky>`, `status <Sky>`, `selftest` |
| `release/gui.py` | The installer window (tkinter) |
| `release/sky_patch.py` | In-place, reversible patching of Sky's `.pac` archives |
| `release/make_release.py` | Builds the release: `data`, `exe` (PyInstaller), `zip` |
| `release/data/manifest.json` | What to rebuild, the settings, the expected checksums, the files the mod replaces |
| `release/data/sky_shader_combos.json` | Shader switch combinations Sky ships (for `fix_materials`) |
| `release/data/sky_vfx_schema.json` | Sky's effect file structure (for `vfx_convert`) |
| `port/assets.py` | The asset build: the same code that built the mod |
| `tools/*.py` | File formats: Sky `.pac` (`fpac`), Horizon `.p3a` (`p3a`), models and motions (`kuro_mdl`, `kuro_materials`), binary JSON (`bjson`), textures (`sky_texture`) |

The `.exe` also contains the mod's eight changed script and table files (`release/data/files`). They are the mod
itself, built from Sky's own files, and are not in this repository.

## Building the installer yourself

With Python 3.13 (the release was built with 3.13.5 and PyInstaller 6.22.3):

```
pip install -r requirements.txt pyinstaller
python release/make_release.py exe
```

`make_release.py data` (which fills `release/data`) needs the mod's full build output, so it isn't reproducible
from this repository alone. The `exe` step packs the Python files above with the data folder. You can also run the
installer from source without building anything: `python release/extract.py install <Horizon folder> <Sky folder>`
(the eight mod files must be in `release/data/files`).

## Versions

- **1.0.2:**
  - Estelle learns Eight Leaves Style at the start of the next battle, at any level. Before, it came only from a
    level-up to 38, which players already past 38 never got.
  - Zeroth Gleam and Nilmoon Gleam put Estelle and the enemies back where they stood. Before, they could be left
    moved after the S-craft.
- **1.0.1:** packaging error. It was meant to carry the S-craft fix above, but the release script took a stale copy
  of the mod's scripts, so it played like 1.0.0. `make_release.py` now takes them from the compiler's output.
- **1.0.0:** first release.

## Credits

Community tools this mod was made with:

- **[KuroTools](https://github.com/nnguyen259/KuroTools)** by Nhan Nguyen (MIT): every script of the mod was
  decompiled and compiled with it. It isn't part of the installer, which ships the compiled scripts.
- **[kuro_mdl_tool](https://github.com/eArmada8/kuro_mdl_tool)** by eArmada8 (GPL-3.0): the model, motion and
  material layouts that `tools/kuro_mdl.py` and `tools/kuro_materials.py` read and write follow it.
- **[ed8pkg2gltf](https://github.com/eArmada8/ed8pkg2gltf)** by eArmada8, built on uyjulian's
  [ed8pkg2glb](https://github.com/uyjulian/ed8pkg2glb) (MIT): used during development for an early motion
  experiment. Nothing from it is in the mod.

Libraries bundled in the installer `.exe`, each under its own license (full texts in
[release/THIRD_PARTY_NOTICES.txt](release/THIRD_PARTY_NOTICES.txt), which ships with the `.exe`):
- Python;
- Tcl/Tk;
- NumPy;
- python-zstandard;
- python-lz4;
- [etcpak](https://github.com/K0lb3/etcpak) and [texture2ddecoder](https://github.com/K0lb3/texture2ddecoder) by
  K0lb3, with the code inside them by Bartosz Taudul, Perfare, Ishotihadus, Marat Dukhan, Binomial and Unity;
- archspec;
- the PyInstaller bootloader.

*Trails in the Sky 2nd Chapter* and *Trails beyond the Horizon* are © Nihon Falcom. This repository contains none
of their files.

## License

The mod's code: GPL-3.0, see [LICENSE](LICENSE). Bundled libraries: their own licenses, see
[release/THIRD_PARTY_NOTICES.txt](release/THIRD_PARTY_NOTICES.txt).
