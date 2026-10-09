"""Phase 0 GUI shell: main viewport, small reference viewport, open file, view buttons."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor  # noqa: E402

from magview import __version__  # noqa: E402
from magview.core.structure import CrystalStructure, StructureLoadError  # noqa: E402
from magview.render.camera import VIEWS  # noqa: E402
from magview.render.model import build_scene_model  # noqa: E402
from magview.render.scene import StructureScene  # noqa: E402
from magview.style.appearance import Appearance  # noqa: E402

FILE_FILTER = "Structures (*.cif *.vasp *.poscar POSCAR* CONTCAR*);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self, appearance: Appearance | None = None) -> None:
        super().__init__()
        self.appearance = appearance or Appearance.load()
        self.crystal: CrystalStructure | None = None
        self.setWindowTitle(f"MagView {__version__}")
        self.resize(1280, 820)

        # Main 3D view.
        self.viewport = QtInteractor(self)
        self.setCentralWidget(self.viewport.interactor)
        self.scene = StructureScene(self.viewport, self.appearance.theme)

        # Reference view: the full unit cell, always visible beside the editor.
        self.reference = QtInteractor(self)
        self.reference.setMinimumSize(260, 220)
        self.ref_scene = StructureScene(self.reference, self.appearance.theme, show_axes=False)
        ref_box = QWidget()
        lay = QVBoxLayout(ref_box)
        lay.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Reference: full unit cell")
        title.setStyleSheet("font-weight: bold")
        lay.addWidget(title)
        lay.addWidget(self.reference.interactor, 1)
        self.info = QLabel("No structure loaded.")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        dock = QDockWidget("Reference", self)
        dock.setObjectName("reference_dock")
        dock.setWidget(ref_box)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self._build_actions()
        self.statusBar().showMessage(f"Appearance: {self.appearance.source}")

    # -- UI construction ----------------------------------------------------------
    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_act = QAction("&Open...", self, shortcut=QKeySequence.Open, triggered=self.open_dialog)
        shot_act = QAction("Save &image...", self, triggered=self.save_image_dialog)
        quit_act = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)
        file_menu.addActions([open_act, shot_act])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        view_menu = self.menuBar().addMenu("&View")
        tb = self.addToolBar("View")
        tb.setObjectName("view_toolbar")
        tb.addAction(open_act)
        tb.addSeparator()
        for view in VIEWS:
            label = "3D" if view == "oblique" else view
            act = QAction(label, self, triggered=lambda _=False, v=view: self.set_view(v))
            act.setToolTip("Default oblique view" if view == "oblique" else f"View along {view}")
            view_menu.addAction(act)
            tb.addAction(act)

    # -- actions ----------------------------------------------------------------
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open structure", "", FILE_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str | Path) -> bool:
        try:
            crystal = CrystalStructure.from_file(path)
        except StructureLoadError as exc:
            QMessageBox.critical(self, "Cannot open structure", str(exc))
            return False
        self.set_crystal(crystal)
        return True

    def set_crystal(self, crystal: CrystalStructure) -> None:
        self.crystal = crystal
        self.scene.set_model(build_scene_model(crystal, self.appearance))
        self.ref_scene.set_model(build_scene_model(crystal, self.appearance, show_bonds=False))
        self.ref_scene.set_view("oblique")
        self.setWindowTitle(f"MagView {__version__} - {crystal.name}")
        self.info.setText(crystal.summary().replace("  ", "\n"))
        self.statusBar().showMessage(f"Loaded {crystal.source or crystal.formula}")
        self.viewport.render()
        self.reference.render()

    def set_view(self, view: str) -> None:
        self.scene.set_view(view)
        self.viewport.render()

    def save_image_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save image", "magview.png", "PNG (*.png)")
        if path:
            self.viewport.screenshot(path)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.viewport.close()
        self.reference.close()
        super().closeEvent(event)
