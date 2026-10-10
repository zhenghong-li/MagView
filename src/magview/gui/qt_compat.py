"""Qt/VTK integration for native startup, input and shortcut text."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QSurfaceFormat
from pyvistaqt import QtInteractor

from magview import osdeps


def configure_surface_format() -> None:
    """Install VTK's core GL format before QApplication creates shared contexts.

    pyvistaqt sets this when constructing its widget, which is too late for
    Cocoa's application-wide context sharing. Match its 3.2 core format.
    """
    if not osdeps.native_gui_workarounds():
        return
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setVersion(3, 2)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    fmt.setRedBufferSize(8)
    fmt.setGreenBufferSize(8)
    fmt.setBlueBufferSize(8)
    fmt.setDepthBufferSize(8)
    fmt.setAlphaBufferSize(8)
    fmt.setStencilBufferSize(0)
    fmt.setSamples(0)
    QSurfaceFormat.setDefaultFormat(fmt)


class ViewportInteractor(QtInteractor):
    """Keep the existing QtInteractor behavior except where Cocoa needs fixes."""

    def render(self) -> None:
        if not osdeps.native_gui_workarounds():
            return super().render()
        # pyvistaqt wraps render() in a new Python thread on Cocoa. Its signal
        # then queues the frame, so immediate screenshots and close() can race
        # that frame. Emitting here renders synchronously on the GUI thread;
        # Qt still queues a caller from any other thread safely.
        if getattr(self, "_closed", False):
            return
        self._rendered = True
        try:
            self.render_signal.emit()
        except RuntimeError:  # the underlying Qt widget was already deleted
            return

    def _zoom(self, factor: float) -> None:
        if not math.isfinite(factor) or factor <= 0:
            return
        camera = self.camera
        if camera.GetParallelProjection():
            camera.SetParallelScale(camera.GetParallelScale() / factor)
        else:
            camera.Dolly(factor)
        self.renderer.ResetCameraClippingRange()
        self.render()

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if not osdeps.native_gui_workarounds():
            return super().wheelEvent(event)
        # Cocoa supplies logical pixel deltas for precision trackpads. A mouse
        # notch (120 angle units) retains VTK's usual 1.1**2 dolly factor.
        pixels = event.pixelDelta().y()
        steps = pixels / 120.0 if pixels else event.angleDelta().y() / 120.0
        if steps:
            self._zoom(1.1 ** (2.0 * steps))
        event.accept()

    def event(self, event) -> bool:
        if osdeps.native_gui_workarounds() and event.type() == QEvent.NativeGesture:
            gesture = event.gestureType()
            if gesture == Qt.ZoomNativeGesture:
                self._zoom(1.0 + event.value())
            elif gesture not in (Qt.BeginNativeGesture, Qt.EndNativeGesture):
                return super().event(event)
            event.accept()
            return True
        return super().event(event)
