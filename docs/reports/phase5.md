# Phase 5 report: minimal magnetic supercell + AgentDFT handoff

Date: 2026-10-09.

## Result
- **237 tests pass**, 0 skipped, including a run in a fresh venv built only from `requirements.txt` (`pip check` clean). 28 tests are new.
- The **GUI self-test** passes 54/54 on NiO and 50/50 on Cs3V9Te13, with 8 new export checks each.
- Artifacts are in `artifacts/phase5/`:
  - `minimal_cells.md` (results table);
  - `handoffs/*.json` (12 sample handoffs, all valid);
  - `cell_*.png` (renders of the magnetic cells);
  - GUI screenshots, and a mock job record (`gui_*/mock_agentdft_job.json`).
- New dependency: **jsonschema** (pinned, with its 3 dependencies), to validate the handoff against a real JSON Schema.

## Minimal magnetic cell: method (`core/magcell.py`)
1. Work in the basis of the **chemical primitive lattice**, so centring translations of conventional cells count. If the input cell is already primitive, it is the reference itself.
2. Test every primitive translation t modulo the canvas lattice: does it map every site onto a site of the same species **with the same moment**? Site matching is tolerance-based (0.01 Å, periodic).
3. The invariant translations plus the canvas lattice generate the magnetic lattice. Its basis comes from a Hermite normal form, then LLL reduction, and it is re-expressed relative to the input cell when that is integral, preferring a diagonal matrix.
4. **Explanation:** each input translation (a, b, c and every centring vector) that is *not* magnetic is reported with an example ("maps V1 (up) → V1 (down)").
5. **Independent check:** spglib's magnetic symmetry dataset gives a magnetic primitive lattice whose volume must equal ours. It does in all 12 test scenarios.

## Results (`artifacts/phase5/minimal_cells.md`)
| Scenario | × prim | vs input cell | Why |
|---|---|---|---|
| Fe bcc FM | 1 | – (primitive only) | the primitive cell, ½ of the conventional input cell |
| square Néel | 2 | **√2×√2** `[[1,1,0],[-1,1,0],[0,0,1]]` | a and b map Cu↑ → Cu↓ |
| square stripe (FM chains ∥ a) | 2 | **1×2×1** | b maps ↑ → ↓ |
| square stripe (FM chains ∥ b) | 2 | **2×1×1** | a maps ↑ → ↓ |
| NiO AFM-II | 2 | – (rhombohedral) | a, b, c and both face centrings are broken |
| NiO AFM-I | 2 | – | the (½,0,½) centring is broken |
| La₂CuO₄ A-type | 2 | **1×1×1** (the conventional cell) | only the (½,½,½) centring is broken: the conventional cell suffices, the primitive one doesn't |
| **Cs3V9Te13 FM** | 1 | **1×1×1** | the chemical cell suffices |
| **Cs3V9Te13 AFM stacking** | 2 | **1×1×2** | c maps V1↑ → V1↓ |
| honeycomb zigzag | 2 | 1×2×1 | b broken |
| simple cubic G-type | 2 | det-2 cell | a, b, c broken |
| triangular 120° (non-collinear) | 3 | √3×√3 | a, b rotate moments by ±120° |

These are exactly the cases named in your spec: Néel → √2×√2, stripe → 1×2 or 2×1, chemical cell when it suffices. The plan's predictions for Cs3V9Te13 (FM → 1×1×1, AFM stacking → c doubled) hold.

## Handoff (`io/handoff.py`, `io/handoff.schema.json`, `docs/handoff_format.md`)
The handoff contains:
- input, reference (primitive) and magnetic cells;
- the integer `supercell_matrix` (magnetic = S @ reference), plus the input-relative matrix when it exists;
- per-site Cartesian moments, with signed magnitudes in the collinear case;
- **detected pattern labels**: per-layer in-plane order (exact matches only) and stacking;
- the magnetic space group (spglib);
- `requires_unfolding` and the unfolding transformation;
- the explanation;
- VASP hints: run-length `MAGMOM` in POSCAR site order, `LNONCOLLINEAR`/`SAXIS` for non-collinear runs, species order and counts.

Validation is JSON Schema followed by consistency checks. Seven tamper tests confirm each rule is enforced. A consumer smoke test writes a POSCAR with pymatgen and checks that MAGMOM lines up.

## AgentDFT bridge (`bridge/agentdft.py`)
`AgentDFTBridge` (validate / submit / status) is the interface. `MockAgentDFTBridge` validates, stores `handoff.json` + `job.json` in a job folder and reports `started (simulated)`, with a note on what a real backend would run ("collinear spin-polarized … and unfold onto the reference cell"). Invalid handoffs are rejected and nothing is stored. **No DFT runner.**

## GUI (Export tab)
**Analyze magnetic cell** shows: supercell required or not, multiplicity, matrix relative to the input cell, `requires_unfolding`, the reasons, the pattern labels, the MSG and the MAGMOM. The other controls:
- **Show the magnetic cell in the main view**: a read-only preview with a purple banner, which leaves editing modes first.
- **Save handoff JSON…** (also under File).
- **Send to AgentDFT (mock)**: shows the job id, status and folder.

Any edit invalidates the analysis, so a stale handoff can't be exported.

## Bug found and fixed
**Site matching by rounded-coordinate hashing split sites.** Cs3V9Te13's CIF coordinates such as 0.1555 sit exactly on 10⁻³ rounding half-points, so floating-point noise put one site into two hash bins, giving 26 sites instead of 25. Matching is now tolerance-based (periodic distance below 0.01 Å).

## Reproduce
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/phase5_artifacts.py
```
