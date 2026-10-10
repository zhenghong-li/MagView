"""2D overlays drawn by VTK (so they also appear in offscreen PNGs):
rubber band, long-press ring, and the mode banner + colored viewport border."""

from __future__ import annotations

import numpy as np
import vtk


def _polyline_actor(color, width: float) -> tuple[vtk.vtkActor2D, vtk.vtkPolyData]:
    poly = vtk.vtkPolyData()
    mapper = vtk.vtkPolyDataMapper2D()
    mapper.SetInputData(poly)
    coord = vtk.vtkCoordinate()
    coord.SetCoordinateSystemToDisplay()
    mapper.SetTransformCoordinate(coord)
    actor = vtk.vtkActor2D()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*color)
    prop.SetLineWidth(width)
    actor.VisibilityOff()
    return actor, poly


def _set_polyline(poly: vtk.vtkPolyData, pts: np.ndarray, closed: bool = True) -> None:
    points = vtk.vtkPoints()
    for x, y in pts:
        points.InsertNextPoint(float(x), float(y), 0.0)
    lines = vtk.vtkCellArray()
    n = len(pts)
    ids = list(range(n)) + ([0] if closed else [])
    lines.InsertNextCell(len(ids))
    for i in ids:
        lines.InsertCellPoint(i)
    poly.SetPoints(points)
    poly.SetLines(lines)
    poly.Modified()


def _set_polygon(poly: vtk.vtkPolyData, pts: np.ndarray) -> None:
    points = vtk.vtkPoints()
    for x, y in pts:
        points.InsertNextPoint(float(x), float(y), 0.0)
    polys = vtk.vtkCellArray()
    polys.InsertNextCell(len(pts))
    for i in range(len(pts)):
        polys.InsertCellPoint(i)
    poly.SetPoints(points)
    poly.SetPolys(polys)
    poly.Modified()


def _set_quads(poly: vtk.vtkPolyData, quads: np.ndarray) -> None:
    """``quads`` is (k, 4, 2): k independent 4-point polygons in display pixels."""
    quads = np.asarray(quads, dtype=float).reshape(-1, 4, 2)
    points = vtk.vtkPoints()
    for x, y in quads.reshape(-1, 2):
        points.InsertNextPoint(float(x), float(y), 0.0)
    polys = vtk.vtkCellArray()
    for q in range(len(quads)):
        polys.InsertNextCell(4)
        for i in range(4):
            polys.InsertCellPoint(4 * q + i)
    poly.SetPoints(points)
    poly.SetPolys(polys)
    poly.Modified()


class BoxOverlay:
    """Rubber-band rectangle plus the ring that shows a long press has armed box mode."""

    def __init__(self, renderer, color=(0.85, 0.45, 0.0), fill_opacity: float = 0.12) -> None:
        self.box, self._box_poly = _polyline_actor(color, 2.0)
        self.ring, self._ring_poly = _polyline_actor(color, 2.0)
        self.fill, self._fill_poly = _polyline_actor(color, 1.0)
        self.fill.GetProperty().SetOpacity(fill_opacity)
        renderer.AddViewProp(self.fill)
        renderer.AddViewProp(self.box)
        renderer.AddViewProp(self.ring)

    def armed(self, x: float, y: float, radius: float = 10.0) -> None:
        t = np.linspace(0, 2 * np.pi, 33)[:-1]
        _set_polyline(self._ring_poly, np.column_stack([x + radius * np.cos(t), y + radius * np.sin(t)]))
        self.ring.VisibilityOn()

    def update(self, x0: float, y0: float, x1: float, y1: float) -> None:
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        _set_polyline(self._box_poly, corners)
        _set_polygon(self._fill_poly, corners)
        self.box.VisibilityOn()
        self.fill.VisibilityOn()

    def hide(self) -> None:
        self.box.VisibilityOff()
        self.fill.VisibilityOff()
        self.ring.VisibilityOff()


class ModeOverlay:
    """Colored border around the viewport and a one-line banner (top-left)."""

    def __init__(self, renderer) -> None:
        self.renderer = renderer
        self.border, self._border_poly = _polyline_actor((1, 1, 1), 1.0)
        renderer.AddViewProp(self.border)
        self.banner = vtk.vtkTextActor()
        tp = self.banner.GetTextProperty()
        tp.SetFontSize(15)
        tp.SetBold(True)
        tp.SetColor(1, 1, 1)
        tp.SetBackgroundOpacity(0.85)
        tp.SetFrame(False)
        self.banner.SetDisplayPosition(10, 10)
        self.banner.VisibilityOff()
        renderer.AddViewProp(self.banner)
        self._shown: tuple[str, tuple] | None = None
        self._size = (0, 0)
        renderer.AddObserver("StartEvent", self._relayout)

    def _relayout(self, *_):
        # Keep the border on the viewport edge when the window is resized.
        if self._shown is not None and tuple(self.renderer.GetSize()) != self._size:
            self.show(*self._shown)

    def show(self, text: str, color) -> None:
        self._shown = (text, tuple(color))
        w, h = self.renderer.GetSize()
        self._size = (w, h)
        # Four thin filled quads, not a 6px line: wide glLineWidth silently
        # degrades to 1px on macOS/Metal and some Linux GL drivers.
        t = 6
        _set_quads(self._border_poly, np.array([
            [[0, 0], [w, 0], [w, t], [0, t]],  # bottom
            [[0, h - t], [w, h - t], [w, h], [0, h]],  # top
            [[0, t], [t, t], [t, h - t], [0, h - t]],  # left
            [[w - t, t], [w, t], [w, h - t], [w - t, h - t]],  # right
        ]))
        self.border.GetProperty().SetColor(*color)
        self.border.VisibilityOn()
        self.banner.SetInput(text)
        self.banner.GetTextProperty().SetBackgroundColor(*color)
        self.banner.SetDisplayPosition(12, h - 34)
        self.banner.VisibilityOn()

    def hide(self) -> None:
        self._shown = None
        self.border.VisibilityOff()
        self.banner.VisibilityOff()


class CursorLabel:
    """Small multi-line readout next to the cursor (live drag feedback)."""

    def __init__(self, renderer) -> None:
        self.renderer = renderer
        self.actor = vtk.vtkTextActor()
        tp = self.actor.GetTextProperty()
        tp.SetFontSize(14)
        tp.SetColor(0.1, 0.1, 0.1)
        tp.SetBackgroundColor(1.0, 1.0, 0.9)
        tp.SetBackgroundOpacity(0.9)
        tp.SetFrame(True)
        tp.SetFrameColor(0.4, 0.4, 0.4)
        tp.SetFontFamilyToCourier()
        self.actor.VisibilityOff()
        renderer.AddViewProp(self.actor)

    def show(self, text: str, x: float, y: float) -> None:
        self.actor.SetInput(text)
        w, h = self.renderer.GetSize()
        lines = text.count("\n") + 1
        width = 9 * max(len(s) for s in text.splitlines())
        px = min(max(4, x + 18), max(4, w - width - 4))
        py = min(max(4, y - 18 - 17 * lines), h - 17 * lines - 4)
        self.actor.SetDisplayPosition(int(px), int(py))
        self.actor.VisibilityOn()

    def hide(self) -> None:
        self.actor.VisibilityOff()
