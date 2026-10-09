"""Command line and GUI smoke tests (subprocesses)."""

import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from conftest import DATA


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
    proc = subprocess.run(
        ["xvfb-run", "-a", "-s", "-screen 0 1400x900x24", sys.executable, "-m", "magview",
         str(DATA / "NiO.cif"), "--gui-screenshot", str(out)],
        capture_output=True, text=True, timeout=180)
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

    proc = subprocess.run(
        ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, "-m",
         "magview.gui.selftest", str(DATA / f"{name}.cif"), "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=300)
    results = json.loads((tmp_path / "selftest.json").read_text())
    failed = [r for r in results if not r["ok"]]
    assert proc.returncode == 0 and not failed, proc.stdout + proc.stderr
    assert len(results) >= 12
