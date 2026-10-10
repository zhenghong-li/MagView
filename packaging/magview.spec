# PyInstaller spec shared by every platform build (platforms/<os>/build.*).
# Run from the repository root, inside a build venv that has requirements.txt,
# requirements-build.txt and MagView itself installed:
#
#     pyinstaller --noconfirm --clean packaging/magview.spec
#
# Output: dist/MagView/ (one folder: the "magview" executable plus _internal/).
# One-folder mode keeps start-up fast and lets users replace the Qt libraries
# (LGPL).

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is defined by PyInstaller)

datas = []
datas += collect_data_files("magview")    # handoff.schema.json
datas += collect_data_files("pymatgen")   # element data, oxidation-state and CIF tables
for dist in ("pymatgen", "spglib", "pyvista", "pyvistaqt", "monty"):
    datas += copy_metadata(dist)          # packages that read their own version at import

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    binaries=collect_dynamic_libs("spglib"),
    datas=datas,
    excludes=["tkinter", "pytest", "_pytest", "IPython", "jupyter_client", "notebook",
              "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
# Qt's GTK3 theme plugin needs a full GTK stack on the host and is never used.
a.binaries = [b for b in a.binaries if "platformthemes/libqgtk3" not in b[0].replace("\\", "/")]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="magview",
    console=True,  # Linux/macOS: irrelevant for the GUI; keeps --render/--help output visible
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MagView", upx=False)  # noqa: F821
