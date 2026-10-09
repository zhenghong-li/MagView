# Phase 1 report: canvas, picking, selection, gestures, undo, site suggestion

Date: 2026-10-09. Produced headlessly: offscreen OSMesa renders, plus the real Qt GUI under Xvfb.

## Result
- **127 tests pass**, 0 skipped: `.venv/bin/python -m pytest -q`, about 50 s. 54 tests are new in Phase 1.
- The **scripted GUI self-test** passes 12 of 12 checks on both NiO and Cs3V9Te13. It sends real Qt mouse and keyboard events to the real window (`python -m magview.gui.selftest`; also part of pytest).
- Artifacts are in `artifacts/phase1/`; start with `contact_sheet.png` and `suggestions.md`.

## What was built
| Module | What it does |
|---|---|
| `core/canvas.py` | The editing canvas = W × input cell (default 2×2×2; any right-handed integer W, e.g. √2×√2). Every canvas site is an (input site, lattice vector R) pair. `resize_moments` carries moments across canvases and **refuses** (CanvasResizeError, naming the conflicting site) when the moments aren't periodic in the new canvas. |
| `core/history.py`, `core/document.py` | Undo/redo with the command pattern. `MagDocument` holds the structure, canvas, Cartesian μB moments, magnetic flags and spin axis. Each edit (accept suggestions, clear, canvas resize) is one snapshot command, and a failed edit leaves the document and the history untouched. |
| `core/interaction.py` | Pure gesture state machine for UX spec §2: click / Shift-add / Ctrl-toggle, empty click clears, camera forwarding, long press (≥ 350 ms, < 4 px) → rubber band, `B` to arm the box, Esc levels, mode switch cancels gestures, moment-drag events (used in Phase 2). |
| `core/suggest.py` | `SiteSuggester` protocol + `ElementRuleSuggester`: one row per symmetry orbit, starting moment = spin-only 2S from pymatgen's charge-balanced oxidation state (3d high spin, 4d/5d low spin, 4f/5f Hund). Excluded rows (d0/d10/f0/f14) are listed with the reason. |
| `render/picking.py` | Analytic ray–sphere picking (exact, testable offscreen), vectorized world → display projection (checked against `vtkCoordinate`), rubber-band membership. |
| `render/overlay.py` | Rubber band (outline + 12% fill), long-press ring, mode banner + colored border. All are VTK 2D actors, so they also appear in offscreen PNGs. |
| `gui/viewport.py` | VTK interactor style → state machine → actions. Left button and moves are arbitrated; middle/right/wheel keep VTK's camera. pyvista's default keys (`q` closes the plotter!) and VTK's char keys (`e` exits, `w` wireframe) are disabled. |
| `gui/panels/suggestion_panel.py`, `gui/main_window.py` | Suggestion dock (check/uncheck, edit \|m\|, Accept / Skip); canvas view with a sub-cell grid; reference view highlights the input sites of the selection; Edit menu (undo/redo, select all magnetic `Ctrl+A`, select equivalent `E`); Canvas menu (1×1×1, 2×2×1, 2×2×2, 3×3×1, custom); View → Moment view. |

## Verification highlights
- **Canvas:** (p, R) bookkeeping reproduces every canvas position for NiO, Cs3V9Te13 and the toy square lattice, and `index_of` is invariant under any canvas translation. A stripe pattern can't shrink to 1×1×1 (refused), while a Néel pattern round-trips exactly through the non-diagonal √2×√2 canvas.
- **Gestures (24 synthetic-event tests):** every row of UX spec §2. Examples: in View mode the camera gets the press immediately; in Moment mode it does not, and a 4 px move turns into a moment drag. A long press withdraws the camera press before arming the box. The release after an Esc-cancelled gesture is ignored. Mode switches cancel drags without committing.
- **Picking:** our projection equals `vtkCoordinate` to 1e-6 px; the ray through a projected center passes through the atom center; the front-most atom is picked at its own pixel; empty corners pick nothing. The offscreen gesture pipeline (machine + picking + overlay) is checked pixel-wise: the rubber band and its fill appear, and nothing changes outside the box.
- **GUI self-test** (real `QTest` events → QVTK → VTK style → state machine), on NiO and on Cs3V9Te13:

  | Check | NiO | Cs3V9Te13 |
  |---|---|---|
  | click selects one site | ✓ | ✓ |
  | Shift-click adds | ✓ | ✓ |
  | quick drag rotates the camera, selection kept | ✓ | ✓ |
  | long press arms the box | ✓ | ✓ |
  | box selects exactly the sites inside | ✓ 48/48 | ✓ 136/136 |
  | Esc clears the selection | ✓ | ✓ |
  | Accept uses the **edited** \|m\| (1.7 μB) | ✓ | ✓ |
  | Ctrl+Z / Ctrl+Y | ✓ | ✓ |
  | canvas 2×2×2 → 1×1×1 (FM) and undo | ✓ | ✓ |
  | Ctrl+A selects all magnetic sites | ✓ 32 | ✓ 72 |

## Bugs found by the new tests (fixed)
1. **Edited magnitudes were silently ignored.** The panel's `Signal(list, dict)` lost the dict when PySide converted it for C++ (`_pythonToCppCopy: Cannot copy-convert dict`), so Accept always used the suggested value. Changed to `Signal(object, object)`; the self-test now edits a magnitude and checks it.
2. **Redo had no Ctrl+Y on Linux**, because Qt's standard Redo key there is Ctrl+Shift+Z. Both are bound now, matching the UX spec.
3. **pyvista binds `q` to close the plotter**, plus `b`, `v` and others. A stray keypress could have closed the editor viewport. Cleared on both viewports.
4. **pyvista's `screenshot()` returns the cached frame** after the first render, so overlays added later were missing from offscreen PNGs. Offscreen code now calls `plotter.render()` first.

## Suggestions on your Cs3V9Te13
pymatgen's charge balance gives Cs⁺, Te²⁻ and **V +2.56 average (mixed valence)**. V1 (3g, 3 sites) and V2 (6k, 6 sites) are two separate rows, each starting at 2.4 μB, marked **low confidence** with "magnitude uncertain". All structures are tabulated in `artifacts/phase1/suggestions.md`, including the SrTiO₃ (Ti⁴⁺ d0, excluded), GdN (4f7, 7 μB) and RuO₂ (4d, low confidence, unchecked) examples.

## Notes and open items
- **Xvfb has no window manager**, so the self-test activates its window explicitly before sending shortcuts. On a desktop this is automatic.
- **The Cs3V9Te13 canvas is dense:** 200 atoms plus VESTA's boundary-mode-1 Te atoms and Cs–Te bonds. The Moment view (bonds hidden, non-magnetic atoms faded) is the intended editing view; Phase 2 switches to it automatically in Moment mode.
- **Spin-up red vs. red V atoms** is still open; it's addressed in Phase 2 (UX spec §5).

## Artifacts (`artifacts/phase1/`)
| File | Content |
|---|---|
| `contact_sheet.png` | everything below, labeled |
| `{nio,cs3v9te13}_canvas_2x2x2.png` | editing canvas with the input-cell grid |
| `{nio,cs3v9te13}_click.png` | offscreen click via the real state machine + picking: site + its periodic images haloed |
| `{nio,cs3v9te13}_box_dragging.png` / `_box_result.png` | long press + Shift-drag rubber band (with armed ring), then the resulting selection |
| `gui_nio/`, `gui_cs3v9te13/` | GUI self-test screenshots per step + `selftest.json` (all checks) |
| `suggestions.md` | suggestion tables for all test structures |

## Reproduce
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/phase1_artifacts.py
xvfb-run -a .venv/bin/python -m magview.gui.selftest tests/data/Cs3V9Te13_300K.cif --out /tmp/st
```
