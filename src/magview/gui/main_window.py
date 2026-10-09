"""MagView main window.

Layout: the main viewport shows the editing canvas (default 2x2x2 input cells,
light grid lines mark the input cells). The right dock holds the reference view
(the full input cell) and structure info. The left dock has tabs for the
magnetic-site suggestions and the Moments panel. Selection is view state; every
edit goes through the document's undo history and triggers a recovery autosave.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QAction, QCursor, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor  # noqa: E402

from magview import __version__  # noqa: E402
from magview.core import interaction as ia  # noqa: E402
from magview.core import magnetic as mg  # noqa: E402
from magview.core import project  # noqa: E402
from magview.core.batch import PatternOption, pattern_options  # noqa: E402
from magview.core.canvas import CanvasResizeError  # noqa: E402
from magview.core.document import MagDocument, NonCollinearError  # noqa: E402
from magview.core.graph import neighbor_graph  # noqa: E402
from magview.core.layers import LayerDecomposition, decompose  # noqa: E402
from magview.core.patterns import MatchResult, match_layer  # noqa: E402
from magview.core.stacking import fm_reference, match_stacking, relation, stacking_library  # noqa: E402
from magview.core.selection import apply_selection  # noqa: E402
from magview.core.structure import CrystalStructure, StructureLoadError  # noqa: E402
from magview.core.suggest import ElementRuleSuggester  # noqa: E402
from magview.gui.panels.layer_panel import LayerPanel  # noqa: E402
from magview.gui.panels.moment_panel import MomentPanel  # noqa: E402
from magview.gui.panels.suggestion_panel import SuggestionPanel  # noqa: E402
from magview.gui.viewport import EditorViewport, ViewportCallbacks  # noqa: E402
from magview.render.arrows import ArrowStyle  # noqa: E402
from magview.render.camera import VIEWS  # noqa: E402
from magview.render.layer_view import LayerScene  # noqa: E402
from magview.render.model import build_scene_model, subcell_edges  # noqa: E402
from magview.render.overlay import CursorLabel  # noqa: E402
from magview.render.picking import display_ray  # noqa: E402
from magview.render.scene import StructureScene  # noqa: E402
from magview.style.appearance import Appearance  # noqa: E402

FILE_FILTER = ("Structures and sessions (*.cif *.vasp *.poscar POSCAR* CONTCAR* *.magview.json);;"
               "All files (*)")
CANVAS_PRESETS = [(1, 1, 1), (2, 2, 1), (2, 2, 2), (3, 3, 1)]
LOCK_KEYS = {Qt.Key_X: "x", Qt.Key_Y: "y", Qt.Key_Z: "z", Qt.Key_A: "a", Qt.Key_B: "b", Qt.Key_C: "c"}


@dataclass
class DragState:
    frame: mg.DragFrame
    targets: np.ndarray  # canvas sites receiving the moment
    original: np.ndarray  # document moments at drag start
    result: mg.DragResult | None = None


class _ViewportKeys(QObject):
    """Keyboard handling for the 3D viewport: axis locks are held keys during a drag,
    other letters are commands (B box, M moment mode, F flip, 0 zero, P patterns, E equivalent)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.w = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        et = event.type()
        if et not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False  # leave Ctrl+Z etc. to the window shortcuts
        key = event.key()
        if et == QEvent.KeyRelease:
            if not event.isAutoRepeat() and LOCK_KEYS.get(key) == self.w.axis_lock:
                self.w.axis_lock = None
                self.w._refresh_drag()
            return key in LOCK_KEYS or key in (Qt.Key_M, Qt.Key_L, Qt.Key_F, Qt.Key_0, Qt.Key_P,
                                               Qt.Key_E, Qt.Key_PageUp, Qt.Key_PageDown)
        if event.isAutoRepeat():
            return True
        if self.w._drag is not None and key in LOCK_KEYS:
            self.w.axis_lock = LOCK_KEYS[key]
            self.w._refresh_drag()
            return True
        if key == Qt.Key_B:
            self.w._arm_box()
        elif key == Qt.Key_M:
            self.w.toggle_moment_mode()
        elif key == Qt.Key_L:
            self.w.toggle_layer_mode()
        elif key == Qt.Key_PageDown:
            self.w.step_layer(1)
        elif key == Qt.Key_PageUp:
            self.w.step_layer(-1)
        elif key == Qt.Key_F:
            self.w.flip_selection()
        elif key == Qt.Key_0:
            self.w.zero_selection()
        elif key == Qt.Key_P:
            self.w.show_patterns_menu()
        elif key == Qt.Key_E:
            self.w.select_equivalent()
        else:
            return key in LOCK_KEYS  # swallow lone axis letters (VTK must not see them)
        return True


class MainWindow(QMainWindow):
    def __init__(self, appearance: Appearance | None = None) -> None:
        super().__init__()
        self.appearance = appearance or Appearance.load()
        self.doc: MagDocument | None = None
        self.session_path: Path | None = None
        self.selection: frozenset[int] = frozenset()
        self.moment_view = False
        self._moment_view_before_mode = False
        self.axis_lock: str | None = None
        self._drag: DragState | None = None
        self._drag_pos: tuple | None = None
        self.layers: LayerDecomposition | None = None
        self.layer_axis = "c"
        self.current_layer = 0
        self.layer_match: MatchResult | None = None
        self._main_dirty = False
        self.autosave_enabled = os.environ.get(project.ENV_AUTOSAVE_DIR, "") != "off"
        self.suggester = ElementRuleSuggester()
        self.settings = QSettings("MagView", "MagView")
        self.setWindowTitle(f"MagView {__version__}")
        self.resize(1480, 900)

        # Main 3D view: the editing canvas.
        self.viewport = QtInteractor(self)
        self.setCentralWidget(self.viewport.interactor)
        self.scene = StructureScene(self.viewport, self.appearance.theme)
        self.editor = EditorViewport(self.viewport, self.scene, ViewportCallbacks(
            select=self._on_select, clear=self._on_clear, info=self._on_info,
            mode_changed=self._on_mode_changed, drag=self._on_drag,
            context_menu=self._context_menu), self.appearance.theme)
        self.cursor_label = CursorLabel(self.viewport.renderer)
        self.layer_scene = LayerScene(self.viewport, self.appearance.theme)
        self._keys = _ViewportKeys(self)
        self.viewport.installEventFilter(self._keys)

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

        # Left dock: suggestions and moments (tabs).
        self.suggestions = SuggestionPanel()
        self.suggestions.accepted.connect(self._accept_suggestions)
        self.suggestions.skipped.connect(lambda: self.statusBar().showMessage("Suggestions skipped"))
        self.sug_dock = QDockWidget("Magnetic sites", self)
        self.sug_dock.setObjectName("suggestion_dock")
        self.sug_dock.setWidget(self.suggestions)
        self.moments_panel = MomentPanel()
        self.mom_dock = QDockWidget("Moments", self)
        self.mom_dock.setObjectName("moment_dock")
        self.mom_dock.setWidget(self.moments_panel)
        self.layer_panel = LayerPanel()
        self.layer_dock = QDockWidget("Layers", self)
        self.layer_dock.setObjectName("layer_dock")
        self.layer_dock.setWidget(self.layer_panel)
        for d in (self.sug_dock, self.mom_dock, self.layer_dock):
            d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
            self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.tabifyDockWidget(self.sug_dock, self.mom_dock)
        self.tabifyDockWidget(self.mom_dock, self.layer_dock)
        self.sug_dock.raise_()
        self.resizeDocks([self.sug_dock, ref_dock], [430, 320], Qt.Horizontal)
        self._wire_moment_panel()
        self._wire_layer_panel()

        self.selection_label = QLabel("")
        self.statusBar().addPermanentWidget(self.selection_label)
        self._build_actions()
        self._load_arrow_settings()
        self.statusBar().showMessage(f"Appearance: {self.appearance.source}")

    # -- UI construction ----------------------------------------------------------
    def _build_actions(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        open_act = QAction("&Open...", self, shortcut=QKeySequence.Open, triggered=self.open_dialog)
        self.save_act = QAction("&Save session", self, shortcut=QKeySequence.Save,
                                triggered=self.save_session)
        save_as = QAction("Save session &as...", self, shortcut=QKeySequence.SaveAs,
                          triggered=lambda: self.save_session(ask=True))
        shot_act = QAction("Save &image...", self, triggered=self.save_image_dialog)
        quit_act = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)
        file_menu.addActions([open_act, self.save_act, save_as, shot_act])
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
        edit.addAction(QAction("Select &equivalent sites  (E)", self, triggered=self.select_equivalent))
        edit.addAction(QAction("&Box select  (B, or long-press)", self, triggered=self._arm_box))
        edit.addSeparator()
        edit.addAction(QAction("&Flip moments  (F)", self, triggered=self.flip_selection))
        edit.addAction(QAction("&Zero moments  (0)", self, triggered=self.zero_selection))
        edit.addAction(QAction("&Patterns...  (P)", self, triggered=self.show_patterns_menu))
        edit.addAction(QAction("&Clear magnetic sites", self, triggered=self._clear_magnetic))
        self.addAction(QAction("Cancel", self, shortcut=Qt.Key_Escape,
                               triggered=lambda: self.editor.key("escape")))

        view_menu = mb.addMenu("&View")
        tb = self.addToolBar("View")
        tb.setObjectName("view_toolbar")
        tb.addAction(open_act)
        tb.addSeparator()
        self.moment_mode_act = QAction("Moments (M)", self, checkable=True,
                                       triggered=lambda on: self.set_mode(ia.Mode.MOMENT if on else ia.Mode.VIEW))
        self.moment_mode_act.setToolTip("Moment-edit mode: drag from an atom to set its moment")
        tb.addAction(self.moment_mode_act)
        self.layer_mode_act = QAction("Layers (L)", self, checkable=True,
                                      triggered=lambda on: self.set_mode(ia.Mode.LAYER if on else ia.Mode.VIEW))
        self.layer_mode_act.setToolTip("Layer editor: click spins up/down layer by layer, with "
                                       "rule-based pattern completion")
        tb.addAction(self.layer_mode_act)
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

    def _wire_moment_panel(self) -> None:
        p = self.moments_panel
        p.apply_vector.connect(lambda v: self._edit(lambda s: self.doc.set_moments(s, v, "Set moment")))
        p.apply_signed.connect(lambda x: self._edit(
            lambda s: self.doc.set_moments(s, x * mg.unit(self.doc.spin_axis), "Set moment")))
        p.apply_magnitude.connect(lambda x: self._edit(lambda s: self.doc.set_magnitude(s, x)))
        p.fm.connect(lambda sign: self._edit(lambda s: self.doc.apply_signs(
            s, np.full(len(s), sign), "FM up" if sign > 0 else "FM down")))
        p.flip.connect(self.flip_selection)
        p.zero.connect(self.zero_selection)
        p.patterns.connect(self.show_patterns_menu)
        p.collinear_toggled.connect(self._set_collinear)
        p.spin_axis_changed.connect(self._set_spin_axis)
        p.arrows_changed.connect(self._arrows_changed)
        p.auto_fit.connect(self.auto_fit_arrows)

    # -- loading / saving -----------------------------------------------------------
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open structure or session", "", FILE_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str | Path) -> bool:
        path = Path(path)
        try:
            if path.name.endswith(".magview.json"):
                self.set_document(project.load(path), session_path=path)
                return True
            crystal = CrystalStructure.from_file(path)
        except (StructureLoadError, project.ProjectError) as exc:
            QMessageBox.critical(self, "Cannot open", str(exc))
            return False
        self.set_document(MagDocument.new(crystal))
        return True

    def set_crystal(self, crystal: CrystalStructure) -> None:
        self.set_document(MagDocument.new(crystal))

    def set_document(self, doc: MagDocument, session_path: Path | None = None) -> None:
        if self.editor.machine.mode != ia.Mode.VIEW:
            self.editor.set_mode(ia.Mode.VIEW)
        self.layers = None
        self.current_layer = 0
        self.doc = doc
        self.session_path = session_path
        doc.history.subscribe(self._on_history)
        crystal = doc.crystal
        self.selection = frozenset()
        self.ref_scene.set_model(build_scene_model(crystal, self.appearance, show_bonds=False))
        self.ref_scene.set_view("oblique")
        self._sync_model_ui()
        self._rebuild_main(reset_camera=True)
        self.suggestions.set_report(self.suggester.suggest(crystal))
        self.setWindowTitle(f"MagView {__version__} - {session_path.name if session_path else crystal.name}")
        self.info.setText(crystal.summary().replace("  ", "\n"))
        self.statusBar().showMessage(f"Loaded {session_path or crystal.source or crystal.formula}; "
                                     f"canvas {doc.canvas.describe()}")
        self._update_actions()
        self._refresh_moment_panel()
        self.reference.render()

    def save_session(self, ask: bool = False) -> bool:
        if self.doc is None:
            return False
        path = self.session_path
        if ask or path is None:
            default = f"{self.doc.crystal.name}.magview.json"
            name, _ = QFileDialog.getSaveFileName(self, "Save session", default,
                                                  "MagView session (*.magview.json)")
            if not name:
                return False
            path = Path(name if name.endswith(".magview.json") else name + ".magview.json")
        project.save(self.doc, path)
        self.session_path = path
        self.statusBar().showMessage(f"Saved {path}")
        return True

    # -- main view ------------------------------------------------------------------
    def _rebuild_main(self, reset_camera: bool = False) -> None:
        doc = self.doc
        grid = subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix)
        has_moments = bool(np.any(np.linalg.norm(doc.moments, axis=1) > 0))
        model = build_scene_model(doc.canvas.structure, self.appearance,
                                  site_moments=doc.moments if has_moments else None,
                                  moment_view=self.moment_view, grid_edges=grid)
        self.scene.spin_axis = doc.spin_axis.copy()
        self.scene.set_model(model, reset_camera=reset_camera)
        self._refresh_selection()
        self.viewport.render()

    def _display_moments(self, canvas_moments: np.ndarray) -> np.ndarray:
        return canvas_moments[self.scene.model.atoms.parent]

    # -- selection ----------------------------------------------------------------
    def _on_select(self, sites: list[int], op: ia.SelectOp, click: bool = False) -> None:
        if self.in_layer_mode and click and len(sites) == 1 and self.layer is not None \
                and sites[0] in set(self.layer.sites.tolist()) and op == ia.SelectOp.REPLACE:
            sign = self.doc.layer_click(sites[0])
            self.statusBar().showMessage(f"{self.doc.site_label(sites[0])}: {'up' if sign > 0 else 'down'}")
            return
        self.set_selection(apply_selection(self.selection, sites, op))

    def _on_clear(self) -> None:
        self.set_selection(frozenset())

    def set_selection(self, sites) -> None:
        self.selection = frozenset(int(s) for s in sites)
        self._refresh_selection()
        self._refresh_moment_panel()
        self.viewport.render()
        self.reference.render()

    def _refresh_selection(self) -> None:
        if self.doc is None or self.scene.model is None:
            return
        if self.in_layer_mode:
            self._refresh_layer()
            n = len(self.selection)
            self.selection_label.setText(f"{n} site{'s' if n != 1 else ''} selected" if n else "")
            return
        sel = np.array(sorted(self.selection), dtype=int)
        main_parent = self.scene.model.atoms.parent
        self.scene.set_selection(np.nonzero(np.isin(main_parent, sel))[0])
        if self.ref_scene.model is not None:
            inputs = np.unique(self.doc.canvas.parent[sel]) if len(sel) else np.zeros(0, int)
            self.ref_scene.set_selection(np.nonzero(np.isin(self.ref_scene.model.atoms.parent, inputs))[0])
        n = len(self.selection)
        self.selection_label.setText(f"{n} site{'s' if n != 1 else ''} selected" if n else "")

    def _refresh_moment_panel(self) -> None:
        if self.doc is None:
            return
        sel = np.array(sorted(self.selection), dtype=int)
        self.moments_panel.set_selection(self.doc.moments[sel], self.doc.crystal.lattice_matrix,
                                         self.doc.collinear, self.doc.spin_axis)

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

    # -- modes ------------------------------------------------------------------------
    def set_mode(self, mode: ia.Mode) -> None:
        self.editor.set_mode(mode)

    def toggle_moment_mode(self) -> None:
        self.set_mode(ia.Mode.VIEW if self.editor.machine.mode == ia.Mode.MOMENT else ia.Mode.MOMENT)

    def _on_mode_changed(self, mode: ia.Mode) -> None:
        self.moment_mode_act.setChecked(mode == ia.Mode.MOMENT)
        self.layer_mode_act.setChecked(mode == ia.Mode.LAYER)
        if mode == ia.Mode.LAYER:
            if not self._enter_layer_mode():
                self.editor.set_mode(ia.Mode.VIEW)
                return
            self.statusBar().showMessage("Mode: layer")
            return
        if self.editor.scene is self.layer_scene:
            self._leave_layer_mode()
        if mode == ia.Mode.MOMENT:
            self._moment_view_before_mode = self.moment_view
            self.moment_view_act.setChecked(True)
            self.mom_dock.raise_()
        else:
            self.moment_view_act.setChecked(self._moment_view_before_mode)
        self.statusBar().showMessage(f"Mode: {mode.value}")

    # -- layer editor -----------------------------------------------------------------
    def _wire_layer_panel(self) -> None:
        p = self.layer_panel
        p.axis_changed.connect(self._set_layer_axis)
        p.layer_selected.connect(self.select_layer)
        p.apply_candidate.connect(self.apply_layer_candidate)
        p.preview_candidate.connect(self._preview_layer_candidate)
        p.clear_layer.connect(lambda: self.doc and self.layers and
                              self.doc.clear_layer(self.layer.sites))
        p.set_selected.connect(self._layer_set_selected)
        p.apply_stacking.connect(self.apply_stacking)
        p.preview_stacking.connect(self._preview_stacking)
        p.flip_layer.connect(lambda: self.doc and self.layer is not None and
                             self.doc.flip_layer(self.layer.sites))

    @property
    def in_layer_mode(self) -> bool:
        return self.editor.machine.mode == ia.Mode.LAYER

    @property
    def layer(self):
        return self.layers.layers[self.current_layer] if self.layers and self.layers.layers else None

    def toggle_layer_mode(self) -> None:
        self.set_mode(ia.Mode.VIEW if self.in_layer_mode else ia.Mode.LAYER)

    def _compute_layers(self) -> None:
        sites = np.nonzero(self.doc.canvas_magnetic)[0]
        self.layers = decompose(self.doc.canvas.structure, sites, self.doc.crystal.lattice_matrix,
                                self.layer_axis)
        self.current_layer = min(self.current_layer, max(0, len(self.layers.layers) - 1))
        self.layer_panel.set_layers([L.label for L in self.layers.layers], self.current_layer)

    def _enter_layer_mode(self) -> bool:
        if self.doc is None or not self.doc.canvas_magnetic.any():
            self.statusBar().showMessage("No magnetic sites yet: accept suggestions or set moments first")
            return False
        self._compute_layers()
        self.scene.set_visible(False)
        self.editor.scene = self.layer_scene
        self.layer_dock.raise_()
        self._refresh_layer(reset_camera=True)
        return True

    def _leave_layer_mode(self) -> None:
        self.layer_scene.clear()
        self.editor.scene = self.scene
        if self._main_dirty:
            self._rebuild_main(reset_camera=False)
            self._main_dirty = False
        self.scene.set_visible(True)
        self.scene.set_view(self.scene._view)
        self._refresh_selection()
        self.viewport.render()

    def _layer_state(self):
        L = self.layer
        signs = self.doc.spin_sign(L.sites)
        pinned = self.doc.pinned[L.sites]
        return L, signs, pinned

    def _refresh_layer(self, reset_camera: bool = False, preview=None) -> None:
        if self.layers is None or self.layer is None:
            return
        L, signs, pinned = self._layer_state()
        pins = {int(i): int(signs[i]) for i in np.nonzero(pinned)[0] if signs[i] != 0}
        labels = [self.doc.site_label(s) for s in L.sites]
        self.layer_match = match_layer(L, pins, labels)
        if preview is None:
            self.layer_panel.set_match(L.lattice.reason, self.layer_match)
            self._refresh_stacking()
        selected = np.isin(L.sites, list(self.selection))
        self.layer_scene.show(self.doc.canvas.structure, L, self.layer_axis, signs, pinned,
                              preview=preview, reset_camera=reset_camera, selected=selected)
        inputs = np.unique(self.doc.canvas.parent[L.sites])
        if self.ref_scene.model is not None:
            self.ref_scene.set_selection(np.nonzero(np.isin(self.ref_scene.model.atoms.parent, inputs))[0])
            self.reference.render()
        self.viewport.render()

    def stacking_state(self):
        """(group layers, reference, match) for the current layer's element."""
        L = self.layer
        group = self.layers.by_element(L.element)
        base = self.doc.spin_sign(group[0].sites)
        ref = fm_reference(self.doc.canvas.structure, group, self.layers.normal,
                           self.layers.period, base)
        match = None
        if ref.ok:
            rels = [relation(self.doc.spin_sign(G.sites), r) for G, r in zip(group, ref.signs)]
            match = match_stacking(rels)
        return group, ref, match

    def _refresh_stacking(self) -> None:
        group, ref, match = self.stacking_state()
        self.layer_panel.set_stacking(self.layer.element, len(group), ref, match)

    def apply_stacking(self, key: str) -> bool:
        group, ref, _ = self.stacking_state()
        lib = {p.key: (p, invalid) for p, invalid in stacking_library(len(group))}
        if not ref.ok or key not in lib or lib[key][1]:
            return False
        pat = lib[key][0]
        sites = np.concatenate([G.sites for G in group])
        signs = np.concatenate([f * r for f, r in zip(pat.flips, ref.signs)])
        self.doc.apply_completion(sites, signs, f"{group[0].element} layers: {pat.label}")
        return True

    def _preview_stacking(self, key: str) -> None:
        if not key:
            self._refresh_layer()
            return
        group, ref, _ = self.stacking_state()
        lib = {p.key: p for p, _ in stacking_library(len(group))}
        if not ref.ok or key not in lib:
            return
        k = [G.index for G in group].index(self.layer.index)
        self._refresh_layer(preview=lib[key].flips[k] * ref.signs[k])

    def select_layer(self, index: int) -> None:
        if self.layers is None or not (0 <= index < len(self.layers.layers)):
            return
        self.current_layer = index
        self.layer_panel.set_current(index)
        self._refresh_layer(reset_camera=True)
        self.statusBar().showMessage(self.layer.label)

    def step_layer(self, delta: int) -> None:
        if self.in_layer_mode and self.layers:
            self.select_layer((self.current_layer + delta) % len(self.layers.layers))

    def _set_layer_axis(self, axis: str) -> None:
        self.layer_axis = axis
        if self.in_layer_mode:
            self.current_layer = 0
            self._compute_layers()
            self._refresh_layer(reset_camera=True)

    def apply_layer_candidate(self, k: int) -> bool:
        if self.layer_match is None or not (0 <= k < len(self.layer_match.candidates)):
            return False
        cand = self.layer_match.candidates[k]
        L = self.layer
        self.doc.apply_completion(L.sites, cand.signs,
                                  f"Complete {L.element} layer {L.index}: {cand.pattern.label}")
        return True

    def _preview_layer_candidate(self, k: int) -> None:
        if self.layer_match is None:
            return
        if 0 <= k < len(self.layer_match.candidates):
            self._refresh_layer(preview=self.layer_match.candidates[k].signs)
        else:
            self._refresh_layer()

    def _layer_set_selected(self, sign: int) -> None:
        if self.layer is None:
            return
        sites = sorted(set(self.layer.sites.tolist()) & set(self.selection))
        if not sites:
            self.statusBar().showMessage("Box-select sites of this layer first (long-press + drag)")
            return
        self.doc.layer_set(sites, sign)

    # -- moment drag ------------------------------------------------------------------
    def _on_drag(self, action) -> None:
        if self.doc is None:
            return
        if isinstance(action, ia.DragStart):
            site = action.site
            if site not in self.selection:
                self.set_selection({site})
            targets = np.array(sorted(self.selection), dtype=int)
            idx = self.editor.press_display_index
            model = self.scene.model
            anchor = model.atoms.cart[idx] if idx is not None else \
                self.doc.canvas.structure.cart_coords[site]
            cam = self.viewport.camera
            view_dir = mg.unit(np.array(cam.focal_point) - np.array(cam.position))
            up = np.array(cam.up, dtype=float)
            up = mg.unit(up - (up @ view_dir) * view_dir)
            frame = mg.DragFrame(anchor, view_dir, up, self.scene.arrow_style.scale,
                                 self.doc.canvas.structure.lattice_matrix,
                                 centered=self.scene.arrow_style.centered)
            self._drag = DragState(frame, targets, self.doc.moments.copy())
        elif isinstance(action, ia.DragUpdate):
            if self._drag is not None:
                self._drag_pos = (action.x, action.y, action.mods)
                self._refresh_drag()
        elif isinstance(action, ia.DragCommit):
            drag, self._drag = self._drag, None
            self.cursor_label.hide()
            if drag is None or drag.result is None or drag.result.below_dead_zone:
                self.scene.update_moments(self._display_moments(self.doc.moments))
                self.viewport.render()
                return
            n = len(drag.targets)
            self.doc.set_moments(drag.targets, drag.result.moment,
                                 f"Drag moment ({n} site{'s' if n > 1 else ''})")
        elif isinstance(action, ia.DragCancel):
            self._drag = None
            self.cursor_label.hide()
            self.scene.update_moments(self._display_moments(self.doc.moments))
            self.viewport.render()

    def _refresh_drag(self) -> None:
        drag = self._drag
        if drag is None or self._drag_pos is None:
            return
        x, y, mods = self._drag_pos
        o, d = display_ray(self.viewport.renderer, x, y)
        p = mg.plane_point(drag.frame, o, d)
        axis = self.doc.spin_axis if self.doc.collinear else None
        res = mg.drag_moment(drag.frame, p, lock=self.axis_lock, snap=mods.shift, collinear_axis=axis)
        drag.result = res
        preview = drag.original.copy()
        if not res.below_dead_zone:
            preview[drag.targets] = mg.project_collinear(res.moment, axis) if axis is not None else res.moment
        self.scene.update_moments(self._display_moments(preview))
        m = res.moment
        r, th, ph = mg.spherical(m)
        abc = mg.crystal_components(m, self.doc.crystal.lattice_matrix)
        # Note: VTK's text renderer treats '|' specially, so no "|m|" here.
        lines = [f"m = {r:.2f} muB" + ("  (below 0.2: no change)" if res.below_dead_zone else ""),
                 f"xyz ({m[0]:+.2f}, {m[1]:+.2f}, {m[2]:+.2f})",
                 f"abc ({abc[0]:+.2f}, {abc[1]:+.2f}, {abc[2]:+.2f})",
                 f"theta {th:5.1f}  phi {ph:5.1f}"]
        lock = res.lock or ("snap" if mods.shift else None)
        if lock:
            lines.append(f"lock: {lock}" + (" + snap" if mods.shift and res.lock else ""))
        if res.note:
            lines.append(res.note)
        if len(drag.targets) > 1:
            lines.append(f"applies to {len(drag.targets)} selected sites")
        self.cursor_label.show("\n".join(lines), x, y)
        self.viewport.render()

    # -- batch edits ------------------------------------------------------------------
    def _edit(self, fn) -> None:
        if self.doc is None or not self.selection:
            self.statusBar().showMessage("Select atoms first")
            return
        fn(np.array(sorted(self.selection), dtype=int))

    def flip_selection(self) -> None:
        self._edit(self.doc.flip if self.doc else None)

    def zero_selection(self) -> None:
        self._edit(self.doc.zero if self.doc else None)

    def pattern_options(self) -> list[PatternOption]:
        sites = np.array(sorted(self.selection), dtype=int)
        return pattern_options(self.doc.canvas.structure, sites, self.doc.crystal.lattice_matrix)

    def apply_pattern(self, key: str) -> bool:
        """Apply the pattern option ``key`` to the selection (one undo step)."""
        opts = {o.key: o for o in self.pattern_options()}
        opt = opts.get(key)
        if opt is None or not opt.available:
            return False
        sites = np.array(sorted(self.selection), dtype=int)
        self.doc.apply_signs(sites, opt.signs, f"Pattern: {opt.label}")
        return True

    def build_patterns_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addSection(f"Patterns for {len(self.selection)} selected sites")
        for opt in self.pattern_options():
            act = menu.addAction(opt.label if opt.available else f"{opt.label}  (n/a)")
            act.setData(opt.key)
            act.setEnabled(opt.available)
            act.setToolTip(opt.reason)
        menu.hovered.connect(self._preview_pattern)
        return menu

    def show_patterns_menu(self) -> None:
        if self.doc is None or not self.selection:
            self.statusBar().showMessage("Select atoms first (click, or long-press and drag)")
            return
        menu = self.build_patterns_menu()
        chosen = menu.exec(QCursor.pos())
        self._end_preview()
        if chosen is not None and chosen.data():
            self.apply_pattern(chosen.data())

    def _preview_pattern(self, action) -> None:
        key = action.data()
        opt = next((o for o in self.pattern_options() if o.key == key), None)
        if opt is None or not opt.available:
            self._end_preview()
            return
        sites = np.array(sorted(self.selection), dtype=int)
        preview = self.doc.moments.copy()
        preview[sites] = np.outer(self.doc.magnitude_for(sites) * opt.signs, mg.unit(self.doc.spin_axis))
        self.scene.update_moments(self._display_moments(preview))
        self.viewport.render()

    def _end_preview(self) -> None:
        if self.doc is not None and self.scene.model is not None:
            moments = self._display_moments(self.doc.moments)
            if np.any(moments):
                self.scene.update_moments(moments)
            else:
                self.scene.set_moments(None)
            self.viewport.render()

    def _context_menu(self, site: int | None) -> None:
        if self.doc is None:
            return
        if self.in_layer_mode:
            if site is not None and self.layer is not None and site in set(self.layer.sites.tolist()):
                self.doc.layer_unset(site)
            return
        if site is not None and site not in self.selection:
            self.set_selection({site})
        menu = QMenu(self)
        if self.selection:
            menu.addAction("Flip  (F)", self.flip_selection)
            menu.addAction("Zero  (0)", self.zero_selection)
            menu.addAction("Set |m|...", self._ask_magnitude)
            menu.addAction("Patterns...  (P)", self.show_patterns_menu)
            menu.addSeparator()
            menu.addAction("Select equivalent  (E)", self.select_equivalent)
        menu.addAction("Select all magnetic  (Ctrl+A)", self.select_all_magnetic)
        menu.exec(QCursor.pos())

    def _ask_magnitude(self) -> None:
        val, ok = QInputDialog.getDouble(self, "Set |m|", "|m| (muB):", 1.0, 0.0, mg.MAX_MOMENT, 2)
        if ok:
            self._edit(lambda s: self.doc.set_magnitude(s, val))

    # -- model settings -------------------------------------------------------------------
    def _set_collinear(self, on: bool) -> None:
        if self.doc is None or on == self.doc.collinear:
            return
        try:
            self.doc.set_collinear(on)
        except NonCollinearError:
            ans = QMessageBox.question(
                self, "Switch to collinear",
                "Some moments are not along the spin axis.\nProject them onto the axis? "
                "(Undo restores them.)", QMessageBox.Yes | QMessageBox.Cancel)
            if ans == QMessageBox.Yes:
                self.doc.set_collinear(True, project=True)
        self._sync_model_ui()

    def _set_spin_axis(self, name: str) -> None:
        if self.doc is None:
            return
        u = mg.axis_vector(name, self.doc.crystal.lattice_matrix)
        if not np.allclose(u, self.doc.spin_axis):
            self.doc.set_spin_axis(u)

    def _sync_model_ui(self) -> None:
        p = self.moments_panel
        if self.doc is None:
            return
        p.collinear.blockSignals(True)
        p.collinear.setChecked(self.doc.collinear)
        p.collinear.blockSignals(False)
        p.set_collinear_ui(self.doc.collinear)
        lat = self.doc.crystal.lattice_matrix
        for i in range(p.axis.count()):
            if np.allclose(mg.axis_vector(p.axis.itemText(i), lat), self.doc.spin_axis):
                p.axis.blockSignals(True)
                p.axis.setCurrentIndex(i)
                p.axis.blockSignals(False)
                break

    def _arrows_changed(self) -> None:
        p = self.moments_panel
        self.scene.arrow_style = ArrowStyle(
            scale=p.scale.value(), shaft_radius=self.appearance.theme.arrow_shaft_radius,
            head_radius_ratio=self.appearance.theme.arrow_head_radius_ratio,
            head_length_ratio=self.appearance.theme.arrow_head_length_ratio,
            centered=p.anchor.currentIndex() == 0, resolution=self.appearance.theme.arrow_resolution)
        self.scene.color_mode = p.color_mode.currentText()
        self.settings.setValue("arrows/scale", p.scale.value())
        self.settings.setValue("arrows/anchor", p.anchor.currentIndex())
        self.settings.setValue("arrows/color_mode", p.color_mode.currentText())
        if self.doc is not None and self.scene.model is not None:
            self._end_preview()

    def _load_arrow_settings(self) -> None:
        p = self.moments_panel
        for w, key, conv in ((p.scale, "arrows/scale", float), (p.anchor, "arrows/anchor", int)):
            val = self.settings.value(key)
            if val is not None:
                w.blockSignals(True)
                (w.setValue if hasattr(w, "setValue") else w.setCurrentIndex)(conv(val))
                w.blockSignals(False)
        mode = self.settings.value("arrows/color_mode")
        if mode:
            p.color_mode.blockSignals(True)
            p.color_mode.setCurrentText(str(mode))
            p.color_mode.blockSignals(False)
        self._arrows_changed()

    def auto_fit_arrows(self) -> None:
        """Scale so the longest arrow spans 80% of the shortest magnetic-magnetic distance."""
        if self.doc is None:
            return
        mags = np.linalg.norm(self.doc.moments, axis=1)
        sites = np.nonzero(mags > 0)[0]
        if len(sites) < 1:
            return
        g = neighbor_graph(self.doc.canvas.structure.lattice_matrix,
                           self.doc.canvas.structure.frac_coords, sites)
        if g.d_nn <= 0:
            return
        self.moments_panel.scale.setValue(round(0.8 * g.d_nn / mags.max(), 2))

    # -- edits ----------------------------------------------------------------------
    def _accept_suggestions(self, items, magnitudes) -> None:
        if self.doc is not None:
            self.doc.accept_suggestions(items, magnitudes)
            self.mom_dock.raise_()
            self._ensure_visible_arrows()

    def _ensure_visible_arrows(self) -> None:
        """Auto-fit the arrow scale once if arrows would hide inside the (moment-view) atoms.
        Only on explicit actions (accept), never during a drag."""
        mags = np.linalg.norm(self.doc.moments, axis=1)
        if not mags.any():
            return
        t = self.appearance.theme
        radius = max(self.appearance.elements[s].radius for s, on in
                     zip(self.doc.crystal.symbols, self.doc.magnetic) if on)
        shown = radius * t.ball_scale * t.moment_view_atom_scale
        half = 0.5 if self.scene.arrow_style.centered else 1.0
        if mags.max() * self.scene.arrow_style.scale * half < 1.6 * shown:
            before = self.scene.arrow_style.scale
            self.auto_fit_arrows()
            self.statusBar().showMessage(f"Arrow scale auto-fitted {before:g} -> "
                                         f"{self.scene.arrow_style.scale:g} A/muB so arrows are visible")

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
        self._sync_model_ui()
        if self.in_layer_mode:
            self._main_dirty = True
            self._compute_layers()
            self._refresh_layer()
        else:
            self._rebuild_main(reset_camera=False)
        self._refresh_moment_panel()
        self._update_actions()
        if self.autosave_enabled:
            try:
                project.autosave(self.doc)
            except OSError as exc:
                self.statusBar().showMessage(f"Autosave failed: {exc}")

    def _update_actions(self) -> None:
        h = self.doc.history if self.doc else None
        self.undo_act.setEnabled(bool(h and h.can_undo))
        self.redo_act.setEnabled(bool(h and h.can_redo))
        self.undo_act.setText(f"&Undo {h.undo_label}" if h and h.can_undo else "&Undo")
        self.redo_act.setText(f"&Redo {h.redo_label}" if h and h.can_redo else "&Redo")

    # -- view ---------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        if self.in_layer_mode:
            self.layer_scene.set_view()
        else:
            self.scene.set_view(view)
        self.viewport.render()

    def _set_moment_view(self, on: bool) -> None:
        self.moment_view = on
        if self.doc is not None:
            if self.in_layer_mode:
                self._main_dirty = True
            else:
                self._rebuild_main(reset_camera=False)

    def save_image_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save image", "magview.png", "PNG (*.png)")
        if path:
            self.viewport.screenshot(path)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.viewport.close()
        self.reference.close()
        super().closeEvent(event)
