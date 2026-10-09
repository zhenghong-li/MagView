"""Pointer/keyboard gesture state machine (pure: no Qt, no VTK).

The GUI translates toolkit events into the small event types below (including
the result of picking: which canvas site, if any, is under the pointer) and
executes the returned actions. Tests drive the same machine with synthetic
events, so every gesture rule in docs/ux_spec.md is checked without a display.

Gesture rules (see docs/ux_spec.md section 2):
* press + release within CLICK_SLOP px -> click (select; Shift adds, Ctrl toggles;
  empty space clears);
* press, hold >= LONG_PRESS_S without moving -> box select armed, then drag;
* `B` arms box select for the next press (touchpad fallback);
* press on empty space (or on an atom in View mode) forwards to the camera;
  in Moment mode, press on an atom + move >= CLICK_SLOP -> moment drag;
* Esc cancels the active gesture; with nothing active it exits to View mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CLICK_SLOP = 4.0  # px
LONG_PRESS_S = 0.35  # s


class Mode(Enum):
    VIEW = "view"
    MOMENT = "moment"
    LAYER = "layer"


class SelectOp(Enum):
    REPLACE = "replace"
    ADD = "add"
    TOGGLE = "toggle"
    SUBTRACT = "subtract"


# --- events -------------------------------------------------------------------

@dataclass(frozen=True)
class Mods:
    shift: bool = False
    ctrl: bool = False


@dataclass(frozen=True)
class Press:
    x: float
    y: float
    t: float
    hit: int | None = None  # canvas site under the pointer
    mods: Mods = Mods()


@dataclass(frozen=True)
class Move:
    x: float
    y: float
    t: float
    mods: Mods = Mods()


@dataclass(frozen=True)
class Release:
    x: float
    y: float
    t: float
    mods: Mods = Mods()


@dataclass(frozen=True)
class Tick:
    t: float


@dataclass(frozen=True)
class Key:
    key: str  # "escape", "b", "m", "l", ...
    mods: Mods = Mods()


# --- actions ------------------------------------------------------------------

@dataclass(frozen=True)
class Select:
    sites: tuple[int, ...]
    op: SelectOp


@dataclass(frozen=True)
class ClearSelection:
    pass


@dataclass(frozen=True)
class ShowInfo:
    site: int | None


@dataclass(frozen=True)
class CameraPress:
    """Forward the button press (at x, y) to the camera controller."""

    x: float
    y: float


@dataclass(frozen=True)
class CameraRelease:
    pass


@dataclass(frozen=True)
class BoxArmed:
    x: float
    y: float


@dataclass(frozen=True)
class BoxUpdate:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class BoxFinish:
    x0: float
    y0: float
    x1: float
    y1: float
    op: SelectOp


@dataclass(frozen=True)
class BoxCancel:
    pass


@dataclass(frozen=True)
class DragStart:
    site: int
    x: float
    y: float


@dataclass(frozen=True)
class DragUpdate:
    site: int
    x: float
    y: float
    mods: Mods


@dataclass(frozen=True)
class DragCommit:
    site: int


@dataclass(frozen=True)
class DragCancel:
    site: int


@dataclass(frozen=True)
class ModeChanged:
    mode: Mode


class GestureState(Enum):
    IDLE = "idle"
    PRESSED = "pressed"  # waiting to see click / long press / drag
    CAMERA = "camera"
    BOX = "box"  # rubber band active (armed or dragging)
    DRAG = "drag"  # moment drag


@dataclass
class InteractionMachine:
    mode: Mode = Mode.VIEW
    state: GestureState = GestureState.IDLE
    _press: Press | None = None
    _camera_forwarded: bool = False
    _box_key_armed: bool = False
    _box_start: tuple[float, float] = (0.0, 0.0)
    _last: tuple[float, float] = (0.0, 0.0)
    log: list = field(default_factory=list)  # every action emitted (for debugging/tests)

    # -- public API ---------------------------------------------------------------
    def handle(self, event) -> list:
        if isinstance(event, Press):
            out = self._press_event(event)
        elif isinstance(event, Move):
            out = self._move_event(event)
        elif isinstance(event, Release):
            out = self._release_event(event)
        elif isinstance(event, Tick):
            out = self._tick_event(event)
        elif isinstance(event, Key):
            out = self._key_event(event)
        else:
            raise TypeError(f"unknown event {event!r}")
        self.log.extend(out)
        return out

    def set_mode(self, mode: Mode) -> list:
        out = self._cancel()
        if mode != self.mode:
            self.mode = mode
            out.append(ModeChanged(mode))
        self.log.extend(out)
        return out

    @property
    def busy(self) -> bool:
        return self.state != GestureState.IDLE

    # -- handlers -----------------------------------------------------------------
    def _press_event(self, e: Press) -> list:
        out = self._cancel() if self.state != GestureState.IDLE else []
        self._press = e
        self._last = (e.x, e.y)
        if self._box_key_armed:
            self._box_key_armed = False
            self.state = GestureState.BOX
            self._box_start = (e.x, e.y)
            return out + [BoxArmed(e.x, e.y)]
        self.state = GestureState.PRESSED
        # Press on empty space, or anywhere in View mode: the camera gets the press
        # right away (no visible change until the pointer moves).
        if e.hit is None or self.mode != Mode.MOMENT:
            self._camera_forwarded = True
            out.append(CameraPress(e.x, e.y))
        else:
            self._camera_forwarded = False
        return out

    def _move_event(self, e: Move) -> list:
        self._last = (e.x, e.y)
        if self.state == GestureState.PRESSED:
            p = self._press
            if _dist(p.x, p.y, e.x, e.y) < CLICK_SLOP:
                return []
            if self._camera_forwarded:
                self.state = GestureState.CAMERA
                return []
            self.state = GestureState.DRAG
            return [DragStart(p.hit, p.x, p.y), DragUpdate(p.hit, e.x, e.y, e.mods)]
        if self.state == GestureState.BOX:
            x0, y0 = self._box_start
            return [BoxUpdate(x0, y0, e.x, e.y)]
        if self.state == GestureState.DRAG:
            return [DragUpdate(self._press.hit, e.x, e.y, e.mods)]
        return []

    def _tick_event(self, e: Tick) -> list:
        if self.state != GestureState.PRESSED or self._press is None:
            return []
        p = self._press
        if e.t - p.t < LONG_PRESS_S or _dist(p.x, p.y, *self._last) >= CLICK_SLOP:
            return []
        out = [CameraRelease()] if self._camera_forwarded else []
        self._camera_forwarded = False
        self.state = GestureState.BOX
        self._box_start = (p.x, p.y)
        return out + [BoxArmed(p.x, p.y)]

    def _release_event(self, e: Release) -> list:
        state, p = self.state, self._press
        self.state = GestureState.IDLE
        self._press = None
        out = []
        if state == GestureState.PRESSED:
            if self._camera_forwarded:
                out.append(CameraRelease())
            self._camera_forwarded = False
            # A click: decided by the modifiers at release time.
            if p.hit is None:
                if not (e.mods.shift or e.mods.ctrl):
                    out.append(ClearSelection())
                out.append(ShowInfo(None))
            else:
                op = SelectOp.TOGGLE if e.mods.ctrl else SelectOp.ADD if e.mods.shift else SelectOp.REPLACE
                out += [Select((p.hit,), op), ShowInfo(p.hit)]
        elif state == GestureState.CAMERA:
            self._camera_forwarded = False
            out.append(CameraRelease())
        elif state == GestureState.BOX:
            x0, y0 = self._box_start
            op = SelectOp.SUBTRACT if e.mods.ctrl else SelectOp.ADD if e.mods.shift else SelectOp.REPLACE
            out.append(BoxFinish(x0, y0, e.x, e.y, op))
        elif state == GestureState.DRAG:
            out.append(DragCommit(p.hit))
        return out

    def _key_event(self, e: Key) -> list:
        k = e.key.lower()
        if k == "escape":
            if self.state != GestureState.IDLE or self._box_key_armed:
                self._box_key_armed = False
                return self._cancel()
            if self.mode != Mode.VIEW:
                return self.set_mode(Mode.VIEW)
            return [ClearSelection()]
        if k == "b" and self.state == GestureState.IDLE:
            self._box_key_armed = True
            return []
        if k == "m" and self.state == GestureState.IDLE:
            return self.set_mode(Mode.VIEW if self.mode == Mode.MOMENT else Mode.MOMENT)
        if k == "l" and self.state == GestureState.IDLE:
            return self.set_mode(Mode.VIEW if self.mode == Mode.LAYER else Mode.LAYER)
        return []

    def _cancel(self) -> list:
        """Abort the active gesture without committing anything."""
        state, p = self.state, self._press
        self.state = GestureState.IDLE
        self._press = None
        out = []
        if self._camera_forwarded:
            out.append(CameraRelease())
        self._camera_forwarded = False
        if state == GestureState.BOX:
            out.append(BoxCancel())
        elif state == GestureState.DRAG and p is not None:
            out.append(DragCancel(p.hit))
        return out


def _dist(x0: float, y0: float, x1: float, y1: float) -> float:
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
