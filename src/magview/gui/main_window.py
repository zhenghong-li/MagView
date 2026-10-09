"""MagView main window.

Layout: the main viewport shows the editing canvas (default 2x2x2 input cells,
light grid lines mark the input cells); the right dock holds the reference view
(the full input cell) and structure info; the left dock holds the magnetic-site
suggestions. Selection is view state; every edit goes through the document's
undo history.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor  # noqa: E402

from magview import __version__  # noqa: E402
from magview.core import interaction as ia  # noqa: E402
from magview.core.canvas import CanvasResizeError  # noqa: E402
from magview.core.document import MagDocument  # noqa: E402
from magview.core.selection import apply_selection  # noqa: E402
from magview.core.structure import CrystalStructure, StructureLoadError  # noqa: E402
from magview.core.suggest import ElementRuleSuggester  # noqa: E402
from magview.gui.panels.suggestion_panel import SuggestionPanel  # noqa: E402
from magview.gui.viewport import EditorViewport, ViewportCallbacks  # noqa: E402
from magview.render.camera import VIEWS  # noqa: E402
from magview.render.model import build_scene_model, subcell_edges  # noqa: E402
from magview.render.scene import StructureScene  # noqa: E402
from magview.style.appearance import Appearance  # noqa: E402

FILE_FILTER = "Structures (*.cif *.vasp *.poscar POSCAR* CONTCAR*);;All files (*)"
CANVAS_PRESETS = [(1, 1, 1), (2, 2, 1), (2, 2, 2), (3, 3, 1)]


class MainWindow(QMainWindow):
    def __init__(self, appearance: Appearance | None = None) -> None:
        super().__init__()
        self.appearance = appearance or Appearance.load()
        self.doc: MagDocument | None = None
        self.selection: frozenset[int] = frozenset()
        self.moment_view = False
        self.suggester = ElementRuleSuggester()
        self.setWindowTitle(f"MagView {__version__}")
        self.resize(1440, 860)

        # Main 3D view: the editing canvas.
        self.viewport = QtInteractor(self)
        self.setCentralWidget(self.viewport.interactor)
        self.scene = StructureScene(self.viewport, self.appearance.theme)
        self.editor = EditorViewport(self.viewport, self.scene, ViewportCallbacks(
            select=self._on_select, clear=self._on_clear, info=self._on_info,
            mode_changed=self._on_mode_changed), self.appearance.theme)

        # Right dock: reference view of the full input cell + structure info.
        self.reference = QtInteractor(self)
        self.reference.setMinimumSize(280, 240)
        self.reference.iren.clear_key_event_callbacks()
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
        ref_dock = QDockWidget("Reference", self)
        ref_dock.setObjectName("reference_dock")
        ref_dock.setWidget(ref_box)
        ref_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, ref_dock)

        # Left dock: magnetic-site suggestions.
        self.suggestions = SuggestionPanel()
        self.suggestions.accepted.connect(self._accept_suggestions)
        self.suggestions.skipped.connect(lambda: self.statusBar().showMessage("Suggestions skipped"))
        sug_dock = QDockWidget("Magnetic sites", self)
        sug_dock.setObjectName("suggestion_dock")
        sug_dock.setWidget(self.suggestions)
        sug_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, sug_dock)
        self.resizeDocks([sug_dock, ref_dock], [430, 320], Qt.Horizontal)

        self.selection_label = QLabel("")
        self.statusBar().addPermanentWidget(self.selection_label)
        self._build_actions()
        self.statusBar().showMessage(f"Appearance: {self.appearance.source}")

    # -- UI construction ----------------------------------------------------------
    def _build_actions(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        open_act = QAction("&Open...", self, shortcut=QKeySequence.Open, triggered=self.open_dialog)
        shot_act = QAction("Save &image...", self, triggered=self.save_image_dialog)
        quit_act = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)
        file_menu.addActions([open_act, shot_act])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        edit = mb.addMenu("&Edit")
        self.undo_act = QAction("&Undo", self, shortcut=QKeySequence.Undo, triggered=self.undo)
        self.redo_act = QAction("&Redo", self, triggered=self.redo)
        self.redo_act.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.undo_act.setEnabled(False)
        self.redo_act.setEnabled(False)
        edit.addActions([self.undo_act, self.redo_act])
        edit.addSeparator()
        edit.addAction(QAction("Select all &magnetic sites", self, shortcut=QKeySequence.SelectAll,
                               triggered=self.select_all_magnetic))
        edit.addAction(QAction("Select &equivalent sites", self, shortcut="E",
                               triggered=self.select_equivalent))
        edit.addAction(QAction("&Clear magnetic sites", self, triggered=self._clear_magnetic))
        # Gesture keys go to the state machine (Esc cancels/clears, B arms box select).
        self.addAction(QAction("Cancel", self, shortcut=Qt.Key_Escape,
                               triggered=lambda: self.editor.key("escape")))
        self.addAction(QAction("Box select", self, shortcut="B",
                               triggered=lambda: self._arm_box()))

        view_menu = mb.addMenu("&View")
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
        view_menu.addSeparator()
        self.moment_view_act = QAction("&Moment view", self, checkable=True,
                                       toggled=self._set_moment_view)
        self.moment_view_act.setToolTip("Shrink atoms, fade non-magnetic atoms, hide bonds")
        view_menu.addAction(self.moment_view_act)
        tb.addSeparator()
        tb.addAction(self.moment_view_act)

        canvas_menu = mb.addMenu("&Canvas")
        for preset in CANVAS_PRESETS:
            canvas_menu.addAction(QAction("x".join(map(str, preset)), self,
                                          triggered=lambda _=False, p=preset: self.resize_canvas(p)))
        canvas_menu.addAction(QAction("Custom...", self, triggered=self._custom_canvas))

    # -- loading ----------------------------------------------------------------
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
        self.doc = MagDocument.new(crystal)
        self.doc.history.subscribe(self._on_history)
        self.selection = frozenset()
        self.ref_scene.set_model(build_scene_model(crystal, self.appearance, show_bonds=False))
        self.ref_scene.set_view("oblique")
        self._rebuild_main(reset_camera=True)
        self.suggestions.set_report(self.suggester.suggest(crystal))
        self.setWindowTitle(f"MagView {__version__} - {crystal.name}")
        self.info.setText(crystal.summary().replace("  ", "\n"))
        self.statusBar().showMessage(f"Loaded {crystal.source or crystal.formula}; "
                                     f"canvas {self.doc.canvas.describe()}")
        self._update_actions()
        self.reference.render()

    def _rebuild_main(self, reset_camera: bool = False) -> None:
        doc = self.doc
        grid = subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix)
        has_moments = bool(np.any(np.linalg.norm(doc.moments, axis=1) > 0))
        model = build_scene_model(doc.canvas.structure, self.appearance,
                                  site_moments=doc.moments if has_moments else None,
                                  moment_view=self.moment_view, grid_edges=grid)
        self.scene.set_model(model, reset_camera=reset_camera)
        self._refresh_selection()
        self.viewport.render()

    # -- selection ----------------------------------------------------------------
    def _on_select(self, sites: list[int], op: ia.SelectOp) -> None:
        self.set_selection(apply_selection(self.selection, sites, op))

    def _on_clear(self) -> None:
        self.set_selection(frozenset())

    def set_selection(self, sites) -> None:
        self.selection = frozenset(int(s) for s in sites)
        self._refresh_selection()
        self.viewport.render()
        self.reference.render()

    def _refresh_selection(self) -> None:
        if self.doc is None or self.scene.model is None:
            return
        sel = np.array(sorted(self.selection), dtype=int)
        main_parent = self.scene.model.atoms.parent
        self.scene.set_selection(np.nonzero(np.isin(main_parent, sel))[0])
        if self.ref_scene.model is not None:
            inputs = np.unique(self.doc.canvas.parent[sel]) if len(sel) else np.zeros(0, int)
            self.ref_scene.set_selection(np.nonzero(np.isin(self.ref_scene.model.atoms.parent, inputs))[0])
        n = len(self.selection)
        self.selection_label.setText(f"{n} site{'s' if n != 1 else ''} selected" if n else "")

    def _on_info(self, site: int | None) -> None:
        if site is None or self.doc is None:
            return
        m = self.doc.moments[site]
        frac = self.doc.canvas.structure.frac_coords[site]
        self.statusBar().showMessage(
            f"{self.doc.site_label(site)}  canvas site {site}  frac "
            f"({frac[0]:.4f}, {frac[1]:.4f}, {frac[2]:.4f})  m = "
            f"({m[0]:.2f}, {m[1]:.2f}, {m[2]:.2f}) muB")

    def select_all_magnetic(self) -> None:
        if self.doc is not None:
            self.set_selection(np.nonzero(self.doc.canvas_magnetic)[0])

    def select_equivalent(self) -> None:
        """Extend the selection to all canvas sites in the same symmetry orbits."""
        if self.doc is None or not self.selection:
            return
        eq = np.array(self.doc.crystal.symmetry.equivalent_atoms)
        orbits = set(eq[self.doc.canvas.parent[sorted(self.selection)]])
        self.set_selection(np.nonzero(np.isin(eq[self.doc.canvas.parent], list(orbits)))[0])

    def _arm_box(self) -> None:
        self.editor.key("b")
        self.statusBar().showMessage("Box select armed: drag a rectangle")

    def _on_mode_changed(self, mode: ia.Mode) -> None:
        self.statusBar().showMessage(f"Mode: {mode.value}")

    # -- edits ----------------------------------------------------------------------
    def _accept_suggestions(self, items, magnitudes) -> None:
        if self.doc is not None:
            self.doc.accept_suggestions(items, magnitudes)

    def _clear_magnetic(self) -> None:
        if self.doc is not None:
            self.doc.clear_magnetic()

    def resize_canvas(self, matrix) -> bool:
        if self.doc is None:
            return False
        try:
            self.doc.resize_canvas(matrix)
        except CanvasResizeError as exc:
            QMessageBox.warning(self, "Canvas not changed", str(exc))
            return False
        return True

    def _custom_canvas(self) -> None:
        text, ok = QInputDialog.getText(self, "Canvas size", "Diagonal multiples n1 n2 n3:",
                                        text=" ".join(map(str, np.diag(self.doc.canvas.matrix)))
                                        if self.doc else "2 2 2")
        if ok:
            try:
                dims = [int(v) for v in text.replace("x", " ").split()]
                assert len(dims) == 3 and min(dims) >= 1
            except (ValueError, AssertionError):
                QMessageBox.warning(self, "Canvas", "Enter three positive integers, e.g. 2 2 1")
                return
            self.resize_canvas(dims)

    def undo(self) -> None:
        if self.doc is not None:
            label = self.doc.history.undo()
            if label:
                self.statusBar().showMessage(f"Undid: {label}")

    def redo(self) -> None:
        if self.doc is not None:
            label = self.doc.history.redo()
            if label:
                self.statusBar().showMessage(f"Redid: {label}")

    def _on_history(self) -> None:
        # Canvas may have changed: drop selections that no longer exist.
        n = len(self.doc.canvas)
        self.selection = frozenset(s for s in self.selection if s < n)
        self._rebuild_main(reset_camera=False)
        self._update_actions()

    def _update_actions(self) -> None:
        h = self.doc.history if self.doc else None
        self.undo_act.setEnabled(bool(h and h.can_undo))
        self.redo_act.setEnabled(bool(h and h.can_redo))
        self.undo_act.setText(f"&Undo {h.undo_label}" if h and h.can_undo else "&Undo")
        self.redo_act.setText(f"&Redo {h.redo_label}" if h and h.can_redo else "&Redo")

    # -- view ---------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        self.scene.set_view(view)
        self.viewport.render()

    def _set_moment_view(self, on: bool) -> None:
        self.moment_view = on
        if self.doc is not None:
            self._rebuild_main(reset_camera=False)

    def save_image_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save image", "magview.png", "PNG (*.png)")
        if path:
            self.viewport.screenshot(path)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.viewport.close()
        self.reference.close()
        super().closeEvent(event)
