# MagView architecture

## Layers

```
gui/      PySide6 + pyvistaqt   thin wiring; no algorithms
  |
render/   pyvista/VTK, no Qt    scene model -> actors; identical onscreen and offscreen
  |
style/    pure Python           VESTA ini parsers, element styles, theme, Appearance
  |
bridge/   pure Python           AgentDFT interface + mock (validates and stores jobs)
  |
io/       pure Python           handoff build + JSON Schema/semantic validation
  |
core/     numpy/pymatgen/spglib structure, images, bonds, canvas, document + history,
                                moments, layers, patterns, stacking, minimal magnetic cell
```

`tests/test_architecture.py` enforces the import rules:
- `core` imports nothing from `style`, `render`, `gui`, VTK, pyvista or Qt.
- `io` and `bridge` import no `style`, `render`, `gui`, VTK, pyvista or Qt.
- `style` imports no VTK and no Qt.
- `render` imports no Qt.

As a result, every algorithm can be tested headlessly, and every PNG artifact comes from the same drawing code the GUI uses.

## Data flow (Phase 0)

```
file --CrystalStructure.from_file--> CrystalStructure (input cell, frac in [0,1), spglib dataset)
     --boundary_images--> DisplayAtoms (parent index + integer shift per displayed atom)
     --find_bonds(VESTA rules)--> BondSet (adds boundary-mode-1 neighbors, then bonds)
     --build_scene_model + Appearance--> SceneModel (numpy arrays only)
     --StructureScene--> pyvista actors --> QtInteractor (GUI) or offscreen PNG
```

## Invariants
- **Sites are indices into the input cell.** Every displayed atom carries its `parent` index and lattice `shift`. Selection and editing always act on the parent.
- **Moments** (from Phase 2) are Cartesian μB, one per site of the editing canvas (default 2×2×2 of the input cell). Images inherit their parent's moment.
- **Minimal-cell analysis** (Phase 5) works on the spglib primitive lattice, so centering translations count.
- **VESTA data** is read at runtime and never stored in the repository.

## Modules (all phases complete)

| Module | Phase | Role |
|---|---|---|
| `core/structure.py` ✓ | 0 | load CIF/POSCAR, reject disorder, spglib dataset (SG, Wyckoff, orbits, primitive lattice) |
| `core/images.py` ✓ | 0 | boundary images for any fractional box |
| `core/bonds.py` ✓ | 0 | VESTA SBOND semantics (boundary modes 0/1), covalent-radius fallback |
| `core/species.py` ✓ | 0 | element-symbol helper |
| `core/canvas.py` ✓ | 1 | editing canvas W × input cell; site ↔ (parent, R) |
| `core/interaction.py` ✓, `core/history.py` ✓, `core/document.py` ✓, `core/selection.py` ✓ | 1 | gesture state machine; undo/redo |
| `core/suggest.py` ✓ | 1 | magnetic-site suggestion (rule-based) |
| `core/magnetic.py` ✓, `core/batch.py` ✓, `core/project.py` ✓, `core/graph.py` ✓ | 2 | drag math, batch patterns, save/load, neighbour graphs + sign propagation |
| `core/lattice2d.py` ✓, `core/layers.py` ✓, `core/patterns.py` ✓ | 2–3 | 2D lattice classification + pattern library; layer decomposition; rule-based matcher |
| `core/stacking.py` ✓ | 4 | inter-layer bonds, FM-stacking reference (frustration/periodicity checks), stacking library + matcher |
| `core/magcell.py` ✓ | 5 | minimal magnetic supercell (primitive-lattice translations, HNF, LLL), explanation, spglib MSG cross-check |
| `style/vesta_ini.py` ✓, `style/elements.py` ✓, `style/theme.py` ✓, `style/appearance.py` ✓ | 0 | appearance |
| `render/model.py` ✓, `render/scene.py` ✓, `render/arrows.py` ✓, `render/camera.py` ✓, `render/offscreen.py` ✓ | 0 | drawing |
| `render/picking.py` ✓, `render/overlay.py` ✓ | 1 | pick → site, rubber-band projection |
| `render/layer_view.py` ✓ | 3 | top view of one layer: up/down discs, pinned rings, NN bonds, preview |
| `io/handoff.py` ✓ + `io/handoff.schema.json` ✓, `bridge/agentdft.py` ✓ | 5 | handoff JSON (schema + semantic validation, pattern labels, VASP hints); AgentDFT interface + mock |
| `gui/main_window.py` ✓, `gui/app.py` ✓ | 0 | shell: main view, reference view, open, view buttons |
| `gui/viewport.py` ✓, `gui/panels/{suggestion,moment,layer,export}_panel.py` ✓, `gui/selftest.py` ✓ | 1–5 | interaction wiring, side panels |

## Interaction path (Phase 1)

```
Qt mouse event -> QVTKRenderWindowInteractor -> EditorInteractorStyle (VTK observers)
   -> pick (analytic ray-sphere) -> core.interaction.InteractionMachine.handle(event)
   -> actions: CameraPress/Release (forwarded to the trackball style), Select/Clear,
      BoxArmed/Update/Finish (render.overlay + render.picking), Drag* (Phase 2)
```
The long-press timer is a Qt single-shot timer that injects `Tick` events. Tests inject the same events directly. `magview.gui.selftest` drives the whole path with QtTest under Xvfb.

## Pattern engine (Phase 2, reused by Phases 3–4)

A collinear pattern is a sign (+1 FM / −1 AFM) per nearest-neighbour bond. `core.graph.neighbor_graph` builds the periodic nearest-neighbour graph of any set of canvas sites; bonds to images, including a site's own image, are kept. `propagate` turns bond signs into site signs by BFS and lists every contradicting bond, so frustration and "canvas too small" are detected exactly. `core.lattice2d` classifies planar nets (square/rectangular, honeycomb, triangular) by coordination and bond-direction classes, and defines the canonical library as per-class signs.

## Layer editor and matcher (Phase 3)

```
magnetic canvas sites --decompose(axis)--> layers (per element, clustered along a/b/c)
   each layer: periodic in-plane NN graph -> lattice2d.classify -> library
clicks --doc.layer_click--> pinned spins (doc.pinned, undoable, saved)
pinned spins --patterns.match_layer--> idle | no_match | matches(candidates)  (never auto-applied)
candidate --doc.apply_completion--> whole layer set (one undo step)
```

**Gotcha:** pyvista remembers its own interactor style and re-applies it on some calls (`iren.update_style()`). The editor style must be registered with `plotter.iren.style = ...`; setting it directly on the VTK interactor got silently reverted after the first layer redraw.

## Rendering notes
- **Atoms:** one glyph actor per opacity level, with sphere radius = VESTA atomic radius × `ball_scale`.
- **Bonds:** VTK tubes over line segments. Bicolor bonds are split at the midpoint.
- **Lighting:** a single headlight with ambient 0.10, diffuse 0.70, specular 0.80 and power 25.6, matching VESTA's LIGHT0/ATOMM.
- **Arrows:** generated in numpy with a constant shaft radius, unlike VTK glyphs, which scale uniformly. The mesh topology depends only on the arrow count, so a drag can rewrite points in place.
- **Axes triad:** a separate overlay renderer whose camera copies the main camera's orientation at every render. It works offscreen, where VTK orientation widgets need an interactor.
- **Render on change:** both GUI views are `QtInteractor(auto_update=False)`. pyvistaqt's default re-renders 5 times per second forever (2-4 CPU cores on an idle window with software GL), so every scene mutation renders explicitly; `test_gui_renders_on_change_not_on_a_timer` catches a forgotten render (stale frame).
- **Offscreen:** with no `DISPLAY`, `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow` (OSMesa, llvmpipe). The GUI smoke test runs the real Qt GUI under Xvfb with Mesa GLX and captures the X screen, so both OpenGL viewports are included.
