"""Gesture state machine (docs/ux_spec.md section 2), driven by synthetic events."""

import pytest

from magview.core import interaction as ia
from magview.core.interaction import (
    BoxArmed,
    BoxCancel,
    BoxFinish,
    BoxUpdate,
    CameraPress,
    CameraRelease,
    ClearSelection,
    DragCancel,
    DragCommit,
    DragStart,
    DragUpdate,
    Key,
    Mode,
    ModeChanged,
    Mods,
    Move,
    Press,
    Release,
    Select,
    SelectOp,
    ShowInfo,
    Tick,
)
from magview.core.selection import apply_selection

SHIFT, CTRL = Mods(shift=True), Mods(ctrl=True)


def kinds(actions):
    return [type(a).__name__ for a in actions]


@pytest.fixture
def m():
    return ia.InteractionMachine()


@pytest.fixture
def mm():
    machine = ia.InteractionMachine()
    machine.set_mode(Mode.MOMENT)
    return machine


def click(machine, hit, mods=Mods(), x=100, y=100, t=0.0):
    out = machine.handle(Press(x, y, t, hit, mods))
    out += machine.handle(Move(x + 1, y + 1, t + 0.05, mods))  # jitter below the slop
    out += machine.handle(Release(x + 1, y + 1, t + 0.1, mods))
    return out


@pytest.mark.parametrize("mods, op", [(Mods(), SelectOp.REPLACE), (SHIFT, SelectOp.ADD),
                                      (CTRL, SelectOp.TOGGLE)])
def test_click_atom_selects(m, mods, op):
    out = click(m, 7, mods)
    assert Select((7,), op) in out and ShowInfo(7) in out
    assert out[0] == CameraPress(100, 100) and out[-3] == CameraRelease()  # View: camera got press
    assert not m.busy


def test_click_empty_clears_unless_modifier(m):
    assert ClearSelection() in click(m, None)
    assert ClearSelection() not in click(m, None, SHIFT)


def test_view_mode_drag_on_atom_rotates(m):
    m.handle(Press(0, 0, 0.0, hit=3))
    assert m.handle(Move(10, 0, 0.1)) == []  # camera already has the press
    assert m.state == ia.GestureState.CAMERA
    assert m.handle(Release(10, 0, 0.2)) == [CameraRelease()]


def test_moment_mode_drag_on_atom(mm):
    out = mm.handle(Press(0, 0, 0.0, hit=3))
    assert out == []  # camera is NOT engaged when pressing an atom in Moment mode
    assert mm.handle(Move(2, 2, 0.05)) == []  # within the click slop
    out = mm.handle(Move(20, 0, 0.1, SHIFT))
    assert out == [DragStart(3, 0, 0), DragUpdate(3, 20, 0, SHIFT)]
    assert mm.handle(Move(30, 5, 0.2)) == [DragUpdate(3, 30, 5, Mods())]
    assert mm.handle(Release(30, 5, 0.3)) == [DragCommit(3)]


def test_moment_mode_empty_space_still_rotates(mm):
    assert mm.handle(Press(0, 0, 0.0, hit=None)) == [CameraPress(0, 0)]


def test_long_press_arms_box(m):
    m.handle(Press(50, 50, 0.0, hit=None))
    assert m.handle(Tick(0.2)) == []  # too early
    out = m.handle(Tick(0.4))
    assert out == [CameraRelease(), BoxArmed(50, 50)]  # camera press withdrawn first
    assert m.handle(Move(80, 90, 0.5)) == [BoxUpdate(50, 50, 80, 90)]
    assert m.handle(Release(80, 90, 0.6, SHIFT)) == [BoxFinish(50, 50, 80, 90, SelectOp.ADD)]


def test_long_press_on_atom_in_moment_mode(mm):
    mm.handle(Press(50, 50, 0.0, hit=4))
    assert mm.handle(Tick(0.36)) == [BoxArmed(50, 50)]  # no camera press to withdraw


def test_moved_press_never_becomes_box(m):
    m.handle(Press(0, 0, 0.0, hit=None))
    m.handle(Move(10, 0, 0.1))
    assert m.handle(Tick(0.5)) == []
    assert m.state == ia.GestureState.CAMERA


@pytest.mark.parametrize("mods, op", [(Mods(), SelectOp.REPLACE), (SHIFT, SelectOp.ADD),
                                      (CTRL, SelectOp.SUBTRACT)])
def test_box_ops(m, mods, op):
    m.handle(Press(0, 0, 0.0))
    m.handle(Tick(1.0))
    assert m.handle(Release(5, 5, 1.1, mods))[-1].op == op


def test_b_key_arms_box_for_next_press(m):
    m.handle(Key("b"))
    out = m.handle(Press(10, 10, 0.0, hit=3))
    assert out == [BoxArmed(10, 10)]
    assert kinds(m.handle(Release(40, 40, 0.1))) == ["BoxFinish"]
    # only for one press
    assert m.handle(Press(10, 10, 1.0, hit=3)) == [CameraPress(10, 10)]


def test_escape_cancels_box(m):
    m.handle(Press(0, 0, 0.0))
    m.handle(Tick(0.5))
    m.handle(Move(9, 9, 0.6))
    assert m.handle(Key("escape")) == [BoxCancel()]
    assert not m.busy
    assert m.handle(Release(9, 9, 0.7)) == []  # the release of a cancelled gesture is ignored


def test_escape_cancels_drag_without_commit(mm):
    mm.handle(Press(0, 0, 0.0, hit=2))
    mm.handle(Move(20, 0, 0.1))
    out = mm.handle(Key("escape"))
    assert out == [DragCancel(2)] and DragCommit(2) not in mm.log


def test_escape_levels(mm):
    assert mm.handle(Key("escape")) == [ModeChanged(Mode.VIEW)]  # idle -> leave the mode
    assert mm.handle(Key("escape")) == [ClearSelection()]  # View mode idle -> clear


def test_mode_switch_cancels_gesture(mm):
    mm.handle(Press(0, 0, 0.0, hit=1))
    mm.handle(Move(30, 0, 0.1))
    out = mm.set_mode(Mode.LAYER)
    assert out == [DragCancel(1), ModeChanged(Mode.LAYER)]


def test_mode_keys(m):
    assert m.handle(Key("m")) == [ModeChanged(Mode.MOMENT)]
    assert m.handle(Key("m")) == [ModeChanged(Mode.VIEW)]
    assert m.handle(Key("l")) == [ModeChanged(Mode.LAYER)]


def test_new_press_while_busy_cancels_previous(mm):
    mm.handle(Press(0, 0, 0.0, hit=1))
    mm.handle(Move(30, 0, 0.1))
    out = mm.handle(Press(5, 5, 0.2, hit=None))
    assert out[0] == DragCancel(1) and out[-1] == CameraPress(5, 5)


def test_selection_algebra():
    s = frozenset({1, 2})
    assert apply_selection(s, [3], SelectOp.ADD) == {1, 2, 3}
    assert apply_selection(s, [2, 3], SelectOp.TOGGLE) == {1, 3}
    assert apply_selection(s, [2], SelectOp.SUBTRACT) == {1}
    assert apply_selection(s, [9], SelectOp.REPLACE) == {9}
