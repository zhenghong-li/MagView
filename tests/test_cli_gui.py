"""Command line and GUI smoke tests (subprocesses)."""

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from conftest import DATA, gui_env, run_subprocess


def test_cli_render(tmp_path):
    out = tmp_path / "x.png"
    proc = subprocess.run([sys.executable, "-m", "magview", str(DATA / "Cs3V9Te13_300K.cif"),
                           "--render", str(out), "--view", "c", "--size", "400", "300"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "P-62m (#189)" in proc.stdout
    assert Image.open(out).size == (400, 300)


def test_cli_bad_file(tmp_path):
    bad = tmp_path / "bad.cif"
    bad.write_text("garbage")
    proc = subprocess.run([sys.executable, "-m", "magview", str(bad), "--render",
                           str(tmp_path / "y.png")], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "error" in proc.stderr


@pytest.mark.gui
@pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not installed")
def test_gui_shell_screenshot(tmp_path):
    out = tmp_path / "gui.png"
    proc = run_subprocess(
        ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, "-m", "magview",
         str(DATA / "NiO.cif"), "--gui-screenshot", str(out)], timeout=180, env=gui_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    img = np.asarray(Image.open(out).convert("RGB")).astype(int)
    assert img.shape[0] > 600 and img.shape[1] > 1000
    # The 3D viewport must contain rendered atoms (Ni gray / O red), not a blank widget.
    center = img[img.shape[0] // 4: 3 * img.shape[0] // 4, img.shape[1] // 8: img.shape[1] // 2]
    red = (center[..., 0] > 150) & (center[..., 1] < 60) & (center[..., 2] < 60)
    assert red.mean() > 0.01


@pytest.mark.gui
@pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not installed")
@pytest.mark.parametrize("name", ["NiO", "Cs3V9Te13_300K"])
def test_gui_selftest(tmp_path, name):
    """Real Qt events: click, Shift-click, camera drag, long-press box, Esc, accept,
    undo/redo, canvas resize, Ctrl+A (see magview.gui.selftest)."""
    import json

    proc = run_subprocess(
        ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, "-m",
         "magview.gui.selftest", str(DATA / f"{name}.cif"), "--out", str(tmp_path)], timeout=300,
        env=gui_env())
    results = json.loads((tmp_path / "selftest.json").read_text())
    failed = [r for r in results if not r["ok"]]
    assert proc.returncode == 0 and not failed, proc.stdout + proc.stderr
    assert len(results) >= 12


SEED_SETTINGS = """
import sys
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow
app = QApplication([])
w = QMainWindow()
w.resize(500, 400)
s = QSettings(sys.argv[1], QSettings.IniFormat)
s.setValue("window/geometry", w.saveGeometry())
s.setValue("arrows/scale", 4.75)
s.sync()
"""


@pytest.mark.gui
@pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not installed")
def test_capture_mode_ignores_user_settings(tmp_path):
    """--gui-screenshot is a batch run: a small window layout saved by an earlier
    interactive session must not shrink the capture, and the capture must not write
    the user's settings or recovery autosaves (this once made the shell-screenshot
    test depend on whoever ran the GUI last)."""
    conf = tmp_path / "xdg" / "MagView" / "MagView.conf"
    conf.parent.mkdir(parents=True)
    env = gui_env(XDG_CONFIG_HOME=str(tmp_path / "xdg"), MAGVIEW_AUTOSAVE_DIR=str(tmp_path / "autosave"))
    seed = run_subprocess([sys.executable, "-c", SEED_SETTINGS, str(conf)], timeout=60,
                          env=dict(env, QT_QPA_PLATFORM="offscreen"))
    assert seed.returncode == 0 and conf.is_file(), seed.stderr
    before = conf.read_bytes()
    out = tmp_path / "gui.png"
    proc = run_subprocess(
        ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, "-m", "magview",
         str(DATA / "NiO.cif"), "--gui-screenshot", str(out)], timeout=180, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert Image.open(out).size[0] > 1000  # default layout, not the saved 500 x 400
    assert conf.read_bytes() == before  # user settings untouched
    assert not (tmp_path / "autosave").exists() or not any((tmp_path / "autosave").iterdir())


def test_gui_env_drops_offscreen_config(monkeypatch):
    """GUI subprocesses must not inherit this process's offscreen setup: with
    QT_QPA_PLATFORM=offscreen and an OSMesa VTK window the "xvfb" tests silently
    exercised a headless hybrid instead of the X/GLX path users run."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("VTK_DEFAULT_OPENGL_WINDOW", "vtkOSOpenGLRenderWindow")
    env = gui_env(FOO="1")
    assert "QT_QPA_PLATFORM" not in env and "VTK_DEFAULT_OPENGL_WINDOW" not in env
    assert env["FOO"] == "1" and env["PATH"] == os.environ["PATH"]
