"""Side panel: minimal magnetic cell analysis, handoff export, mock AgentDFT submission."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ExportPanel(QWidget):
    analyze = Signal()
    preview_toggled = Signal(bool)
    save_handoff = Signal()
    submit = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.analyze_btn = QPushButton("Analyze magnetic cell")
        self.analyze_btn.setToolTip("Find the minimal magnetic supercell of the current configuration")
        lay.addWidget(self.analyze_btn)
        box = QGroupBox("Minimal magnetic cell")
        bl = QVBoxLayout(box)
        self.summary = QLabel("Not analyzed yet.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(self.summary)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(170)
        bl.addWidget(self.details)
        self.preview = QCheckBox("Show the magnetic cell in the main view (read-only)")
        self.preview.setEnabled(False)
        bl.addWidget(self.preview)
        lay.addWidget(box)
        out = QGroupBox("AgentDFT handoff")
        ol = QVBoxLayout(out)
        self.save_btn = QPushButton("Save handoff JSON...")
        self.submit_btn = QPushButton("Send to AgentDFT (mock)")
        self.submit_btn.setToolTip("Mock backend: validates and stores the handoff, simulates 'started'")
        for b in (self.save_btn, self.submit_btn):
            b.setEnabled(False)
            ol.addWidget(b)
        self.job = QLabel("")
        self.job.setWordWrap(True)
        self.job.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ol.addWidget(self.job)
        lay.addWidget(out)
        lay.addStretch(1)
        self.analyze_btn.clicked.connect(self.analyze.emit)
        self.preview.toggled.connect(self.preview_toggled.emit)
        self.save_btn.clicked.connect(self.save_handoff.emit)
        self.submit_btn.clicked.connect(self.submit.emit)

    def show_result(self, cell, handoff: dict) -> None:
        need = "supercell REQUIRED" if cell.requires_supercell else "no supercell needed"
        rel = cell.describe_matrix()
        self.summary.setText(
            f"<b>{need}</b>: {cell.multiplicity} x chemical primitive cell, {len(cell.species)} sites."
            f"<br>Relative to the input cell: <b>{rel}</b>"
            f"<br>requires_unfolding = {handoff['requires_unfolding']}")
        lines = ["Why:"] + [f"  - {e}" for e in cell.explanation]
        lines += ["", "Patterns:"] + [f"  - {s}" for s in handoff["patterns"]["labels"]]
        lines += ["", f"Supercell matrix (magnetic = S @ reference): {handoff['supercell_matrix']}"]
        if cell.msg and "uni_number" in cell.msg:
            lines.append(f"Magnetic space group (spglib): UNI #{cell.msg['uni_number']}, "
                         f"type {cell.msg['msg_type']}, {cell.msg['n_operations']} operations")
        lines += ["", f"VASP MAGMOM = {handoff['dft_hints']['vasp']['MAGMOM']}"]
        self.details.setPlainText("\n".join(lines))
        for w in (self.preview, self.save_btn, self.submit_btn):
            w.setEnabled(True)

    def invalidate(self) -> None:
        self.summary.setText("Configuration changed: analyze again.")
        self.preview.blockSignals(True)
        self.preview.setChecked(False)
        self.preview.blockSignals(False)
        for w in (self.preview, self.save_btn, self.submit_btn):
            w.setEnabled(False)
