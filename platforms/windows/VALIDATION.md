# Native Windows source validation

Validated 2026-10-10 on Windows 11 x64, build 26200, with native Python 3.13.13,
PySide6/Qt 6.8.3, VTK 9.7.1 and PyVista 0.49.0. GPU: Intel Arc Graphics;
driver OpenGL 4.5.0, build 32.0.101.6874. Source-only delivery.

| Check | Result |
|---|---|
| Full `python -m pytest -q --tb=short` | **326 passed, 5 skipped**, 99.87 s |
| Final native GUI regression rerun | **3 passed**, 47.92 s (NiO, Cs3V9Te13, repeated output directory) |
| NiO GUI, 200% display scale | **92/92** |
| Cs3V9Te13 GUI, 200% display scale | **88/88** |
| NiO GUI without any usable VESTA directory | **92/92**, built-in appearance fallback |
| NiO / Cs3V9Te13 GUI at 100% effective scale | **92/92** and **88/88** |
| 125% / 150% selection and camera smoke | **12/12** each |
| Chinese/spaced paths and UTF-8 session labels | Pass, including Greek text and emoji |
| Windows PowerShell 5.1 `setup.ps1`, `setup.cmd` | Pass |
| `run.cmd` from outside the repository, offscreen PNG | Pass, 400 x 300 |
| `run.ps1 --help`, `pip check`, `git diff --check` | Pass |

The full GUI checks include atom/box selection, moment dragging and numerical
agreement with the delivered cursor position, non-collinear moments, axis
locks, undo/redo, FM/AFM patterns, layer editing, stacking, magnetic-cell
analysis/preview, handoff export, session save/load, recovery, error dialogs,
hover cursors, keyboard focus and saved window geometry.

Five skips are intentional: four Linux apt/system-dependency checks and one
Qt `offscreen` platform test because that platform cannot initialize the native
Windows OpenGL widget. The native desktop GUI tests run and pass.

The screen's normal device-pixel ratio is 2.0. Additional checks use Qt scale
factors 0.5, 0.625 and 0.75, verified as effective DPR 1.0, 1.25 and 1.5; the
Windows system-wide display settings were not changed. The original local
`Cs3V9Te13_300K.cif` was also opened during the 100% GUI test.

Per-step GUI results are in [evidence](evidence/). Large screenshots and logs
remain local under ignored `build/`. No executables, virtual environments or
VESTA data are committed. This validates the source configuration on this
host; Windows 10, other GPU drivers, ARM64 and headless Windows have not been
tested. Linux dependency pins and packaging files are unchanged.
