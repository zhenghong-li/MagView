"""QApplication bootstrap."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_API", "pyside6")


def run(path: str | None = None, screenshot: str | None = None, view: str | None = None,
        delay_ms: int = 1500) -> int:
    """Start the GUI. With ``screenshot`` set, capture the screen to a PNG and quit
    (used by the Xvfb smoke test; the capture includes the OpenGL viewports)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from magview.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
    win.show()
    if screenshot:
        # Batch/capture mode must never block: reject any unexpected modal dialog
        # (e.g. the crash-recovery prompt when a stale autosave exists). Armed
        # before load_file because the prompt fires during loading.
        def reject_modals() -> None:
            modal = app.activeModalWidget()
            if modal is not None and modal.objectName() != "busy_progress":
                print(f"note: rejected modal dialog in capture mode: {modal.windowTitle()!r}")
                modal.reject() if hasattr(modal, "reject") else modal.close()

        watchdog = QTimer()
        watchdog.timeout.connect(reject_modals)
        watchdog.start(400)
    if path:
        if not win.load_file(path) and screenshot:
            return 1
    if view:
        win.set_view(view)

    if screenshot:
        def grab() -> None:
            app.processEvents()
            win.viewport.render()
            win.reference.render()
            app.processEvents()
            screen = win.screen()
            pixmap = screen.grabWindow(0, win.x(), win.y(), win.frameGeometry().width(),
                                       win.frameGeometry().height())
            ok = pixmap.save(screenshot)
            print(f"screenshot {'saved' if ok else 'FAILED'}: {screenshot}")
            win.close()
            app.exit(0 if ok else 1)

        QTimer.singleShot(delay_ms, grab)
    return app.exec()
