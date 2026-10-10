"""Layering rules: core/, io/ and bridge/ are GUI- and VTK-free; render/ is Qt-free;
osdeps.py (the only OS-specific module) uses the standard library alone."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "magview"

FORBIDDEN = {
    "core": {"PySide6", "qtpy", "pyvistaqt", "vtk", "vtkmodules", "pyvista", "magview.render",
             "magview.gui", "magview.style"},
    "render": {"PySide6", "qtpy", "pyvistaqt", "magview.gui"},
    "style": {"PySide6", "qtpy", "pyvistaqt", "vtk", "vtkmodules", "pyvista", "magview.render",
              "magview.gui"},
    # The handoff and the bridge run headless (AgentDFT side): no GUI or drawing stack.
    "io": {"PySide6", "qtpy", "pyvistaqt", "vtk", "vtkmodules", "pyvista", "magview.render",
           "magview.gui", "magview.style"},
    "bridge": {"PySide6", "qtpy", "pyvistaqt", "vtk", "vtkmodules", "pyvista", "magview.render",
               "magview.gui", "magview.style"},
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_layer_imports(package):
    bad = []
    for path in sorted((SRC / package).rglob("*.py")):
        for mod in imported_modules(path):
            for forbidden in FORBIDDEN[package]:
                if mod == forbidden or mod.startswith(forbidden + "."):
                    bad.append(f"{path.relative_to(SRC)} imports {mod}")
    assert not bad, "\n".join(bad)


def test_osdeps_is_stdlib_only():
    """osdeps.py is imported by core/, style/ and render/, so it may use nothing but
    the standard library (and must stay the single home of OS-specific code)."""
    import sys

    mods = imported_modules(SRC / "osdeps.py")
    assert {m for m in mods if m.split(".")[0] not in sys.stdlib_module_names} == set()


def test_no_os_checks_outside_osdeps():
    """OS branches belong in osdeps.py; elsewhere they would scatter platform logic
    over the shared code base again."""
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "osdeps.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("sys.platform", "os.name ==", "platform.system("):
            if needle in text:
                bad.append(f"{path.relative_to(SRC)}: {needle}")
    assert not bad, "\n".join(bad)
