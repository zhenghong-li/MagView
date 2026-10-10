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
from magview.core.stacking import Reference, StackingMatch, stacking_library


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
    apply_stacking = Signal(str)  # stacking pattern key
    preview_stacking = Signal(str)  # "" ends the preview
    flip_layer = Signal()

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
        self.stack_box = QGroupBox("Stacking between layers (rule-based)")
        sl = QVBoxLayout(self.stack_box)
        self.stack_ref = QLabel("")
        self.stack_ref.setWordWrap(True)
        self.stack_rel = QLabel("")
        self.stack_rel.setWordWrap(True)
        self.stack_status = QLabel("")
        self.stack_status.setWordWrap(True)
        sl.addWidget(self.stack_ref)
        sl.addWidget(self.stack_rel)
        sl.addWidget(self.stack_status)
        self.stack_buttons_box = QVBoxLayout()
        sl.addLayout(self.stack_buttons_box)
        self.flip_btn = QPushButton("Flip this layer (custom per-layer flip)")
        sl.addWidget(self.flip_btn)
        lay.addWidget(self.stack_box)
        lay.addStretch(1)
        self.flip_btn.clicked.connect(self.flip_layer.emit)
        self.stack_buttons: dict[str, _HoverButton] = {}

        self.axis.currentTextChanged.connect(self.axis_changed.emit)
        self.layers.currentRowChanged.connect(lambda r: r >= 0 and self.layer_selected.emit(r))
        self.clear_btn.clicked.connect(self.clear_layer.emit)
        self.up_btn.clicked.connect(lambda: self.set_selected.emit(1))
        self.down_btn.clicked.connect(lambda: self.set_selected.emit(-1))
        self.candidate_buttons: list[_HoverButton] = []
        # Content signatures: unchanged content skips widget rebuilds (hover previews
        # refresh the panel every move; delete/recreate flickers).
        self._match_sig: tuple | None = None
        self._stack_sig: tuple | None = None

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
        sig = (lattice_text, result.status, result.reason,
               tuple((c.pattern.label, c.explanation) for c in result.candidates),
               tuple((e.pattern.label, e.reason) for e in result.excluded))
        if sig == self._match_sig:
            return  # identical content: keep the existing buttons (no flicker)
        self._match_sig = sig
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

    def set_stacking(self, element: str, n_layers: int, ref: Reference, match: StackingMatch | None) -> None:
        if not ref.ok:
            sig = (element, n_layers, False, ref.reason)
        else:
            rels = tuple(match.relations) if match else ()
            sig = (element, n_layers, True, rels, match.status, match.reason,
                   tuple(sorted(p.key for p in match.candidates)),
                   tuple(sorted((p.key, r) for p, r in match.excluded)),
                   tuple((p.key, p.label, invalid) for p, invalid in stacking_library(len(rels))))
        if sig == self._stack_sig:
            return  # identical content: keep the existing buttons (no flicker)
        self._stack_sig = sig
        for b in self.stack_buttons.values():
            self.stack_buttons_box.removeWidget(b)
            b.deleteLater()
        self.stack_buttons = {}
        self.stack_box.setTitle(f"Stacking of the {n_layers} {element} layer(s) (reference: layer 0)")
        if not ref.ok:
            self.stack_ref.setText(f"<b>No reference:</b> {ref.reason}. Use per-layer flips.")
            self.stack_rel.setText("")
            self.stack_status.setText("")
            return
        self.stack_ref.setText(f"Reference = layer 0 transported along inter-layer bonds "
                               f"(FM stacking).")
        names = {1: "same", -1: "flipped", 0: "other order", None: "unset"}
        rels = match.relations if match else ()
        self.stack_rel.setText("Layers: " + ", ".join(
            f"{k}: {'ref' if k == 0 else names[r]}" for k, r in enumerate(rels)))
        status = {"idle": "Waiting", "no_match": "No match", "matches": "Suggestions"}[match.status]
        self.stack_status.setText(f"<b>{status}:</b> {match.reason}")
        suggested = {p.key for p in match.candidates}
        reasons = {p.key: r for p, r in match.excluded}
        for k, (pat, invalid) in enumerate(stacking_library(len(rels))):
            star = "  <- suggested" if pat.key in suggested else ""
            b = _HoverButton(f"Apply {pat.label}{star}", k)
            b.setEnabled(invalid is None)
            b.setToolTip(invalid or reasons.get(pat.key, "consistent with the assigned layers"))
            if pat.key in suggested:
                b.setStyleSheet("font-weight: bold")
            b.clicked.connect(lambda _=False, key=pat.key: self.apply_stacking.emit(key))
            b.hovered.connect(lambda _k, key=pat.key: self.preview_stacking.emit(key))
            b.left.connect(lambda: self.preview_stacking.emit(""))
            self.stack_buttons_box.addWidget(b)
            self.stack_buttons[pat.key] = b
