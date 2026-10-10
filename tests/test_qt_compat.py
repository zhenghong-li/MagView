"""Native input, Retina distances and GL startup without creating a GL context."""

from types import SimpleNamespace

import pytest
import vtk
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QNativeGestureEvent, QPointingDevice, QSurfaceFormat, QWheelEvent

from magview import osdeps
from magview.core import interaction as ia
from magview.gui.qt_compat import ViewportInteractor, configure_surface_format
from magview.gui.shortcuts import command_modifier_text, shortcut_text
from magview.render.overlay import BoxOverlay, CursorLabel, ModeOverlay


@pytest.fixture
def viewport(monkeypatch):
    monkeypatch.setattr(osdeps, "PLATFORM", "darwin")
    camera = vtk.vtkCamera()
    camera.SetPosition(0, 0, 10)
    renderer = vtk.vtkRenderer()
    renderer.SetActiveCamera(camera)
    renders = []
    view = SimpleNamespace(camera=camera, renderer=renderer, render=lambda: renders.append(True))
    view._zoom = lambda factor: ViewportInteractor._zoom(view, factor)
    return view, renders


def wheel(pixels=0, angle=0):
    return QWheelEvent(QPointF(20, 30), QPointF(20, 30), QPoint(0, pixels), QPoint(0, angle),
                       Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)


def test_trackpad_pixel_steps_are_smooth_and_reversible(viewport):
    view, renders = viewport
    before = view.camera.GetDistance()
    event = wheel(pixels=1)
    ViewportInteractor.wheelEvent(view, event)
    assert event.isAccepted()
    after = view.camera.GetDistance()
    assert before * 0.99 < after < before
    ViewportInteractor.wheelEvent(view, wheel(pixels=-1))
    assert view.camera.GetDistance() == pytest.approx(before)
    assert len(renders) == 2


def test_mouse_notch_and_parallel_layer_zoom(viewport):
    view, _ = viewport
    before = view.camera.GetDistance()
    ViewportInteractor.wheelEvent(view, wheel(angle=120))
    assert view.camera.GetDistance() == pytest.approx(before / 1.1**2)
    view.camera.ParallelProjectionOn()
    before = view.camera.GetParallelScale()
    ViewportInteractor.wheelEvent(view, wheel(pixels=120))
    assert view.camera.GetParallelScale() == pytest.approx(before / 1.1**2)


def test_native_pinch_uses_incremental_magnification(viewport):
    view, renders = viewport
    event = QNativeGestureEvent(Qt.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
                                QPointF(20, 30), QPointF(20, 30), QPointF(20, 30),
                                0.2, QPointF())
    before = view.camera.GetDistance()
    assert ViewportInteractor.event(view, event)
    assert event.isAccepted()
    assert view.camera.GetDistance() == pytest.approx(before / 1.2)
    assert len(renders) == 1


def test_native_render_emits_on_the_calling_thread(monkeypatch):
    import threading

    monkeypatch.setattr(osdeps, "PLATFORM", "darwin")
    threads = []
    view = SimpleNamespace(_closed=False,
                           render_signal=SimpleNamespace(emit=lambda: threads.append(threading.get_ident())))
    ViewportInteractor.render(view)
    assert threads == [threading.get_ident()]
    assert view._rendered
    view._closed = True
    ViewportInteractor.render(view)
    assert len(threads) == 1


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_retina_click_jitter_and_long_press(scale):
    machine = ia.InteractionMachine(mode=ia.Mode.MOMENT, click_slop=ia.CLICK_SLOP * scale)
    machine.handle(ia.Press(10, 10, 0, hit=0))
    assert machine.handle(ia.Move(10 + 3 * scale, 10, 0.1)) == []
    assert machine.state == ia.GestureState.PRESSED
    assert any(isinstance(a, ia.BoxArmed) for a in machine.handle(ia.Tick(0.4)))
    machine.handle(ia.Key("escape"))
    machine.handle(ia.Press(10, 10, 1, hit=0))
    assert any(isinstance(a, ia.DragStart)
               for a in machine.handle(ia.Move(10 + 5 * scale, 10, 1.1)))


def test_retina_overlays_relayout_when_pixel_ratio_changes():
    renderer = vtk.vtkRenderer()
    renderer.GetSize = lambda: (800, 600)
    scale = [1.0]
    ratio = lambda: scale[0]
    mode = ModeOverlay(renderer, pixel_ratio=ratio)
    mode.show("MOMENT EDIT", (1, 0, 0))
    assert mode.banner.GetPosition() == (12, 566)
    scale[0] = 2.0
    mode._relayout()
    assert mode.banner.GetPosition() == (24, 532)
    box = BoxOverlay(renderer, pixel_ratio=ratio)
    box.armed(50, 50)
    assert box._ring_poly.GetPoint(0)[:2] == pytest.approx((70, 50))
    label = CursorLabel(renderer, pixel_ratio=ratio)
    label.show("1234567890\nvalue", 790, 590)
    x, y = label.actor.GetPosition()
    assert x + 180 <= 800 - 8
    assert y + 68 <= 600 - 8


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_early_gl_format_policy(monkeypatch, platform):
    monkeypatch.setattr(osdeps, "PLATFORM", platform)
    previous = QSurfaceFormat.defaultFormat()
    initial = QSurfaceFormat()
    initial.setVersion(2, 0)
    QSurfaceFormat.setDefaultFormat(initial)
    try:
        configure_surface_format()
        actual = QSurfaceFormat.defaultFormat()
        if platform == "darwin":
            assert actual.version() == (3, 2)
            assert actual.profile() == QSurfaceFormat.CoreProfile
            assert actual.samples() == 0
        else:
            assert actual.version() == (2, 0)
    finally:
        QSurfaceFormat.setDefaultFormat(previous)


def test_shortcut_labels_use_native_qt_text():
    for sequence in ("Ctrl+O", "Ctrl+Z", "Ctrl+Shift+S"):
        assert shortcut_text(sequence) == QKeySequence(sequence).toString(QKeySequence.NativeText)
    assert command_modifier_text()
