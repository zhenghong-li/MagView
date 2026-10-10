"""Editing canvas, undo history and the magnetic document."""

import numpy as np
import pytest

from magview.core.canvas import Canvas, CanvasResizeError, lattice_points_in_cell
from magview.core.document import MagDocument
from magview.core.history import FunctionCommand, History, MacroCommand
from magview.core.suggest import ElementRuleSuggester

SQRT2 = [[1, 1, 0], [-1, 1, 0], [0, 0, 1]]  # sqrt(2) x sqrt(2) rotated cell


# --- canvas ---------------------------------------------------------------------

@pytest.mark.parametrize("matrix, det", [((2, 2, 2), 8), ((3, 3, 1), 9), (SQRT2, 2),
                                         ([[2, 1, 0], [0, 1, 0], [0, 0, 1]], 2)])
def test_lattice_points(matrix, det):
    m = np.diag(matrix) if np.ndim(matrix) == 1 else np.array(matrix)
    pts = lattice_points_in_cell(m)
    assert len(pts) == det
    f = pts @ np.linalg.inv(m)
    assert np.all((f > -1e-9) & (f < 1 - 1e-9))


@pytest.mark.parametrize("name", ["NiO", "Cs3V9Te13_300K", "toy_square_CuO2"])
def test_canvas_mapping(load, name):
    c = load(name)
    cv = Canvas.build(c)
    assert len(cv) == 8 * len(c) and cv.n_cells == 8
    # canvas site position == parent position + R (mod the canvas lattice)
    pos = (c.frac_coords[cv.parent] + cv.translation) @ c.lattice_matrix
    frac_in_canvas = pos @ np.linalg.inv(cv.structure.lattice_matrix)
    d = frac_in_canvas - cv.structure.frac_coords
    assert np.allclose(d - np.rint(d), 0, atol=1e-8)
    assert cv.structure.symbols == tuple(c.symbols[p] for p in cv.parent)
    # every (parent, R) pair round-trips, including R outside the canvas
    for i in range(0, len(cv), 7):
        p, r = cv.parent[i], cv.translation[i]
        assert cv.index_of(p, r) == i
        assert cv.index_of(p, r + np.array([2, -2, 4])) == i


def test_non_diagonal_canvas(load):
    c = load("toy_square_CuO2")
    cv = Canvas.build(c, SQRT2)
    assert len(cv) == 2 * len(c)
    assert cv.structure.structure.volume == pytest.approx(2 * c.structure.volume)
    assert not cv.is_diagonal


def test_resize_extends_and_refuses(load):
    c = load("toy_square_CuO2")
    cu = np.array([s == "Cu" for s in c.symbols])
    small = Canvas.build(c, (1, 1, 1))
    big = Canvas.build(c, (2, 2, 1))
    m_small = np.where(cu[:, None], [0, 0, 1.0], 0.0)
    m_big = small.resize_moments(big, m_small)  # FM extends periodically
    assert np.allclose(m_big, np.where(cu[big.parent][:, None], [0, 0, 1.0], 0.0))
    # stripe along a: sign alternates with R_a -> cannot shrink to 1x1x1
    stripe = m_big * np.where(big.translation[:, 0] % 2 == 0, 1, -1)[:, None]
    with pytest.raises(CanvasResizeError, match="cannot hold"):
        big.resize_moments(small, stripe)
    # Neel (checkerboard) fits the sqrt2 x sqrt2 cell exactly, and comes back unchanged
    neel = m_big * np.where((big.translation[:, 0] + big.translation[:, 1]) % 2 == 0, 1, -1)[:, None]
    rot = Canvas.build(c, SQRT2)
    m_rot = big.resize_moments(rot, neel)
    assert np.allclose(rot.resize_moments(big, m_rot), neel)


# --- history --------------------------------------------------------------------

def test_history_undo_redo():
    box = {"v": 0}
    h = History(box)

    def setter(val):
        old = {}

        def do(t):
            old["v"] = t["v"]
            t["v"] = val

        return FunctionCommand(f"set {val}", do, lambda t: t.__setitem__("v", old["v"]))

    h.push(setter(1))
    h.push(setter(2))
    assert box["v"] == 2 and h.undo_label == "set 2"
    assert h.undo() == "set 2" and box["v"] == 1
    assert h.redo() == "set 2" and box["v"] == 2
    h.undo()
    h.push(setter(5))  # a new edit clears the redo stack
    assert not h.can_redo and box["v"] == 5
    assert h.undo() == "set 5" and h.undo() == "set 1" and h.undo() is None and box["v"] == 0


def test_history_failed_command_not_recorded():
    h = History(object())

    def boom(_):
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        h.push(FunctionCommand("bad", boom, lambda t: None))
    assert not h.can_undo


def test_macro_reverts_in_reverse_order():
    log = []
    cmds = [FunctionCommand(str(i), lambda t, i=i: log.append(f"do{i}"),
                            lambda t, i=i: log.append(f"undo{i}")) for i in range(3)]
    h = History(None)
    h.push(MacroCommand("all", cmds))
    h.undo()
    assert log == ["do0", "do1", "do2", "undo2", "undo1", "undo0"]


def test_history_limit_and_notify():
    calls = []
    h = History(None, limit=3)
    h.subscribe(lambda: calls.append(1))
    for i in range(5):
        h.push(FunctionCommand(str(i), lambda t: None, lambda t: None))
    assert len(calls) == 5
    n = 0
    while h.undo():
        n += 1
    assert n == 3


# --- document -------------------------------------------------------------------

def test_accept_suggestions_and_undo(load):
    c = load("NiO")
    doc = MagDocument.new(c)
    report = ElementRuleSuggester().suggest(c)
    ni = report.candidates[0]
    doc.accept_suggestions([ni], {ni.orbit: 1.5})
    assert doc.magnetic.sum() == 4
    m = np.linalg.norm(doc.moments, axis=1)
    on = doc.canvas_magnetic
    assert np.allclose(m[on], 1.5) and np.allclose(m[~on], 0)
    assert np.allclose(doc.moments[on], [0, 0, 1.5])  # FM along the spin axis
    doc.history.undo()
    assert doc.magnetic.sum() == 0 and not doc.moments.any()
    doc.history.redo()
    assert doc.magnetic.sum() == 4


def test_failed_resize_changes_nothing(load):
    c = load("toy_square_CuO2")
    doc = MagDocument.new(c, (2, 2, 1))
    stripe = np.where(doc.canvas.translation[:, 0] % 2 == 0, 1.0, -1.0)
    doc.moments[:, 2] = stripe * (np.array(c.symbols)[doc.canvas.parent] == "Cu")
    snapshot = doc.moments.copy()
    with pytest.raises(CanvasResizeError):
        doc.resize_canvas((1, 1, 1))
    assert len(doc.canvas) == 4 * len(c) and np.allclose(doc.moments, snapshot)
    assert not doc.history.can_undo
    doc.resize_canvas((4, 2, 1))  # a multiple is fine
    assert len(doc.canvas) == 8 * len(c)
    doc.history.undo()
    assert np.allclose(doc.moments, snapshot)


def test_resize_shrink_keeps_pins(load):
    """FM 2x2x2 canvas; a pin in cell [0,0,1] must survive shrinking to 1x1x1
    (the pin's old site folds onto a kept site, so the new site is pinned)."""
    doc = MagDocument.new(load("toy_square_CuO2"), (2, 2, 2))
    doc.accept_suggestions(list(ElementRuleSuggester().suggest(doc.crystal).candidates))
    site = int(np.nonzero(np.all(doc.canvas.translation == [0, 0, 1], axis=1))[0][0])
    parent = int(doc.canvas.parent[site])
    doc.layer_set([site], 1)
    doc.resize_canvas((1, 1, 1))
    assert doc.pinned.sum() == 1
    assert doc.pinned[int(np.nonzero(doc.canvas.parent == parent)[0][0])]
    doc.history.undo()  # undo the resize: the original pin is back, nothing duplicated
    assert doc.pinned.sum() == 1 and doc.pinned[site]


def test_site_label(load):
    doc = MagDocument.new(load("Cs3V9Te13_300K"))
    i = int(np.nonzero(doc.canvas.translation[:, 0] == 1)[0][0])
    assert "cell [1, 0, 0]" in doc.site_label(i)


def test_history_revision_tracks_state_changes():
    """revision changes exactly when the document state changes (push/undo/redo); the
    GUI uses it to discard background results computed for an older state."""
    h = History(None)
    assert h.revision == 0
    h.push(FunctionCommand("a", lambda t: None, lambda t: None))
    assert h.revision == 1
    assert h.undo() == "a" and h.revision == 2
    assert h.undo() is None and h.revision == 2  # nothing to undo: unchanged
    assert h.redo() == "a" and h.revision == 3

    def boom(_):
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        h.push(FunctionCommand("bad", boom, lambda t: None))
    assert h.revision == 3  # a failed edit changed nothing
