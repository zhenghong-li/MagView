# Phase 2 report: direct manipulation of magnetic moments

Date: 2026-10-09. Produced headlessly: offscreen OSMesa renders, plus the real Qt GUI under Xvfb.

## Result
- **177 tests pass**, 0 skipped (`.venv/bin/python -m pytest -q`, about 90 s). 50 tests are new.
- The **GUI self-test** passes 29 of 29 checks on **NiO** and on **Cs3V9Te13**, driving the real window with QtTest events. It covers 12 Phase 1 checks and 17 new ones (listed below).
- Artifacts are in `artifacts/phase2/`; start with `contact_sheet.png`.
- The **UX spec is now frozen** (`docs/ux_spec.md`). Implementation changes are recorded with reasons in its §8.

## What was built
| Piece | Description |
|---|---|
| Moment mode | `M` or the toolbar toggles it. Amber border + banner, moment view switches on automatically (and is restored on exit), the Moments tab is raised. `Esc` cancels a drag first, then leaves the mode. |
| Drag editing (`core/magnetic.py`) | The arrow lies in the plane through the atom, perpendicular to the view, and its **tip follows the cursor**. Collinear model: locked to ±spin axis. If the axis points at the viewer, vertical motion sets the magnitude (with a hint). Hold `X/Y/Z/A/B/C` to lock an axis; `Shift` snaps to ±axes / 15° steps and 0.1 μB. Range is clamped to 10 μB; a 0.2 μB dead zone turns tiny drags into clicks. Dragging a *selected* atom sets the whole selection. A live readout shows \|m\|, xyz, abc, θ/φ and the active lock. Release = one undo step; Esc = no change. |
| Live rendering | `StructureScene.update_moments` rewrites the arrow mesh in place while the set of arrows is unchanged (constant-thickness numpy geometry). |
| Batch ops | FM ↑/↓, Flip (`F`), Zero (`0`), Set \|m\| (keeps directions), exact entry (signed \|m\| along the axis, or \|m\|/θ/φ in non-collinear mode). Each is one undo step. |
| Patterns popover (`P`, button, context menu; `core/batch.py`) | **Planar selection** → its 2D lattice library (square: Néel, 2 stripes; honeycomb: Néel, 3 zigzag, 3 stripe; triangular: 3 stripe). **3D selection** → A-type a/b/c (layer parity, periodic-consistency check), C-type a/b/c, G-type (bipartite check). Hovering previews; patterns that don't apply are disabled, with the reason as a tooltip. |
| Pattern engine (`core/graph.py`, `core/lattice2d.py`) | Periodic nearest-neighbour graph of any site set (including self-image bonds); BFS sign propagation that reports every conflicting bond; classification by coordination + bond-direction classes. Phase 3's matcher reuses it unchanged. |
| Model settings | Collinear ↔ non-collinear switch. Going back to collinear with non-collinear moments asks first, then projects; nothing changes silently, and the step is undoable. Spin axis x/y/z/a/b/c (collinear moments keep their signed magnitudes). |
| Arrow styles | Scale (Å/μB), anchor (centered / tail), color (sign / direction / element / uniform), Auto-fit. Persisted with QSettings. |
| Sessions (`core/project.py`) | `.magview.json` holds the structure itself, canvas matrix, Cartesian moments, flags, spin axis and model. Atomic write. Recovery autosave after every edit (`~/.cache/magview/autosave/`, or `$MAGVIEW_AUTOSAVE_DIR`). |
| Context menu | Right-click without moving: Flip, Zero, Set \|m\|, Patterns, Select equivalent / all magnetic. Right-drag still zooms. |

## Verification highlights
- **Drag math** (13 tests):
  - free drag = screen vector / tip scale;
  - out-of-plane cursor components are dropped;
  - an axis lock makes the tip's projection follow the cursor (checked with an oblique axis);
  - a lattice-axis lock with a negative drag gives a negative moment;
  - an axis pointing at the viewer falls back to vertical motion;
  - snapping to an axis or a 45° step and to 0.1 μB;
  - clamping and the dead zone;
  - spherical ↔ Cartesian and abc components (hexagonal check).
- **Pattern engine** (22 tests):
  - Honeycomb zigzag gives every site exactly 2 FM and 1 AFM neighbours.
  - Néel on a CuO₂ square layer is the (Rₐ+R_b) parity checkerboard.
  - A 2×1 canvas refuses Néel ("does not fit the canvas periodicity") but allows the stripe that fits.
  - A 1×1×1 simple-cubic canvas has 3 self-image bonds, so AFM there is impossible.
  - On a 2×2×3 canvas, A-type along c is refused ("3 layers… odd: double the canvas") and G is unavailable.
  - fcc NiO: G-type is unavailable because the graph is not bipartite; A-type (AFM-I) is available.
  - **The Cs3V9Te13 V layer gets only FM** ("net not recognized"): the negative control holds.
- **Document**: collinear projection on set, magnetic flagging, flip/zero/magnitude/direction with undo, preferred magnitudes for patterns, spin-axis change, explicit projection when switching back to collinear, session round trip (non-diagonal canvas, non-collinear), error handling, autosave.
- **GUI self-test** (new Phase 2 checks, both structures):

  | # | Check |
  |---|---|
  | 9 | `M` enters Moment mode (banner, moment view, toolbar state) |
  | 10–11 | collinear drag up → +axis; drag down → −axis; result equals the core math at the pixel actually sent; exactly one undo step; only the dragged site changes |
  | 12 | Esc during a drag: no change, no history entry |
  | 13 | below the dead zone: no change |
  | 14 | non-collinear + held `X`: moment purely along x; free drag follows the cursor |
  | 15 | explicit projection back to collinear; `F` flips, `0` zeroes |
  | 16 | dragging a selected atom sets all selected sites |
  | 17 | Patterns menu built (9 entries for NiO); A-type c alternates by layer |
  | 18 | session save/load round trip; autosave file written |
  | 19 | Esc leaves Moment mode |

## Problems found and fixed in this phase
1. **The centered arrow lagged the cursor.** The tip reached only halfway, which the first offscreen drag replay showed. The drag now maps distance so that the tip stays under the cursor (UX §8).
2. **Red-on-red.** In moment view, atoms are now desaturated; see the before/after renders.
3. **Key conflict:** `B` (box select) vs. the held `B` lock while dragging. Letter keys moved to a viewport key filter.
4. **A self-test hang** (my test bug: a modal dialog under Xvfb). The self-test now has a watchdog that closes any unexpected modal and records a failure.
5. **VTK text renders `|` incorrectly.** Overlays avoid it.
6. **The self-test compared against float cursor positions**, while Qt sends integer pixels (about 0.1 μB at Cs3V9Te13's zoom). The expectation is now evaluated at the pixel actually sent.

## Artifacts (`artifacts/phase2/`)
| File | Content |
|---|---|
| `contact_sheet.png` | overview |
| `drag_sequence.png` | 6-frame offscreen replay of a drag on CuO₂: the arrow tip tracks the cursor ring; halos show the site and its periodic images updating together |
| `cs3v9te13_before_fix.png`, `_after_fix.png` | red-on-red problem and fix |
| `pattern_*` | Néel / stripes on the square net and FeSe's rotated Fe square net; Néel / zigzag / stripe on the honeycomb; stripe on the triangular net; G / C / A on simple cubic; NiO AFM-I. Top views are tilted 35° so up/down read as arrows. |
| `noncollinear_triangular_120deg.png` | non-collinear 120° order on a 3×3 canvas, colored by direction |
| `style_gallery.png` | anchor, color modes, scale |
| `gui_nio/`, `gui_cs3v9te13/` | per-step GUI screenshots (`06_drag_live.png` shows the live readout) + `selftest.json` |

## Notes for later phases
- Top-down layer views show moments along the view axis end-on. Phase 3's layer editor therefore draws up/down as colored disc glyphs, not arrows.
- On large canvases the default 1 Å/μB scale makes drags sensitive (70 px ≈ 7.6 μB on Cs3V9Te13). Use Shift-snap, the exact fields or Auto-fit. A zoom-dependent drag gain could be added if you find it awkward on the host.

## Reproduce
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/phase2_artifacts.py
xvfb-run -a .venv/bin/python -m magview.gui.selftest tests/data/NiO.cif --out /tmp/st
```
