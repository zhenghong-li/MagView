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
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from magview.gui.main_window import MainWindow
    from magview.render.picking import pick_atom, project_to_display

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
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
    results: list[dict] = []

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

    (out / "selftest.json").write_text(json.dumps(results, indent=2))
    win.close()
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
