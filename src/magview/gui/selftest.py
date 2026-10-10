"""Scripted end-to-end GUI test: real Qt events through the real window.

    xvfb-run -a python -m magview.gui.selftest STRUCTURE.cif --out DIR
    QT_QPA_PLATFORM=offscreen python -m magview.gui.selftest ...  # no X server needed

Sends synthetic mouse/keyboard events (QtTest) to the main viewport and checks
the outcome after each step: click select, Shift-click add, camera drag
(no selection change), long-press rubber band, Esc, accepting suggestions,
undo/redo, canvas resize, moment drags, layer editing, stacking, export and
magnetic-cell preview, plus robustness regressions (phase6: read-only preview
edit guard, corrupt session files, camera preservation, focus-independent key
handling, mid-drag key swallowing, Shift-pan, autosave recovery, save errors).
Captures of MagView's widgets and OpenGL buffers are saved per step.
Exit status 0 means every check passed; 77 means the GUI cannot
start headlessly here (pytest treats it as a skip).
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
    import shutil

    # Hermetic run: autosaves, mock jobs and QSettings (window geometry/dock state,
    # arrow settings) live under --out, never in the developer's real directories or
    # an inherited shared directory (the autosave checks count files per run). They
    # start empty: a previous run into the same --out would otherwise leave an autosave
    # behind, and its recovery prompt would derail this run.
    for state in ("autosave", "agentdft_jobs", "xdg_config"):
        target = (out / state).resolve()
        if target.parent != out.resolve():
            raise ValueError(f"self-test state folder escapes output directory: {target}")
        shutil.rmtree(target, ignore_errors=True)
    os.environ["MAGVIEW_AUTOSAVE_DIR"] = str(out / "autosave")
    os.environ["MAGVIEW_AGENTDFT_DIR"] = str(out / "agentdft_jobs")
    os.environ["XDG_CONFIG_HOME"] = str(out / "xdg_config")
    os.environ["MAGVIEW_SETTINGS_DIR"] = str(out / "xdg_config")

    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from magview.gui.main_window import MainWindow
    from magview.gui.capture import capture_window
    from magview.render.picking import pick_atom, project_to_display

    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        win = MainWindow()
    except Exception as exc:
        # 77 = the GUI cannot start headlessly here (pytest treats it as a skip).
        print(f"[SKIP] cannot create the main window: {exc}")
        return 77

    # Watchdog: an unexpected modal dialog would block forever under Xvfb.
    # The expected modal busy indicator of threaded load/analyze ("busy_progress")
    # is left alone: closing it must not abort the work (it is purely visual).
    from PySide6.QtCore import QTimer

    def close_modals():
        modal = app.activeModalWidget()
        if modal is None or modal.objectName() == "busy_progress":
            return
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
    # Windows may refuse native foreground activation when launched by a
    # background terminal. Qt shortcuts still need an active window in the
    # test process, even though QtTest sends keys directly to its widgets.
    QApplication.setActiveWindow(win)
    # Probe: the QOpenGLWidget viewport cannot initialize GL on some platforms
    # (e.g. Qt's offscreen platform without GL support). Report as unavailable
    # (77) instead of crashing deeper in the checks.
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    if isinstance(win.viewport, QOpenGLWidget):
        app.processEvents()
        QTest.qWait(200)
        if not win.viewport.isValid():
            print("[SKIP] GL viewport cannot initialize on this platform")
            return 77
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
        capture_window(win).save(str(out / f"{name}.png"))
        QApplication.setActiveWindow(win)

    def qpt(x: float, y: float) -> QPoint:
        # VTK display coordinates are physical pixels; QtTest uses logical pixels.
        rw, rh = ren.GetSize()
        return QPoint(int(round(x * widget.width() / max(1, rw))),
                      int(round(widget.height() - 1 - y * widget.height() / max(1, rh))))

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
    actual_start = win.editor.style.GetInteractor().GetEventPosition()
    QTest.qWait(500)  # long press arms the box
    armed = win.editor.machine.state.name == "BOX"
    for k in range(1, 9):
        QTest.mouseMove(widget, qpt(bx0 + (bx1 - bx0) * k / 8, by0 + (by1 - by0) * k / 8))
        QTest.qWait(5)
    shot("02_box_dragging")
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, qpt(bx1, by1))
    actual_end = win.editor.style.GetInteractor().GetEventPosition()
    QTest.qWait(50)
    expected = set(win.editor.sites_in_box(*actual_start, *actual_end))
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
        phase6_checks(win, widget, check, shot, qpt, out, app, watchdog, args.file)

    (out / "selftest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
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

        # Qt rounds to logical pixels, then VTK scales to physical pixels. At
        # high DPI the delivered endpoint can differ from round(x1), round(y1).
        actual_x, actual_y = win.editor.style.GetInteractor().GetEventPosition()
        o, d = display_ray(ren, actual_x, actual_y)
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
    auto_dir = Path(os.environ["MAGVIEW_AUTOSAVE_DIR"])
    win._flush_autosave()  # autosave is debounced; flush before checking the file
    auto = list(auto_dir.glob("*.magview.json"))
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


def phase6_checks(win, widget, check, shot, qpt, out, app, watchdog, src) -> None:
    """Robustness regressions: read-only preview edit guard, corrupt session files,
    camera preservation, panel-focus keys, mid-drag keys, Esc in fields, Shift-pan,
    view buttons in layer mode, mode chip, autosave debounce + recovery offer."""
    import numpy as np
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox

    from magview.core import interaction as ia
    from magview.render.picking import pick_atom, project_to_display

    ren = win.viewport.renderer
    win.set_mode(ia.Mode.VIEW)
    win.set_selection(set())
    QTest.qWait(60)
    doc = win.doc

    def auto_answer(accept=True):
        """Poll for a modal QMessageBox and click it away (Yes/Ok, or No)."""
        state = {"answered": False}
        timer = QTimer()

        def poll():
            m = app.activeModalWidget()
            if isinstance(m, QMessageBox) and not state["answered"]:
                buttons = (QMessageBox.Yes, QMessageBox.Ok) if accept else (QMessageBox.No,)
                for sb in buttons:
                    btn = m.button(sb)
                    if btn is not None:
                        state["answered"] = True
                        btn.click()
                        return

        timer.timeout.connect(poll)
        timer.start(80)
        return timer, state

    def magnetic_target():
        model = win.scene.model
        xy = project_to_display(ren, model.atoms.cart)
        w, h = ren.GetSize()
        mag = win.doc.canvas_magnetic[model.atoms.parent]
        for i in np.argsort(np.linalg.norm(xy - [w / 2, h / 2], axis=1)):
            x, y = xy[i]
            if mag[i] and 120 < x < w - 120 and 120 < y < h - 120 and pick_atom(ren, model, x, y) == i:
                return int(i), int(model.atoms.parent[i]), float(x), float(y)
        return None

    def empty_point():
        model = win.scene.model
        w, h = ren.GetSize()
        for x in np.linspace(15, w - 15, 24):
            for y in np.linspace(15, h - 15, 18):
                if pick_atom(ren, model, x, y) is None:
                    return float(x), float(y)
        return None

    def camera():
        cam = win.viewport.camera
        return np.array(cam.position), np.array(cam.focal_point), float(cam.parallel_scale)

    # 20. The read-only magnetic-cell preview blocks every edit/select/info path.
    win.set_selection({0})
    cam_pre = camera()
    check("analyze for preview", win.analyze_magnetic_cell())
    win.export_panel.preview.setChecked(True)
    QTest.qWait(100)
    before, n_hist = doc.moments.copy(), len(doc.history._undo)
    win.flip_selection()
    win.zero_selection()
    win.select_all_magnetic()
    win.select_equivalent()
    win._on_select([0], ia.SelectOp.REPLACE, click=True)
    win._on_clear()
    win._on_info(0)
    win.show_patterns_menu()
    win._ask_magnitude()
    win._ask_direction()
    win.resize_canvas((1, 1, 1))
    win._accept_suggestions(win.suggestions.report.candidates[:1], {})
    win._context_menu(0)
    QTest.qWait(50)
    check("read-only preview blocks edits, selection, info and menus",
          np.array_equal(doc.moments, before) and len(doc.history._undo) == n_hist
          and win.selection == {0} and app.activePopupWidget() is None
          and win._build_context_menu(0) is None
          and "read-only" in win.statusBar().currentMessage())
    # Undo/redo stay functional during the preview: they edit the document (never
    # wrong sites) and exit the preview cleanly, restoring the pre-preview camera.
    win.undo()
    QTest.qWait(80)
    pos, foc, scale = camera()
    check("undo during preview exits it and restores the camera",
          not win.previewing and not win.editor.mode_overlay.banner.GetVisibility()
          and int(win.scene.model.atoms.parent.max()) + 1 == len(doc.canvas)
          and np.allclose(pos, cam_pre[0]) and np.allclose(foc, cam_pre[1])
          and abs(scale - cam_pre[2]) < 1e-6)
    win.redo()  # restore the moments the checks below expect
    QTest.qWait(60)
    win.export_panel.preview.setChecked(False)
    QTest.qWait(100)
    pos, foc, scale = camera()
    check("unticking the preview restores the user's camera",
          np.allclose(pos, cam_pre[0]) and np.allclose(foc, cam_pre[1])
          and abs(scale - cam_pre[2]) < 1e-6)

    # 20b. Moment-only edits take the scene's in-place fast path; a fast-path
    # failure falls back to a full rebuild (StructureScene.set_moments contract).
    mag_site = int(np.nonzero(doc.canvas_magnetic)[0][0])
    model_id = id(win.scene.model)
    doc.flip([mag_site])
    QTest.qWait(30)
    check("moment-only edit keeps the scene (in-place set_moments)",
          id(win.scene.model) == model_id)
    original_set_moments = win.scene.set_moments
    calls = {"n": 0}

    def flaky_set_moments(moments, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("simulated shape mismatch")
        return original_set_moments(moments, *a, **k)

    win.scene.set_moments = flaky_set_moments
    try:
        doc.flip([mag_site])  # must not raise; falls back to the full rebuild
    finally:
        win.scene.set_moments = original_set_moments
    check("fast-path failure falls back to a rebuild",
          id(win.scene.model) != model_id
          and np.allclose(win.scene.model.moments, win._display_moments(doc.moments)))
    model_id = id(win.scene.model)
    doc.zero([mag_site])
    QTest.qWait(30)
    check("the fast path resumes after a fallback", id(win.scene.model) == model_id)

    # 21. A stale preview banner must not survive loading a new file.
    check("re-analyze after fast-path edits", win.analyze_magnetic_cell())
    win.export_panel.preview.setChecked(True)
    QTest.qWait(80)
    check("preview banner shown", win.editor.mode_overlay.banner.GetVisibility() == 1)
    # The autosave from earlier phases is newer than the CIF, so the recovery offer
    # pops up here: decline it (the load itself must proceed normally).
    watchdog.stop()
    timer, state = auto_answer(accept=False)
    loaded = win.load_file(src)
    timer.stop()
    watchdog.start(1500)
    check("recovery offer can be declined", state["answered"])
    check("loading a file drops the preview banner", loaded
          and not win.previewing and not win.editor.mode_overlay.banner.GetVisibility())
    doc = win.doc
    QTest.qWait(80)

    # 22. A corrupt/hand-edited session file gets a dialog, never a traceback.
    bad = Path(out) / "broken.magview.json"
    bad.write_text('{"format": "magview-project", "version": 1}')  # missing keys -> KeyError
    doc_id = id(win.doc)
    watchdog.stop()
    timer, state = auto_answer()
    ok = win.load_file(bad)
    timer.stop()
    watchdog.start(1500)
    check("corrupt session file: friendly dialog, document untouched",
          not ok and state["answered"] and id(win.doc) == doc_id)

    # 23. Recovery: a newer autosave is offered on open and restores the edits.
    win.doc.set_moments([0], [0, 0, 1.5], "test: seed moment")
    sess = Path(out) / "recovery.magview.json"
    win.session_path = sess
    check("save session for recovery test", win.save_session())
    QTest.qWait(30)
    win.doc.set_moments([0], [0, 0, 2.5], "test: unsaved edit")
    win._flush_autosave()
    watchdog.stop()
    timer, state = auto_answer(accept=True)
    ok = win.load_file(sess)
    timer.stop()
    watchdog.start(1500)
    check("newer recovery autosave offered and restored",
          ok and state["answered"] and abs(win.doc.moments[0][2] - 2.5) < 1e-9)
    doc = win.doc

    # 24. Autosave is debounced, not synchronous per edit.
    win._flush_autosave()
    doc.zero([0])
    QTest.qWait(20)
    debounced = win._autosave_pending
    win._flush_autosave()
    auto_files = list(Path(os.environ["MAGVIEW_AUTOSAVE_DIR"]).glob("*.magview.json"))
    check("autosave debounced then flushed", debounced and not win._autosave_pending
          and len(auto_files) == 1)

    # 25. Camera preservation across layer mode entry, layer stepping and exit.
    win.set_view("oblique")
    QTest.qWait(60)
    cam3d = camera()
    win.set_mode(ia.Mode.LAYER)
    QTest.qWait(100)
    check("view toolbar disabled in layer mode",
          win.in_layer_mode and all(not a.isEnabled() for a in win.view_actions))
    cam_layer = camera()
    win.step_layer(1)
    QTest.qWait(60)
    pos, foc, scale = camera()
    check("PgUp/PgDn layer step keeps the camera",
          np.allclose(pos, cam_layer[0]) and np.allclose(foc, cam_layer[1]))
    win.set_mode(ia.Mode.VIEW)
    QTest.qWait(80)
    pos, foc, scale = camera()
    check("leaving layer mode restores the 3D camera",
          all(a.isEnabled() for a in win.view_actions)
          and np.allclose(pos, cam3d[0]) and np.allclose(foc, cam3d[1])
          and abs(scale - cam3d[2]) < 1e-6)

    # 26. Status-bar mode chip follows the mode.
    chip0 = win.mode_label.text()
    win.set_mode(ia.Mode.MOMENT)
    QTest.qWait(60)
    chip1 = win.mode_label.text()
    win.set_mode(ia.Mode.VIEW)
    QTest.qWait(60)
    check("status-bar mode chip", chip0 == "VIEW" and chip1 == "MOMENT EDIT"
          and win.mode_label.text() == "VIEW", f"{chip0!r}/{chip1!r}")

    # 27. Letter keys work when a panel has focus, but text fields keep their keys.
    win.set_selection({0})
    win.mom_dock.raise_()
    win.moments_panel.flip_btn.setFocus()
    QTest.qWait(40)
    QTest.keyClick(win.moments_panel.flip_btn, Qt.Key_M)
    QTest.qWait(60)
    check("M with panel focus toggles moment mode", win.editor.machine.mode == ia.Mode.MOMENT)
    win.moments_panel.scale.setFocus()
    QTest.qWait(40)
    n_hist = len(doc.history._undo)
    QTest.keyClick(win.moments_panel.scale, Qt.Key_0)
    QTest.qWait(30)
    check("0 types into the focused spin box (no zero edit)", len(doc.history._undo) == n_hist)
    QTest.keyClick(win.moments_panel.scale, Qt.Key_M)
    QTest.qWait(30)
    check("letters in a text field do not switch modes", win.editor.machine.mode == ia.Mode.MOMENT)
    QTest.keyClick(win.moments_panel.scale, Qt.Key_Escape)
    QTest.qWait(30)
    check("Esc in a panel field keeps the mode", win.editor.machine.mode == ia.Mode.MOMENT)
    win.viewport.setFocus()
    QTest.qWait(40)
    QTest.keyClick(win.viewport, Qt.Key_Escape)
    QTest.qWait(60)
    check("Esc on the viewport exits the mode", win.editor.machine.mode == ia.Mode.VIEW)

    # 28. Command keys are ignored mid-drag; a held axis lock never sticks. And
    # idle hover in Moment mode outlines the atom under the cursor (open hand).
    win.set_mode(ia.Mode.MOMENT)
    QTest.qWait(80)
    t = magnetic_target()
    check("magnetic target for drag", t is not None)
    if t is not None:
        disp, site, x, y = t
        # QTest.mouseMove without a pressed button can move the *desktop* cursor
        # instead of delivering an event on Windows (another app may cover us).
        # Send an actual Qt hover motion to the widget, still through the VTK
        # event bridge, without depending on foreground ownership.
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        def hover_at(point):
            event = QMouseEvent(QEvent.MouseMove, QPointF(point),
                                QPointF(widget.mapToGlobal(point)),
                                Qt.NoButton, Qt.NoButton, Qt.NoModifier)
            QApplication.sendEvent(widget, event)

        hover_at(qpt(x, y))
        check("hover outlines the atom (open-hand cursor)",
              win.editor._hover_site == site
              and win.viewport.cursor().shape() == Qt.OpenHandCursor)
        ep0 = empty_point()
        if ep0 is not None:
            hover_at(qpt(*ep0))
            check("hover clears over empty space", win.editor._hover_site is None)
        before, n_hist = doc.moments.copy(), len(doc.history._undo)
        QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, qpt(x, y))
        for k in range(1, 5):
            QTest.mouseMove(widget, qpt(x, y + 10 * k))
            QTest.qWait(5)
        check("closed-hand cursor during a drag",
              win.viewport.cursor().shape() == Qt.ClosedHandCursor)
        QTest.keyClick(widget, Qt.Key_F)
        QTest.qWait(15)
        mid_ok = np.array_equal(doc.moments, before) and len(doc.history._undo) == n_hist
        QTest.keyPress(widget, Qt.Key_Z)
        QTest.qWait(15)
        locked = win.axis_lock == "z"
        QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, qpt(x, y + 40))
        QTest.qWait(60)
        QTest.keyRelease(widget, Qt.Key_Z)  # late release after commit must be harmless
        QTest.qWait(20)
        check("command key F ignored mid-drag", mid_ok)
        check("axis lock held mid-drag and cleared on commit",
              locked and win.axis_lock is None)
        check("drag commits exactly once after swallowed keys",
              len(doc.history._undo) == n_hist + 1
              and doc.moments[site] @ doc.spin_axis > 0.2)
    win.set_mode(ia.Mode.VIEW)
    QTest.qWait(60)

    # 29. Shift + left drag on empty space pans (focal point translates, distance kept).
    ep = empty_point()
    if ep is None:
        check("Shift+drag pans", False, "no empty pixel found")
    else:
        pos0, foc0, _ = camera()
        QTest.mousePress(widget, Qt.LeftButton, Qt.ShiftModifier, qpt(*ep))
        for k in range(1, 9):
            QTest.mouseMove(widget, qpt(ep[0] + 9 * k, ep[1]))
            QTest.qWait(5)
        QTest.mouseRelease(widget, Qt.LeftButton, Qt.ShiftModifier, qpt(ep[0] + 72, ep[1]))
        QTest.qWait(60)
        pos1, foc1, _ = camera()
        d0, d1 = pos0 - foc0, pos1 - foc1
        check("Shift+drag on empty space pans",
              np.linalg.norm(foc1 - foc0) > 1e-3
              and abs(np.linalg.norm(d1) - np.linalg.norm(d0)) < 1e-3 * np.linalg.norm(d0)
              and np.allclose(d1 / np.linalg.norm(d1), d0 / np.linalg.norm(d0), atol=1e-3),
              f"focal moved {np.linalg.norm(foc1 - foc0):.3f}")

    # 30. Save failures become dialogs, not crashes.
    watchdog.stop()
    timer, state = auto_answer()
    win.session_path = Path(out) / "no_such_dir" / "x.magview.json"
    ok = win.save_session()
    QTest.qWait(30)
    timer.stop()
    watchdog.start(1500)
    win.session_path = None
    check("save to an unwritable path shows a dialog", not ok and state["answered"])

    # 31. Context menu contents follow the mode (spec section 2); the patterns
    # popover can anchor at the selection centroid for keyboard invocation.
    view_menu = win._build_context_menu(0)  # View mode
    view_entries = [a.text() for a in view_menu.actions()] if view_menu else []
    win.set_mode(ia.Mode.MOMENT)
    QTest.qWait(60)
    moment_menu = win._build_context_menu(0)
    moment_entries = [a.text() for a in moment_menu.actions()] if moment_menu else []
    win.set_mode(ia.Mode.VIEW)
    QTest.qWait(40)
    check("View-mode context menu: info/select only",
          any("info" in t.lower() for t in view_entries)
          and not any("Flip" in t for t in view_entries), ", ".join(view_entries))
    check("Moment-mode context menu: Flip / Set direction",
          any("Flip" in t for t in moment_entries)
          and any("Set direction" in t for t in moment_entries), ", ".join(moment_entries))
    check("patterns centroid helper", win._selection_centroid_global() is not None)
    win.set_selection(set())
    check("patterns centroid needs a selection", win._selection_centroid_global() is None)

    # 32. Window geometry persists via QSettings (a fresh window restores it).
    # Stay inside the available screen: restoreGeometry() clamps out-of-screen
    # sizes (xvfb's default screen is only 1280x1024), which would fake a failure.
    avail = win.screen().availableGeometry()
    win.resize(min(win.width() - 120, avail.width() - 100),
               max(400, min(win.height() - 60, avail.height() - 100)))
    QTest.qWait(80)
    win.settings.setValue("window/geometry", win.saveGeometry())  # what closeEvent saves
    win.settings.setValue("window/state", win.saveState())
    expected = (win.width(), win.height())
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # The offscreen platform controls top-level geometry itself (and a second GL
        # window is unstable there); exercise the restore path on the live window.
        win._restore_window_state()
        ok = (win.settings.value("window/geometry") is not None
              and win.settings.value("window/state") is not None)
        check("window geometry persisted via QSettings", ok)
    else:
        from magview.gui.main_window import MainWindow

        w2 = MainWindow()
        # headless X servers have no window manager, so restored geometry can
        # differ by a few pixels (frame extents); compare with a tolerance.
        ok = (abs(w2.width() - expected[0]) <= 24
              and abs(w2.height() - expected[1]) <= 24)
        check("window geometry restored from QSettings", ok,
              f"expected {expected[0]}x{expected[1]}, got {w2.width()}x{w2.height()}")
        w2.close()
        QTest.qWait(60)


import os  # noqa: E402  (used by phase2_checks and phase6_checks)

if __name__ == "__main__":
    sys.exit(main())
