"""Document moment edits (each one undo step) and session files."""

import json

import numpy as np
import pytest

from magview.core import project
from magview.core.document import MagDocument, NonCollinearError
from magview.core.suggest import ElementRuleSuggester


@pytest.fixture
def doc(load):
    d = MagDocument.new(load("NiO"))
    d.accept_suggestions(list(ElementRuleSuggester().suggest(d.crystal).candidates))
    return d


def ni_sites(doc):
    return np.nonzero(doc.canvas_magnetic)[0]


def test_set_moments_collinear_projection(doc):
    s = ni_sites(doc)[:2]
    doc.set_moments(s, [0.5, 0.0, -1.5])
    assert np.allclose(doc.moments[s], [0, 0, -1.5])  # projected on the z spin axis
    doc.history.undo()
    assert np.allclose(doc.moments[s], [0, 0, 2.0])


def test_set_moment_flags_parent_magnetic(load):
    d = MagDocument.new(load("NiO"))
    o_site = int(np.nonzero(np.array(d.crystal.symbols)[d.canvas.parent] == "O")[0][0])
    d.set_moments([o_site], [0, 0, 0.5])
    assert d.magnetic[d.canvas.parent[o_site]]


def test_flip_zero_magnitude_direction(doc):
    s = ni_sites(doc)[:3]
    doc.flip(s)
    assert np.allclose(doc.moments[s], [0, 0, -2.0])
    doc.zero(s[:1])
    assert np.allclose(doc.moments[s[0]], 0)
    doc.set_magnitude(s, 3.0)  # zero moment takes the spin axis, others keep direction
    assert np.allclose(doc.moments[s], [[0, 0, 3.0], [0, 0, -3.0], [0, 0, -3.0]])
    doc.set_collinear(False)
    doc.set_direction(s, [1, 0, 0])  # keeps |m|
    assert np.allclose(doc.moments[s], [3.0, 0, 0])
    for _ in range(5):
        doc.history.undo()
    assert np.allclose(doc.moments[s], [0, 0, 2.0])


def test_apply_signs_uses_preferred_magnitude(doc):
    s = ni_sites(doc)[:4]
    doc.zero(s[:2])
    doc.apply_signs(s, [1, -1, 1, -1], "test")
    assert np.allclose(doc.moments[s, 2], [2.0, -2.0, 2.0, -2.0])  # 2.0 = accepted magnitude


def test_spin_axis_change_keeps_signed_magnitudes(doc):
    s = ni_sites(doc)[:2]
    doc.flip(s[:1])
    doc.set_spin_axis([1, 0, 0])
    assert np.allclose(doc.moments[s], [[-2.0, 0, 0], [2.0, 0, 0]])


def test_collinear_switch_requires_explicit_projection(doc):
    s = ni_sites(doc)[:1]
    doc.set_collinear(False)
    doc.set_moments(s, [1.0, 0, 1.0])
    with pytest.raises(NonCollinearError):
        doc.set_collinear(True)
    assert not doc.collinear and np.allclose(doc.moments[s], [1.0, 0, 1.0])
    doc.set_collinear(True, project=True)
    assert doc.collinear and np.allclose(doc.moments[s], [0, 0, 1.0])
    doc.history.undo()
    assert not doc.collinear and np.allclose(doc.moments[s], [1.0, 0, 1.0])


def test_session_round_trip(doc, tmp_path):
    doc.resize_canvas([[1, 1, 0], [-1, 1, 0], [0, 0, 1]])
    doc.set_collinear(False)
    doc.set_moments(ni_sites(doc)[:1], [1.0, 2.0, 0.5])
    path = project.save(doc, tmp_path / "x.magview.json")
    assert not (tmp_path / "x.magview.json.tmp").exists()
    d2 = project.load(path)
    assert np.allclose(d2.moments, doc.moments) and np.array_equal(d2.canvas.matrix, doc.canvas.matrix)
    assert np.array_equal(d2.magnetic, doc.magnetic) and d2.collinear is False
    assert np.allclose(d2.site_magnitude, doc.site_magnitude)
    assert d2.crystal.symmetry.number == 225 and d2.crystal.labels == doc.crystal.labels


def test_session_errors(tmp_path):
    bad = tmp_path / "bad.magview.json"
    bad.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(project.ProjectError):
        project.load(bad)
    bad.write_text("{not json")
    with pytest.raises(project.ProjectError):
        project.load(bad)


def test_autosave_dir(doc, tmp_path, monkeypatch):
    monkeypatch.setenv(project.ENV_AUTOSAVE_DIR, str(tmp_path / "auto"))
    path = project.autosave(doc)
    assert path.parent == tmp_path / "auto" and path.name == "NiO.magview.json"
    assert np.allclose(project.load(path).moments, doc.moments)
