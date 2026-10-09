# MagView

MagView is a real-time crystal-structure editor modeled on VESTA, with an interactive **magnetic-ordering workflow**:

1. It suggests which sites are magnetic.
2. You assign moments by dragging arrows, with batch FM/AFM patterns, or layer by layer with rule-based completion (FM / Néel / stripe / zigzag).
3. You choose how the layers stack.
4. It finds the minimal magnetic supercell and writes a JSON handoff for an AgentDFT backend.

MagView is built in phases; see `docs/reports/`. **Status: Phase 0** (foundations: structure loading, VESTA-like rendering, GUI shell).

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
```

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
