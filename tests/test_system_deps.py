"""platforms/linux-wsl/setup_system_deps.sh: list, check and idempotent install."""

import os
import subprocess
import sys

import pytest

from conftest import SETUP_SCRIPT

LIST_FILE = SETUP_SCRIPT.parent / "system-packages.txt"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux apt system dependencies")


def run(*args):
    return subprocess.run(["bash", str(SETUP_SCRIPT), *args], capture_output=True, text=True,
                          timeout=120)


def listed_packages():
    pkgs = []
    for line in LIST_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pkgs.append(line.split()[0])
    return pkgs


def test_list_matches_file():
    proc = run("--list")
    assert proc.returncode == 0
    assert proc.stdout.split() == listed_packages()
    assert {"libosmesa6", "python3.12-venv", "xvfb"} <= set(proc.stdout.split())


def test_check_reports_complete():
    proc = run("--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all" in proc.stdout


@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() != 0, reason="install mode needs root")
def test_install_is_idempotent_noop():
    proc = run()
    assert proc.returncode == 0, proc.stderr
    assert "nothing to do" in proc.stdout


def test_rejects_unknown_option():
    assert run("--bogus").returncode == 2
