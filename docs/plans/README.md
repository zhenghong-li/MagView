# Native builds: plans

| Platform | Status | Plan |
|---|---|---|
| Linux / WSL (x86_64) | released (0.1.0): `platforms/linux-wsl/build.sh` | done |
| Windows 10/11 (x64) | planned | [windows-build.md](windows-build.md) |
| macOS (Apple Silicon, Intel) | planned | [macos-build.md](macos-build.md) |

**Design rule (kept by both plans):** one code base. Nothing is forked per platform. OS differences in the code go into `src/magview/osdeps.py` only (the architecture test forbids `sys.platform` checks elsewhere). Everything outside the code that differs per OS goes into `platforms/<os>/`: build scripts, installer definitions, Info.plist, entitlements. The PyInstaller spec, launcher and license collection in `packaging/` are shared.

**Definition of done for a platform:**
- The packaged app passes the same 92-check GUI self-test (`magview FILE --selftest DIR`) on that OS.
- The manual QA checklist in its plan is ticked.
- The archives and their `.sha256` files are attached to the GitHub Release of the version tag.

## Shared groundwork (do once, before either build)

| ID | Work | Why |
|---|---|---|
| G1 | **Per-OS branches in `osdeps.py`.** Add per-OS folders, display detection and VESTA lookup, each with unit tests that monkeypatch the platform. Folders: Windows `%LOCALAPPDATA%\MagView\Cache` and `%APPDATA%\MagView`; macOS `~/Library/Caches/MagView` and `~/Library/Application Support/MagView`. `has_display()` returns true on Windows and macOS. | Today `has_display()` is false there (no `DISPLAY` variable), so `--render` would request OSMesa, which the Windows and macOS VTK wheels don't have. |
| G2 | **UTF-8 everywhere.** Add `encoding="utf-8"` to the 10 text read/write sites (sessions, handoffs, jobs, schema), plus an architecture test that rejects `read_text()`/`open()` without an explicit encoding. | Windows defaults to cp1252. A hand-edited session with non-ASCII text would be garbled. |
| G3 | **A VESTA-folder setting in the GUI**: *Preferences → VESTA folder…*, stored in QSettings and validated (it must contain `elements.ini`). `$MAGVIEW_VESTA_DIR` keeps priority. | Setting environment variables for apps started from the Start menu or Finder is impractical. This is a small new UI element and needs your OK (D6). |
| G4 | **Native shortcut labels.** Build the 11 hard-coded "Ctrl+…" strings (F1 table, menus, status bar, panel hints) from `QKeySequence(...).toString(QKeySequence.NativeText)`. | macOS users must see ⌘Z, not Ctrl+Z. Windows and Linux text stays the same. |
| G5 | **Portable test harness.** `run_subprocess` kills the process tree on Windows (`CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`). GUI tests run natively where a desktop session exists, and through `xvfb-run` only on Linux. Tests that hard-code POSIX paths are made OS-neutral. | The suite currently assumes `killpg`, `xvfb-run` and `/` paths. |
| G6 | **CI on GitHub Actions** (free for public repos; the stored token has the `workflow` scope). `test.yml` runs the suite on `ubuntu-24.04`, `windows-2022`, `macos-14` (arm64) and an Intel macOS image. `release.yml` triggers on tag `v*`: it builds each platform, runs the packaged self-test and uploads to a **draft** Release, which you publish. GL on runners comes from `pyvista/setup-headless-display-action` (Mesa on Windows). | Reproducible builds for the two OSes this container cannot run, and an automatic regression net. |
| G7 | **Per-OS lock files** `requirements/{linux,windows,macos}.txt`, generated on each OS. `requirements.txt` stays the Linux lock. | `requirements.txt` is a Linux freeze. Windows-only and macOS-only transitive dependencies (e.g. `colorama`) are missing from it, so pins would drift. |
| G8 | **App icon** as `.png`, `.ico` and `.icns`, rendered by MagView itself (a spin arrow through an atom), with the script in `packaging/`. | There is no icon. Windows and macOS show a generic one, and installers need one. |
| G9 | *(optional)* **Older-glibc Linux build.** Build the Linux release on `ubuntu-22.04` with `actions/setup-python` 3.12. | The 0.1.0 bundle needs glibc 2.39 (Ubuntu 24.04+). This would also cover Ubuntu 22.04 WSL installs. |

Later optimization, not blocking: the bundle is 950 MB unpacked (290 MB compressed), 554 MB of it VTK. Excluding unused VTK libraries could roughly halve it; the packaged self-test is the gate for every exclusion.

## Decisions needed from you

| ID | Decision | Default if you don't choose |
|---|---|---|
| D1 | **A license for MagView.** The repository has none, so it is "all rights reserved" by default, which is unusual for a public release. Bundling LGPL Qt is compatible with any license, including MIT, BSD-3-Clause, Apache-2.0 and GPL-3.0. | Ask again before the first Windows/macOS release |
| D2 | **Code-signing budget.** Windows: Azure Trusted Signing (about $10/month if eligible) or an OV certificate (a few hundred $/year). macOS: Apple Developer Program, $99/year (needed for notarization). | Unsigned, with documented first-launch workarounds; poor on macOS 15+ (see the macOS plan) |
| D3 | **Mac hardware for manual QA.** CI covers automation, but Retina, trackpad and Gatekeeper behavior need a real Mac. | Apple Silicon only, QA by CI and screenshots |
| D4 | **Publisher name and bundle identifier**, e.g. `io.github.zhenghong-li.magview`. | That identifier |
| D5 | **Whether the installers register `.cif` files.** `.magview.json` is always registered. `.cif` is often owned by VESTA. | `.cif` offered unticked |
| D6 | **The Preferences → VESTA folder UI (G3).** | Implement it; it's small |
