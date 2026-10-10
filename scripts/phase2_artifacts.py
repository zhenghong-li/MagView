"""Regenerate artifacts/phase2/: drag frames, pattern gallery, style gallery, GUI self-tests.

    .venv/bin/python scripts/phase2_artifacts.py [--no-gui]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
OUT = ROOT / "artifacts" / "phase2"
sys.path.insert(0, str(ROOT / "scripts"))
from render_gallery import contact_sheet, run_isolated  # noqa: E402

MAGNETIC = {"Cu", "Cr", "Co", "Ni", "Fe", "V", "Mn"}


def new_doc(name_or_crystal, matrix=(2, 2, 2)):
    from magview.core.document import MagDocument
    from magview.core.structure import CrystalStructure
    from magview.core.suggest import ElementRuleSuggester

    c = name_or_crystal if isinstance(name_or_crystal, CrystalStructure) else \
        CrystalStructure.from_file(DATA / f"{name_or_crystal}.cif")
    doc = MagDocument.new(c, matrix)
    doc.accept_suggestions(list(ElementRuleSuggester().suggest(c).candidates))
    return doc


def magnetic_sites(doc, layer_z: float | None = None):
    sym = np.array(doc.crystal.symbols)[doc.canvas.parent]
    s = np.nonzero(np.isin(sym, list(MAGNETIC)))[0]
    if layer_z is not None:
        s = s[np.isclose(doc.canvas.structure.frac_coords[s, 2], layer_z)]
    return s


def apply(doc, sites, key):
    from magview.core.batch import pattern_options

    opts = {o.key: o for o in pattern_options(doc.canvas.structure, sites, doc.crystal.lattice_matrix)}
    opt = opts[key]
    assert opt.available, (key, opt.reason)
    doc.apply_signs(sites, opt.signs, opt.label)
    return opt


def auto_scale(doc) -> float:
    """Arrow scale so the longest arrow spans 80% of the shortest magnetic-magnetic distance."""
    from magview.core.graph import neighbor_graph

    mags = np.linalg.norm(doc.moments, axis=1)
    sites = np.nonzero(mags > 0)[0]
    g = neighbor_graph(doc.canvas.structure.lattice_matrix, doc.canvas.structure.frac_coords, sites)
    return round(0.8 * g.d_nn / mags.max(), 2) if g.d_nn > 0 else 1.0


def render_doc(doc, path, appearance, view="oblique", size=(700, 560), color_mode="sign",
               centered=True, scale=None, title=None, moment_view=True, tilt=0.0):
    from magview.render.arrows import ArrowStyle
    from magview.render.model import build_scene_model, subcell_edges
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    plotter = make_plotter(size)
    plotter.enable_anti_aliasing("ssaa")
    scene = StructureScene(plotter, appearance.theme)
    t = appearance.theme
    if scale == "auto":
        scale = auto_scale(doc)
    scene.arrow_style = ArrowStyle(scale or t.vector_scale, t.arrow_shaft_radius, t.arrow_head_radius_ratio,
                                   t.arrow_head_length_ratio, centered, t.arrow_resolution)
    scene.color_mode = color_mode
    scene.spin_axis = doc.spin_axis
    grid = subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix)
    model = build_scene_model(doc.canvas.structure, appearance, site_moments=doc.moments,
                              moment_view=moment_view, grid_edges=grid)
    scene.set_model(model)
    scene.set_view(view)
    if tilt:
        # Tilt a top view so moments along the view axis read as arrows, not dots.
        plotter.camera.Elevation(tilt)
        plotter.camera.OrthogonalizeViewUp()
        plotter.reset_camera()
        plotter.camera.parallel_scale *= 0.92
    if title:
        plotter.add_text(title, position="upper_left", font_size=11, color="black")
    plotter.render()
    plotter.screenshot(str(path))
    plotter.close()
    print("wrote", path.relative_to(ROOT))
    return path


def drag_sequence(appearance, items) -> None:
    """Offscreen replay of a drag: real picking + drag math + live readout overlay.

    CuO2 square net (sparse), non-collinear model, cursor moved on a circle around
    the dragged Cu atom: the arrow tip follows the cursor (ring marker).
    """
    from magview.core import magnetic as mg
    from magview.render.model import build_scene_model, subcell_edges
    from magview.render.offscreen import make_plotter
    from magview.render.overlay import BoxOverlay, CursorLabel
    from magview.render.picking import display_ray, pick_atom, project_to_display
    from magview.render.scene import StructureScene

    doc = new_doc("toy_square_CuO2", (2, 2, 1))
    doc.set_collinear(False)
    plotter = make_plotter((900, 700))
    plotter.enable_anti_aliasing("ssaa")
    scene = StructureScene(plotter, appearance.theme)
    model = build_scene_model(doc.canvas.structure, appearance, site_moments=doc.moments,
                              moment_view=True,
                              grid_edges=subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix))
    scene.set_model(model)
    ren = plotter.renderer
    xy = project_to_display(ren, model.atoms.cart)
    w, h = ren.GetSize()
    mag = doc.canvas_magnetic[model.atoms.parent]
    cand = [i for i in np.argsort(np.linalg.norm(xy - [w / 2, h / 2], axis=1))
            if mag[i] and pick_atom(ren, model, *xy[i]) == i]
    disp = cand[0]
    site = model.atoms.parent[disp]
    scene.set_selection(np.nonzero(model.atoms.parent == site)[0])
    x0, y0 = xy[disp]
    cam = plotter.camera
    view_dir = mg.unit(np.array(cam.focal_point) - np.array(cam.position))
    up = np.array(cam.up, float)
    up = mg.unit(up - (up @ view_dir) * view_dir)
    frame = mg.DragFrame(model.atoms.cart[disp], view_dir, up, scene.arrow_style.scale,
                         doc.canvas.structure.lattice_matrix, centered=scene.arrow_style.centered)
    label = CursorLabel(ren)
    marker = BoxOverlay(ren, color=(0.1, 0.1, 0.1))
    tiles = []
    radius = [10, 60, 95, 95, 95, 130]
    angles = [90, 90, 90, 20, -60, -150]
    for k, (r, a) in enumerate(zip(radius, angles)):
        x = x0 + r * np.cos(np.radians(a))
        y = y0 + r * np.sin(np.radians(a))
        o, d = display_ray(ren, x, y)
        res = mg.drag_moment(frame, mg.plane_point(frame, o, d))
        preview = doc.moments.copy()
        if not res.below_dead_zone:
            preview[site] = res.moment
        scene.update_moments(preview[model.atoms.parent])
        m = res.moment
        label.show(f"m = {res.magnitude:.2f} muB\nxyz ({m[0]:+.2f}, {m[1]:+.2f}, {m[2]:+.2f})"
                   + ("\n(below 0.2: no change)" if res.below_dead_zone else ""), x, y)
        marker.armed(x, y, radius=6)
        plotter.render()
        img = Image.fromarray(plotter.screenshot(return_img=True))
        cx, cy = int(x0), int(h - y0)
        tile = img.crop((cx - 260, cy - 230, cx + 300, cy + 210))
        p = OUT / f"_drag_{k}.png"
        tile.save(p)
        tiles.append((p, f"frame {k}: cursor = ring, arrow tip follows"))
    plotter.close()
    path = OUT / "drag_sequence.png"
    contact_sheet(tiles, path, cols=3, cell_w=560)
    for p, _ in tiles:
        p.unlink()
    print("wrote", path.relative_to(ROOT))
    items.append((path, "Live drag replay: arrow tip follows the cursor (ring)"))


def pattern_gallery(appearance, items) -> list[Path]:
    from pymatgen.core import Lattice, Structure

    from magview.core.structure import CrystalStructure

    paths = []
    cases = [("toy_square_CuO2", (2, 2, 1), 0.0, ["neel", "stripe:[100]", "stripe:[010]"], "c"),
             ("toy_honeycomb_Cr", (2, 2, 1), 0.0, ["neel", "zigzag:[210]", "stripe:[210]"], "c"),
             ("toy_triangular_Co", (2, 2, 1), 0.0, ["stripe:[100]"], "c"),
             ("FeSe", (2, 2, 1), 0.0, ["neel", "stripe:[110]"], "c")]
    for name, matrix, z, keys, view in cases:
        for key in keys:
            doc = new_doc(name, matrix)
            sites = magnetic_sites(doc, z)
            opt = apply(doc, sites, key)
            tag = key.replace(":", "_").replace("[", "").replace("]", "").replace("-", "m")
            path = OUT / f"pattern_{name}_{tag}.png"
            render_doc(doc, path, appearance, view=view, tilt=-35,
                       title=f"{doc.crystal.formula}: {opt.label}")
            items.append((path, f"{name}: {opt.label}"))
            paths.append(path)
    cubic = CrystalStructure.from_pymatgen(Structure(Lattice.cubic(4.0), ["Mn"], [[0, 0, 0]]))
    for key in ("G", "C:c", "A:c"):
        doc = new_doc(cubic)
        opt = apply(doc, magnetic_sites(doc), key)
        path = OUT / f"pattern_cubic_{key.replace(':', '_')}.png"
        render_doc(doc, path, appearance, title=f"simple cubic Mn: {opt.label}")
        items.append((path, f"simple cubic: {opt.label}"))
    doc = new_doc("NiO")
    opt = apply(doc, magnetic_sites(doc), "A:c")
    path = OUT / "pattern_NiO_A_c.png"
    render_doc(doc, path, appearance, title="NiO: A-type along c (AFM-I)")
    items.append((path, "NiO: A-type along c (AFM-I)"))
    # Non-collinear: 120-degree order on the triangular net (3x3 canvas), direction colors.
    doc = new_doc("toy_triangular_Co", (3, 3, 1))
    doc.set_collinear(False)
    s = magnetic_sites(doc, 0.0)
    t = doc.canvas.translation[s]
    phase = ((t[:, 0] - t[:, 1]) % 3) * 120.0
    vec = np.column_stack([np.cos(np.radians(phase)), np.sin(np.radians(phase)), np.zeros(len(s))]) * 2.0
    doc.set_moments(s, vec, "120 deg")
    path = OUT / "noncollinear_triangular_120deg.png"
    render_doc(doc, path, appearance, view="c", color_mode="direction",
               title="Non-collinear: 120 deg order (3x3 canvas), color = direction")
    items.append((path, "Non-collinear 120 deg (direction colors)"))
    return paths


def style_gallery(appearance, items) -> None:
    doc = new_doc("toy_honeycomb_Cr", (2, 2, 1))
    apply(doc, magnetic_sites(doc, 0.0), "zigzag:[210]")
    tiles = []
    for mode, centered, scale, title in (("sign", True, 1.0, "color: sign, centered"),
                                         ("sign", False, 1.0, "anchor: tail at atom"),
                                         ("direction", True, 1.0, "color: direction"),
                                         ("element", True, 1.0, "color: element"),
                                         ("uniform", True, 1.0, "color: uniform"),
                                         ("sign", True, 0.6, "scale 0.6 A/muB")):
        p = OUT / f"style_{mode}_{'c' if centered else 't'}_{scale}.png"
        render_doc(doc, p, appearance, view="oblique", size=(520, 420), color_mode=mode,
                   centered=centered, scale=scale, title=title)
        tiles.append(p)
    sheet = OUT / "style_gallery.png"
    contact_sheet([(p, p.stem) for p in tiles], sheet, cols=3, cell_w=520)
    for p in tiles:
        p.unlink()
    items.append((sheet, "Arrow style options (zigzag honeycomb)"))


def desaturation_fix(appearance, items) -> None:
    from dataclasses import replace

    from magview.style.appearance import Appearance

    doc = new_doc("Cs3V9Te13_300K", (1, 1, 1))
    before_app = Appearance(appearance.elements, replace(appearance.theme, moment_view_desaturate=0.0),
                            appearance.vesta_rules, appearance.vesta_dir, appearance.vesta_style)
    a = render_doc(doc, OUT / "cs3v9te13_before_fix.png", before_app,
                   title="Phase 1 moment view: V red = spin-up red")
    b = render_doc(doc, OUT / "cs3v9te13_after_fix.png", appearance,
                   title="Phase 2: atoms desaturated, arrows stand out")
    items.append((a, "Before: V atom and up-arrow merge"))
    items.append((b, "After: desaturated atoms in moment view"))


def gui_selftests(items) -> bool:
    if shutil.which("xvfb-run") is None:
        print("skip GUI self-tests: xvfb-run missing")
        return False
    ok = True
    for name in ("NiO", "Cs3V9Te13_300K"):
        out = OUT / f"gui_{name.split('_')[0].lower()}"
        if out.exists():
            shutil.rmtree(out)
        proc = run_isolated(["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, "-m",
                               "magview.gui.selftest", str(DATA / f"{name}.cif"), "--out", str(out)], timeout=600)
        print(proc.stdout.strip())
        shutil.rmtree(out / "autosave", ignore_errors=True)
        results = json.loads((out / "selftest.json").read_text())
        ok &= proc.returncode == 0 and all(r["ok"] for r in results)
        items.append((out / "06_drag_live.png", f"GUI live drag - {name.split('_')[0]}"))
        items.append((out / "08_pattern_A_c.png", f"GUI pattern A-type c - {name.split('_')[0]}"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gui", action="store_true")
    args = ap.parse_args()
    from magview.render.offscreen import configure_offscreen
    from magview.style.appearance import Appearance

    configure_offscreen()
    if OUT.exists():
        for p in OUT.glob("*.png"):
            p.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    appearance = Appearance.load()
    items: list = []
    drag_sequence(appearance, items)
    desaturation_fix(appearance, items)
    pattern_gallery(appearance, items)
    style_gallery(appearance, items)
    ok = True if args.no_gui else gui_selftests(items)
    contact_sheet(items, OUT / "contact_sheet.png", cols=4, cell_w=520)
    print("wrote", (OUT / "contact_sheet.png").relative_to(ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
