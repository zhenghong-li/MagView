"""Side panel: suggested magnetic sites (accept, edit magnitude, or skip)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from magview.core.suggest import SiteSuggestion, SuggestionReport

COLUMNS = ["Use", "Orbit", "Sites", "Start |m| (muB)", "Confidence", "Why"]
CONFIDENCE_COLORS = {"high": "#2e7d32", "medium": "#b26a00", "low": "#b71c1c"}


class SuggestionPanel(QWidget):
    # (list[SiteSuggestion], {orbit: magnitude}); 'object' so PySide passes the dict through.
    accepted = Signal(object, object)
    skipped = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.report: SuggestionReport | None = None
        self._spins: dict[int, QDoubleSpinBox] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.header = QLabel("Open a structure (File > Open, Ctrl+O) to get magnetic-site "
                             "suggestions.<br>F1 lists every key and mouse gesture.")
        self.header.setWordWrap(True)
        lay.addWidget(self.header)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        hh = self.table.horizontalHeader()
        for c in range(len(COLUMNS) - 1):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.accept_btn = QPushButton("Accept checked")
        self.accept_btn.setToolTip("Mark the checked orbits magnetic and start them ferromagnetic "
                                   "along the spin axis (one undo step)")
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setToolTip("Ignore the suggestions; assign magnetic sites manually")
        buttons.addWidget(self.accept_btn)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.accept_btn.clicked.connect(self._accept)
        self.skip_btn.clicked.connect(self._skip)
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        self.accept_btn.setEnabled(on)
        self.skip_btn.setEnabled(on)

    def set_report(self, report: SuggestionReport) -> None:
        self.report = report
        self._spins.clear()
        oxi = report.oxidation_states
        oxi_txt = ", ".join(f"{k} {v:+.2f}".rstrip("0").rstrip(".") for k, v in oxi.items()) if oxi else "none found"
        self.header.setText(f"<b>Suggested magnetic sites</b> ({report.method})<br>"
                            f"Oxidation-state guess: {oxi_txt}")
        self.table.setRowCount(len(report.items))
        for row, s in enumerate(report.items):
            use = QTableWidgetItem()
            if s.candidate:
                use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                use.setCheckState(Qt.Checked if s.default_on else Qt.Unchecked)
            else:
                use.setFlags(Qt.NoItemFlags)
            self.table.setItem(row, 0, use)
            self.table.setItem(row, 1, self._ro(s.label))
            self.table.setItem(row, 2, self._ro(str(len(s.sites))))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 10.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(1)
            spin.setValue(s.magnitude)
            spin.setEnabled(s.candidate)
            self.table.setCellWidget(row, 3, spin)
            self._spins[s.orbit] = spin
            conf = self._ro(s.confidence)
            conf.setForeground(QColor(CONFIDENCE_COLORS.get(s.confidence, "#000")))
            self.table.setItem(row, 4, conf)
            why = self._ro(s.rationale)
            why.setToolTip(s.rationale)
            self.table.setItem(row, 5, why)
            if not s.candidate:
                for c in (1, 2, 4, 5):
                    self.table.item(row, c).setForeground(QColor("#888"))
        self.table.resizeRowsToContents()
        n = len(report.candidates)
        self.status.setText(f"{n} candidate orbit(s), {len(report.excluded)} excluded."
                            if report.items else "No magnetic candidates found in this structure.")
        self._set_enabled(bool(report.candidates))

    @staticmethod
    def _ro(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    def checked(self) -> list[SiteSuggestion]:
        if self.report is None:
            return []
        out = []
        for row, s in enumerate(self.report.items):
            item = self.table.item(row, 0)
            if s.candidate and item.checkState() == Qt.Checked:
                out.append(s)
        return out

    def magnitudes(self) -> dict[int, float]:
        return {orbit: spin.value() for orbit, spin in self._spins.items()}

    def _accept(self) -> None:
        items = self.checked()
        if not items:
            self.status.setText("Nothing checked.")
            return
        self.accepted.emit(items, self.magnitudes())
        self.status.setText("Accepted: " + ", ".join(s.label for s in items) + ". Undo with Ctrl+Z.")

    def _skip(self) -> None:
        self.skipped.emit()
        self.status.setText("Skipped. Assign magnetic sites manually (Phase 2 tools).")
