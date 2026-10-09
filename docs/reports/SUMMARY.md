# MagView: summary of the work (start here)

All six phases of the approved plan are implemented and committed, one commit per phase:

```
6b330b9 Phase 5: minimal magnetic supercell and AgentDFT handoff
8762e2d Phase 4: inter-layer stacking with rule-based completion
0de2f78 Phase 3: layer-by-layer editing with rule-based pattern completion
d33f73d Phase 2: direct manipulation of magnetic moments
67c04de Phase 1: editing canvas, picking, selection gestures, undo, site suggestion
bc0b7a3 Phase 0: foundations - structure loading, VESTA-like rendering, GUI shell
```

**Verification:**
- **237 automated tests pass**, including a run in a fresh venv built only from `requirements.txt`.
- A **scripted GUI self-test** drives the real Qt window with synthetic mouse and keyboard events under Xvfb: 54 checks on NiO and 50 on your **Cs3V9Te13** CIF, all passing.
- Each phase has a report (`docs/reports/phaseN.md`) and rendered evidence (`artifacts/phaseN/contact_sheet.png`).

## What to look at first (5 minutes)
1. `artifacts/phase5/minimal_cells.md`: your examples, computed (Néel → √2×√2, stripe → 1×2 / 2×1, FM → chemical cell, Cs3V9Te13 AFM stacking → 1×1×2, NiO AFM-II rhombohedral, …).
2. `artifacts/phase3/matcher_sequences.png` and `matcher_truth_table.md`: click-by-click rule-based completion, with Cs3V9Te13 as the negative control (the matcher stays silent).
3. `artifacts/phase2/drag_sequence.png` and `gui_*/06_drag_live.png`: direct manipulation; the arrow tip follows the cursor.
4. `artifacts/phase4/contact_sheet.png`: stacking, including frustration that is detected and reported rather than guessed.
5. `artifacts/phase5/handoffs/*.json` and `docs/handoff_format.md`: the AgentDFT handoff.

## Running it on your host
- **WSL (WSLg):**
  1. `sudo scripts/setup_system_deps.sh`
  2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e . --no-deps`
  3. `QT_QPA_PLATFORM=xcb .venv/bin/python -m magview tests/data/Cs3V9Te13_300K.cif`

  The xcb setting is suggested because the Qt Wayland client libraries aren't in the package list.
- **Windows / macOS:** create a venv, then `pip install -r requirements.txt` and `pip install -e . --no-deps`. Point `MAGVIEW_VESTA_DIR` at your VESTA folder (the one with `elements.ini` and `style.ini`) to get VESTA's colors and bond rules. Without it, MagView falls back to approximate pymatgen colors.
- **Workflow:**
  1. Open a CIF; check the Magnetic sites tab and Accept.
  2. `M` for moment editing: drag, `F`/`0`/`P`.
  3. `L` for layers: click spins, take suggestions, stack.
  4. Export tab: Analyze, then Save handoff or Send (mock).

## Decisions I made while you were away
1. **Phases 1–5 continued after Phase 0** under your "decide on your own" instruction. Each is a separate commit, so any phase can be reviewed or reverted on its own.
2. **UX spec frozen at Phase 2.** Every deviation is logged with its reason in `docs/ux_spec.md` §8:
   - the arrow tip follows the cursor;
   - moment-view atoms are desaturated;
   - letter keys go through a viewport key filter;
   - Ctrl+Y and Ctrl+Shift+Z both redo;
   - the arrow scale is auto-fitted once on Accept;
   - and others.
3. **Stacking is defined through inter-layer nearest-neighbour bonds.** That makes frustration exact. Example: Néel planes in La₂CuO₄ or NiO are reported "frustrated" rather than stacked arbitrarily.
4. **The minimal cell is computed relative to the chemical primitive cell**, so centrings count. Example: La₂CuO₄ A-type fits in the conventional cell but needs 2× the primitive cell. The handoff gives both matrices.
5. **jsonschema** was added (pinned) to validate the handoff against a real JSON Schema.

## Bugs that the tests caught (all fixed)
- Edited suggestion magnitudes were silently dropped: PySide `Signal(list, dict)` lost the dict.
- pyvista bound `q` to "close plotter"; it is now disabled in the editor.
- pyvista silently replaced the editor's interactor style after redraws, which stopped clicks from working. The style is now registered through pyvista.
- Site matching by rounded-coordinate hashing split a Cs3V9Te13 site (CIF values like 0.1555 sit on rounding half-points). Matching is now tolerance-based.
- Smaller ones: VTK text garbles `|`; pyvista's `screenshot()` returns a cached frame; the centered arrow lagged the cursor; red arrows merged into red V and O atoms.

## For you to do or decide
- **Dockerfile:** the final package list is `scripts/system-packages.txt` (37 packages, unchanged since Phase 0). `scripts/setup_system_deps.sh --list` prints it.
- **Calibrate against VESTA on the host:** ball-and-stick radius scale (currently 0.5, inferred from `ATOMP`), parallel vs perspective projection, default orientation, bicolor bonds. Checklist in `docs/vesta_reference.md`.
- **Your Cs3V9Te13 results:**
  - V is suggested at 2.4 μB, flagged low confidence because the average valence is V^+2.56.
  - The V nets are trimers, so the matcher correctly stays silent and you assign spins manually.
  - FM V planes → 1×1×1; AFM stacking → 1×1×2.
- **Known limits, by design or deferred:**
  - The matcher covers nearest-neighbour canonical patterns only (FM, Néel, stripe, zigzag), per spec.
  - Layers are taken along a, b or c only (no [hkl] such as NiO (111)); AFM-II is still reachable with the moment tools.
  - VESTA bond boundary mode 2 (molecules) is treated as mode 1.
  - Drags are sensitive on large canvases at 1 Å/μB (use Shift-snap, the exact fields or Auto-fit).
  - The GUI has only been exercised under Xvfb + Mesa so far, not yet on a real desktop.
