"""Capture MagView's widgets and VTK buffers without reading other desktop windows."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRectF
from PySide6.QtGui import QImage, QPainter


def capture_window(win):
    # QWidget.grab captures Qt controls even when the window is covered. VTK owns
    # native child windows, so composite their framebuffers into the same pixmap.
    pixmap = win.grab()
    painter = QPainter(pixmap)
    try:
        for view in (win.viewport, win.reference):
            if not view.isVisible():
                continue
            pixels = np.ascontiguousarray(view.screenshot(return_img=True))
            h, w, channels = pixels.shape
            fmt = QImage.Format_RGB888 if channels == 3 else QImage.Format_RGBA8888
            frame = QImage(pixels.data, w, h, pixels.strides[0], fmt).copy()
            origin = view.mapTo(win, QPoint(0, 0))
            painter.drawImage(QRectF(origin.x(), origin.y(), view.width(), view.height()), frame)
    finally:
        painter.end()
    return pixmap
