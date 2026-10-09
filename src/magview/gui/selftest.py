"""Scripted end-to-end GUI test: real Qt events through the real window.

    xvfb-run -a python -m magview.gui.selftest STRUCTURE.cif --out DIR

Sends synthetic mouse/keyboard events (QtTest) to the main viewport and checks
the outcome after each step: click select, Shift-click add, camera drag
(no selection change), long-press rubber band, Esc, accepting suggestions,
undo/redo, canvas resize. Screenshots of the X screen are saved per step (they
include the OpenGL viewports). Exit status 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase1-only", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import os

    os.environ.setdefault("MAGVIEW_AUTOSAVE_DIR", str(out / "autosave"))
    os.environ.setdefault("MAGVIEW_AGENTDFT_DIR", str(out / "agentdft_jobs"))

    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from magview.gui.main_window import MainWindow
    from magview.render.picking import pick_atom, project_to_display

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()

    # Watchdog: an unexpected modal dialog would block forever under Xvfb.
    from PySide6.QtCore import QTimer

    def close_modals():
        modal = app.activeModalWidget()
        if modal is not None:
            results.append({"step": "no unexpected modal dialog", "ok": False,
                            "detail": f"closed: {modal.windowTitle()!r}"})
            print(f"[FAIL] unexpected modal dialog {modal.windowTitle()!r}")
            modal.reject() if hasattr(modal, "reject") else modal.close()

    watchdog = QTimer()
    watchdog.timeout.connect(close_modals)
    watchdog.start(1500)
    results: list[dict] = []
    win.show()
    # Xvfb has no window manager: activate explicitly so window shortcuts fire.
    win.activateWindow()
    win.raise_()
    QTest.qWaitForWindowActive(win, 3000)
    QTest.qWait(200)
    assert win.load_file(args.file)
    QTest.qWait(500)

    widget = win.viewport  # QtInteractor is the QWidget receiving the events
    ren = win.viewport.renderer

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append({"step": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    def shot(name: str) -> None:
        app.processEvents()
        win.viewport.render()
        win.reference.render()
        app.processEvents()
        g = win.frameGeometry()
        win.screen().grabWindow(0, g.x(), g.y(), g.width(), g.height()).save(str(out / f"{name}.png"))

    def qpt(x: float, y: float) -> QPoint:
        return QPoint(int(round(x)), int(round(widget.height() - 1 - y)))

    model = win.scene.model
    xy = project_to_display(ren, model.atoms.cart)
    w, h = ren.GetSize()

    def clickable_sites(n: int) -> list[tuple[int, float, float]]:
        """Displayed atoms whose own center pixel picks them (front-most), as (site, x, y)."""
        found, seen = [], set()
        for i in np.argsort(np.linalg.norm(xy - [w / 2, h / 2], axis=1)):
            x, y = xy[i]
            if not (20 < x < w - 20 and 20 < y < h - 20):
                continue
            if pick_atom(ren, model, x, y) == i and model.atoms.parent[i] not in seen:
                seen.add(int(model.atoms.parent[i]))
                found.append((int(model.atoms.parent[i]), float(x), float(y)))
            if len(found) == n:
                break
        return found

    (s1, x1, y1), (s2, x2, y2) = clickable_sites(2)

    # 1. Click selects exactly the clicked site (an image atom selects its parent).
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, qpt(x1, y1))
    QTest.qWait(50)
    check("click selects one site", win.selection == {s1}, f"site {s1}")
    shot("01_click")

    # 2. Shift-click adds.
    QTest.mouseClick(widget, Qt.LeftButton, Qt.ShiftModifier, qpt(x2, y2))
    QTest.qWait(50)
    check("shift-click adds", win.selection == {s1, s2}, f"{sorted(win.selection)}")

    # 3. Quick drag on empty space rotates the camera and keeps the selection.
    cam0 = np.array(win.viewport.camera.position)
    ex, ey = 30.0, h / 2
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, qpt(ex, ey))
    for k in range(1, 11):
        QTest.mouseMove(widget, qpt(ex + 12 * k, ey + 3 * k))
        QTest.qWait(5)
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, qpt(ex + 120, ey + 30))
    QTest.qWait(50)
    moved = np.linalg.norm(np.array(win.viewport.camera.position) - cam0)
    check("drag rotates camera", moved > 1e-3 and win.selection == {s1, s2}, f"camera moved {moved:.3f}")
    win.set_view("oblique")
    QTest.qWait(100)
    xy = project_to_display(ren, model.atoms.cart)

    # 4. Long press + drag = rubber band (replace selection).
    bx0, by0, bx1, by1 = w * 0.30, h * 0.30, w * 0.62, h * 0.66
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, qpt(bx0, by0))
    QTest.qWait(500)  # long press arms the box
    armed = win.editor.machine.state.name == "BOX"
    for k in range(1, 9):
        QTest.mouseMove(widget, qpt(bx0 + (bx1 - bx0) * k / 8, by0 + (by1 - by0) * k / 8))
        QTest.qWait(5)
    shot("02_box_dragging")
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, qpt(bx1, by1))
    QTest.qWait(50)
    expected = set(win.editor.sites_in_box(bx0, by0, bx1, by1))
    check("long-press arms box", armed)
    check("box selects sites inside", len(expected) > 2 and win.selection == expected,
          f"{len(win.selection)} selected, {len(expected)} expected")
    shot("03_box_result")

    # 5. Esc clears the selection (View mode, no gesture active).
    QTest.keyClick(win, Qt.Key_Escape)
    QTest.qWait(50)
    check("Esc clears selection", win.selection == frozenset())

    # 6. Accept suggestions -> moments; undo/redo restore exactly.
    doc = win.doc
    before = doc.moments.copy()
    first = win.suggestions.report.candidates[0]
    win.suggestions._spins[first.orbit].setValue(1.7)  # edited magnitude must be honored
    win.suggestions.accept_btn.click()
    QTest.qWait(100)
    after = doc.moments.copy()
    mags = np.linalg.norm(after, axis=1)[np.isin(doc.canvas.parent, first.sites)]
    check("accept sets edited moments", len(mags) > 0 and np.allclose(mags, 1.7),
          f"{first.label}: |m| = {sorted(set(np.round(mags, 3)))}")
    win.moment_view_act.setChecked(True)
    QTest.qWait(100)
    shot("04_accepted_moment_view")
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
    QTest.qWait(50)
    check("Ctrl+Z undoes accept", np.allclose(win.doc.moments, before))
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier)
    QTest.qWait(50)
    check("Ctrl+Y redoes accept", np.allclose(win.doc.moments, after))

    # 7. Canvas resize keeps a FM configuration and is undoable.
    n0 = len(win.doc.canvas)
    ok = win.resize_canvas((1, 1, 1))
    QTest.qWait(50)
    check("canvas 2x2x2 -> 1x1x1", ok and len(win.doc.canvas) == n0 // 8, f"{len(win.doc.canvas)} sites")
    win.undo()
    QTest.qWait(50)
    check("undo canvas resize", len(win.doc.canvas) == n0)

    # 8. Select all magnetic sites + equivalent-site expansion.
    QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier)
    QTest.qWait(50)
    check("Ctrl+A selects magnetic sites", win.selection == set(np.nonzero(win.doc.canvas_magnetic)[0]),
          f"{len(win.selection)} sites")
    shot("05_select_all_magnetic")

    if not args.phase1_only:
        phase2_checks(win, widget, check, shot, qpt)
        phase3_checks(win, widget, check, shot, qpt)
        phase4_checks(win, widget, check, shot, qpt)
        phase5_checks(win, widget, check, shot, qpt, out)

    (out / "selftest.json").write_text(json.dumps(results, indent=2))
    win.close()
    return 0 if all(r["ok"] for r in results) else 1


def phase2_checks(win, widget, check, shot, qpt) -> None:
    """Moment editing: mode, drags (collinear, axis lock, selection-wide, Esc), keys,
    patterns, session save/load."""
    import tempfile

    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from magview.core import interaction as ia
    from magview.core import magnetic as mg
    from magview.core import project
    from magview.render.picking import pick_atom, project_to_display

    doc = win.doc
    ren = win.viewport.renderer
    win.set_selection(set())

    # 9. M enters Moment mode: banner, moment view on.
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key_M)
    QTest.qWait(80)
    check("M enters moment mode", win.editor.machine.mode == ia.Mode.MOMENT and win.moment_view
          and win.moment_mode_act.isChecked())

    def magnetic_target():
        model = win.scene.model
        xy = project_to_display(ren, model.atoms.cart)
        w, h = ren.GetSize()
        mag = doc.canvas_magnetic[model.atoms.parent]
        best = None
        for i in np.argsort(np.linalg.norm(xy - [w / 2, h / 2], axis=1)):
            x, y = xy[i]
            if mag[i] and 120 < x < w - 120 and 120 < y < h - 120 and pick_atom(ren, model, x, y) == i:
                best = (int(i), int(model.atoms.parent[i]), float(x), float(y))
                break
        return best

    def drag(x0, y0, x1, y1, steps=10, hold=None, esc=False, shot_name=None):
        QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, qpt(x0, y0))
        for k in range(1, steps + 1):
            QTest.mouseMove(widget, qpt(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps))
            QTest.qWait(5)
            if k == 2 and hold is not None:
                QTest.keyPress(widget, hold)
        if shot_name:
            shot(shot_name)
        if esc:
            QTest.keyClick(win, Qt.Key_Escape)
            QTest.qWait(20)
        QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, qpt(x1, y1))
        if hold is not None:
            QTest.keyRelease(widget, hold)
        QTest.qWait(60)

    def expected(site_disp, x0, y0, x1, y1, lock=None, axis=None):
        cam = win.viewport.camera
        view_dir = mg.unit(np.array(cam.focal_point) - np.array(cam.position))
        up = np.array(cam.up, float)
        up = mg.unit(up - (up @ view_dir) * view_dir)
        frame = mg.DragFrame(win.scene.model.atoms.cart[site_disp], view_dir, up,
                             win.scene.arrow_style.scale, doc.canvas.structure.lattice_matrix,
                             centered=win.scene.arrow_style.centered)
        from magview.render.picking import display_ray

        # Qt delivers integer pixels: evaluate at the pixel actually sent.
        o, d = display_ray(ren, round(x1), round(y1))
        return mg.drag_moment(frame, mg.plane_point(frame, o, d), lock=lock, collinear_axis=axis)

    disp, site, x, y = magnetic_target()
    n_hist = len(doc.history._undo)

    # 10. Collinear drag up -> +spin axis; exactly one undo step; only that site changes.
    before = doc.moments.copy()
    drag(x, y, x, y + 70, shot_name="06_drag_live")
    exp = expected(disp, x, y, x, y + 70, axis=doc.spin_axis)
    changed = np.nonzero(np.linalg.norm(doc.moments - before, axis=1) > 1e-9)[0]
    check("collinear drag up sets +axis moment",
          np.allclose(doc.moments[site], exp.moment, atol=1e-6) and exp.moment @ doc.spin_axis > 0.5
          and list(changed) == [site], f"m = {np.round(doc.moments[site], 3).tolist()}")
    check("drag is one undo step", len(doc.history._undo) == n_hist + 1
          and doc.history.undo_label.startswith("Drag moment"))
    shot("07_after_drag")

    # 11. Drag down flips the sign.
    drag(x, y, x, y - 70)
    check("collinear drag down sets -axis moment", doc.moments[site] @ doc.spin_axis < -0.5,
          f"m = {np.round(doc.moments[site], 3).tolist()}")

    # 12. Esc during a drag cancels: no change, no history entry.
    before, n_hist = doc.moments.copy(), len(doc.history._undo)
    drag(x, y, x, y + 90, esc=True)
    check("Esc cancels a drag", np.allclose(doc.moments, before) and len(doc.history._undo) == n_hist)

    # 13. Tiny drag (below 0.2 muB dead zone) changes nothing.
    before = doc.moments.copy()
    win.moments_panel.scale.setValue(5.0)  # 5 A per muB: a 6 px drag is far below 0.2 muB
    QTest.qWait(30)
    drag(x, y, x + 1, y + 6, steps=3)
    check("dead zone: tiny drag changes nothing", np.allclose(doc.moments, before))
    win.moments_panel.scale.setValue(1.0)
    QTest.qWait(30)

    # 14. Non-collinear model + held X lock -> moment along +x for a rightward drag.
    win.moments_panel.collinear.setChecked(False)
    QTest.qWait(30)
    disp, site, x, y = magnetic_target()
    drag(x, y, x + 80, y + 10, hold=Qt.Key_X)
    m = doc.moments[site]
    check("non-collinear drag with X lock", not doc.collinear and abs(m[0]) > 0.3
          and abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9, f"m = {np.round(m, 3).tolist()}")
    # free drag gives a general in-plane vector
    drag(x, y, x + 50, y + 50)
    exp = expected(disp, x, y, x + 50, y + 50)
    check("free non-collinear drag follows the cursor",
          np.allclose(doc.moments[site], exp.moment, atol=1e-6), f"|m| = {exp.magnitude:.2f}")
    # Back to collinear: project explicitly (the checkbox would ask in a dialog).
    win.doc.set_collinear(True, project=True)
    QTest.qWait(30)
    check("projected back to collinear", doc.collinear and win.moments_panel.collinear.isChecked())

    # 15. F flips the selection; 0 zeroes it (keys on the viewport).
    win.set_selection({site})
    m0 = doc.moments[site].copy()
    QTest.keyClick(widget, Qt.Key_F)
    QTest.qWait(30)
    check("F flips", np.allclose(doc.moments[site], -m0))
    QTest.keyClick(widget, Qt.Key_0)
    QTest.qWait(30)
    check("0 zeroes", np.allclose(doc.moments[site], 0))
    win.undo()
    win.undo()

    # 16. Selection-wide drag: every selected site gets the same moment.
    QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier)
    QTest.qWait(30)
    sel = sorted(win.selection)
    disp, site, x, y = magnetic_target()
    drag(x, y, x, y + 60)
    same = np.allclose(doc.moments[sel], doc.moments[site])
    check("drag on a selected atom sets the whole selection", same and len(sel) > 1
          and np.linalg.norm(doc.moments[site]) > 0.2, f"{len(sel)} sites")

    # 17. Patterns: menu lists options; applying A-type along c alternates by layer.
    menu = win.build_patterns_menu()
    labels = [a.text() for a in menu.actions() if a.data()]
    check("patterns menu built", any(t.startswith("A-type") for t in labels), f"{len(labels)} entries")
    ok = win.apply_pattern("A:c")
    z = doc.canvas.structure.frac_coords[sel, 2]
    s = np.sign(doc.moments[sel] @ doc.spin_axis)
    layers = np.round(z * 4).astype(int)  # NiO 2x2x2: Ni layers every c/2 -> 4 per canvas
    alternating = all(len(set(s[layers == L])) == 1 for L in set(layers)) and len(set(s)) == 2
    check("apply pattern A-type along c", ok and alternating)
    shot("08_pattern_A_c")

    # 18. Session save/load round trip.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.magview.json"
        project.save(doc, path)
        doc2 = project.load(path)
        check("session save/load round trip", np.allclose(doc2.moments, doc.moments)
              and np.array_equal(doc2.canvas.matrix, doc.canvas.matrix)
              and np.array_equal(doc2.magnetic, doc.magnetic))
    auto = list((Path(os.environ["MAGVIEW_AUTOSAVE_DIR"])).glob("*.magview.json"))
    check("autosave written after edits", len(auto) == 1)

    # 19. Esc leaves Moment mode (no gesture active).
    QTest.keyClick(win, Qt.Key_Escape)
    QTest.qWait(50)
    check("Esc exits moment mode", win.editor.machine.mode == ia.Mode.VIEW
          and not win.moment_mode_act.isChecked())


def phase3_checks(win, widget, check, shot, qpt) -> None:
    """Layer editor: L mode, click-to-toggle, matcher suggestions, apply, undo, unset, PgDn."""
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from magview.core import interaction as ia
    from magview.render.picking import pick_atom, project_to_display

    doc = win.doc
    ren = win.viewport.renderer
    win.set_selection(set())
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key_L)
    QTest.qWait(100)
    check("L enters layer mode", win.in_layer_mode and win.layer_mode_act.isChecked()
          and win.layer_scene.model is not None, win.layer.label if win.layer else "")
    win.layer_panel.clear_btn.click()
    QTest.qWait(50)
    L = win.layer
    check("clear layer: nothing pinned", not doc.pinned[L.sites].any()
          and not doc.spin_sign(L.sites).any())

    def click_local(i, button=Qt.LeftButton):
        model = win.layer_scene.model
        xy = project_to_display(ren, model.atoms.cart)
        for d in np.nonzero(model.local == i)[0]:
            x, y = xy[d]
            if pick_atom(ren, model, round(x), round(y)) == d:
                QTest.mouseClick(widget, button, Qt.NoModifier, qpt(x, y))
                QTest.qWait(60)
                return True
        return False

    g = L.graph
    if L.lattice.supported:
        k = int(np.nonzero(L.lattice.bond_class == 0)[0][0])
        i, j = int(g.i[k]), int(g.j[k])
        cls0, cls1 = L.lattice.classes[0].label, L.lattice.classes[1].label
        ok = click_local(i) and click_local(j)
        keys = {c.pattern.key for c in win.layer_match.candidates}
        expect = {"fm", f"stripe:{cls0}"} if L.lattice.kind in ("square", "rectangular") else None
        check("two parallel neighbours -> FM + stripe candidates",
              ok and doc.pinned[L.sites].sum() == 2 and win.layer_match.status == "matches"
              and (expect is None or keys == expect), f"{sorted(keys)}")
        shot("09_layer_two_pins")
        click_local(j)  # up -> down
        keys = {c.pattern.key for c in win.layer_match.candidates}
        expect = {"neel", f"stripe:{cls1}"} if L.lattice.kind in ("square", "rectangular") else None
        check("antiparallel neighbours -> Neel + other stripe",
              win.layer_match.status == "matches" and (expect is None or keys == expect), f"{sorted(keys)}")
        neel = [n for n, c in enumerate(win.layer_match.candidates) if c.pattern.key == "neel"]
        # hover preview leaves the document untouched
        before = doc.moments.copy()
        win._preview_layer_candidate(neel[0])
        QTest.qWait(30)
        shot("10_layer_preview_neel")
        win._preview_layer_candidate(-1)
        check("hover preview does not edit", np.allclose(doc.moments, before))
        win.layer_panel.candidate_buttons[neel[0]].click()
        QTest.qWait(80)
        s = doc.spin_sign(L.sites)
        check("apply Neel completes the layer (all NN bonds antiparallel)",
              np.all(s != 0) and np.all(s[g.i] == -s[g.j]))
        shot("11_layer_neel_applied")
        QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
        QTest.qWait(60)
        check("undo completion", (doc.spin_sign(L.sites) != 0).sum() == 2)
        click_local(i, Qt.RightButton)
        check("right-click unsets a pin", doc.pinned[L.sites].sum() == 1
              and win.layer_match.status == "idle")
    else:
        ok = click_local(0) and click_local(1)
        check("negative control: manual clicks still pin spins", ok and doc.pinned[L.sites].sum() == 2)
        check("negative control: matcher stays silent",
              win.layer_match.status == "idle" and not win.layer_match.candidates
              and "no canonical patterns" in win.layer_match.reason, win.layer_match.reason)
        shot("09_layer_negative_control")
    n_layers = len(win.layers.layers)
    QTest.keyClick(widget, Qt.Key_PageDown)
    QTest.qWait(80)
    check("PgDn steps to the next layer", win.current_layer == (1 % n_layers), f"{n_layers} layers")
    QTest.keyClick(win, Qt.Key_Escape)
    QTest.qWait(80)
    check("Esc leaves layer mode, 3D scene back", win.editor.machine.mode == ia.Mode.VIEW
          and win.editor.scene is win.scene and win.scene.actors["atoms"].GetVisibility())


def phase4_checks(win, widget, check, shot, qpt) -> None:
    """Stacking: reference, idle state, suggestion from one flipped site, apply, per-layer flip."""
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from magview.core import interaction as ia
    from magview.render.picking import pick_atom, project_to_display

    doc = win.doc
    ren = win.viewport.renderer
    win.set_mode(ia.Mode.LAYER)
    QTest.qWait(80)
    win.select_layer(0)
    L0 = win.layer
    group = win.layers.by_element(L0.element)
    n = len(group)
    doc.apply_completion(L0.sites, np.ones(len(L0.sites), int), "test: layer 0 FM")
    for G in group[1:]:
        doc.clear_layer(G.sites)
    QTest.qWait(60)
    group, ref, match = win.stacking_state()
    check("stacking reference from layer 0", ref.ok, ref.reason)
    check("stacking idle until another layer is assigned", match.status == "idle", match.reason)

    win.select_layer(win.layers.layers.index(group[1]))
    QTest.qWait(60)
    model = win.layer_scene.model
    xy = project_to_display(ren, model.atoms.cart)
    d = next(d for d in range(len(model.atoms)) if pick_atom(ren, model, round(xy[d][0]), round(xy[d][1])) == d)
    for _ in range(2):  # unpinned -> up -> down
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, qpt(*xy[d]))
        QTest.qWait(80)
    group, ref, match = win.stacking_state()
    keys = {c.key for c in match.candidates}
    check("one flipped site in layer 1 suggests AFM stacking",
          match.relations[1] == -1 and "afm" in keys and "fm" not in keys,
          f"relations {match.relations} -> {sorted(keys)}")
    shot("12_stacking_suggestion")
    win.layer_panel.stack_buttons["afm"].click()
    QTest.qWait(80)
    group, ref, match = win.stacking_state()
    check("apply AFM stacking: layers alternate", list(match.relations) == [(-1) ** k for k in range(n)],
          f"{match.relations}")
    win.layer_panel.flip_btn.click()
    QTest.qWait(80)
    group, ref, match = win.stacking_state()
    check("flip this layer (custom)", match.relations[1] == 1, f"{match.relations}")
    if n == 2:
        check("two layers both same -> FM suggested", {c.key for c in match.candidates} == {"fm"})
    else:
        check("custom flips -> no canonical stacking", match.status == "no_match", match.reason)
    QTest.keyClick(win, Qt.Key_Escape)
    QTest.qWait(60)


def phase5_checks(win, widget, check, shot, qpt, out) -> None:
    """Minimal magnetic cell, preview, handoff save, mock AgentDFT submission."""
    import json

    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from magview.core import interaction as ia
    from magview.core.magcell import minimal_magnetic_cell
    from magview.io.handoff import validate_handoff

    doc = win.doc
    win.set_mode(ia.Mode.VIEW)
    QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier)
    QTest.qWait(40)
    check("A-type along c applied for export", win.apply_pattern("A:c"))
    QTest.qWait(60)
    win.export_panel.analyze_btn.click()
    QTest.qWait(150)
    cell = win.mag_cell
    ref = minimal_magnetic_cell(doc)
    check("analyze: minimal magnetic cell", cell is not None and cell.multiplicity == ref.multiplicity == 2
          and "REQUIRED" in win.export_panel.summary.text(), win.export_panel.summary.text())
    check("handoff valid", win.handoff is not None and validate_handoff(win.handoff) == []
          and win.handoff["requires_unfolding"] is True)
    shot("13_export_analysis")
    win.export_panel.preview.setChecked(True)
    QTest.qWait(150)
    n_parent = int(win.scene.model.atoms.parent.max()) + 1
    check("preview shows the magnetic cell", win.previewing and n_parent == len(cell.species),
          f"{n_parent} sites")
    shot("14_magnetic_cell_preview")
    win.export_panel.preview.setChecked(False)
    QTest.qWait(100)
    check("preview off restores the canvas", not win.previewing
          and int(win.scene.model.atoms.parent.max()) + 1 == len(doc.canvas))
    path = win.save_handoff_dialog(path=str(out / "handoff.json"))
    data = json.loads(path.read_text()) if path else {}
    check("handoff saved and re-validates", bool(path) and validate_handoff(data) == []
          and data["multiplicity"] == 2)
    ticket = win.submit_to_agentdft()
    job_dir = Path(ticket.path) if ticket else None
    check("mock AgentDFT: job stored, started (simulated)",
          ticket is not None and ticket.status == "started (simulated)"
          and (job_dir / "handoff.json").is_file() and (job_dir / "job.json").is_file(),
          ticket.job_id if ticket else "")
    check("bridge status round trip", ticket is not None
          and win.bridge.status(ticket.job_id).status == "started (simulated)")
    site = int(np.nonzero(doc.canvas_magnetic)[0][0])
    doc.flip([site])
    QTest.qWait(80)
    check("an edit invalidates the analysis", win.mag_cell is None
          and not win.export_panel.save_btn.isEnabled())


import os  # noqa: E402  (used by phase2_checks)

if __name__ == "__main__":
    sys.exit(main())
