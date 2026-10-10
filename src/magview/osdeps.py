"""Everything that depends on the operating system, in one module.

MagView's code is otherwise identical on Linux/WSL, Windows and macOS: every
OS-specific path, lookup and default lives here, and the rest of the package
asks this module instead of testing ``sys.platform`` itself. Standard library
only (tests/test_architecture.py), so core/, style/ and render/ may import it.

Linux/WSL and native Windows are supported. macOS packaging is still planned.

Named ``osdeps``, not ``platform``: a module called ``platform`` shadows the
standard library one whenever this directory is on ``sys.path`` (frozen builds,
running a file inside the package), and numpy and pymatgen import ``platform``.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

VESTA_DIR_NAME = "VESTA-gtk3-x86_64"  # folder name of the Linux VESTA build
PLATFORM = sys.platform


def _env_dir(name: str, fallback: Path) -> Path:
    value = os.environ.get(name, "")
    return Path(value) if os.path.isabs(value) else fallback


def cache_dir() -> Path:
    """Cache root: %LOCALAPPDATA%/MagView/Cache on Windows; on Linux
    $XDG_CACHE_HOME/magview, falling back to ~/.cache for unset/relative XDG."""
    if PLATFORM == "win32":
        return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / "MagView" / "Cache"
    return _env_dir("XDG_CACHE_HOME", Path.home() / ".cache") / "magview"


def data_dir() -> Path:
    """Per-user data: %APPDATA%/MagView on Windows, ~/.magview on Linux."""
    if PLATFORM == "win32":
        return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / "MagView"
    return Path.home() / ".magview"


def has_display() -> bool:
    """Native desktop platforms do not advertise their display through X11 vars."""
    if PLATFORM in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def settings_file() -> Path | None:
    """Optional INI settings override; isolates tests from the Windows registry."""
    override = os.environ.get("MAGVIEW_SETTINGS_DIR")
    if not override:
        return None
    folder = Path(override)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "MagView.ini"


def atomic_replace(source: Path, target: Path) -> None:
    """Retry a short Windows file lock (e.g. antivirus); never truncate the target."""
    attempts = 3 if PLATFORM == "win32" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1)


def configure_stdio() -> None:
    """UTF-8 console output; tolerate missing console streams."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        elif PLATFORM == "win32" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def vesta_candidates(roots: Iterable[Path]) -> Iterator[Path]:
    """VESTA folders to try, in order: ``<ancestor>/vesta_tools/VESTA-gtk3-x86_64``
    for every ancestor of each root (each ancestor once)."""
    seen: set[Path] = set()
    for root in roots:
        for anc in [root, *root.parents]:
            if anc not in seen:
                seen.add(anc)
                yield anc / "vesta_tools" / VESTA_DIR_NAME
                if PLATFORM == "win32":
                    for parent in (anc, anc / "Apps", anc / "Apps" / "Others"):
                        yield parent / "VESTA-win64"
                        yield parent / "VESTA"
    if PLATFORM == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(variable)
            if value and os.path.isabs(value):
                for parent in (Path(value), Path(value) / "Programs"):
                    yield parent / "VESTA"
                    yield parent / "VESTA-win64"
