"""Regenerate artifacts/phase3/: layer decompositions, matcher click sequences, truth table, GUI.

    .venv/bin/python scripts/phase3_artifacts.py [--no-gui]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
OUT = ROOT / "artifacts" / "phase3"
sys.path.insert(0, str(ROOT / "scripts"))
from render_gallery import contact_sheet, run_isolated  # noqa: E402

LAYER_COLORS = [(0.89, 0.10, 0.11), (0.22, 0.49, 0.72), (0.30, 0.69, 0.29), (0.60, 0.31, 0.64),
                (1.00, 0.50, 0.00), (0.65, 0.34, 0.16), (0.97, 0.51, 0.75), (0.40, 0.40, 0.40)]


def new_doc(name, matrix=(2, 2, 2)):
    from magview.core.document import MagDocument
    from magview.core.structure import CrystalStructure
    from magview.core.suggest import ElementRuleSuggester

    c = CrystalStructure.from_file(DATA / f"{name}.cif")
    d = MagDocument.new(c, matrix)
    d.accept_suggestions(list(ElementRuleSuggester().suggest(c).candidates))
    return d


def layers_of(doc, axis="c"):
    from magview.core.layers import decompose

    return decompose(doc.canvas.structure, np.nonzero(doc.canvas_magnetic)[0],
                     doc.crystal.lattice_matrix, axis)


def decomposition_render(name, appearance, items, axis="c"):
    """3D canvas: magnetic sites colored by layer, everything else faded."""
    from magview.render.model import build_scene_model, subcell_edges
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    doc = new_doc(name)
    dec = layers_of(doc, axis)
    model = build_scene_model(doc.canvas.structure, appearance, moment_view=True,
                              grid_edges=subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix))
    layer_of = {}
    for k, L in enumerate(dec.layers):
        for s in L.sites:
            layer_of[int(s)] = k
    for d, p in enumerate(model.atoms.parent):
        if int(p) in layer_of:
            model.atom_rgb[d] = LAYER_COLORS[layer_of[int(p)] % len(LAYER_COLORS)]
            model.atom_opacity[d] = 1.0
            model.atom_radius[d] *= 1.4
    plotter = make_plotter((760, 600))
    plotter.enable_anti_aliasing("ssaa")
    scene = StructureScene(plotter, appearance.theme)
    scene.set_model(model)
    lines = [f"{doc.crystal.formula}: {len(dec.layers)} magnetic layers along {axis} (canvas 2x2x2)"]
    lines += [f"  {L.label}" for L in dec.layers[:6]]
    plotter.add_text("\n".join(lines), position="upper_left", font_size=9, color="black")
    path = OUT / f"layers_{name}.png"
    plotter.render()
    plotter.screenshot(str(path))
    plotter.close()
    print("wrote", path.relative_to(ROOT))
    items.append((path, f"{doc.crystal.formula}: layer decomposition along {axis}"))


def layer_frame(doc, L, title, path, appearance, preview=None):
    from magview.core.patterns import match_layer
    from magview.render.layer_view import LayerScene
    from magview.render.offscreen import make_plotter

    signs = doc.spin_sign(L.sites)
    pinned = doc.pinned[L.sites]
    pins = {int(i): int(signs[i]) for i in np.nonzero(pinned)[0] if signs[i] != 0}
    res = match_layer(L, pins)
    plotter = make_plotter((620, 640))
    plotter.enable_anti_aliasing("ssaa")
    scene = LayerScene(plotter, appearance.theme)
    scene.show(doc.canvas.structure, L, "c", signs, pinned, preview=preview)
    plotter.camera.parallel_scale *= 1.25  # room for the caption
    status = {"idle": "Waiting", "no_match": "No match", "matches": "Suggestions"}[res.status]
    text = [title, f"{status}: {res.reason}"] + [f"  - {c.pattern.label}" for c in res.candidates]
    plotter.add_text("\n".join(text), position="upper_left", font_size=11, color="black")
    plotter.render()
    plotter.screenshot(str(path))
    plotter.close()
    print("wrote", path.relative_to(ROOT))
    return res


def bond_pair(L, cls):
    k = int(np.nonzero(L.lattice.bond_class == cls)[0][0])
    return int(L.graph.i[k]), int(L.graph.j[k])


def click_sequences(appearance, items) -> None:
    tiles = []
    # Square net (CuO2): the user's example, then antiparallel, then apply Neel.
    doc = new_doc("toy_square_CuO2", (2, 2, 1))
    L = layers_of(doc).layers[0]
    doc.clear_layer(L.sites)
    i, j = bond_pair(L, 0)
    doc.layer_click(L.sites[i])
    p = OUT / "seq_square_1.png"
    layer_frame(doc, L, "Square net: 1 click", p, appearance)
    tiles.append((p, "square: 1 pinned -> waiting"))
    doc.layer_click(L.sites[j])
    p = OUT / "seq_square_2.png"
    res = layer_frame(doc, L, "Square net: 2 parallel neighbours", p, appearance,
                      preview=None)
    tiles.append((p, "2 parallel neighbours -> FM / stripe"))
    stripe = [c for c in res.candidates if c.pattern.name == "stripe"][0]
    p = OUT / "seq_square_3.png"
    layer_frame(doc, L, f"Hover preview: {stripe.pattern.label}", p, appearance, preview=stripe.signs)
    tiles.append((p, "hover preview of the stripe"))
    doc.layer_click(L.sites[j])  # -> down
    p = OUT / "seq_square_4.png"
    res = layer_frame(doc, L, "Square net: antiparallel neighbours", p, appearance)
    tiles.append((p, "antiparallel -> Neel / other stripe"))
    neel = [c for c in res.candidates if c.pattern.key == "neel"][0]
    doc.apply_completion(L.sites, neel.signs, "Neel")
    p = OUT / "seq_square_5.png"
    layer_frame(doc, L, "Applied: Neel (checkerboard)", p, appearance)
    tiles.append((p, "applied Neel (one undo step)"))

    # Honeycomb: 2 parallel neighbours -> 4 candidates; a third pin narrows to zigzag.
    doc = new_doc("toy_honeycomb_Cr", (2, 2, 1))
    L = layers_of(doc).layers[0]
    doc.clear_layer(L.sites)
    i, j = bond_pair(L, 0)
    doc.layer_click(L.sites[i])
    doc.layer_click(L.sites[j])
    p = OUT / "seq_honeycomb_1.png"
    layer_frame(doc, L, "Honeycomb: 2 parallel neighbours", p, appearance)
    tiles.append((p, "honeycomb: 4 candidates"))
    # third pin: neighbour of j along class 1, antiparallel -> zigzag with class-1 bonds AFM
    g = L.graph
    k = [n for n in range(g.m) if L.lattice.bond_class[n] == 1 and j in (g.i[n], g.j[n])][0]
    third = int(g.j[k] if g.i[k] == j else g.i[k])
    doc.layer_click(L.sites[third])
    doc.layer_click(L.sites[third])  # up -> down
    p = OUT / "seq_honeycomb_2.png"
    res = layer_frame(doc, L, "Honeycomb: third pin, antiparallel", p, appearance)
    tiles.append((p, f"3 pins -> {len(res.candidates)} candidates left"))
    # fourth pin: neighbour of i along class 2, parallel -> fixes the last bond class
    k = [n for n in range(g.m) if L.lattice.bond_class[n] == 2 and i in (g.i[n], g.j[n])][0]
    fourth = int(g.j[k] if g.i[k] == i else g.i[k])
    doc.layer_click(L.sites[fourth])
    p = OUT / "seq_honeycomb_2b.png"
    res = layer_frame(doc, L, "Honeycomb: fourth pin, parallel", p, appearance)
    assert len(res.candidates) == 1, [c.pattern.key for c in res.candidates]
    tiles.append((p, f"4 pins -> unique: {res.candidates[0].pattern.name}"))
    doc.apply_completion(L.sites, res.candidates[0].signs, "zigzag")
    p = OUT / "seq_honeycomb_3.png"
    layer_frame(doc, L, f"Applied: {res.candidates[0].pattern.label}", p, appearance)
    tiles.append((p, "applied zigzag"))

    # Negative control: Cs3V9Te13 V layer.
    doc = new_doc("Cs3V9Te13_300K", (2, 2, 1))
    L = layers_of(doc).layers[0]
    doc.clear_layer(L.sites)
    for s in L.sites[:3]:
        doc.layer_click(s)
    p = OUT / "seq_cs3v9te13_negative.png"
    layer_frame(doc, L, "Cs3V9Te13 V layer: 3 pins", p, appearance)
    tiles.append((p, "Cs3V9Te13: matcher silent (negative control)"))

    sheet = OUT / "matcher_sequences.png"
    contact_sheet(tiles, sheet, cols=3, cell_w=620)
    for t, _ in tiles:
        t.unlink()
    print("wrote", sheet.relative_to(ROOT))
    items.append((sheet, "Click sequences -> rule-based suggestions"))


def truth_table() -> None:
    from magview.core.patterns import match_layer

    lines = ["# Phase 3: matcher truth table", "",
             "Generated by `scripts/phase3_artifacts.py` from the real matcher. Spins: `+` up, "
             "`-` down. 'NN' means nearest neighbours; the class is the lattice direction of the bond.",
             "", "The exhaustive soundness and completeness check is `tests/test_layers_matcher.py::"
             "test_truth_table`. For every pattern, both global signs and every 2- and 3-site pin "
             "set, the candidates equal a brute-force check over the library.", ""]
    for name, kind in (("toy_square_CuO2", "square"), ("toy_honeycomb_Cr", "honeycomb"),
                       ("toy_triangular_Co", "triangular"), ("Cs3V9Te13_300K", "V trimer net")):
        doc = new_doc(name, (2, 2, 1))
        L = layers_of(doc).layers[0]
        lines += [f"## {kind} ({doc.crystal.formula}): {L.lattice.reason}", "",
                  "| Pins | Status | Candidates |", "|---|---|---|"]
        scen = []
        if L.lattice.supported:
            for cls in range(len(L.lattice.classes)):
                i, j = bond_pair(L, cls)
                lab = L.lattice.classes[cls].label
                scen.append((f"NN along {lab}: + +", {i: 1, j: 1}))
                scen.append((f"NN along {lab}: + -", {i: 1, j: -1}))
            scen.append(("one site only", {0: 1}))
        else:
            scen = [("3 sites + - +", {0: 1, 1: -1, 2: 1}), ("2 sites + +", {0: 1, 1: 1})]
        for title, pins in scen:
            res = match_layer(L, pins)
            cands = ", ".join(c.pattern.label for c in res.candidates) or f"none ({res.reason})"
            lines.append(f"| {title} | {res.status} | {cands} |")
        lines.append("")
    path = OUT / "matcher_truth_table.md"
    path.write_text("\n".join(lines))
    print("wrote", path.relative_to(ROOT))


def gui_selftests(items) -> bool:
    if shutil.which("xvfb-run") is None:
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
        # keep only the Phase 3 screenshots here
        for png in out.glob("*.png"):
            if not png.name.startswith(("09_", "10_", "11_")):
                png.unlink()
        for png in sorted(out.glob("*.png")):
            items.append((png, f"GUI {png.stem} - {name.split('_')[0]}"))
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
    for name in ("La2CuO4", "NiO", "Cs3V9Te13_300K"):
        decomposition_render(name, appearance, items)
    click_sequences(appearance, items)
    truth_table()
    ok = True if args.no_gui else gui_selftests(items)
    contact_sheet(items, OUT / "contact_sheet.png", cols=3, cell_w=620)
    print("wrote", (OUT / "contact_sheet.png").relative_to(ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
