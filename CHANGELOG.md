# Changelog

## 0.1.0 (2026-10-10)

First release, packaged for Linux and WSL (x86_64).

- Structure loading (CIF, POSCAR) with symmetry from spglib; VESTA-like rendering with
  the user's own VESTA colors, radii and bond rules when available.
- Editing canvas with click and long-press box selection, undo/redo, and magnetic-site
  suggestions.
- Moment editing: drag arrows with axis locks and snapping, batch FM/AFM patterns,
  layer-by-layer up/down with rule-based completion (FM / Neel / stripe / zigzag),
  inter-layer stacking.
- Minimal magnetic supercell (relative to the chemical primitive cell, with an spglib
  magnetic-space-group cross-check) and a validated JSON handoff for AgentDFT
  (mock bridge).
- Sessions (`.magview.json`) and recovery autosaves; Help > keyboard and mouse
  reference (F1).
- Release package: one folder, no installation; the packaged app passes the same
  92-check GUI self-test as the source tree.
