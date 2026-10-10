# macOS

## Run from source

Use native Python 3.12 on macOS 15 or newer (the current dependency pins include
macOS 15 wheels). Apple Silicon and Intel must use Python matching their CPU.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e . --no-deps
.venv/bin/python -m magview tests/data/NiO.cif
```

Open a CIF with File > Open or Command+O. Press M for moment editing and L for
layers; use the Export tab to analyze the magnetic cell and save the handoff.
Command-click toggles selection. A trackpad supports pinch and smooth
two-finger scroll to zoom; Shift+drag on empty space pans. F1 opens the complete
keyboard and mouse reference. Shortcut hints use native Command symbols.

Run the tests in a normal desktop Terminal session:

```sh
.venv/bin/python -m pytest -q
```

The suite includes native GUI checks for opening CIFs, selecting and dragging
moments, layers, session/image/handoff export and trackpad events. The graphics
preflight needs access to AppKit and a working OpenGL context; a shell sandbox
that blocks macOS application registration cannot run these checks.

## Packaging

App bundles and DMGs remain planned; see [docs/plans/macos-build.md](../../docs/plans/macos-build.md).

This folder will hold the macOS-only build files: `build.sh` (PyInstaller `.app` bundle, packaged self-test, DMG), `Info.plist` additions, entitlements and the signing/notarization script. The application code is the shared `src/magview`; OS-specific code goes into `src/magview/osdeps.py`.
