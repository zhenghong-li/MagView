"""Headless widget tests for the GUI side panels (Qt offscreen platform, no GL).

The panels are plain widgets (no QtInteractor), so they run with
QT_QPA_PLATFORM=offscreen. Skipped with a reason when Qt cannot start.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest


@pytest.fixture(scope="session")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    try:
        app = QApplication.instance() or QApplication(["magview-panel-tests"])
        app.processEvents()
    except Exception as exc:
        pytest.skip(f"Qt cannot start headlessly: {exc}")
    return app


@pytest.fixture()
def panel(qapp):
    from magview.gui.panels.moment_panel import MomentPanel

    p = MomentPanel()
    yield p
    p.close()
    p.deleteLater()


AX = np.array([0.0, 0.0, 1.0])
LAT = np.eye(3) * 8.0


def test_moment_panel_uniform_selection_stamps_fields(qapp, panel):
    moments = np.array([[0.0, 0.0, 2.0], [0.0, 0.0, 2.0]])
    panel.set_selection(moments, LAT, True, AX)
    assert not panel._mixed
    assert panel.apply_btn.isEnabled() and panel.mag_only_btn.isEnabled()
    assert panel.mag.value() == pytest.approx(2.0)
    assert panel.signed.value() == pytest.approx(2.0)


def test_moment_panel_mixed_selection_blanks_fields(qapp, panel):
    """A mixed selection must not keep the previous selection's values (they would
    stamp onto every site on Apply)."""
    panel.set_selection(np.array([[0.0, 0.0, 2.0]]), LAT, True, AX)  # uniform first
    assert panel.signed.value() == pytest.approx(2.0)
    panel.set_selection(np.array([[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]]), LAT, True, AX)
    assert panel._mixed
    assert panel.signed.value() == panel.signed.minimum()  # displays "mixed", not 2.0
    assert not panel.apply_btn.isEnabled() and not panel.mag_only_btn.isEnabled()
    # Batch operations that do not read the fields stay available.
    assert panel.flip_btn.isEnabled() and panel.zero_btn.isEnabled()


def test_moment_panel_mixed_apply_requires_touched_field(qapp, panel):
    panel.set_selection(np.array([[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]]), LAT, True, AX)
    sent = []
    panel.apply_signed.connect(sent.append)
    panel._apply()  # nothing entered: refused
    assert sent == []
    panel.signed.setValue(-3.0)  # user enters a value -> touched
    panel._apply()
    assert sent == [-3.0]
    # "Set |m| only" needs the magnitude field touched.
    mags = []
    panel.apply_magnitude.connect(mags.append)
    panel.mag.setValue(1.2)
    assert panel.mag_only_btn.isEnabled()
    panel.mag_only_btn.click()
    assert mags == [1.2]


def test_moment_panel_mixed_noncollinear_needs_all_components(qapp, panel):
    panel.collinear.setChecked(False)
    panel.set_selection(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), LAT, False, AX)
    assert panel._mixed
    assert not panel.apply_btn.isEnabled()
    panel.mag.setValue(2.0)
    assert not panel.apply_btn.isEnabled()  # theta/phi still untouched
    panel.theta.setValue(90.0)
    panel.phi.setValue(45.0)
    assert panel.apply_btn.isEnabled()
    vecs = []
    panel.apply_vector.connect(vecs.append)
    panel._apply()
    assert len(vecs) == 1 and np.allclose(np.linalg.norm(vecs[0]), 2.0)


def test_moment_panel_leaving_mixed_restores_fields(qapp, panel):
    panel.set_selection(np.array([[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]]), LAT, True, AX)
    assert panel._mixed
    panel.set_selection(np.array([[0.0, 0.0, 1.0]]), LAT, True, AX)
    assert not panel._mixed and panel.apply_btn.isEnabled()
    assert panel.signed.specialValueText() == "" and panel.signed.value() == pytest.approx(1.0)
    panel.set_selection(np.zeros((0, 3)), LAT, True, AX)
    assert not panel.apply_btn.isEnabled()


# -- Layer panel: content diffing (no delete/recreate flicker) ------------------------


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _match(status="matches", reason="ok", labels=("Neel", "FM")):
    cands = [_ns(pattern=_ns(label=lab, key=lab.lower()), explanation=f"why {lab}")
             for lab in labels]
    return _ns(status=status, reason=reason, candidates=cands,
               excluded=[_ns(pattern=_ns(label="G", key="g"), reason="not bipartite")])


def test_layer_panel_match_buttons_are_diffed(qapp):
    from magview.gui.panels.layer_panel import LayerPanel

    p = LayerPanel()
    try:
        p.set_match("square", _match())
        first = list(p.candidate_buttons)
        p.set_match("square", _match())  # identical content: buttons are kept
        assert p.candidate_buttons == first
        p.set_match("square", _match(reason="different reason"))
        assert p.candidate_buttons != first  # changed content rebuilds
        p.set_match("square", _match(labels=("Neel",)))
        assert len(p.candidate_buttons) == 1
    finally:
        p.close()
        p.deleteLater()


def test_layer_panel_stacking_buttons_are_diffed(qapp):
    from magview.core.stacking import stacking_library
    from magview.gui.panels.layer_panel import LayerPanel

    p = LayerPanel()
    try:
        ref_ok = _ns(ok=True, reason="", signs=[np.ones(4, int)] * 2)
        match = _ns(relations=[1, -1], status="matches", reason="fits",
                    candidates=[_ns(key="afm")], excluded=[(_ns(key="fm"), "disagrees")])
        p.set_stacking("Ni", 2, ref_ok, match)
        first = dict(p.stack_buttons)
        p.set_stacking("Ni", 2, ref_ok, match)
        assert p.stack_buttons == first  # identical: kept
        assert len(first) == len(stacking_library(2))
        p.set_stacking("Ni", 2, _ns(ok=False, reason="frustrated"), None)
        assert p.stack_buttons == {}  # no reference: cleared once
    finally:
        p.close()
        p.deleteLater()
