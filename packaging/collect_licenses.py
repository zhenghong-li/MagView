"""Copy the license files of every bundled third-party distribution.

    python packaging/collect_licenses.py OUT_DIR

Run with the build venv's Python. Writes OUT_DIR/<name>-<version>/<license files>
and OUT_DIR/THIRD_PARTY_LICENSES.txt (name, version, declared license). Build
and test tools that are not bundled are skipped.
"""

from __future__ import annotations

import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

# Verbatim FSF texts for packages that declare LGPL-3.0 but ship no license file
# (PySide6, shiboken6); the LGPL-3.0 is a set of additions to the GPL-3.0.
FSF_TEXTS = [Path(__file__).resolve().parent / "licenses" / n for n in ("LGPL-3.0.txt", "GPL-3.0.txt")]
NOT_BUNDLED = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
               "altgraph", "pytest", "pluggy", "iniconfig", "packaging", "magview"}
LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", re.IGNORECASE)


def declared_license(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    text = (meta.get("License") or "").strip().splitlines()
    if text and len(text[0]) < 80:
        return text[0]
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                   if c.startswith("License ::")]
    return ", ".join(classifiers) or "see license files"


def main(out: str) -> int:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in NOT_BUNDLED:
            continue
        target = out_dir / f"{name}-{dist.version}"
        copied = 0
        for f in dist.files or []:
            if LICENSE_FILE.search(str(f)):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    target.mkdir(exist_ok=True)
                    shutil.copy(src, target / src.name)
                    copied += 1
        declared = declared_license(dist)
        if not copied and "LGPL-3.0" in declared:
            target.mkdir(exist_ok=True)
            for text in FSF_TEXTS:
                shutil.copy(text, target / text.name)
                copied += 1
        rows.append(f"{name:32s} {dist.version:16s} {declared}"
                    + ("" if copied else "   (no license file shipped by the package)"))
    (out_dir / "THIRD_PARTY_LICENSES.txt").write_text(
        "Third-party software bundled with MagView (name, version, declared license).\n"
        "Qt for Python (PySide6) is LGPL-3.0: its Qt libraries live in _internal/ and\n"
        "may be replaced by compatible builds.\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} distributions -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
