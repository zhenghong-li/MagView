# macOS

**Status: planned.** See [docs/plans/macos-build.md](../../docs/plans/macos-build.md).

This folder will hold the macOS-only build files: `build.sh` (PyInstaller `.app` bundle, packaged self-test, DMG), `Info.plist` additions, entitlements and the signing/notarization script. The application code is the shared `src/magview`; OS-specific code goes into `src/magview/osdeps.py`.
