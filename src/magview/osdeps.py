"""Everything that depends on the operating system, in one module.

MagView's code is otherwise identical on Linux/WSL, Windows and macOS: every
OS-specific path, lookup and default lives here, and the rest of the package
asks this module instead of testing ``sys.platform`` itself. Standard library
only (tests/test_architecture.py), so core/, style/ and render/ may import it.

The released behavior is Linux/WSL. The Windows and macOS branches arrive with
the native builds (docs/plans/windows-build.md, docs/plans/macos-build.md).

Named ``osdeps``, not ``platform``: a module called ``platform`` shadows the
standard library one whenever this directory is on ``sys.path`` (frozen builds,
running a file inside the package), and numpy and pymatgen import ``platform``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

VESTA_DIR_NAME = "VESTA-gtk3-x86_64"  # folder name of the Linux VESTA build


def cache_dir() -> Path:
    """Per-user cache root (recovery autosaves): ``$XDG_CACHE_HOME/magview``, or
    ``~/.cache/magview`` when the variable is unset or, per the XDG spec, not an
    absolute path."""
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "magview"


def data_dir() -> Path:
    """Per-user data root (mock AgentDFT jobs): ``~/.magview``."""
    return Path.home() / ".magview"


def has_display() -> bool:
    """True when a windowing system is reachable (X11 or Wayland, e.g. WSLg)."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def vesta_candidates(roots: Iterable[Path]) -> Iterator[Path]:
    """VESTA folders to try, in order: ``<ancestor>/vesta_tools/VESTA-gtk3-x86_64``
    for every ancestor of each root (each ancestor once)."""
    seen: set[Path] = set()
    for root in roots:
        for anc in [root, *root.parents]:
            if anc not in seen:
                seen.add(anc)
                yield anc / "vesta_tools" / VESTA_DIR_NAME
