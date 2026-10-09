# Phase 3 report: layer-by-layer configuration + rule-based completion

Date: 2026-10-09. Produced headlessly: offscreen OSMesa renders, plus the real Qt GUI under Xvfb.

## Result
- **197 tests pass**, 0 skipped (about 95 s). 20 tests are new, including exhaustive matcher truth tables.
- The **GUI self-test** passes on **NiO** (39 checks) and **Cs3V9Te13** (35 checks: its layer net is the negative control).
- Artifacts are in `artifacts/phase3/`: `contact_sheet.png`, `matcher_sequences.png` and `matcher_truth_table.md`.

## What was built
| Piece | Description |
|---|---|
| `core/layers.py` | Magnetic canvas sites are grouped by element, then clustered into layers along a, b or c (default c). Clustering handles periodic wrap and buckling up to 0.35 Å. Each layer gets its periodic in-plane nearest-neighbour graph and a 2D lattice classification. |
| `core/patterns.py` | The matcher. For every canonical pattern of the layer's lattice, bond signs are propagated over the graph. A pattern matches only if **one global sign** reproduces **every pinned spin**. Status is `idle` (fewer than 2 pins or an unsupported net: show nothing), `no_match` (say so, do nothing) or `matches` (list the candidates). Excluded patterns carry a reason (e.g. "site Ni0 (a) cell [1,1,0] would be up", "does not fit the canvas periodicity"). **Never auto-applies.** |
| `doc.pinned` + layer ops | Clicked spins are *pinned* (undoable, saved in sessions, carried across canvas resizes). Operations: `layer_click` (unpinned → up → down → up), `layer_unset`, `layer_set` (many sites), `clear_layer`, `apply_completion` (whole layer, one undo step, pins unchanged). |
| `render/layer_view.py` | Top view of one layer. Moments along the view axis would be invisible end-on, so spins are discs: red "+" up, blue "−" down, gray unset. Dark rings mark pinned sites; candidate previews show lighter colors on unpinned sites; nearest-neighbour bonds and the canvas outline are drawn. |
| Layer mode (GUI) | `L` / toolbar, teal border + banner. Layers panel: stacking axis, layer list (PgUp/PgDn), lattice classification, suggestions as buttons (hover = preview, click = apply), excluded patterns with reasons, Clear layer, Selected: up/down. The reference view highlights the current layer in the full cell. |

## Your example, as the matcher answers it
On a square layer, two adjacent atoms with the **same** spin along [100] give exactly **FM** and **stripe with FM chains along [100]**. With **opposite** spins they give **Néel** and the **stripe along [010]**. On the honeycomb, two parallel neighbours leave 4 candidates (FM, 2 zigzags, 1 stripe). A third, antiparallel pin leaves 2, and a fourth pin fixing the last bond direction leaves a **unique zigzag** (`matcher_sequences.png`). The full table for square, honeycomb and triangular is in `artifacts/phase3/matcher_truth_table.md`.

## Verification highlights
- **Decomposition** on 7 structures:
  - CuO₂ toy: 2 × 4 Cu, square.
  - La₂CuO₄: 4 CuO₂ planes per 2×2×2 canvas, square.
  - FeSe: 2 × 8 Fe, square (bonds along [110]/[1-10]).
  - Honeycomb and triangular toys.
  - NiO: 4 (001) layers, square.
  - **Cs3V9Te13: 2 V layers of 36 sites, unsupported.**
  - Element grouping: Mn and Fe in the same plane become separate sublattices.
- **Exhaustive truth tables** (square, honeycomb, triangular, FeSe): for every pattern, both global signs, and every 2- and 3-site pin set, the matcher's candidates **exactly equal a brute-force check** over the library (soundness + completeness).
- **Explicit cases:**
  - Fewer than 2 pins: idle.
  - Unsupported net: idle even with 10 pins.
  - An up-up-down chain along [100]: `no_match`, with every library pattern excluded.
  - A 2×1 canvas: Néel excluded with "does not fit the canvas".
- **GUI** (NiO): `L` enters layer mode; Clear layer; two parallel clicks → {FM, stripe [110]}; flip one → {Néel, stripe [1-10]}; hover preview leaves the document untouched; Apply Néel → every in-layer nearest-neighbour bond antiparallel; Ctrl+Z restores the 2 pins; right-click unpins → idle; PgDn → next layer; Esc → back to the 3D view.
- **GUI** (Cs3V9Te13): clicks still pin spins (manual assignment works), and the matcher stays silent with "no canonical patterns: coordination 2 with 3 bond directions is not a square, honeycomb or triangular net".

## Bug found and fixed
**pyvista silently replaced the editor's interactor style.** pyvista remembers "its" style and re-applies it on some calls (`iren.update_style()`). After the first layer redraw, our style was gone and every further click did nothing. The GUI self-test caught it: the third click didn't toggle. The fix registers the style through pyvista's own setter (`plotter.iren.style = ...`). The bug could have struck in any mode, so this matters beyond Phase 3.

## Artifacts (`artifacts/phase3/`)
| File | Content |
|---|---|
| `layers_{La2CuO4,NiO,Cs3V9Te13_300K}.png` | layer decomposition (each layer one color) |
| `matcher_sequences.png` | square: 1 pin → waiting; 2 parallel → FM/stripe; hover preview; antiparallel → Néel/stripe; applied Néel. Honeycomb: 4 → 2 → 1 candidates, applied zigzag. Cs3V9Te13: silent. |
| `matcher_truth_table.md` | generated table of scenarios → candidates |
| `gui_nio/09–11`, `gui_cs3v9te13/09` | real-GUI screenshots of layer mode |

## Notes
- The matcher works on nearest-neighbour bonds only, as specified: a small, deterministic library. Longer-period orders (↑↑↓↓ double stripe, 120°) are not suggested; you can still set them by hand or with the Phase 2 tools.
- Inter-layer stacking (FM/AFM between layers) is Phase 4. The panel already has a slot for it.

## Reproduce
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/phase3_artifacts.py
```
