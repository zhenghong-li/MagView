"""Side panel for the layer editor: axis, layer list, lattice info, matcher suggestions."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from magview.core.patterns import MatchResult


class _HoverButton(QPushButton):
    hovered = Signal(int)
    left = Signal()

    def __init__(self, text: str, index: int) -> None:
        super().__init__(text)
        self.index = index

    def event(self, e) -> bool:  # noqa: D401 (Qt override)
        if e.type() == QEvent.Enter:
            self.hovered.emit(self.index)
        elif e.type() == QEvent.Leave:
            self.left.emit()
        return super().event(e)


class LayerPanel(QWidget):
    axis_changed = Signal(str)
    layer_selected = Signal(int)
    apply_candidate = Signal(int)
    preview_candidate = Signal(int)  # -1 ends the preview
    clear_layer = Signal()
    set_selected = Signal(int)  # +1 / -1: pin the selected sites of this layer

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        top = QFormLayout()
        self.axis = QComboBox()
        self.axis.addItems(["c", "a", "b"])
        self.axis.setToolTip("Layers are stacked along this lattice axis")
        top.addRow("Stacking axis", self.axis)
        lay.addLayout(top)

        self.layers = QListWidget()
        self.layers.setMaximumHeight(150)
        lay.addWidget(QLabel("Layers (grouped by element; PgUp/PgDn to step)"))
        lay.addWidget(self.layers)
        self.lattice = QLabel("")
        self.lattice.setWordWrap(True)
        lay.addWidget(self.lattice)

        legend = QLabel("<span style='color:#d6301f'><b>+</b> red = up</span>, "
                        "<span style='color:#2166bf'><b>-</b> blue = down</span>, gray = unset. "
                        "Ring = pinned by a click. Click: up / down; right-click: unset.")
        legend.setWordWrap(True)
        lay.addWidget(legend)

        self.match_box = QGroupBox("Pattern completion (rule-based)")
        self.match_layout = QVBoxLayout(self.match_box)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.match_layout.addWidget(self.status)
        self.cand_box = QVBoxLayout()
        self.match_layout.addLayout(self.cand_box)
        self.excluded = QLabel("")
        self.excluded.setWordWrap(True)
        self.excluded.setStyleSheet("color: #777")
        self.match_layout.addWidget(self.excluded)
        lay.addWidget(self.match_box)

        row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear layer")
        self.up_btn = QPushButton("Selected: up")
        self.down_btn = QPushButton("Selected: down")
        row.addWidget(self.clear_btn)
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        lay.addLayout(row)
        lay.addStretch(1)
        self.stacking_slot = QVBoxLayout()  # filled by Phase 4
        lay.addLayout(self.stacking_slot)

        self.axis.currentTextChanged.connect(self.axis_changed.emit)
        self.layers.currentRowChanged.connect(lambda r: r >= 0 and self.layer_selected.emit(r))
        self.clear_btn.clicked.connect(self.clear_layer.emit)
        self.up_btn.clicked.connect(lambda: self.set_selected.emit(1))
        self.down_btn.clicked.connect(lambda: self.set_selected.emit(-1))
        self.candidate_buttons: list[_HoverButton] = []

    def set_layers(self, labels: list[str], current: int) -> None:
        self.layers.blockSignals(True)
        self.layers.clear()
        self.layers.addItems(labels)
        if labels:
            self.layers.setCurrentRow(current)
        self.layers.blockSignals(False)

    def set_current(self, row: int) -> None:
        self.layers.blockSignals(True)
        self.layers.setCurrentRow(row)
        self.layers.blockSignals(False)

    def set_match(self, lattice_text: str, result: MatchResult) -> None:
        self.lattice.setText(f"<b>Lattice:</b> {lattice_text}")
        for b in self.candidate_buttons:
            self.cand_box.removeWidget(b)
            b.deleteLater()
        self.candidate_buttons = []
        status = {"idle": "Waiting", "no_match": "No match", "matches": "Suggestions"}[result.status]
        self.status.setText(f"<b>{status}:</b> {result.reason}")
        for k, cand in enumerate(result.candidates):
            b = _HoverButton(f"Apply: {cand.pattern.label}", k)
            b.setToolTip(cand.explanation)
            b.clicked.connect(lambda _=False, k=k: self.apply_candidate.emit(k))
            b.hovered.connect(self.preview_candidate.emit)
            b.left.connect(lambda: self.preview_candidate.emit(-1))
            self.cand_box.addWidget(b)
            self.candidate_buttons.append(b)
        if result.excluded and result.status != "idle":
            self.excluded.setText("Excluded: " + "; ".join(
                f"{e.pattern.label} ({e.reason})" for e in result.excluded))
        else:
            self.excluded.setText("")
