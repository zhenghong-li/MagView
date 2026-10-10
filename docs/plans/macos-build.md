# Plan: MagView for macOS

**Goal:** `MagView.app` for Apple Silicon and Intel Macs, installed by dragging it from a DMG to Applications. Same code base as Linux/WSL (see [README.md](README.md) for the design rule and the shared groundwork G1–G9).

**Deliverables (GitHub Release assets):**
- `MagView-X.Y.Z-macos-arm64.dmg` and `MagView-X.Y.Z-macos-x86_64.dmg`.
- A `.sha256` file for each.

**Why two DMGs, not one universal2:** VTK, numpy, scipy, spglib and pymatgen publish single-architecture macOS wheels. A universal2 app would need every extension module merged with `lipo`, which is fragile and doubles the size.

**Minimum macOS:** the highest `minos` of any bundled binary. Expected macOS 12–14, measured in M3 and written into `LSMinimumSystemVersion`.

## What already works for macOS
- All dependencies have macOS wheels for both architectures.
- The PyInstaller spec, launcher and license collector in `packaging/` are shared.
- Qt stores settings under `~/Library/Preferences` with no code change.
- **Modifier mapping.** Qt maps ⌘ to `ControlModifier`, so ⌘-click toggles the selection, which is the macOS convention. Qt turns Ctrl-click into a right click, so the context menu works. The viewport key filter already ignores ⌘ combinations.
- **Trackpads.** Pinch zoom is handled by pyvistaqt. Pan without a middle button is Shift+drag on empty space, which already exists.

## Phases

### M1. Run from source; test suite on macOS (size M)
*Prerequisites:* G1 (folders, `has_display`, VESTA lookup), G2, G5, G6 (CI jobs on `macos-14` arm64 and an Intel image).

- **VESTA lookup.** Search `MAGVIEW_VESTA_DIR`, then the GUI setting (G3), then `/Applications/VESTA.app` and `~/Applications/VESTA.app`. Verify where `elements.ini` and `style.ini` sit inside the app bundle (expected `Contents/Resources`).
- **Spike: OpenGL on the CI runners.** macOS arm64 runners are VMs without GPU acceleration. Find out whether VTK gets a usable OpenGL 4.1 context there (Apple's software renderer).
  - If yes: run the 92-check GUI self-test in CI.
  - If no: CI runs everything except the GUI tests, and the self-test runs on a real Mac (D3) for each release.

**Verify:** full pytest on both macOS runners; the GUI self-test on a real Mac.

### M2. macOS behavior (size M)

| Item | Work |
|---|---|
| ⌘ in labels | G4: menus, the F1 table and hints show ⌘Z, ⇧⌘S, … |
| Opening files from Finder | Finder sends "open document" as an Apple Event, not as a command-line argument. Handle `QEvent.FileOpen` in an app-level event filter and route it to `load_file`, including a launch triggered by the double-click. Without this, double-clicking a session opens an empty window. |
| App menu | Set `QAction` roles explicitly (About, Preferences for G3, Quit) so Qt puts them in the MagView menu. Check that no shortcut collides with the system ones (⌘H, ⌘Q, ⌘M). |
| Retina | At device pixel ratio 2, and when moving the window between a Retina and a non-Retina screen: picking, box select, hover outline, patterns popover, cursor label, screenshots. |
| Trackpad scrolling | Two-finger scroll sends many small `pixelDelta` steps. Tune wheel zoom so a swipe isn't a jump. |
| Dark mode | The 3D view stays white (VESTA look). Check that the panels stay readable in dark mode, including the red/green confidence colors; otherwise pin a light palette. |
| App translocation | A quarantined app runs from a random read-only path. Never write next to the app; all data already goes to `~/Library` (G1). |

### M3. Packaging (size S)
`platforms/macos/build.sh` mirrors the Linux `build.sh`:

1. Create a fresh venv from `requirements/macos.txt` + `requirements-build.txt`.
2. Run PyInstaller with the shared spec, plus a `BUNDLE` step for `MagView.app`.
3. Fill `Info.plist`:
   - `CFBundleIdentifier` (D4), version and `NSHighResolutionCapable`.
   - `LSMinimumSystemVersion`, measured with `otool -l` (`minos`) over every Mach-O in the bundle.
   - `CFBundleDocumentTypes`.
   - `UTExportedTypeDeclarations` for `.magview.json` (conforms to `public.json`), and `UTImportedTypeDeclarations` for `.cif` (`chemical/x-cif`, role Viewer, D5).
4. Add the icon (`.icns`, G8). README, licenses and examples go into `Contents/Resources`.
5. Run the packaged self-test: `MagView.app/Contents/MacOS/magview examples/NiO.cif --selftest out`.
6. Build the DMG with `hdiutil create` (the app plus an Applications link) and write the `.sha256`.

### M4. Signing and notarization (decision D2; strongly recommended)
Gatekeeper treats unsigned, un-notarized apps from the internet much more harshly since macOS 15. The Ctrl-click → Open bypass is gone, so users must go to *System Settings → Privacy & Security → Open Anyway* after the first blocked launch.

**Signed path:**
1. Sign every Mach-O inside-out (`--deep` is deprecated for this) with a Developer ID Application certificate, the hardened runtime and minimal entitlements. Add `com.apple.security.cs.allow-unsigned-executable-memory` or `disable-library-validation` only if testing shows Python or VTK needs them.
2. Sign the app, run `notarytool submit --wait`, and staple both the app and the DMG.

In CI, the certificate (`.p12` + password) and an App Store Connect API key are repository secrets.

**Unsigned path:** the README documents *Open Anyway*, and `xattr -dr com.apple.quarantine /Applications/MagView.app` for terminal users.

### M5. QA and release (size S)
Manual checklist, with the DMG downloaded through a browser so it is quarantined like a user's copy:

| Area | Checks |
|---|---|
| Hardware | Apple Silicon (macOS 14 or 15) and, if available (D3), an Intel Mac on the minimum macOS |
| Displays | Retina, plus an external non-Retina monitor (drag the window across) |
| Input | Trackpad: pinch, two-finger scroll, Shift+drag pan, Ctrl-click menu, ⌘-click toggle. Mouse with wheel. |
| First launch | The Gatekeeper flow on a quarantined DMG (signed or unsigned) |
| VESTA | `VESTA.app` present and absent; the Preferences setting |
| Finder | Double-click a `.magview.json` while MagView is closed and while it is running; *Open With* on a `.cif` |
| Appearance | Dark mode |

Then attach both DMGs and their `.sha256` files to the version's GitHub Release (G6 automates this from the tag).

## Risks

| Risk | Mitigation |
|---|---|
| Apple removes OpenGL in a future macOS | Still functional (OpenGL 4.1 over Metal) through macOS 15. Long-term exit: VTK's WebGPU backend. Track VTK releases. |
| No GPU on CI runners | M1 spike; the GUI self-test on a real Mac per release (D3) |
| Notarization rejects nested unsigned binaries | Inside-out signing script that walks every Mach-O; `notarytool log` reviewed in CI |
| Intel runners are being retired by GitHub | Use the last x86_64 macOS image while it exists (check current labels); afterwards build Intel locally, or drop Intel after notice |
| Wheel `minos` raises the minimum macOS | Measured in M3. Pinning older wheels in `requirements/macos.txt` can lower it if needed. |
