# MagView

MagView is a real-time crystal-structure editor modeled on VESTA, with an interactive **magnetic-ordering workflow**:

1. It suggests which sites are magnetic.
2. You assign moments by dragging arrows, with batch FM/AFM patterns, or layer by layer with rule-based completion (FM / Néel / stripe / zigzag).
3. You choose how the layers stack.
4. It finds the minimal magnetic supercell and writes a JSON handoff for an AgentDFT backend.

MagView is built in phases; see `docs/reports/`. **Status: all phases (0–5) done.** Features: structure loading, VESTA-like rendering, editing canvas, selection, undo/redo, magnetic-site suggestions, moment editing, session files, the layer editor with rule-based completion, inter-layer stacking, and the **minimal magnetic supercell + AgentDFT handoff** (mock bridge). Phase reports: `docs/reports/phase0.md` … `phase5.md`; handoff format: `docs/handoff_format.md`.

## Setup (Ubuntu 24.04 container or WSL)

```bash
sudo scripts/setup_system_deps.sh            # idempotent; installs scripts/system-packages.txt
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # every package pinned
.venv/bin/pip install -e . --no-deps
.venv/bin/python -m pytest -q                # aborts early with a hint if system libs are missing
```

`scripts/setup_system_deps.sh --check` reports any missing system packages. `--list` prints the list, which can be mirrored into a Dockerfile. Packages installed with apt live in the container layer, so run the script again after the container is recreated. The venv in the workspace survives the rebuild, but it needs those libraries.

On Windows or macOS hosts no system packages are needed; just create the venv and install from `requirements.txt`.

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

Mouse (View mode): left-drag rotates, middle-drag or Shift+left-drag pans, wheel zooms. Click selects; Shift-click adds, Ctrl-click toggles. **Long-press (0.35 s) then drag** draws a rubber band (or press `B`, then drag). `Esc` cancels or clears, `Ctrl+A` selects all magnetic sites, `E` selects equivalent sites, and `Ctrl+Z` / `Ctrl+Y` undo and redo.

**Moment mode** (`M` or the toolbar):
- Drag from an atom to set its moment; the arrow tip follows the cursor. Hold `X`/`Y`/`Z`/`A`/`B`/`C` to lock to an axis, and `Shift` to snap.
- Dragging a selected atom sets the whole selection.
- `F` flips, `0` zeroes, `P` opens the Patterns menu (FM, Néel, stripe, zigzag, A/C/G-type).
- A right-click opens the context menu.
- `Ctrl+S` saves a `.magview.json` session; a recovery autosave runs after every edit.

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

## VESTA data

MagView reads element colors and radii (`elements.ini`) and bond rules plus display defaults (`style.ini`) from your local VESTA installation **at runtime**. It looks first at `$MAGVIEW_VESTA_DIR`, then at `vesta_tools/VESTA-gtk3-x86_64` in any parent directory. VESTA's files are never copied into this repository, because its license forbids redistribution. Without VESTA, MagView falls back to pymatgen's approximate color scheme and covalent-radius bond rules. See `docs/vesta_reference.md`.

## Layout

```
src/magview/core     pure algorithms (numpy, pymatgen, spglib)
src/magview/style    VESTA ini parsers, element styles, render theme
src/magview/render   pyvista scene (no Qt), offscreen PNG
src/magview/gui      PySide6 GUI
tests/               pytest; tests/data holds the test structures
docs/                architecture, UX spec, VESTA notes, phase reports
artifacts/phaseN/    rendered evidence for each phase
scripts/             system deps, gallery, test-structure generator
```
