"""MagView main window.

Layout: the main viewport shows the editing canvas (default 2x2x2 input cells,
light grid lines mark the input cells). The right dock holds the reference view
(the full input cell) and structure info. The left dock has tabs for the
magnetic-site suggestions and the Moments, Layers and Export panels. Selection is
view state; every edit goes through the document's undo history and schedules a
(debounced) recovery autosave.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import numpy as np  # noqa: E402
import pyvista as pv  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QEvent,
    QEventLoop,
    QObject,
    QPoint,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QTextEdit,
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
from magview.bridge.agentdft import HandoffRejected, MockAgentDFTBridge  # noqa: E402
from magview.core.magcell import as_structure, minimal_magnetic_cell  # noqa: E402
from magview.gui.panels.export_panel import ExportPanel  # noqa: E402
from magview.gui.panels.layer_panel import LayerPanel  # noqa: E402
from magview.io.handoff import HandoffError, build_handoff, save_handoff  # noqa: E402
from magview.gui.panels.moment_panel import MomentPanel  # noqa: E402
from magview.gui.panels.suggestion_panel import SuggestionPanel  # noqa: E402
from magview.gui.viewport import EditorViewport, ViewportCallbacks  # noqa: E402
from magview.render.arrows import ArrowStyle  # noqa: E402
from magview.render.camera import VIEWS  # noqa: E402
from magview.render.layer_view import UNSET_RGB, LayerScene  # noqa: E402
from magview.render.model import build_scene_model, subcell_edges  # noqa: E402
from magview.render.overlay import CursorLabel  # noqa: E402
from magview.render.picking import display_ray  # noqa: E402
from magview.render.scene import StructureScene  # noqa: E402
from magview.style.appearance import Appearance  # noqa: E402

FILE_FILTER = ("Structures and sessions (*.cif *.vasp *.poscar POSCAR* CONTCAR* *.magview.json);;"
               "All files (*)")
CANVAS_PRESETS = [(1, 1, 1), (2, 2, 1), (2, 2, 2), (3, 3, 1)]
LOCK_KEYS = {Qt.Key_X: "x", Qt.Key_Y: "y", Qt.Key_Z: "z", Qt.Key_A: "a", Qt.Key_B: "b", Qt.Key_C: "c"}
COMMAND_KEYS = (Qt.Key_M, Qt.Key_L, Qt.Key_F, Qt.Key_0, Qt.Key_P, Qt.Key_E)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
AUTOSAVE_DEBOUNCE_MS = 2000


def _is_text_entry(widget) -> bool:
    """True when the widget (or an ancestor) is a text/number entry field that
    should keep its keys (spin boxes, line edits, combo boxes)."""
    while widget is not None:
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
            return True
        widget = widget.parentWidget()
    return False


@contextmanager
def _wait_cursor():
    QGuiApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        yield
    finally:
        QGuiApplication.restoreOverrideCursor()


def scene_fingerprint(doc) -> tuple | None:
    """Document state whose change requires a full scene rebuild. Two fingerprints that
    compare equal mean only the moment vectors changed, so the scene's in-place
    ``set_moments`` fast path is enough (see the StructureScene interface contract)."""
    if doc is None:
        return None
    return (doc.canvas.matrix.tobytes(), len(doc.canvas), doc.magnetic.tobytes(),
            doc.pinned.tobytes(), doc.collinear, doc.spin_axis.tobytes())


def recovery_autosave(crystal: CrystalStructure, opened: Path) -> Path | None:
    """The recovery autosave of this structure when it exists and is newer than the file
    being opened (None otherwise). Keyed by structure content, not just the file name."""
    try:
        auto = project.autosave_path(crystal)
        if auto.is_file() and (not opened.is_file()
                               or auto.stat().st_mtime > opened.stat().st_mtime + 1e-6):
            return auto
    except OSError:
        pass
    return None


class _BusyInputBlocker(QObject):
    """Swallows user input aimed at one window while a background job runs.

    The busy dialog is modal but only appears after a short delay; until then the
    local event loop would deliver clicks, drags and shortcuts (an edit, a second
    Analyze, Ctrl+O) that change or replace the document the job is working on."""

    BLOCKED = frozenset({QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                         QEvent.MouseButtonDblClick, QEvent.KeyPress, QEvent.KeyRelease,
                         QEvent.Wheel, QEvent.ContextMenu, QEvent.Shortcut,
                         QEvent.ShortcutOverride})

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() not in self.BLOCKED:
            return False
        if isinstance(obj, QWidget):
            return obj.window() is self.window
        return event.type() == QEvent.Shortcut  # QActions receive shortcuts directly


class _WorkerSignals(QObject):
    finished = Signal(object)  # work result
    failed = Signal(object)  # the raised exception


class _FuncRunnable(QRunnable):
    def __init__(self, fn, signals: _WorkerSignals) -> None:
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:
        try:
            self._signals.finished.emit(self._fn())
        except Exception as exc:  # any failure becomes a friendly dialog on the GUI thread
            self._signals.failed.emit(exc)


@dataclass
class DragState:
    frame: mg.DragFrame
    targets: np.ndarray  # canvas sites receiving the moment
    original: np.ndarray  # document moments at drag start
    result: mg.DragResult | None = None


class _ViewportKeys(QObject):
    """Letter-key commands and held axis locks, installed on the QApplication so they work
    wherever the keyboard focus is inside the main window (text-entry fields keep their keys).

    These stay an event filter rather than window shortcuts because B is "box select" when
    idle but "lock to b" during a drag (docs/ux_spec.md section 8)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.w = window

    @staticmethod
    def _swallowable(key) -> bool:
        return key in LOCK_KEYS or key in COMMAND_KEYS or key in (Qt.Key_PageUp, Qt.Key_PageDown)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        et = event.type()
        if et not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        w = self.w
        # Dispatch on the event's target, not on window activation: headless/test
        # platforms can lose activation after a modal dialog, and letter commands
        # must keep working whenever the focused widget belongs to this window.
        target = obj if isinstance(obj, QWidget) else QApplication.focusWidget()
        if not isinstance(target, QWidget) or target.window() is not w:
            return False
        app = QApplication.instance()
        if app is not None and (app.activeModalWidget() is not None
                                or app.activePopupWidget() is not None):
            return False  # menus and dialogs keep their keys (mnemonics, Esc, ...)
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False  # leave Ctrl+Z etc. to the window shortcuts
        key = event.key()
        if key == Qt.Key_Escape:
            if et == QEvent.KeyPress and not event.isAutoRepeat():
                w._escape(target)  # Esc itself decides: gestures cancel, text fields keep it
            return True
        if et == QEvent.KeyRelease:
            if not event.isAutoRepeat() and LOCK_KEYS.get(key) == w.axis_lock:
                w.axis_lock = None
                w._refresh_drag()
                return True
            return self._swallowable(key)
        if w._drag is not None or w.editor.machine.busy:
            # Mid-gesture: only the held axis-lock keys are meaningful; command keys
            # would corrupt the drag.
            if w._drag is not None and not event.isAutoRepeat() and key in LOCK_KEYS:
                w.axis_lock = LOCK_KEYS[key]
                w._refresh_drag()
            return self._swallowable(key)
        if _is_text_entry(target):
            return False  # spin boxes, line edits and combo boxes keep their keys
        if event.isAutoRepeat():
            return self._swallowable(key)
        if key == Qt.Key_B:
            w._arm_box()
        elif key == Qt.Key_M:
            w.toggle_moment_mode()
        elif key == Qt.Key_L:
            w.toggle_layer_mode()
        elif key == Qt.Key_PageDown:
            if not w.in_layer_mode:
                return False  # let lists/tables scroll
            w.step_layer(1)
        elif key == Qt.Key_PageUp:
            if not w.in_layer_mode:
                return False
            w.step_layer(-1)
        elif key == Qt.Key_F:
            w.flip_selection()
        elif key == Qt.Key_0:
            w.zero_selection()
        elif key == Qt.Key_P:
            w.show_patterns_menu(at_cursor=False)
        elif key == Qt.Key_E:
            w.select_equivalent()
        else:
            return key in LOCK_KEYS  # swallow lone axis letters (VTK must not see them)
        return True


class MainWindow(QMainWindow):
    def __init__(self, appearance: Appearance | None = None, settings: QSettings | None = None,
                 autosave: bool = True) -> None:
        """``settings``: where the window layout and arrow settings persist (default: the
        user's QSettings). ``autosave=False`` disables recovery autosaves and the
        restore prompt (batch/capture runs)."""
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
        self.mag_cell = None
        self.handoff: dict | None = None
        self.previewing = False
        self.bridge = MockAgentDFTBridge()
        self.autosave_enabled = autosave and os.environ.get(project.ENV_AUTOSAVE_DIR, "") != "off"
        self._autosave_pending = False
        self._saved_camera: tuple | None = None  # camera before layer mode / preview
        self._scene_fp: tuple | None = None  # fingerprint of the document shown in the main scene
        self._pending_workers: set = set()
        self.suggester = ElementRuleSuggester()
        self.settings = settings if settings is not None else QSettings("MagView", "MagView")
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
        app = QApplication.instance()
        if app is not None:
            # App-level so the letter commands work when a panel (not the viewport) has focus.
            app.installEventFilter(self._keys)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._flush_autosave)

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
        self.export_panel = ExportPanel()
        self.export_dock = QDockWidget("Export", self)
        self.export_dock.setObjectName("export_dock")
        self.export_dock.setWidget(self.export_panel)
        self.export_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.export_dock)
        self.tabifyDockWidget(self.layer_dock, self.export_dock)
        self.sug_dock.raise_()
        self.resizeDocks([self.sug_dock, ref_dock], [430, 320], Qt.Horizontal)
        self._wire_moment_panel()
        self._wire_layer_panel()
        e = self.export_panel
        e.analyze.connect(self.analyze_magnetic_cell)
        e.preview_toggled.connect(self.preview_magnetic_cell)
        e.save_handoff.connect(lambda: self.save_handoff_dialog())
        e.submit.connect(self.submit_to_agentdft)

        self.mode_label = QLabel()
        self.statusBar().addPermanentWidget(self.mode_label)
        self.selection_label = QLabel("")
        self.statusBar().addPermanentWidget(self.selection_label)
        self._build_actions()
        self._load_arrow_settings()
        self._set_mode_chip(ia.Mode.VIEW)
        self._restore_window_state()
        self.statusBar().showMessage(f"Appearance: {self.appearance.source}")

    def _restore_window_state(self) -> None:
        """Restore the geometry/dock layout of the last run (QSettings; sizes are in
        device-independent pixels, so this is HiDPI-safe)."""
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("window/state")
        if state is not None:
            self.restoreState(state)

    def _set_mode_chip(self, mode: ia.Mode) -> None:
        """Permanent status-bar mode chip (transient showMessage text gets erased)."""
        t = self.appearance.theme
        text, color = {
            ia.Mode.VIEW: ("VIEW", (0.42, 0.42, 0.42)),
            ia.Mode.MOMENT: ("MOMENT EDIT", t.moment_mode_color),
            ia.Mode.LAYER: ("LAYER", t.layer_mode_color),
        }[mode]
        r, g, b = (round(255 * c) for c in color)
        self.mode_label.setText(text)
        self.mode_label.setStyleSheet(
            f"QLabel {{ font-weight: bold; color: rgb({r},{g},{b}); border: 1px solid "
            f"rgb({r},{g},{b}); border-radius: 3px; padding: 0 5px; margin: 1px 4px; }}")

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
        handoff_act = QAction("Export AgentDFT &handoff...", self,
                              triggered=lambda: self.save_handoff_dialog())
        file_menu.addActions([open_act, self.save_act, save_as, shot_act, handoff_act])
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
                               triggered=lambda: self._escape()))

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
        self.view_actions: list[QAction] = []
        for view in VIEWS:
            label = "3D" if view == "oblique" else view
            act = QAction(label, self, triggered=lambda _=False, v=view: self.set_view(v))
            act.setToolTip("Default oblique view" if view == "oblique" else f"View along {view}")
            view_menu.addAction(act)
            tb.addAction(act)
            self.view_actions.append(act)
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
        p.patterns.connect(lambda: self.show_patterns_menu(at_cursor=False))
        p.collinear_toggled.connect(self._set_collinear)
        p.spin_axis_changed.connect(self._set_spin_axis)
        p.arrows_changed.connect(self._arrows_changed)
        p.auto_fit.connect(self.auto_fit_arrows)

    # -- loading / saving -----------------------------------------------------------
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open structure or session", "", FILE_FILTER)
        if path:
            self.load_file(path)

    def _run_blocking(self, fn, label: str):
        """Run ``fn()`` on a worker thread behind a modal busy dialog so the GUI stays
        responsive. Returns ``(True, result)`` or ``(False, exception)``. Effectively
        synchronous for the caller: a local event loop waits for the worker, but a
        rejected/closed dialog never aborts the wait (only the worker does)."""
        signals = _WorkerSignals(self)
        loop = QEventLoop(self)
        out: dict = {}
        signals.finished.connect(lambda res: (out.__setitem__("result", res), loop.quit()))
        signals.failed.connect(lambda exc: (out.__setitem__("error", exc), loop.quit()))
        dialog = QProgressDialog(label, "", 0, 0, self)
        dialog.setObjectName("busy_progress")  # the GUI self-test watchdog ignores it
        dialog.setWindowTitle("MagView")
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setMinimumWidth(360)
        dialog.setMinimumDuration(10 ** 9)  # shown explicitly below, on our own timer
        show_later = QTimer(self)
        show_later.setSingleShot(True)
        show_later.timeout.connect(dialog.show)
        show_later.start(400)  # fast operations never flash a dialog
        runnable = _FuncRunnable(fn, signals)
        self._pending_workers.add(runnable)
        signals.finished.connect(lambda *_: self._pending_workers.discard(runnable))
        signals.failed.connect(lambda *_: self._pending_workers.discard(runnable))
        blocker = _BusyInputBlocker(self)
        app = QApplication.instance()
        app.installEventFilter(blocker)
        try:
            QThreadPool.globalInstance().start(runnable)
            loop.exec()
        finally:
            app.removeEventFilter(blocker)
            blocker.deleteLater()
            show_later.stop()
            dialog.close()
        if "error" in out:
            return False, out["error"]
        return True, out.get("result")

    def load_file(self, path: str | Path) -> bool:
        path = Path(path)

        def work():
            if path.name.endswith(".magview.json"):
                return project.load(path), path
            return MagDocument.new(CrystalStructure.from_file(path)), None

        ok, result = self._run_blocking(work, f"Opening {path.name} ...")
        if not ok:
            exc = result
            if isinstance(exc, (StructureLoadError, project.ProjectError)):
                message = str(exc)
            else:
                # Corrupt or hand-edited files can raise anything (KeyError, ValueError, ...).
                message = f"{path.name}: not a loadable file ({type(exc).__name__}: {exc})"
            QMessageBox.critical(self, "Cannot open", message)
            return False
        doc, session_path = result
        doc = self._maybe_restore(doc, path)
        self.set_document(doc, session_path=session_path)
        return True

    def _maybe_restore(self, doc: MagDocument, opened: Path) -> MagDocument:
        """Offer to restore a newer recovery autosave over the file just opened."""
        if not self.autosave_enabled:
            return doc
        auto = recovery_autosave(doc.crystal, opened)
        if auto is None:
            return doc
        when = datetime.fromtimestamp(auto.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        ans = QMessageBox.question(
            self, "Recovery autosave",
            f"A recovery autosave for {doc.crystal.name} is newer than {opened.name}\n"
            f"({auto.name}, saved {when}).\nRestore the autosaved edits?")
        if ans != QMessageBox.Yes:
            return doc
        try:
            with _wait_cursor():
                return project.load(auto)
        except Exception as exc:  # a corrupt autosave must not break the open either
            QMessageBox.warning(self, "Restore failed", f"Could not restore {auto}:\n{exc}")
            return doc

    def set_document(self, doc: MagDocument, session_path: Path | None = None) -> None:
        self._flush_autosave()  # the previous document's recovery file stays current
        if self.editor.machine.mode != ia.Mode.VIEW:
            self.editor.set_mode(ia.Mode.VIEW)
        self.layers = None
        self.current_layer = 0
        self.mag_cell = None
        self.handoff = None
        self.previewing = False
        self.editor.mode_overlay.hide()  # drop a stale read-only preview banner
        self._saved_camera = None  # camera bookmarks belong to the old document
        self.export_panel.invalidate()
        self.doc = doc
        self.session_path = session_path
        doc.history.subscribe(self._on_history)
        crystal = doc.crystal
        self.selection = frozenset()
        with _wait_cursor():
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
        self._set_mode_chip(self.editor.machine.mode)
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
        try:
            project.save(self.doc, path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save {path}:\n{exc}")
            return False
        self.session_path = path
        self.statusBar().showMessage(f"Saved {path}")
        return True

    # -- recovery autosave (debounced) ---------------------------------------------
    def _schedule_autosave(self) -> None:
        """Autosave is debounced: rapid edit bursts coalesce into one write."""
        if not self.autosave_enabled or self.doc is None:
            return
        self._autosave_pending = True
        self._autosave_timer.start()

    def _flush_autosave(self) -> None:
        self._autosave_timer.stop()
        if not self._autosave_pending or not self.autosave_enabled or self.doc is None:
            return
        self._autosave_pending = False
        try:
            project.autosave(self.doc)
        except OSError as exc:
            self.statusBar().showMessage(f"Autosave failed: {exc}")

    # -- preview edit guard -----------------------------------------------------------
    def _editing_blocked(self, what: str = "edit") -> bool:
        """True while the read-only magnetic-cell preview is on: scene indices are
        magnetic-cell indices there, so every edit/select/info path must refuse
        (and say so) instead of silently hitting wrong canvas sites."""
        if not self.previewing:
            return False
        self.statusBar().showMessage(
            f"Cannot {what} in the read-only magnetic-cell preview - "
            "untick the preview in the Export tab first")
        return True

    # -- main view ------------------------------------------------------------------
    def _rebuild_main(self, reset_camera: bool = False) -> None:
        doc = self.doc
        with _wait_cursor():
            grid = subcell_edges(doc.crystal.lattice_matrix, doc.canvas.matrix)
            has_moments = bool(np.any(np.linalg.norm(doc.moments, axis=1) > 0))
            model = build_scene_model(doc.canvas.structure, self.appearance,
                                      site_moments=doc.moments if has_moments else None,
                                      moment_view=self.moment_view, grid_edges=grid)
            self.scene.spin_axis = doc.spin_axis.copy()
            self.scene.set_model(model, reset_camera=reset_camera)
            self._refresh_selection()
            self.viewport.render()
        self._scene_fp = scene_fingerprint(doc)

    def _fast_moment_refresh(self) -> bool:
        """Moment-only edit: update arrows and atom colors in place via the scene's fast
        path (StructureScene.set_moments contract) instead of a full scene rebuild.
        Returns False when a rebuild is needed: anything but the moments changed, no
        fast path exists, or the fast path rejected the update. Never raises."""
        scene = self.scene
        if self.doc is None or scene.model is None or self._scene_fp != scene_fingerprint(self.doc):
            return False
        try:
            set_moments = scene.set_moments
        except AttributeError:
            return False
        try:
            set_moments(self._display_moments(self.doc.moments))
        except Exception:
            return False  # fall back to the full rebuild; the fast path must never break the app
        self.viewport.render()
        return True

    def _display_moments(self, canvas_moments: np.ndarray) -> np.ndarray:
        return canvas_moments[self.scene.model.atoms.parent]

    # -- selection ----------------------------------------------------------------
    def _on_select(self, sites: list[int], op: ia.SelectOp, click: bool = False) -> None:
        if self._editing_blocked("change the selection"):
            return
        if self.in_layer_mode and click and len(sites) == 1 and self.layer is not None \
                and sites[0] in set(self.layer.sites.tolist()) and op == ia.SelectOp.REPLACE:
            sign = self.doc.layer_click(sites[0])
            self.statusBar().showMessage(f"{self.doc.site_label(sites[0])}: {'up' if sign > 0 else 'down'}")
            return
        self.set_selection(apply_selection(self.selection, sites, op))

    def _on_clear(self) -> None:
        if self._editing_blocked("change the selection"):
            return
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
        if self._editing_blocked("inspect sites"):
            return
        m = self.doc.moments[site]
        frac = self.doc.canvas.structure.frac_coords[site]
        self.statusBar().showMessage(
            f"{self.doc.site_label(site)}  canvas site {site}  frac "
            f"({frac[0]:.4f}, {frac[1]:.4f}, {frac[2]:.4f})  m = "
            f"({m[0]:.2f}, {m[1]:.2f}, {m[2]:.2f}) muB")

    def select_all_magnetic(self) -> None:
        if self._editing_blocked("select sites"):
            return
        if self.doc is not None:
            self.set_selection(np.nonzero(self.doc.canvas_magnetic)[0])

    def select_equivalent(self) -> None:
        """Extend the selection to all canvas sites in the same symmetry orbits."""
        if self._editing_blocked("select sites"):
            return
        if self.doc is None or not self.selection:
            return
        eq = np.array(self.doc.crystal.symmetry.equivalent_atoms)
        orbits = set(eq[self.doc.canvas.parent[sorted(self.selection)]])
        self.set_selection(np.nonzero(np.isin(eq[self.doc.canvas.parent], list(orbits)))[0])

    def _arm_box(self) -> None:
        self.editor.key("b")
        self.editor.update_cursor()  # crosshair while the box is armed
        self.statusBar().showMessage("Box select armed: drag a rectangle")

    def _escape(self, target: QWidget | None = None) -> None:
        """Esc: cancel the active gesture / exit the mode, but let panel text fields
        keep their Esc (editing a field must not exit the mode)."""
        if not isinstance(target, QWidget):
            target = QApplication.focusWidget()
        if not self.editor.machine.busy and _is_text_entry(target):
            return
        self.editor.key("escape")
        self.editor.update_cursor()

    # -- modes ------------------------------------------------------------------------
    def set_mode(self, mode: ia.Mode) -> None:
        if self.previewing and mode != ia.Mode.VIEW:
            self.export_panel.preview.setChecked(False)  # leave the read-only preview first
        self.editor.set_mode(mode)

    def toggle_moment_mode(self) -> None:
        self.set_mode(ia.Mode.VIEW if self.editor.machine.mode == ia.Mode.MOMENT else ia.Mode.MOMENT)

    def _on_mode_changed(self, mode: ia.Mode) -> None:
        self.moment_mode_act.setChecked(mode == ia.Mode.MOMENT)
        self.layer_mode_act.setChecked(mode == ia.Mode.LAYER)
        self._set_mode_chip(mode)
        # The view toolbar drives the 3D scene; in Layer mode it would silently
        # control the layer top view instead, so disable it.
        for act in self.view_actions:
            act.setEnabled(mode != ia.Mode.LAYER)
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

    # -- camera preservation -----------------------------------------------------------
    def _snapshot_camera(self) -> None:
        """Remember the current camera so a transient view (layer mode, magnetic-cell
        preview) can restore it instead of resetting the user's framing."""
        cam = self.viewport.camera
        self._saved_camera = (tuple(cam.position), tuple(cam.focal_point), tuple(cam.up),
                              float(cam.parallel_scale), bool(cam.parallel_projection))

    def _restore_camera(self) -> None:
        if self._saved_camera is None:
            return
        pos, foc, up, scale, parallel = self._saved_camera
        self._saved_camera = None
        cam = self.viewport.camera
        cam.position, cam.focal_point, cam.up = pos, foc, up
        cam.parallel_projection = parallel
        cam.parallel_scale = scale
        self.viewport.renderer.ResetCameraClippingRange()

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
        self._snapshot_camera()
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
        self._restore_camera()  # give the user back their 3D framing, not a reset view
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
        elif not reset_camera and self._recolor_layer_discs(signs, pinned, preview):
            self.viewport.render()
            return  # hover preview: discs recolored in place, no actor rebuild flicker
        selected = np.isin(L.sites, list(self.selection))
        self.layer_scene.show(self.doc.canvas.structure, L, self.layer_axis, signs, pinned,
                              preview=preview, reset_camera=reset_camera, selected=selected)
        inputs = np.unique(self.doc.canvas.parent[L.sites])
        if self.ref_scene.model is not None:
            self.ref_scene.set_selection(np.nonzero(np.isin(self.ref_scene.model.atoms.parent, inputs))[0])
            self.reference.render()
        self.viewport.render()

    def _recolor_layer_discs(self, signs, pinned, preview) -> bool:
        """Rewrite the disc glyph colors in place for a hover preview (same geometry,
        same pins). False when the scene does not match the expected state."""
        scene = self.layer_scene
        model = scene.model
        actor = scene.actors.get("discs") if model is not None else None
        if actor is None or scene.layer is not self.layer:
            return False
        try:
            shown = np.asarray(signs, int).copy()
            mask = ~np.asarray(pinned, bool)
            preview = np.asarray(preview, int)
            is_preview = mask & (preview != shown)
            shown[mask] = preview[mask]
            t = self.appearance.theme
            rgb = np.array([t.spin_up_color if s > 0 else t.spin_down_color if s < 0 else UNSET_RGB
                            for s in shown])
            rgb[is_preview] = 0.55 * rgb[is_preview] + 0.45  # lighter, like LayerScene._blend
            u8 = np.clip(np.rint(rgb[model.local] * 255), 0, 255).astype(np.uint8)
            mesh = actor.mapper.GetInputDataObject(0, 0)
            n = len(model.atoms)
            if mesh is None or n == 0 or mesh.GetNumberOfPoints() % n:
                return False
            per = mesh.GetNumberOfPoints() // n
            data = pv.wrap(mesh)
            if tuple(data["rgb"].shape) != (per * n, 3):
                return False
            data.point_data["rgb"] = np.repeat(u8, per, axis=0)
            mesh.Modified()
            return True
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

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
        # Stepping through layers (PgUp/PgDn or the list) keeps the user's camera.
        self._refresh_layer(reset_camera=False)
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

    # -- export / handoff -----------------------------------------------------------------
    def analyze_magnetic_cell(self) -> bool:
        if self.doc is None:
            return False
        doc, axis = self.doc, self.layer_axis
        revision = doc.history.revision

        def work():
            cell = minimal_magnetic_cell(doc)  # spglib MSG search: seconds on big cells
            return cell, build_handoff(doc, cell, axis=axis)

        ok, result = self._run_blocking(work, "Analyzing the minimal magnetic cell ...")
        if not ok:
            QMessageBox.warning(self, "Analysis failed", str(result))
            return False
        if self.doc is not doc or doc.history.revision != revision:
            # Never present (or export) a result for a configuration that is gone.
            self.statusBar().showMessage("The configuration changed during the analysis - "
                                         "analyze again")
            return False
        self.mag_cell, self.handoff = result
        self.export_panel.show_result(self.mag_cell, self.handoff)
        self.export_dock.raise_()
        self.statusBar().showMessage(f"Magnetic cell: {self.mag_cell.multiplicity} x primitive "
                                     f"({self.mag_cell.describe_matrix()} of the input cell)")
        return True

    def preview_magnetic_cell(self, on: bool) -> None:
        """Show the (read-only) magnetic cell with its moments in the main view."""
        if on and self.mag_cell is None:
            return
        if self.editor.machine.mode != ia.Mode.VIEW:
            self.editor.set_mode(ia.Mode.VIEW)
        self.previewing = bool(on)
        if on:
            self._snapshot_camera()
            crystal = CrystalStructure.from_pymatgen(as_structure(self.mag_cell))
            model = build_scene_model(crystal, self.appearance, site_moments=self.mag_cell.moments,
                                      moment_view=True)
            self.scene.set_model(model, reset_camera=True)
            self.editor.mode_overlay.show("MAGNETIC CELL PREVIEW (read-only) - untick in the Export "
                                          "tab to return", (0.45, 0.35, 0.75))
        else:
            self.editor.mode_overlay.hide()
            self._rebuild_main(reset_camera=False)
            self._restore_camera()  # unticking the preview keeps the user's framing
        self.viewport.render()

    def save_handoff_dialog(self, path: str | None = None) -> Path | None:
        if self.handoff is None and not self.analyze_magnetic_cell():
            return None
        if path is None:
            name, _ = QFileDialog.getSaveFileName(self, "Save AgentDFT handoff",
                                                  f"{self.doc.crystal.name}.handoff.json",
                                                  "Handoff JSON (*.json)")
            if not name:
                return None
            path = name
        try:
            out = save_handoff(self.handoff, path)
        except HandoffError as exc:
            QMessageBox.critical(self, "Invalid handoff", "\n".join(exc.errors))
            return None
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save {path}:\n{exc}")
            return None
        self.statusBar().showMessage(f"Saved handoff {out}")
        return out

    def submit_to_agentdft(self):
        if self.handoff is None and not self.analyze_magnetic_cell():
            return None
        try:
            ticket = self.bridge.submit(self.handoff)
        except HandoffRejected as exc:
            QMessageBox.critical(self, "Handoff rejected", "\n".join(exc.report.errors))
            return None
        self.export_panel.job.setText(f"<b>Job {ticket.job_id}</b>: {ticket.status}<br>"
                                      f"{ticket.history[-1]['detail']}<br><i>{ticket.message}</i><br>"
                                      f"Stored in {ticket.path}")
        self.statusBar().showMessage(f"AgentDFT (mock): job {ticket.job_id} {ticket.status}")
        return ticket

    # -- moment drag ------------------------------------------------------------------
    def _on_drag(self, action) -> None:
        if self.doc is None:
            return
        if isinstance(action, ia.DragStart):
            if self._editing_blocked("draw moments"):
                return
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
            self._drag_pos = None
        elif isinstance(action, ia.DragUpdate):
            if self._drag is not None:
                self._drag_pos = (action.x, action.y, action.mods)
                self._refresh_drag()
        elif isinstance(action, ia.DragCommit):
            drag, self._drag = self._drag, None
            self.axis_lock = None  # the held lock key may never see its KeyRelease
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
            self.axis_lock = None
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
        if self._editing_blocked():
            return
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
        if self._editing_blocked("apply patterns"):
            return False
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

    def _selection_centroid_global(self) -> QPoint | None:
        """Selection centroid in global screen coordinates (keyboard-invoked popovers
        open there, not wherever the mouse happens to be)."""
        if not self.selection or self.scene.model is None:
            return None
        parent = self.scene.model.atoms.parent
        disp = np.nonzero(np.isin(parent, sorted(self.selection)))[0]
        if len(disp) == 0:
            return None
        xy = self.editor.display_xy(self.scene.model.atoms.cart[disp].mean(axis=0, keepdims=True))[0]
        widget = self.viewport.interactor
        w_px, h_px = self.viewport.renderer.GetSize()  # device pixels
        qw, qh = max(1, widget.width()), max(1, widget.height())  # Qt logical pixels
        gx = xy[0] * qw / max(1, w_px)
        gy = qh - xy[1] * qh / max(1, h_px)  # VTK display y is bottom-up
        return widget.mapToGlobal(QPoint(int(gx), int(gy)))

    def show_patterns_menu(self, at_cursor: bool = True) -> None:
        if self._editing_blocked("apply patterns"):
            return
        if self.doc is None or not self.selection:
            self.statusBar().showMessage("Select atoms first (click, or long-press and drag)")
            return
        pos = QCursor.pos() if at_cursor else (self._selection_centroid_global() or QCursor.pos())
        menu = self.build_patterns_menu()
        chosen = menu.exec(pos)
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
        if self._editing_blocked("edit"):
            return
        if self.in_layer_mode:
            if site is not None and self.layer is not None and site in set(self.layer.sites.tolist()):
                self.doc.layer_unset(site)
            return
        menu = self._build_context_menu(site)
        if menu is not None:
            menu.exec(QCursor.pos())

    def _build_context_menu(self, site: int | None) -> QMenu | None:
        """The context menu for the current mode; None while the read-only
        magnetic-cell preview is on (or with no document)."""
        if self.doc is None or self.previewing:
            return None
        if site is not None and site not in self.selection:
            self.set_selection({site})
        menu = QMenu(self)
        if self.editor.machine.mode == ia.Mode.MOMENT:
            if self.selection:
                menu.addAction("Flip  (F)", self.flip_selection)
                menu.addAction("Zero  (0)", self.zero_selection)
                menu.addAction("Set |m|...", self._ask_magnitude)
                menu.addAction("Set direction...", self._ask_direction)
                menu.addAction("Patterns...  (P)", lambda: self.show_patterns_menu())
                menu.addSeparator()
                menu.addAction("Select equivalent  (E)", self.select_equivalent)
        elif site is not None:
            # View mode (spec section 2): info and selection entries only, no edits.
            menu.addAction("Atom info", lambda s=site: self._on_info(s))
            menu.addAction("Select equivalent  (E)", self.select_equivalent)
        menu.addAction("Select all magnetic  (Ctrl+A)", self.select_all_magnetic)
        return menu

    def _ask_magnitude(self) -> None:
        if self._editing_blocked("set moments"):
            return
        val, ok = QInputDialog.getDouble(self, "Set |m|", "|m| (muB):", 1.0, 0.0, mg.MAX_MOMENT, 2)
        if ok:
            self._edit(lambda s: self.doc.set_magnitude(s, val))

    def _ask_direction(self) -> None:
        """Set the direction of the selected moments (each keeps its |m|)."""
        if self._editing_blocked("set moments"):
            return
        if self.doc is None or not self.selection:
            self.statusBar().showMessage("Select atoms first")
            return
        text, ok = QInputDialog.getText(self, "Set direction",
                                        "Direction: three Cartesian numbers, or an axis "
                                        "name (x, y, z, a, b, c):", text="0 0 1")
        if not ok:
            return
        try:
            parts = text.replace(",", " ").split()
            if len(parts) == 1:
                direction = mg.axis_vector(parts[0], self.doc.crystal.lattice_matrix)
            elif len(parts) == 3:
                direction = np.array([float(p) for p in parts])
            else:
                raise ValueError("need one axis name or three numbers")
            if np.linalg.norm(direction) == 0:
                raise ValueError("zero vector")
        except ValueError as exc:
            QMessageBox.warning(self, "Set direction", f"Not a direction: {text!r} ({exc})")
            return
        self._edit(lambda s: self.doc.set_direction(s, direction))

    # -- model settings -------------------------------------------------------------------
    def _set_collinear(self, on: bool) -> None:
        if self._editing_blocked("change the moment model"):
            return
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
        if self._editing_blocked("change the spin axis"):
            return
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
        if self._editing_blocked("accept suggestions"):
            return
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
        if self._editing_blocked("clear magnetic sites"):
            return
        if self.doc is not None:
            self.doc.clear_magnetic()

    def resize_canvas(self, matrix) -> bool:
        if self._editing_blocked("resize the canvas"):
            return False
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
        preview_off = False
        if self.mag_cell is not None:
            self.mag_cell = None
            self.handoff = None
            if self.previewing:
                self.previewing = False
                self.editor.mode_overlay.hide()
                preview_off = True
            self.export_panel.invalidate()
        # Canvas may have changed: drop selections that no longer exist.
        n = len(self.doc.canvas)
        self.selection = frozenset(s for s in self.selection if s < n)
        self._sync_model_ui()
        if self.in_layer_mode:
            self._main_dirty = True
            self._compute_layers()
            self._refresh_layer()
        elif preview_off or not self._fast_moment_refresh():
            self._rebuild_main(reset_camera=False)
            if preview_off:
                self._restore_camera()  # undo/redo also exits the preview with the user's camera
        self._refresh_moment_panel()
        self._update_actions()
        self._schedule_autosave()

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
        path, _ = QFileDialog.getSaveFileName(self, "Save image", "magview.png",
                                              "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if path:
            self._save_image(path)

    def _save_image(self, path: str) -> bool:
        """Screenshot to ``path``; unsupported extensions fall back to PNG and I/O
        failures become a dialog instead of a traceback."""
        path = str(path)
        if Path(path).suffix.lower() not in IMAGE_SUFFIXES:
            path += ".png"
        try:
            self.viewport.screenshot(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Save image failed", f"Could not save {path}:\n{exc}")
            return False
        self.statusBar().showMessage(f"Saved image {path}")
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self._flush_autosave()  # recovery file reflects the last edit even on quit
        self.viewport.close()
        self.reference.close()
        super().closeEvent(event)
