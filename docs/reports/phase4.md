# Phase 4 report: inter-layer magnetic coupling (stacking)

Date: 2026-10-09.

## Result
- **209 tests pass**, 0 skipped. 12 tests are new.
- The **GUI self-test** passes 45/45 on NiO and 41/41 on Cs3V9Te13, with 6 new stacking checks each.
- Artifacts are in `artifacts/phase4/`: `contact_sheet.png` and `stacking_outcomes.md`.

## How stacking is defined (deterministic, no guessing)
1. **Reference = FM stacking.** Layer 0's spins are carried up the stack along the shortest **inter-layer nearest-neighbour bonds** (bonds ending in the next layer at the right height), each demanding parallel spins.
   - If a site receives contradictory demands, the stacking is **frustrated** and no reference exists. Example: a Néel plane over a body-centred (La₂CuO₄) or fcc (NiO) neighbour plane, where each site sees two up and two down neighbours.
   - If carrying layer 0 around the whole canvas period doesn't return it unchanged, the stacking is **inconsistent with the canvas**.
   - Either case is reported with its reason, and only per-layer flips are offered.
2. **Relation of each layer to its reference:** same (+1), flipped (−1), different in-plane order (0), or unassigned. This is computed from the assigned spins only, so **a single clicked spin in another layer already fixes that layer's relation.**
3. **Stacking library** (flip sequences relative to the reference):
   - FM `+ + + +`
   - AFM `+ − + −`: needs an even number of layers per canvas period, otherwise "double the canvas along c".
   - double `+ + − −` / `+ − − +`: needs a multiple of 4 layers.
4. **Smart completion** uses the same rule as the in-plane matcher: patterns that agree with every layer whose relation is known are suggested (bold, "← suggested"). Hovering previews; clicking applies to every layer of that element in one undo step. Fewer than one assigned layer besides layer 0 → idle. A layer with a different in-plane order → no match, with the reason.
5. **Custom per-layer flip** ("Flip this layer") is always available. It doesn't need a reference.

## What was built
| Piece | Description |
|---|---|
| `core/stacking.py` | `interlayer_bonds` (periodic, height-filtered shortest bonds), `fm_reference` (transport + frustration/periodicity checks), `relation`, `stacking_library`, `match_stacking` |
| `MagDocument.flip_layer` | custom flip, one undo step |
| Layers panel → Stacking box | reference status, per-layer relations ("0: ref, 1: flipped, 2: unset…"), one button per library pattern (disabled with the reason when invalid, bold when suggested, hover preview), Flip this layer |
| Auto-fitted arrows on Accept | If accepted moments would be shorter than the moment-view atoms (e.g. Cu 1 μB at 1 Å/μB), the arrow scale is auto-fitted once, with a status message. It happens only on that explicit action, never during a drag. Found while reviewing the Phase 4 renders. |

## Verification highlights
- **Library validity** for n = 1–4 layers; AFM is refused for odd n and the double patterns for n ≠ 4k, each with a reason.
- **Direct stacking** (CuO₂ toy) carries a Néel plane unchanged to the next layer.
- **Frustration is detected exactly:** a Néel plane over the body-centred La₂CuO₄ stacking and over the fcc NiO (001) stacking. FM planes stack fine in both.
- **Single-layer canvas:** the layer above is its own periodic image, so FM works and AFM is refused ("double the canvas").
- **Relations and matching:** an incomplete layer 0 means no reference; {+, −, ?, ?} → {AFM, + − − +}; {+, +, ?, ?} → {FM, + + − −}; custom flips → no match.
- **GUI** (real Qt events):
  - layer 0 set to FM and the other layers cleared → idle;
  - **one click-click (up→down) on a single site of layer 1 → "AFM stacking ← suggested"** (NiO also suggests `+ − − +` with 4 layers);
  - Apply → relations (+, −, +, −);
  - Flip this layer → (+, +, +, −) → no canonical stacking (NiO), or {FM} (Cs3V9Te13, 2 layers).

## Outcomes (`artifacts/phase4/stacking_outcomes.md`)
| Structure | In-plane | Stacking | Result |
|---|---|---|---|
| CuO₂ toy | Néel | FM | C-type |
| CuO₂ toy | Néel | AFM | G-type |
| FeSe | stripe | AFM | applied |
| La₂CuO₄ | FM | AFM | A-type |
| **Cs3V9Te13** | FM (V planes) | AFM | applied (c-doubling is checked in Phase 5) |
| La₂CuO₄ | Néel | – | **not offered: frustrated** |
| NiO (001) | Néel | – | **not offered: frustrated** |

## Reproduce
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/phase4_artifacts.py
```
