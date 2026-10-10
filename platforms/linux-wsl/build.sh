#!/usr/bin/env bash
# Build the self-contained Linux/WSL release:
#     dist/MagView-<version>-linux-x86_64.tar.gz (+ .sha256)
#
#   platforms/linux-wsl/build.sh            build, self-test the bundle, package
#   platforms/linux-wsl/build.sh --no-gui   skip the Xvfb GUI self-test
#
# Needs the packages in system-packages.txt (run setup_system_deps.sh first; the
# "Release packaging" ones are for PyInstaller). Builds in a fresh venv under
# build/, so nothing from the development venv leaks into the bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
gui_test=1
[[ "${1:-}" == "--no-gui" ]] && gui_test=0

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
NAME="MagView-$VERSION-linux-x86_64"
STAGE="build/stage/$NAME"
echo "== building $NAME"

rm -rf build/venv build/work build/dist build/stage
python3 -m venv build/venv
build/venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt -r requirements-build.txt
build/venv/bin/pip install --quiet --disable-pip-version-check --no-deps .
build/venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
    --distpath build/dist --workpath build/work packaging/magview.spec

mkdir -p "$STAGE"
cp -a build/dist/MagView/. "$STAGE/"
sed "s/@VERSION@/$VERSION/" platforms/linux-wsl/README-bundle.txt > "$STAGE/README.txt"
mkdir "$STAGE/examples"  # generated structures only (not the user's experimental CIF)
cp tests/data/{NiO,Fe_bcc,La2CuO4,FeSe,toy_square_CuO2,toy_honeycomb_Cr,toy_triangular_Co}.cif "$STAGE/examples/"
build/venv/bin/python packaging/collect_licenses.py "$STAGE/licenses"

echo "== smoke test: offscreen render"
"$STAGE/magview" tests/data/NiO.cif --render build/smoke.png --size 400 300
if (( gui_test )); then
    echo "== GUI self-test of the bundle (Xvfb, clean environment)"
    home="$(mktemp -d)"
    xvfb-run -a -s "-screen 0 1600x1000x24" sh -c \
        "exec env -i HOME='$home' PATH=/usr/bin:/bin LANG=C.UTF-8 DISPLAY=\$DISPLAY \
         XAUTHORITY=\$XAUTHORITY '$STAGE/magview' tests/data/NiO.cif --selftest build/selftest" \
        > build/selftest.log 2>&1 || { tail -20 build/selftest.log; echo "GUI self-test FAILED"; exit 1; }
    rm -rf "$home"
    echo "   $(grep -c '^\[PASS\]' build/selftest.log) checks passed"
fi

mkdir -p dist
tar -C build/stage -czf "dist/$NAME.tar.gz" "$NAME"
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "== done: dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
