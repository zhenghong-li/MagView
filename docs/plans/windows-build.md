# Plan: MagView for Windows

**Goal:** MagView on Windows 10 (22H2) and 11, x64, with no Python and no admin rights needed. Same code base as Linux/WSL (see [README.md](README.md) for the design rule and the shared groundwork G1–G9).

**Deliverables (GitHub Release assets):**
- `MagView-X.Y.Z-windows-x64-setup.exe`: per-user installer (Inno Setup).
- `MagView-X.Y.Z-windows-x64.zip`: portable folder; unzip and run.
- A `.sha256` file for each.

**Out of scope:** Windows on ARM (it runs the x64 build under emulation; there are no native ARM64 VTK wheels to build against) and the Microsoft Store.

**Implementation update (2026-10-10):** Native source support now lives in [platforms/windows](../../platforms/windows/README.md), including the Windows dependency lock, setup/launch scripts, native test harness and high-DPI self-test coordinates. The current delivery is source-only. Executable packaging, installer, signing, software rendering and CI portions below remain optional roadmap items. See the [validation report](../../platforms/windows/VALIDATION.md) for checks actually run.

## What already works for Windows
- All dependencies ship Windows x64 wheels: PySide6-Essentials, VTK, pymatgen, spglib, numpy, scipy.
- The PyInstaller spec, launcher and license collector in `packaging/` are shared.
- Qt stores settings in the registry (`HKCU\Software\MagView`) with no code change.
- High-DPI: the one display↔Qt coordinate conversion (patterns popover) already converts device to logical pixels.

## Phases

### W1. Run from source; test suite green on Windows (size M)
*Prerequisites:* G1 (folders, `has_display`, VESTA lookup), G2 (UTF-8), G5 (portable harness), G6 (CI with a `windows-2022` job).

**Expected fixes:**
- **Autosave retries.** `os.replace` can fail with `PermissionError` while antivirus or a backup tool briefly holds the target. Session save and autosave get a short retry (3 attempts, 100 ms apart) and then the existing error dialog.
- **Test paths.** Tests that build POSIX paths get rewritten with `tmp_path` / `Path` joins only.
- **VESTA lookup on Windows.** Search `MAGVIEW_VESTA_DIR`, then the GUI setting (G3), then `VESTA-win64` / `VESTA` folders in `%ProgramFiles%`, `C:\`, `%USERPROFILE%`, Desktop and Downloads. The Windows VESTA zip keeps `elements.ini` and `style.ini` at the top of its folder; verify on a real install.

**Verify:**
- Full pytest on the `windows-2022` runner.
- The GUI self-test natively on the runner, with Mesa GL from `setup-headless-display-action`: 92/92.

### W2. OpenGL robustness (size M, needs a spike)
VTK needs OpenGL 3.2. Any GPU driver from the last decade provides it. **Microsoft Basic Render Driver does not** (OpenGL 1.1): that's what VMs, Remote Desktop without GPU, some Hyper-V setups and the CI runners use. There the 3D view stays black or VTK aborts.

1. **Detect it at start-up.** Query the render window's OpenGL support once and show a clear dialog instead of a black view or a crash. The dialog names the GPU driver, explains what's needed, and offers the software-rendering shortcut.
2. **Software fallback.** Ship Mesa llvmpipe (mesa-dist-win, MIT, about 30 MB) and a second Start-menu entry, *MagView (software rendering)*.
   - **Spike:** check that VTK inside `QOpenGLWidget` uses Mesa's `opengl32.dll` when it is loaded first.
   - **Option A:** a launcher that preloads the Mesa DLL via `os.add_dll_directory` + `ctypes`.
   - **Option B:** Mesa's per-application deployment, i.e. a separate small exe whose folder holds the Mesa DLLs.
   - Pick whichever works with PyInstaller's one-folder layout.
3. **High-DPI check** at 100 %, 125 %, 150 % and 200 % display scaling: picking, box select, hover outline, the patterns popover, the cursor label, screenshots.

### W3. Packaging (size S)
`platforms/windows/build.ps1` mirrors the Linux `build.sh`:

1. Create a fresh venv from `requirements/windows.txt` + `requirements-build.txt`.
2. Run PyInstaller with the shared spec. Spec additions for Windows:
   - **`MagView.exe`**, windowed (`console=False`), with icon (G8) and version resource. No console window for GUI users.
   - **`magview-cli.exe`**, console, in the same folder. It keeps `--render` and `--help` output visible in a terminal; both exes share `_internal\`.
3. Stage `README.txt` (Windows wording), `licenses\` and `examples\`.
4. Run the packaged self-test: `magview-cli.exe examples\NiO.cif --selftest out`.
5. Zip the result and write the `.sha256`.

**Check:** a VirusTotal scan of the zip. PyInstaller's prebuilt bootloader sometimes trips heuristic antivirus engines; if it does, build the bootloader from source in CI and sign (W5).

### W4. Installer (size S)
`platforms/windows/magview.iss` (Inno Setup 6, free):
- **Install location:** per user, `%LOCALAPPDATA%\Programs\MagView`. No UAC prompt; an *all users* option is available for lab machines.
- **Shortcuts:** Start-menu entries *MagView* and *MagView (software rendering)* (W2); an optional desktop shortcut.
- **File association:** `.magview.json` always, as ProgID `MagView.Session` with an "Open with MagView" verb. `.cif` is offered **unticked** (D5), because VESTA usually owns it.
- **Uninstall:** removes program files only. Sessions, autosaves and settings stay.
- **Silent install** with `/VERYSILENT /SUPPRESSMSGBOXES` for scripted lab installs.

### W5. Code signing (decision D2)
- **Signed:** sign `MagView.exe`, `magview-cli.exe` and the setup exe with Azure Trusted Signing or an OV certificate, plus an RFC 3161 timestamp, in `release.yml` using repository secrets.
- **Unsigned:** SmartScreen shows "Windows protected your PC". The README documents *More info → Run anyway*. Reputation builds up slowly per release.

### W6. QA and release (size S)
Manual checklist, run on your Windows machine plus a VM:

| Area | Checks |
|---|---|
| Real GPU | Windows 11, 100 % and 150 % scaling: open the examples, drag moments, box select, layer mode, analyze, export a handoff |
| No GPU | Windows 10 22H2 VM with the Basic Render Driver: the W2 dialog appears and the software-rendering shortcut works |
| Paths | Install and data folders under a user name with spaces and non-ASCII characters; a session saved there reopens |
| VESTA | `VESTA-win64` present (VESTA colors) and absent (fallback); the Preferences setting |
| Explorer | Double-click a `.magview.json`; *Open with* on a `.cif` |
| Lifecycle | Install, upgrade over the previous version, uninstall (data kept) |

Then attach the zip, the setup exe and the `.sha256` files to the version's GitHub Release (G6 automates this from the tag).

## Risks

| Risk | Mitigation |
|---|---|
| No usable OpenGL (VMs, RDP, runners) | W2 detection dialog + Mesa fallback; Mesa in CI |
| Antivirus false positives on PyInstaller exes | Self-built bootloader, signing (W5), VirusTotal check before each release |
| `PermissionError` on atomic replace (antivirus, OneDrive-synced folders) | Retry in save/autosave (W1); autosaves live in `%LOCALAPPDATA%` (not synced) |
| Size (≈ 1 GB installed) | VTK trimming (see README.md); Inno Setup LZMA2 compression for the download |
| Wheel or Qt changes between Linux and Windows pins | Per-OS lock files (G7); CI runs every pin set |
