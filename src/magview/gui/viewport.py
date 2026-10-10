"""Bridge between the VTK render window and the pure interaction state machine.

``EditorInteractorStyle`` receives VTK mouse events, picks the atom under the
pointer, feeds ``core.interaction.InteractionMachine`` and executes its actions:
camera presses/releases are forwarded to the trackball style, selections and
box gestures are reported to the owner through callbacks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import vtk
from PySide6.QtCore import Qt, QTimer

from magview import osdeps
from magview.core import interaction as ia
from magview.render.overlay import BoxOverlay, ModeOverlay
from magview.render.picking import atoms_in_box, pick_atom

MODE_BANNERS = {
    ia.Mode.MOMENT: "MOMENT EDIT - drag from an atom to set its moment - long-press to box-select - Esc to exit",
    ia.Mode.LAYER: "LAYER EDIT - click atoms to toggle up/down - Esc to exit",
}


@dataclass
class ViewportCallbacks:
    select: Callable[..., None]  # (canvas sites, op, click=bool)
    clear: Callable[[], None]
    info: Callable[[int | None], None]
    mode_changed: Callable[[ia.Mode], None] = lambda mode: None
    drag: Callable[[object], None] = lambda action: None  # DragStart/Update/Commit/Cancel
    context_menu: Callable[[int | None], None] = lambda site: None  # right click (no drag)


class EditorInteractorStyle(vtk.vtkInteractorStyleTrackballCamera):
    """Trackball camera whose left button and mouse moves are arbitrated by the
    interaction state machine. Middle/right buttons and the wheel keep VTK's defaults."""

    def __init__(self, viewport: "EditorViewport") -> None:
        super().__init__()
        self.viewport = viewport
        self.AddObserver("LeftButtonPressEvent", self._on_press)
        self.AddObserver("LeftButtonReleaseEvent", self._on_release)
        self.AddObserver("MouseMoveEvent", self._on_move)
        self.AddObserver("RightButtonPressEvent", self._on_right_press)
        self.AddObserver("RightButtonReleaseEvent", self._on_right_release)
        self.AddObserver("LeaveEvent", lambda *_: self.viewport.leave())
        self._right_press: tuple[int, int] | None = None
        # Swallow VTK's built-in key bindings (e = exit, w = wireframe, ...).
        self.AddObserver("CharEvent", lambda *_: None)
        self.AddObserver("KeyPressEvent", lambda *_: None)

    def _mods(self) -> ia.Mods:
        iren = self.GetInteractor()
        return ia.Mods(shift=bool(iren.GetShiftKey()), ctrl=bool(iren.GetControlKey()))

    def _pos(self) -> tuple[int, int]:
        return self.GetInteractor().GetEventPosition()

    def _on_press(self, *_):
        x, y = self._pos()
        self.viewport.press(x, y, self._mods())

    def _on_release(self, *_):
        x, y = self._pos()
        self.viewport.release(x, y, self._mods())

    def _on_right_press(self, *_):
        self._right_press = self._pos()
        self.OnRightButtonDown()  # right drag keeps VTK's dolly (zoom)

    def _on_right_release(self, *_):
        self.OnRightButtonUp()
        if self._right_press is None:
            return
        x0, y0 = self._right_press
        x, y = self._pos()
        self._right_press = None
        if abs(x - x0) + abs(y - y0) < ia.CLICK_SLOP * self.viewport.pixel_ratio():
            self.viewport.cb.context_menu(self.viewport.pick(x, y))

    def _on_move(self, *_):
        x, y = self._pos()
        if self.viewport.move(x, y, self._mods()):
            self.OnMouseMove()


class EditorViewport:
    """Owns the state machine for one QtInteractor-based viewport."""

    def __init__(self, plotter, scene, callbacks: ViewportCallbacks, theme) -> None:
        self.plotter = plotter
        self.scene = scene
        self.cb = callbacks
        self.theme = theme
        self.machine = ia.InteractionMachine()
        self.style = EditorInteractorStyle(self)
        plotter.iren.clear_key_event_callbacks()  # pyvista binds q (close), b, v, ...
        # Register through pyvista: it re-applies its remembered style on some calls
        # (update_style); setting it any other way gets silently reverted.
        plotter.iren.style = self.style
        self.box = BoxOverlay(plotter.renderer, pixel_ratio=self.pixel_ratio)
        self.mode_overlay = ModeOverlay(plotter.renderer, pixel_ratio=self.pixel_ratio)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.tick)
        self._camera_down = False
        self._press_pan = False  # Shift+left press on empty space pans (spec section 2)
        self._last_display_pick: int | None = None
        self.press_display_index: int | None = None  # displayed atom under the last press
        self._hover_site: int | None = None  # canvas site under the cursor (Moment mode)
        self._hover_actor = None  # lazily created wireframe sphere, reused forever
        self._hover_source = None
        self._cursor_shape = None

    # -- event entry points ---------------------------------------------------------
    @staticmethod
    def now() -> float:
        return time.monotonic()

    def press(self, x: float, y: float, mods: ia.Mods) -> None:
        # VTK supplies physical pixels; retain the same logical click/drag
        # threshold after a Retina screen change, including long-press jitter.
        self.machine.click_slop = ia.CLICK_SLOP * self.pixel_ratio()
        hit = self.pick(x, y)
        self.press_display_index = self._last_display_pick
        self._press_pan = bool(mods.shift) and hit is None
        self._run(self.machine.handle(ia.Press(x, y, self.now(), hit, mods)))
        if self.machine.state == ia.GestureState.PRESSED:
            self._timer.start(int(ia.LONG_PRESS_S * 1000) + 15)

    def move(self, x: float, y: float, mods: ia.Mods) -> bool:
        """Returns True if VTK's own mouse-move handling should run (camera / idle)."""
        self._run(self.machine.handle(ia.Move(x, y, self.now(), mods)))
        state = self.machine.state
        if state == ia.GestureState.IDLE and self.machine.mode == ia.Mode.MOMENT \
                and self.scene.model is not None:
            self._set_hover(self.pick(x, y), self._last_display_pick)
        else:
            self._set_hover(None)
        return state in (ia.GestureState.IDLE, ia.GestureState.CAMERA)

    def release(self, x: float, y: float, mods: ia.Mods) -> None:
        self._timer.stop()
        self._run(self.machine.handle(ia.Release(x, y, self.now(), mods)))

    def leave(self) -> None:
        """Pointer left the viewport: drop hover feedback."""
        self._set_hover(None)
        self.update_cursor()

    def tick(self) -> None:
        self._run(self.machine.handle(ia.Tick(self.now())))

    def key(self, key: str) -> None:
        self._run(self.machine.handle(ia.Key(key)))

    def set_mode(self, mode: ia.Mode) -> None:
        self._run(self.machine.set_mode(mode))

    # -- helpers ----------------------------------------------------------------------
    def pixel_ratio(self) -> float:
        return osdeps.gui_pixel_ratio(self.plotter.interactor.devicePixelRatioF())

    def pick(self, x: float, y: float) -> int | None:
        """Canvas site under display pixel (x, y); remembers the displayed atom too."""
        model = self.scene.model
        self._last_display_pick = None
        if model is None:
            return None
        i = pick_atom(self.plotter.renderer, model, x, y)
        self._last_display_pick = i
        return None if i is None else int(model.atoms.parent[i])

    def sites_in_box(self, x0, y0, x1, y1) -> list[int]:
        model = self.scene.model
        if model is None:
            return []
        idx = atoms_in_box(self.plotter.renderer, model, x0, y0, x1, y1)
        return sorted({int(model.atoms.parent[i]) for i in idx})

    def _run(self, actions) -> None:
        render = False
        for a in actions:
            if isinstance(a, ia.CameraPress):
                iren = self.style.GetInteractor()
                cur = iren.GetEventPosition()
                iren.SetEventPosition(int(a.x), int(a.y))
                # Shift+left on empty space pans: drive the middle-button (pan) path.
                if self._press_pan:
                    self.style.OnMiddleButtonDown()
                else:
                    self.style.OnLeftButtonDown()
                iren.SetEventPosition(*cur)
                self._camera_down = True
            elif isinstance(a, ia.CameraRelease):
                if self._camera_down:
                    if self._press_pan:
                        self.style.OnMiddleButtonUp()
                    else:
                        self.style.OnLeftButtonUp()
                self._camera_down = False
                self._press_pan = False
            elif isinstance(a, ia.Select):
                self.cb.select(list(a.sites), a.op, click=a.click)
            elif isinstance(a, ia.ClearSelection):
                self.cb.clear()
            elif isinstance(a, ia.ShowInfo):
                self.cb.info(a.site)
            elif isinstance(a, ia.BoxArmed):
                self.box.armed(a.x, a.y)
                render = True
            elif isinstance(a, ia.BoxUpdate):
                self.box.update(a.x0, a.y0, a.x1, a.y1)
                render = True
            elif isinstance(a, ia.BoxFinish):
                self.box.hide()
                self.cb.select(self.sites_in_box(a.x0, a.y0, a.x1, a.y1), a.op)
                render = True
            elif isinstance(a, ia.BoxCancel):
                self.box.hide()
                render = True
            elif isinstance(a, ia.ModeChanged):
                if a.mode in MODE_BANNERS:
                    color = self.theme.moment_mode_color if a.mode == ia.Mode.MOMENT else \
                        self.theme.layer_mode_color
                    self.mode_overlay.show(MODE_BANNERS[a.mode], color)
                else:
                    self.mode_overlay.hide()
                self.cb.mode_changed(a.mode)
                render = True
            elif isinstance(a, (ia.DragStart, ia.DragUpdate, ia.DragCommit, ia.DragCancel)):
                self.cb.drag(a)
        if self.machine.mode != ia.Mode.MOMENT or self.machine.state != ia.GestureState.IDLE:
            self._set_hover(None)
        self.update_cursor()
        if render:
            self.plotter.render()

    # -- cursor and hover feedback (spec sections 1-2) ---------------------------------
    def update_cursor(self) -> None:
        """Cursor follows the interaction state: open hand over a grabbable atom,
        closed hand while dragging a moment, crosshair when box select is armed."""
        m = self.machine
        if m.state == ia.GestureState.DRAG:
            self._set_cursor(Qt.ClosedHandCursor)
        elif m.state == ia.GestureState.BOX or m._box_key_armed:
            self._set_cursor(Qt.CrossCursor)
        elif m.mode == ia.Mode.MOMENT and m.state == ia.GestureState.IDLE \
                and self._hover_site is not None:
            self._set_cursor(Qt.OpenHandCursor)
        else:
            self._set_cursor(None)

    def _set_cursor(self, shape) -> None:
        if shape == self._cursor_shape:
            return
        self._cursor_shape = shape
        widget = self.plotter.interactor
        if shape is None:
            widget.unsetCursor()
        else:
            widget.setCursor(shape)

    def _set_hover(self, site: int | None, display_index: int | None = None) -> None:
        """Thin outline around the atom under the cursor (single reused actor, so idle
        mouse moves never rebuild the scene; renders only when the target changes)."""
        if site == self._hover_site:
            return
        model = self.scene.model
        if site is not None and (model is None or display_index is None):
            site = None
        self._hover_site = site
        if site is None:
            if self._hover_actor is not None:
                self._hover_actor.VisibilityOff()
                self.plotter.render()
        else:
            actor = self._hover_outline()
            self._hover_source.SetRadius(float(model.atom_radius[display_index]) * 1.2)
            actor.SetPosition(*model.atoms.cart[display_index])
            actor.VisibilityOn()
            self.plotter.render()
        self.update_cursor()

    def _hover_outline(self):
        if self._hover_actor is None:
            self._hover_source = vtk.vtkSphereSource()
            self._hover_source.SetThetaResolution(24)
            self._hover_source.SetPhiResolution(16)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(self._hover_source.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetRepresentationToWireframe()
            prop.SetColor(*self.theme.moment_mode_color)
            prop.SetLineWidth(1.5)
            prop.LightingOff()
            actor.VisibilityOff()
            self.plotter.renderer.AddActor(actor)
            self._hover_actor = actor
        return self._hover_actor

    def display_xy(self, points: np.ndarray) -> np.ndarray:
        from magview.render.picking import project_to_display

        return project_to_display(self.plotter.renderer, points)
