# AgentDFT handoff format (`magview.handoff` 1.0)

The machine-readable schema is `src/magview/io/handoff.schema.json` (JSON Schema 2020-12). `magview.io.handoff.validate_handoff` checks the schema **and** the consistency rules below. MagView never writes a handoff that fails validation, and the mock bridge rejects one.

## Top level
| Key | Content |
|---|---|
| `schema`, `schema_version` | `"magview.handoff"`, `"1.0"` |
| `created_utc`, `generator` | timestamp; `{name: "MagView", version}` |
| `source` | original file, its sha256, formula, space group (number, symbol) |
| `input_cell` | the cell as loaded: `lattice` (rows, Å), `species`, `frac_coords`, `labels` |
| `reference_cell` | the **chemical primitive cell** used as the unfolding target: the input cell itself if it is primitive, otherwise spglib's primitive cell in the input orientation. Includes `parent_input_site` and a `description`. |
| `magnetic_cell` | the minimal magnetic supercell: `lattice`, `species` (**grouped by species in POSCAR order**), `frac_coords`, `labels`, `parent_input_site` |
| `supercell_matrix` | integer S with `magnetic_cell.lattice = S @ reference_cell.lattice` |
| `supercell_matrix_input` | integer M with `magnetic_cell.lattice = M @ input_cell.lattice`, or `null` if the magnetic cell is not a supercell of the input cell (e.g. NiO AFM-II from the conventional fcc cell) |
| `multiplicity` | \|det S\| = magnetic sites / reference sites |
| `canvas_matrix` | the editing canvas used in MagView (informational) |
| `moments` | `frame: "cartesian"`, `unit: "mu_B"`, `vectors` (one per magnetic-cell site, same order), `collinear`, `spin_axis` (unit vector), `signed_magnitudes` (collinear only: m·spin_axis) |
| `patterns` | `labels` (e.g. "Cu layers (square): Neel (checkerboard)", "Cu stacking along c: AFM stacking"), `stacking_axis`, and per-layer entries (`element`, `index`, `height_A`, `lattice`, `pattern`, `relation_to_layer0`) |
| `magnetic_symmetry` | spglib magnetic dataset summary: `uni_number`, `msg_type`, `n_operations`, `primitive_volume` |
| `requires_unfolding` | `true` exactly when `multiplicity > 1` |
| `unfolding` | when required: `supercell_matrix` S, `reference: "reference_cell"`, and a note (reference k-points K map to supercell k = S K) |
| `explanation` | human-readable reasons: whether the chemical cell suffices, and which translations are broken ("Translation c is not a magnetic translation: it maps V1 (up) → V1 (down)") |
| `dft_hints.vasp` | `ISPIN: 2`, `LNONCOLLINEAR`, `MAGMOM` (run-length encoded, e.g. `6*0 9*2.4 9*-2.4 26*0`), `SAXIS` (non-collinear: `[0,0,1]`, so the MAGMOM triples are Cartesian), `species_order`, `species_counts` |

## Consistency rules (beyond the schema)
- The lengths of `frac_coords`, `labels`, `parent_input_site` and `moments.vectors` equal the number of magnetic sites.
- `magnetic_cell.lattice == supercell_matrix @ reference_cell.lattice` (1e-4 Å).
- `multiplicity == |det S|`, and the number of magnetic sites equals `multiplicity × len(reference_cell.species)`.
- `requires_unfolding == (multiplicity > 1)`; `unfolding` is present when it is true.
- Collinear: every vector lies along `spin_axis`, and `signed_magnitudes` match.
- `MAGMOM` expands to N values (collinear) or 3N values (non-collinear), and `species_counts` sum to N.

## Consuming it (VASP-style backend)
1. Write POSCAR from `magnetic_cell` (`lattice`, `species`, `frac_coords`). The sites are already grouped by species, so `MAGMOM` lines up. `tests/test_magcell_handoff.py::test_vasp_consumer_smoke` does exactly this with pymatgen.
2. INCAR: `ISPIN = 2`, `MAGMOM = <dft_hints.vasp.MAGMOM>`. For a non-collinear run: `LNONCOLLINEAR = .TRUE.`, `SAXIS = 0 0 1`. For collinear + SOC, set `SAXIS` to `moments.spin_axis`.
3. If `requires_unfolding`: compute bands in `magnetic_cell` and unfold onto `reference_cell` with `supercell_matrix`.

## Mock bridge
`magview.bridge.agentdft.MockAgentDFTBridge` validates the handoff and stores it in `<root>/<job_id>/handoff.json` together with `job.json` (status `started (simulated)` and a history). `<root>` is `$MAGVIEW_AGENTDFT_DIR`, defaulting to `~/.magview/agentdft_jobs`. A real backend implements the same three methods: `validate`, `submit` and `status` (`AgentDFTBridge`).
