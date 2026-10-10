# Windows

Native Windows source setup. Application code remains shared
in `src/magview`; OS defaults live in `src/magview/osdeps.py`.

## Run from source

Use **native Windows 64-bit Python 3.13** (tested), Windows 10/11 x64, and a
graphics driver supporting OpenGL 3.2+. Commands below run from the repository
root in PowerShell. WSL's `.venv` is separate and is not reused.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File platforms\windows\setup.ps1
.\platforms\windows\run.cmd tests\data\NiO.cif
.\platforms\windows\run.cmd "D:\Projects\DFT\Cs3V9Te13_300K.cif"
```

`setup.ps1 -Python 'C:\path\to\python.exe'` selects a different interpreter.
Dependencies and the editable package go into `.venv-windows`. Activation and
administrator privileges are unnecessary. `run.ps1` accepts the same arguments
as `run.cmd`; either launcher works from other working directories too.

On another Windows computer: install Python 3.13 x64 (enable **Add python.exe to
PATH**), download/extract or clone this repository, then double-click
`platforms\windows\run.cmd`. On first launch it calls `setup.cmd` to install
dependencies; later launches reuse the local environment. Internet access is
needed for the first setup. Use **File > Open** to choose a CIF, or pass its path
as shown above. Only source code and configuration are distributed.

Optional VESTA settings, read from your installation at runtime:

```powershell
$env:MAGVIEW_VESTA_DIR = 'D:\Apps\Others\VESTA-win64'
.\platforms\windows\run.cmd tests\data\NiO.cif
```

VESTA is also discovered in ancestor folders named `VESTA` / `VESTA-win64`,
`Apps\Others`, and common Program Files locations. Without VESTA, the built-in
fallback is used. No VESTA files are copied into the bundle.

## Test

```powershell
$env:PYTHONUTF8 = '1'
.\.venv-windows\Scripts\python.exe -m pytest -q
.\.venv-windows\Scripts\python.exe -m magview.gui.selftest tests\data\NiO.cif --out build\windows-selftest
.\platforms\windows\run.cmd tests\data\NiO.cif --render build\nio.png --view c
```

GUI tests open native desktop windows; run them sequentially in a logged-in
desktop session. They exercise selection, moment drags, undo/redo, patterns,
layers, stacking, session/recovery and handoff export. Linux apt checks are
skipped on Windows. Qt's optional `offscreen` platform may be unavailable;
native GUI checks still run. Test settings use temporary INI files and never
overwrite your registry settings. Captures contain only MagView controls and
its VTK framebuffers, even when another window covers the app.

## Windows configuration

| Data | Default | Override |
|---|---|---|
| Recovery autosaves | `%LOCALAPPDATA%\MagView\Cache\autosave` | `MAGVIEW_AUTOSAVE_DIR`; `off` disables |
| Mock AgentDFT jobs | `%APPDATA%\MagView\agentdft_jobs` | `MAGVIEW_AGENTDFT_DIR` |
| Qt settings | `HKCU\Software\MagView\MagView` | `MAGVIEW_SETTINGS_DIR` selects an INI folder |

`requirements.txt` is the Windows lock file. Qt/PySide and shiboken are pinned
together to **6.8.3**: 6.11.2 and 6.12.0 failed to load QtCore on the validation
host (Windows ICU/DLL entry points). Linux retains its existing dependency pins.
Project and handoff JSON use UTF-8; atomic saves retry short Windows file locks.
GUI self-tests convert between Qt logical and VTK physical pixels for high DPI.

This source configuration uses native GPU OpenGL. Mesa software rendering and
headless Windows CI have not been validated. See [VALIDATION.md](VALIDATION.md)
for the host and checks actually run. Executable packaging is outside this
delivery; the [older Windows roadmap](../../docs/plans/windows-build.md)
contains optional future packaging ideas.
