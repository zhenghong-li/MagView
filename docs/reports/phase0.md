# Phase 0 report: foundations

Date: 2026-10-09. All results below were produced in the headless container (Ubuntu 24.04, Python 3.12.3, Mesa llvmpipe).

## Result
- **73 tests pass**, with 0 skipped and 0 failed: `.venv/bin/python -m pytest -q`. A fresh venv built only from `requirements.txt` also passes all 73, and `pip check` reports no broken requirements.
- **15 PNG artifacts** are in `artifacts/phase0/`. Start with `contact_sheet.png`.
- The **GUI shell runs**: the real PySide6 window under Xvfb, with both OpenGL viewports rendering (`gui_shell.png`).

## Tests by area
| File | Tests | What they establish |
|---|---|---|
| `test_system_deps.py` | 4 | `--list` matches the package file; `--check` exits 0; a second install run is a no-op ("nothing to do"); unknown options are rejected |
| `test_architecture.py` | 3 | layering: `core` has no Qt/VTK/style; `style` has no Qt/VTK; `render` has no Qt |
| `test_vesta_ini.py` | 6 | 97 element rows; the Fe row; 914 SBOND rules (Fe–O 2.44693 Å; the H···O row is style 5); the STYLE defaults; built-in theme == theme read from VESTA; how far pymatgen's colors differ |
| `test_elements.py` | 9 | symbol parsing (`Fe2+` → Fe); VESTA source; pymatgen fallback; gray for unknown symbols; fallback bond rules (no metal–metal) |
| `test_structure.py` | 15 | space groups 229/225/139/129/189/123/191/191; Cs3V9Te13 details; fcc primitive volume = V/4; disorder rejected; bad or missing files; POSCAR round trip; coordinate wrapping |
| `test_images_bonds.py` | 13 | image counts (bcc Fe 2→9; NiO Ni 14, O 13; Cs3V9Te13 Cs 10, V 11, Te 19); every image maps to its parent; larger boxes; NiO octahedra completed (every Ni has CN 6); boundary mode 0; no Fe–Fe bonds; Cs3V9Te13 follows VESTA's cutoffs |
| `test_render.py` | 20 | camera conventions; constant-thickness arrows (same shaft radius at 1 and 3 μB, tip and tail positions); arrow mesh is closed with outward normals; arrow colors; non-blank renders; **color accuracy**; moment view |
| `test_cli_gui.py` | 3 | `--render` CLI; clean error on a bad CIF; **GUI screenshot under Xvfb** with atoms visible in the 3D viewport |

**Color accuracy check.** Each structure is rendered flat-lit at 640×480 with no anti-aliasing. Every atom center is projected to screen with VTK's own transform. For each atom not covered by a nearer atom, the pixel is compared with the element color from `elements.ini`. The test passes when the maximum channel error is ≤ 2/255; it currently passes exactly (e.g. V = (230, 26, 0), Te = (173, 162, 82)). Writing this test exposed an initial bug in the test itself: atoms on a cell face have their center pixel covered by the black cell edge in front of them. Color renders now hide the cell lines.

## Your Cs3V9Te13 CIF
- It is copied unmodified to `tests/data/Cs3V9Te13_300K.cif` (sha256 in `tests/data/README.md`).
- pymatgen parses it despite the embedded `_shelx_res_file` and the 15.8k-line `_shelx_hkl_file`. It loads as Cs3V9Te13 with Z = 1, 25 sites, P-62m (#189), a = 10.089 Å, c = 8.212 Å.
- Wyckoff sites: Cs1 3f, V1 3g, V2 6k, Te1 4h, Te2 6i, Te3 3g.
- CIF labels survive symmetry expansion.
- **All 9 V atoms are at z = ½**, a single V layer per cell, with V1 and V2 as two separate orbits.
- **Bonds behave as in VESTA.** VESTA's V–Te cutoff is 2.72642 Å, so V1 shows 5 bonds (4 Te2 + 1 Te3), but V2 shows only its 2 Te3 bonds: the Te1–V2 (2.740 Å) and Te2–V2 (2.817 Å) bonds are just over the cutoff. No V–V or Te–Te bonds are drawn. This is VESTA's behavior, not a MagView bug, but it means VESTA's default picture understates V2's coordination.

## Findings and decisions
1. **VESTA can't run in this container.** `VESTA-gui` needs `libwebkit2gtk-4.0.so.37`, which Ubuntu 24.04 no longer ships. Visual comparison happens on your host (checklist in `docs/vesta_reference.md`).
2. **pymatgen's "VESTA" color scheme isn't VESTA 3.90's.** Only 38 of 95 elements match within 3/255. Cs, C, Te and most lanthanides are completely different. MagView reads `elements.ini` at runtime and treats pymatgen only as an approximate fallback.
3. **Offscreen backend.** VTK 9.7 wheels work with both OSMesa and EGL (llvmpipe). Without a display, MagView selects OSMesa explicitly so results are reproducible. A render takes about 0.15 s.
4. **spglib 2.7** deprecates its legacy None-return errors. MagView opts in to exceptions and wraps them in `StructureLoadError`.
5. **Moment view added early.** In the default ball-and-stick view, NiO arrows were buried among bonds and red O atoms. The spec's moment view (atoms 0.6×, non-magnetic atoms at 25% opacity, no bonds) fixes this and is used for the arrow artifacts.
6. **Open UX issue for Phase 2:** the spin-up red is too close to VESTA's red for O and V (see `cs3v9te13_v_moments.png`, where V and its arrow merge into one red pin). Recorded in `docs/ux_spec.md` §5.
7. **Still to calibrate on the host:** the ball-and-stick radius scale (0.5, inferred from `ATOMP` = 50), parallel vs perspective projection, the default orientation, and whether default bonds are bicolor.

## System packages: please sync your Dockerfile
`scripts/system-packages.txt` is final for Phase 0. It has 37 packages; `scripts/setup_system_deps.sh --list` prints them:

```
python3.12-venv libosmesa6 libgl1 libegl1 libopengl0 libgl1-mesa-dri libglx-mesa0
libx11-6 libx11-xcb1 libxext6 libxrender1 libxt6t64 libxcursor1 libxi6 libxrandr2
libglib2.0-0t64 libfontconfig1 libfreetype6 libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0
libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0
libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-xfixes0 libxcb-shm0
libxcb-sync1 libxcb-render0 xvfb xauth
```

Compared with the plan: `libglu1-mesa` was dropped because nothing needs it. `ldd` over all VTK and PySide6 libraries finds only the Wayland client libraries missing (`libwayland-cursor/-server/-egl`). Those are used only by Qt's Wayland platform plugin, which the container never loads (it uses xcb under Xvfb). They are left out on purpose.

## Pinned versions (direct dependencies)
numpy 2.5.3 · pymatgen 2026.9.24 (pymatgen-core 2026.10.2) · spglib 2.7.0 · vtk 9.7.1 · pyvista 0.49.0 · PySide6-Essentials 6.12.0 · pyvistaqt 0.13.1 · QtPy 2.4.3 · pytest 9.1.1. The full freeze has 59 packages in `requirements.txt`.

## Artifacts (`artifacts/phase0/`)
| File | Content |
|---|---|
| `contact_sheet.png` | all of the below, labeled |
| `{Fe_bcc,NiO,La2CuO4,FeSe,Cs3V9Te13_300K}_oblique.png` / `_c.png` | VESTA-style renders (colors, radii, bicolor bonds, boundary-mode-1 polyhedra, a/b/c triad) |
| `nio_afm2_arrows.png`, `nio_afm2_arrows_b.png` | NiO AFM-II on a 2×2×2 conventional supercell: FM (111) planes alternating, moments along [-1 -1 2], moment view |
| `cs3v9te13_v_moments.png` | Cs3V9Te13 with V moments along +c: the V sublattice at z = ½ |
| `gui_shell.png` | the real GUI under Xvfb: main view, reference view with the full cell, structure info |

## How to reproduce
```bash
cd magview
sudo scripts/setup_system_deps.sh && scripts/setup_system_deps.sh --check
.venv/bin/python -m pytest -q
.venv/bin/python scripts/render_gallery.py
```
On the host: `python -m magview tests/data/NiO.cif`.

## Next: Phase 1
Editing canvas (2×2×2), picking (pick → parent site), selection (click, Shift/Ctrl, long-press box), the interaction state machine with synthetic-event tests, undo/redo, and the magnetic-site suggestion engine and panel. Cs3V9Te13 is the case where V is mixed-valence, so its suggested magnitude must be flagged as uncertain.
