# Windows

**Status: planned.** See [docs/plans/windows-build.md](../../docs/plans/windows-build.md).

This folder will hold the Windows-only build files: `build.ps1` (PyInstaller build, packaged self-test, portable zip) and `magview.iss` (Inno Setup per-user installer). The application code is the shared `src/magview`; OS-specific code goes into `src/magview/osdeps.py`.
