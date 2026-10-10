"""Shared fixtures and the system-library preflight.

The preflight renders one pixel offscreen before any test runs. If the system
OpenGL/OSMesa libraries are missing (e.g. after the container was recreated),
the whole session stops with a pointer to scripts/setup_system_deps.sh instead
of failing in confusing ways later.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
SETUP_SCRIPT = ROOT / "scripts" / "setup_system_deps.sh"

PREFLIGHT = """
from magview.render.offscreen import make_plotter
import pyvista as pv
p = make_plotter((8, 8))
p.add_mesh(pv.Sphere())
img = p.screenshot(return_img=True)
p.close()
assert img.shape == (8, 8, 3)
"""


def pytest_configure(config):
    """Hermetic test session: Qt settings (window layout, arrow settings), recovery
    autosaves and mock AgentDFT jobs go to a throwaway directory, never the
    developer's real ~/.config / ~/.cache / ~/.magview. Subprocesses (CLI, GUI
    capture, self-test) inherit the environment."""
    home = Path(tempfile.mkdtemp(prefix="magview-tests-"))
    config._magview_test_home = home
    os.environ["XDG_CONFIG_HOME"] = str(home / "config")
    os.environ["MAGVIEW_AUTOSAVE_DIR"] = str(home / "autosave")
    os.environ["MAGVIEW_AGENTDFT_DIR"] = str(home / "agentdft_jobs")


def pytest_unconfigure(config):
    home = getattr(config, "_magview_test_home", None)
    if home is not None:
        shutil.rmtree(home, ignore_errors=True)


def run_subprocess(cmd, timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run(capture_output=True, text=True)`` that kills the whole process
    group on timeout: killing only ``xvfb-run`` leaves its Xvfb server and the GUI
    process running forever."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        pytest.fail(f"timed out after {timeout} s: {' '.join(map(str, cmd))}\n{out}\n{err}")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def pytest_sessionstart(session):
    # Run in a subprocess: a missing shared library can abort the interpreter.
    proc = subprocess.run([sys.executable, "-c", PREFLIGHT], capture_output=True, text=True,
                          timeout=120)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-8:])
        pytest.exit(
            "Offscreen rendering preflight failed: system libraries are probably missing "
            f"(container recreated?).\nFix: sudo {SETUP_SCRIPT}\n--- preflight output ---\n{tail}",
            returncode=3,
        )


@pytest.fixture(scope="session", autouse=True)
def _offscreen():
    from magview.render.offscreen import configure_offscreen

    configure_offscreen()


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def load():
    from magview.core.structure import CrystalStructure

    cache: dict[str, CrystalStructure] = {}

    def _load(name: str) -> CrystalStructure:
        if name not in cache:
            cache[name] = CrystalStructure.from_file(DATA / f"{name}.cif")
        return cache[name]

    return _load


@pytest.fixture(scope="session")
def vesta_dir():
    from magview.style.vesta_ini import find_vesta_dir

    d = find_vesta_dir(ROOT)
    if d is None:
        pytest.skip("no local VESTA installation found (set MAGVIEW_VESTA_DIR)")
    return d


@pytest.fixture(scope="session")
def appearance():
    """VESTA-backed appearance when available, else the built-in fallback."""
    from magview.style.appearance import Appearance

    return Appearance.load()
