# MagView

> **Start here:** `docs/reports/SUMMARY.md` (what was built, where the evidence is, how to run it on your host).

MagView is a real-time crystal-structure editor modeled on VESTA, with an interactive **magnetic-ordering workflow**:

1. It suggests which sites are magnetic.
2. You assign moments by dragging arrows, with batch FM/AFM patterns, or layer by layer with rule-based completion (FM / Néel / stripe / zigzag).
3. You choose how the layers stack.
4. It finds the minimal magnetic supercell and writes a JSON handoff for an AgentDFT backend.

**Status: version 0.1.0, packaged for Linux/WSL, with native Windows source support; macOS is planned** (see below). It was built in phases; see `docs/reports/`. Features: structure loading, VESTA-like rendering, editing canvas, selection, undo/redo, magnetic-site suggestions, moment editing, session files, the layer editor with rule-based completion, inter-layer stacking, and the **minimal magnetic supercell + AgentDFT handoff** (mock bridge). Phase reports: `docs/reports/phase0.md` … `phase5.md`; handoff format: `docs/handoff_format.md`.

## Download and run (Linux / WSL)

Get `MagView-<version>-linux-x86_64.tar.gz` from the [Releases page](https://github.com/zhenghong-li/MagView/releases); nothing else to install (Python, Qt, VTK and pymatgen are inside):

```bash
tar -xzf MagView-0.1.0-linux-x86_64.tar.gz
sudo apt install libgl1 libegl1 libgl1-mesa-dri libxcb1 libwayland-client0 fonts-dejavu-core   # OpenGL + fonts, if missing
cd MagView-0.1.0-linux-x86_64 && ./magview examples/NiO.cif
```

Needs x86_64 Linux with glibc 2.39+ (Ubuntu 24.04+; on Windows: WSL 2 with WSLg). See `README.txt` in the download for details.

| Platform | Status |
|---|---|
| Linux / WSL (x86_64) | released: tarball on the Releases page |
| Windows 10/11 (x64) | native source: [Windows instructions](platforms/windows/README.md) |
| macOS 12+ (Apple Silicon and Intel) | planned: `docs/plans/macos-build.md` |

## Develop from source (Ubuntu 24.04 container or WSL)

```bash
sudo platforms/linux-wsl/setup_system_deps.sh   # idempotent apt install (as root: drop sudo)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt       # every package pinned
.venv/bin/pip install -e . --no-deps
.venv/bin/python -m pytest -q                   # aborts early with a hint if system libs are missing
```

`platforms/linux-wsl/setup_system_deps.sh --check` reports any missing system packages. `--list` prints the list, which can be mirrored into a Dockerfile. Packages installed with apt live in the container layer, so run the script again after the container is recreated. The venv in the workspace survives the rebuild, but it needs those libraries.

Build the release tarball (fresh build venv, PyInstaller, GUI self-test of the packaged app): `platforms/linux-wsl/build.sh` writes `dist/MagView-<version>-linux-x86_64.tar.gz`. Packaged builds go to GitHub Releases, never into git.

## Native Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File platforms\windows\setup.ps1
.\platforms\windows\run.cmd tests\data\NiO.cif
```

Uses native 64-bit Python 3.13 and a separate `.venv-windows`. On first launch,
`run.cmd` runs setup automatically if the environment is missing. See
[Windows source setup and tests](platforms/windows/README.md).
macOS native builds remain planned in `docs/plans/`.

## Usage

```bash
.venv/bin/python -m magview tests/data/NiO.cif                     # GUI
.venv/bin/python -m magview FILE.cif --render out.png --view c     # offscreen PNG, no display needed
.venv/bin/python scripts/render_gallery.py                         # regenerate artifacts/phase0/
.venv/bin/python scripts/phase1_artifacts.py                       # regenerate artifacts/phase1/
.venv/bin/python scripts/phase2_artifacts.py                       # regenerate artifacts/phase2/
.venv/bin/python scripts/phase3_artifacts.py                       # regenerate artifacts/phase3/
.venv/bin/python scripts/phase4_artifacts.py                       # regenerate artifacts/phase4/
.venv/bin/python scripts/phase5_artifacts.py                       # regenerate artifacts/phase5/
xvfb-run -a .venv/bin/python -m magview.gui.selftest FILE.cif --out DIR   # scripted GUI test
```

Mouse (View mode): left-drag rotates, middle-drag or Shift+left-drag pans, wheel zooms. Click selects; Shift-click adds, Ctrl-click toggles. **Long-press (0.35 s) then drag** draws a rubber band (or press `B`, then drag). `Esc` cancels or clears, `Ctrl+A` selects all magnetic sites, `E` selects equivalent sites, and `Ctrl+Z` / `Ctrl+Y` undo and redo. `F1` (Help menu) shows every key and mouse gesture.

**Moment mode** (`M` or the toolbar):
- Drag from an atom to set its moment; the arrow tip follows the cursor. Hold `X`/`Y`/`Z`/`A`/`B`/`C` to lock to an axis, and `Shift` to snap.
- Dragging a selected atom sets the whole selection.
- `F` flips, `0` zeroes, `P` opens the Patterns menu (FM, Néel, stripe, zigzag, A/C/G-type).
- A right-click opens the context menu.
- `Ctrl+S` saves a `.magview.json` session; a recovery autosave follows every edit (offered back when you reopen the structure).

**Layer mode** (`L`):
- One magnetic layer at a time, seen along the stacking axis (default c).
- Click toggles up/down; right-click unsets; `PgUp`/`PgDn` changes layer.
- After two or more clicks, the panel lists the canonical patterns (FM, Néel, stripe, zigzag) that agree with your clicks. Hover to preview, click to apply. Nothing is applied automatically.
- The Stacking box offers FM/AFM/double stacking of all layers, relative to layer 0. A single clicked spin in another layer is enough for a suggestion. Frustrated stackings are reported instead of guessed.

**Export tab:**
- **Analyze magnetic cell** finds the minimal magnetic supercell and explains why it is needed (e.g. "Translation c maps V1 (up) → V1 (down)"). You can preview the cell.
- **Save handoff JSON** writes the AgentDFT handoff (cells, moments, supercell matrix, pattern labels, `requires_unfolding`, VASP MAGMOM).
- **Send to AgentDFT (mock)** stores the job and reports "started (simulated)".

Views: `oblique` (default), `a`, `b`, `c`, `a*`, `b*`, `c*`.

## Files MagView writes

| What | Where | Override |
|---|---|---|
| Recovery autosaves (offered back when you reopen a structure) | `$XDG_CACHE_HOME/magview/autosave`, default `~/.cache/magview/autosave` | `MAGVIEW_AUTOSAVE_DIR=<dir>`; `MAGVIEW_AUTOSAVE_DIR=off` disables autosave |
| Mock AgentDFT jobs | `~/.magview/agentdft_jobs` | `MAGVIEW_AGENTDFT_DIR=<dir>` |
| Window layout and arrow settings | `~/.config/MagView/MagView.conf` (Qt settings) | `XDG_CONFIG_HOME` |

On Windows, autosaves use `%LOCALAPPDATA%\MagView\Cache\autosave`, jobs use `%APPDATA%\MagView\agentdft_jobs`, and Qt settings use `HKCU\Software\MagView\MagView`. `MAGVIEW_SETTINGS_DIR` selects an INI settings folder on every platform.

Sessions (`.magview.json`), handoffs and images are written only where you save them.

## VESTA data

MagView reads element colors and radii (`elements.ini`) and bond rules plus display defaults (`style.ini`) from your local VESTA installation **at runtime**. It looks first at `$MAGVIEW_VESTA_DIR`, then at `vesta_tools/VESTA-gtk3-x86_64` in any parent directory. On Windows it also checks `VESTA-win64` / `VESTA` in ancestor folders, `Apps\Others`, and Program Files. VESTA's files are never copied into this repository, because its license forbids redistribution. Without VESTA, MagView falls back to pymatgen's approximate color scheme and covalent-radius bond rules. See `docs/vesta_reference.md`.

## Layout

```
src/magview/          one code base for every platform
  core/ io/ bridge/   pure algorithms, handoff, AgentDFT bridge (no Qt, no VTK)
  style/ render/      VESTA ini parsers and theme; pyvista scene (no Qt), offscreen PNG
  gui/                PySide6 GUI
  osdeps.py           the only OS-specific code (folders, display, VESTA lookup)
platforms/linux-wsl/  system packages, setup script, release build script (Linux and WSL)
platforms/windows/    native Windows dependency pins and source setup/launch scripts
platforms/macos/      macOS build files (planned)
packaging/            PyInstaller spec, launcher and license collection shared by all platforms
tests/                pytest; tests/data holds the test structures
docs/                 architecture, UX spec, VESTA notes, handoff format, phase reports
docs/plans/           Windows and macOS build plans
artifacts/phaseN/     rendered evidence for each phase
scripts/              dev tools: artifact galleries, test-structure generator
```


AI assistance is credited through `Co-authored-by` trailers in commit messages.

## License

[MIT](LICENSE) — use it for anything, just keep the copyright notice.
