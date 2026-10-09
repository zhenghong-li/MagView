"""Layering rules: core/ is GUI- and VTK-free; render/ is Qt-free."""

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
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
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
