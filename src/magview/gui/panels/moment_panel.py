"""Side panel: moments of the selection, batch operations, model and arrow settings."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from magview.core import magnetic as mg
from magview.render.arrows import COLOR_MODES

AXES = ("z", "x", "y", "c", "a", "b")


def _spin(lo: float, hi: float, step: float, decimals: int, value: float = 0.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setValue(value)
    s.setKeyboardTracking(False)
    return s


class MomentPanel(QWidget):
    apply_vector = Signal(object)  # np.ndarray (3,), mu_B
    apply_signed = Signal(float)  # collinear: signed |m| along the spin axis
    apply_magnitude = Signal(float)  # keep directions
    fm = Signal(int)  # +1 / -1
    flip = Signal()
    zero = Signal()
    patterns = Signal()
    collinear_toggled = Signal(bool)
    spin_axis_changed = Signal(str)
    arrows_changed = Signal()
    auto_fit = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        sel = QGroupBox("Selection")
        sl = QVBoxLayout(sel)
        self.summary = QLabel("Nothing selected.")
        self.summary.setWordWrap(True)
        sl.addWidget(self.summary)

        form = QFormLayout()
        self.signed = _spin(-mg.MAX_MOMENT, mg.MAX_MOMENT, 0.1, 2)
        self.mag = _spin(0, mg.MAX_MOMENT, 0.1, 2)
        self.theta = _spin(0, 180, 5, 1)
        self.phi = _spin(0, 360, 5, 1)
        self.signed_row = QLabel("m along axis (muB)")
        form.addRow(self.signed_row, self.signed)
        form.addRow("|m| (muB)", self.mag)
        form.addRow("theta (deg from +z)", self.theta)
        form.addRow("phi (deg from +x)", self.phi)
        sl.addLayout(form)
        row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply to selection")
        self.mag_only_btn = QPushButton("Set |m| only")
        self.mag_only_btn.setToolTip("Keep each direction, set the magnitude")
        row.addWidget(self.apply_btn)
        row.addWidget(self.mag_only_btn)
        sl.addLayout(row)

        grid = QGridLayout()
        self.up_btn = QPushButton("FM up")
        self.down_btn = QPushButton("FM down")
        self.flip_btn = QPushButton("Flip  (F)")
        self.zero_btn = QPushButton("Zero  (0)")
        self.patterns_btn = QPushButton("Patterns...  (P)")
        self.patterns_btn.setToolTip("FM/AFM patterns that fit the selection (one click to apply)")
        for k, b in enumerate((self.up_btn, self.down_btn, self.flip_btn, self.zero_btn)):
            grid.addWidget(b, k // 2, k % 2)
        grid.addWidget(self.patterns_btn, 2, 0, 1, 2)
        sl.addLayout(grid)
        lay.addWidget(sel)

        model = QGroupBox("Moment model")
        ml = QFormLayout(model)
        self.collinear = QCheckBox("Collinear (up/down along the spin axis)")
        self.collinear.setChecked(True)
        self.axis = QComboBox()
        self.axis.addItems(AXES)
        ml.addRow(self.collinear)
        ml.addRow("Spin axis", self.axis)
        lay.addWidget(model)

        arrows = QGroupBox("Arrows")
        al = QFormLayout(arrows)
        self.scale = _spin(0.05, 5.0, 0.1, 2, 1.0)
        self.anchor = QComboBox()
        self.anchor.addItems(["centered on atom", "tail at atom"])
        self.color_mode = QComboBox()
        self.color_mode.addItems(COLOR_MODES)
        self.fit_btn = QPushButton("Auto-fit scale")
        al.addRow("Scale (A per muB)", self.scale)
        al.addRow("Anchor", self.anchor)
        al.addRow("Color", self.color_mode)
        al.addRow(self.fit_btn)
        lay.addWidget(arrows)
        lay.addStretch(1)

        self.apply_btn.clicked.connect(self._apply)
        self.mag_only_btn.clicked.connect(lambda: self.apply_magnitude.emit(self.mag.value()))
        self.up_btn.clicked.connect(lambda: self.fm.emit(1))
        self.down_btn.clicked.connect(lambda: self.fm.emit(-1))
        self.flip_btn.clicked.connect(self.flip.emit)
        self.zero_btn.clicked.connect(self.zero.emit)
        self.patterns_btn.clicked.connect(self.patterns.emit)
        self.collinear.toggled.connect(self.collinear_toggled.emit)
        self.axis.currentTextChanged.connect(self.spin_axis_changed.emit)
        self.scale.valueChanged.connect(lambda _: self.arrows_changed.emit())
        self.anchor.currentIndexChanged.connect(lambda _: self.arrows_changed.emit())
        self.color_mode.currentIndexChanged.connect(lambda _: self.arrows_changed.emit())
        self.fit_btn.clicked.connect(self.auto_fit.emit)
        # Mixed selections show "mixed" in every field; only fields the user touched
        # afterwards may be applied, so stale values never stamp onto all sites.
        self._n_selected = 0
        self._mixed = False
        self._touched: set[QDoubleSpinBox] = set()
        for w in (self.signed, self.mag, self.theta, self.phi):
            w.valueChanged.connect(lambda _v, w=w: self._field_touched(w))
        self.collinear.toggled.connect(lambda _on: self._update_apply_state())
        self.set_collinear_ui(True)
        self.set_selection(np.zeros((0, 3)), np.eye(3), True, np.array([0, 0, 1.0]))

    # -- state from the window ---------------------------------------------------
    def set_collinear_ui(self, collinear: bool) -> None:
        for w in (self.signed, self.signed_row):
            w.setVisible(collinear)
        for w in (self.theta, self.phi):
            w.setEnabled(not collinear)

    def set_selection(self, moments: np.ndarray, lattice: np.ndarray, collinear: bool,
                      axis: np.ndarray) -> None:
        n = len(moments)
        self._n_selected = n
        for b in (self.up_btn, self.down_btn, self.flip_btn,
                  self.zero_btn, self.patterns_btn):
            b.setEnabled(n > 0)
        if n == 0:
            self._set_mixed(False)
            self.summary.setText("Nothing selected. Click an atom, or long-press and drag "
                                 "to box-select.")
            return
        same = np.allclose(moments, moments[0], atol=1e-6)
        if not same:
            mags = np.linalg.norm(moments, axis=1)
            self.summary.setText(f"<b>{n} sites</b>: mixed moments, |m| from {mags.min():.2f} "
                                 f"to {mags.max():.2f} muB - enter a value to apply to all")
            self._set_mixed(True)
            return
        self._set_mixed(False)
        m = moments[0]
        r, th, ph = mg.spherical(m)
        abc = mg.crystal_components(m, lattice)
        self.summary.setText(
            f"<b>{n} site{'s' if n > 1 else ''}</b>: m = ({m[0]:.2f}, {m[1]:.2f}, {m[2]:.2f}) muB<br>"
            f"|m| = {r:.2f} muB, theta = {th:.1f}, phi = {ph:.1f}<br>"
            f"along a,b,c unit vectors: ({abc[0]:.2f}, {abc[1]:.2f}, {abc[2]:.2f})")
        for w, v in ((self.mag, r), (self.theta, th), (self.phi, ph),
                     (self.signed, float(m @ mg.unit(axis)))):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)

    # -- mixed-selection fields ----------------------------------------------------
    def _set_mixed(self, on: bool) -> None:
        """For mixed selections every field shows "mixed" (special-value text) instead of
        the previous selection's stale value."""
        self._mixed = on
        self._touched.clear()
        for w in (self.signed, self.mag, self.theta, self.phi):
            w.blockSignals(True)
            w.setSpecialValueText("mixed" if on else "")
            if on:
                w.setValue(w.minimum())
            w.blockSignals(False)
        self._update_apply_state()

    def _field_touched(self, field: QDoubleSpinBox) -> None:
        if self._mixed:
            self._touched.add(field)
            self._update_apply_state()

    def _update_apply_state(self) -> None:
        n = self._n_selected
        if not self._mixed:
            ready = mag_only = n > 0
            tip = ""
        else:
            if self.collinear.isChecked():
                ready = self.signed in self._touched
            else:  # a full vector is applied: every component must be entered explicitly
                ready = all(w in self._touched for w in (self.mag, self.theta, self.phi))
            mag_only = self.mag in self._touched
            tip = "Mixed selection: enter a value first"
        for b, on, default in ((self.apply_btn, ready, ""), (self.mag_only_btn, mag_only,
                                                             "Keep each direction, set the magnitude")):
            b.setEnabled(bool(on))
            b.setToolTip(default if on else tip)

    def _apply(self) -> None:
        if self._mixed:
            # Only explicitly entered values may be applied to a mixed selection.
            if self.collinear.isChecked():
                if self.signed not in self._touched:
                    return
            elif not all(w in self._touched for w in (self.mag, self.theta, self.phi)):
                return
        if self.collinear.isChecked():
            self.apply_signed.emit(self.signed.value())
        else:
            self.apply_vector.emit(mg.from_spherical(self.mag.value(), self.theta.value(),
                                                     self.phi.value()))
