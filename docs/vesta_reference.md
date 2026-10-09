# VESTA reference notes

These notes come from VESTA 3.90.9a (Linux gtk3 build) at `../vesta_tools/VESTA-gtk3-x86_64/`. MagView reads these files **at runtime** from the user's installation, located through `$MAGVIEW_VESTA_DIR` or by searching for `vesta_tools/VESTA-gtk3-x86_64`. It never copies them, because the VESTA license forbids redistributing them.

Confidence levels: **certain** means the value was verified by parsing and tests. **inferred** means it follows from the data's structure but has not been confirmed against VESTA's own UI. **to calibrate** means it needs a side-by-side comparison with VESTA on the host.

## `elements.ini` (certain)

The file has one row per element: `Z  symbol  r_atomic  r_vdW  r_ionic  R  G  B`. Radii are in Å; RGB values are floats in [0, 1].

- 97 rows: Z = 1–95, plus `D` (deuterium, Z = 1) and `XX` (Z = 96, a gray fallback for unknown species).
- Example: `26 Fe 1.26 1.88 0.780 0.71051 0.44662 0.00136`.
- `parse_elements_ini` checks that every row parses; `test_vesta_ini.py` covers this.

**Finding:** pymatgen ships a "VESTA" color scheme (`pymatgen/core/ElementColorSchemes.yaml`), but it matches VESTA 3.90's `elements.ini` to within 3/255 for only **38 of 95** elements. The common transition metals, H and O agree. C, Cs, Te and most lanthanides differ completely; Cs, for example, is purple in pymatgen and teal in VESTA. MagView therefore treats the pymatgen scheme as an approximate fallback only.

## `style.ini` (format `#VESTA_STYLE_VER 3.5.0`)

### Header (lines 2–14), meaning unknown
These are 13 short numeric lines before `SBOND`, for example `255 0 0 255` and `1.000 2.000 1.000 2.000 1.000`. They are stored as raw tokens in `VestaStyle.header`. MagView does not use them.

### `SBOND`: bond-search rules (certain except where noted)
There are 914 rows, ending with the terminator `0 0 0 0`:

```
idx  A1  A2  d_min  d_max  search_mode  boundary_mode  show_polyhedra  search_by_label  style
255  Fe  O   0.00000 2.44693  0  1  1  0  1
306  H   O   1.20000 2.10000  0  0  0  0  5      <- hydrogen bond
```

- Nearly all rows are cation–anion pairs. **Metal–metal pairs mostly have no rule**, so VESTA draws no Fe–Fe bonds in bcc Fe and no V–V bonds in Cs3V9Te13.
- `boundary_mode` (inferred from VESTA's UI wording):
  - 0: never search outside the boundary.
  - 1: every A1 atom inside the boundary pulls in its bonded A2 neighbors from outside it. This is what completes coordination polyhedra. Most rows use 1.
  - 2: recursive search for molecules, used by most C–X rows. MagView currently treats 2 as 1.
- `style` (inferred, 0-based): 0 unicolor cylinder, 1 bicolor cylinder, 2 color line, 3 gradient line, 4 dotted line, 5 dashed line. Ordinary rows use 1 (bicolor); the H/D···O hydrogen-bond rows use 5.

**Visible consequence (Cs3V9Te13):** the V–Te cutoff is 2.72642 Å. VESTA therefore shows V2 (6k) with only its two Te3 bonds, because Te1–V2 (2.740 Å) and Te2–V2 (2.817 Å) fall just beyond the cutoff. V1 shows 5 bonds (4 Te2 + 1 Te3). MagView reproduces this, and `test_cs3v9te13_follows_vesta_cutoffs` asserts it.

### `STYLE` block (default display)
| Key | Value | Meaning used by MagView | Confidence |
|---|---|---|---|
| `ATOMS 0 0 1` | radius type 0 | atomic radius (column 3 of elements.ini) | inferred |
| `ATOMP 24 24 0 50 2.0 0` | 24 x 24 | sphere stacks/slices | inferred |
| | field 4 = 50 | ball-and-stick radius scale = 50 % | **to calibrate** |
| `BONDP 1 16 0.250 2.000 127 127 127` | 1 / 16 / 0.25 / 2.0 / gray | bond style (bicolor), cylinder sides, radius (Å), line width (px), unicolor RGB | inferred (style), certain (numbers) |
| `VECTS 1.000000` | 1.0 | vector length scale (Å per unit) | inferred |
| `UCOLP 0 1 1.000 0 0 0` | width 1, black | unit-cell line | inferred |
| `PROJT 0 0.962` | 0 | parallel projection | **to calibrate** |
| `BKGRC 255 255 255` | white | background | certain |
| `DPTHQ 1 -0.5 3.5` | | depth cueing on (not implemented) | inferred |
| `LIGHT0 1` | ambient 26, diffuse 179, specular 255; direction (0,0,-1) in eye space | one headlight; VTK ambient 0.10, diffuse 0.70 | inferred |
| `ATOMM 204 204 204 255 / 25.600` | | material specular 0.8, shininess 25.6 | inferred |

`RenderTheme()` defaults are transcribed from this table. `test_theme_from_vesta_matches_builtin_defaults` fails if the two drift apart.

### Other files (not used)
- `mspgr.dat`: magnetic space-group tables. MagView uses spglib's magnetic symmetry instead.
- `spgra.dat`, `spgro.dat`, `wyckoff.dat`: space-group and Wyckoff tables (spglib covers these).
- `elements/NN.txt`: X-ray anomalous scattering factors f1/f2 (irrelevant).
- `bvparm2020.cif`: bond-valence parameters. Possibly useful later for oxidation-state suggestions.

## Running VESTA itself
`VESTA-gui` links against `libwebkit2gtk-4.0.so.37`, which Ubuntu 24.04 no longer provides. VESTA therefore **cannot run in the MagView container**, and visual comparison has to happen on the host. Calibration checklist for the host:

1. Open `tests/data/NiO.cif` in VESTA and in `python -m magview tests/data/NiO.cif`.
2. Compare ball size relative to the cell edge. That confirms or corrects `ball_scale = 0.5`.
3. Compare the default orientation and the projection (parallel or perspective).
4. Compare bond coloring (bicolor or gray unicolor). That confirms the style-code mapping.
