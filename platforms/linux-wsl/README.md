# Linux and WSL

Everything Linux/WSL-specific outside the shared code base (`src/magview`).

| File | Purpose |
|---|---|
| `system-packages.txt` | apt packages for development, tests and release builds (single source of truth; mirror it into container images) |
| `setup_system_deps.sh` | idempotent installer for that list (`--check`, `--list`) |
| `build.sh` | builds the release tarball `dist/MagView-<version>-linux-x86_64.tar.gz`: fresh venv, PyInstaller (`packaging/magview.spec`), packaged GUI self-test |
| `README-bundle.txt` | the `README.txt` shipped inside the tarball |

Releases are published on GitHub (Releases page), never committed.
