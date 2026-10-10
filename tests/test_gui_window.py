"""Window-level GUI tests.

The full scripted interaction suite lives in ``magview.gui.selftest`` and runs
here in a subprocess on Qt's offscreen platform (no X server, no xvfb-run
needed; the existing test_gui_selftest covers the xvfb variant). The subprocess
keeps the GUI away from this process's offscreen-VTK configuration. Pure
helpers of the main window are tested in-process below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import DATA, run_subprocess

GUI_UNAVAILABLE = 77  # selftest exit code when the GUI cannot start headlessly


@pytest.mark.gui
def test_gui_selftest_offscreen(tmp_path):
    """Every phase of magview.gui.selftest, including phase6 robustness checks
    (read-only preview guard, corrupt session, camera preservation, focus-proof
    key handling, hover feedback, autosave recovery, save errors)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("VTK_DEFAULT_OPENGL_WINDOW", None)  # the Qt widget must not get an OSMesa window
    proc = run_subprocess(
        [sys.executable, "-m", "magview.gui.selftest", str(DATA / "NiO.cif"),
         "--out", str(tmp_path)], timeout=300, env=env)
    if proc.returncode == GUI_UNAVAILABLE:
        pytest.skip("Qt offscreen platform cannot host the GUI here: "
                    + (proc.stdout + proc.stderr).strip().splitlines()[-1])
    results_file = tmp_path / "selftest.json"
    assert results_file.is_file(), proc.stdout + proc.stderr
    import json

    results = json.loads(results_file.read_text())
    failed = [r for r in results if not r["ok"]]
    assert proc.returncode == 0 and not failed, proc.stdout + proc.stderr
    assert len(results) >= 40  # phases 1-6


# -- pure helpers (importable without a display) ----------------------------------------


def test_scene_fingerprint_discriminates_moment_only(load):
    """The fingerprint behind the moment-only fast path: unchanged exactly when
    nothing but the moment vectors changed."""
    from magview.core.document import MagDocument
    from magview.gui.main_window import scene_fingerprint

    doc = MagDocument.new(load("NiO"))
    fp0 = scene_fingerprint(doc)
    assert scene_fingerprint(doc) == fp0
    doc.set_moments([0], [0, 0, 1.0])  # also flags the site magnetic: structural
    assert scene_fingerprint(doc) != fp0
    fp1 = scene_fingerprint(doc)
    for edit in (lambda: doc.flip([0]), lambda: doc.zero([0]),
                 lambda: doc.set_moments([0], [0, 0, 2.0]),
                 lambda: doc.set_magnitude([0], 3.0), lambda: doc.set_direction([0], [0, 0, 1]),
                 lambda: doc.apply_signs([0], [-1], "test")):
        edit()
        assert scene_fingerprint(doc) == fp1
    doc.layer_click(0)  # touches pinned
    assert scene_fingerprint(doc) != fp1
    doc.history.undo()
    doc.zero([0])  # all-zero moments are periodic in any canvas
    doc.resize_canvas((1, 1, 1))
    assert scene_fingerprint(doc) != fp1


def test_recovery_autosave_candidate(load, tmp_path, monkeypatch):
    """recovery_autosave finds the recovery file of the same structure only, and only
    when it is newer than the file being opened."""
    from pymatgen.core import Lattice, Structure

    from magview.core import project
    from magview.core.structure import CrystalStructure
    from magview.gui.main_window import recovery_autosave

    monkeypatch.setenv(project.ENV_AUTOSAVE_DIR, str(tmp_path / "autosave"))
    nio = load("NiO")
    session = tmp_path / "NiO.magview.json"
    session.write_text("{}")
    assert recovery_autosave(nio, session) is None  # no autosave yet
    auto = project.autosave_path(nio)
    auto.write_text("{}")
    old = session.stat().st_mtime
    os.utime(auto, (old - 10, old - 10))  # autosave older than the session: no offer
    assert recovery_autosave(nio, session) is None
    os.utime(auto, (old + 10, old + 10))  # autosave newer: offered
    assert recovery_autosave(nio, session) == auto
    missing = tmp_path / "gone.magview.json"
    assert recovery_autosave(nio, missing) == auto  # opened file gone: still offered
    # A different structure that happens to have the same file name is never offered
    # this recovery file (it used to be: autosaves were keyed by the file stem only).
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    Structure(Lattice.cubic(5.0), ["Mn", "O"], [[0, 0, 0], [.5, .5, .5]]).to(
        filename=str(other_dir / "NiO.cif"))
    other = CrystalStructure.from_file(other_dir / "NiO.cif")
    assert other.name == nio.name
    assert recovery_autosave(other, missing) is None