"""Regenerate the Phase 0 artifact gallery in artifacts/phase0/.

    .venv/bin/python scripts/render_gallery.py [--no-gui]

Outputs: an oblique and a view-along-c render per structure, NiO AFM-II and
Cs3V9Te13 moment-view renders, a GUI screenshot taken under Xvfb, and a
labeled contact sheet of everything.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data"
OUT = ROOT / "artifacts" / "phase0"
SIZE = (1200, 900)

STRUCTURES = ["Fe_bcc", "NiO", "La2CuO4", "FeSe", "Cs3V9Te13_300K"]


def nio_afm2():
    """NiO 2x2x2 conventional supercell with AFM-II order: FM (111) planes, alternating."""
    from magview.core.structure import CrystalStructure

    base = CrystalStructure.from_file(DATA / "NiO.cif")
    sup = base.structure.copy()
    sup.make_supercell([2, 2, 2])
    crystal = CrystalStructure.from_pymatgen(sup)
    plane = np.rint((crystal.frac_coords * 2.0).sum(axis=1)).astype(int)  # (111) plane index
    direction = np.array([-1.0, -1.0, 2.0]) / np.sqrt(6)  # in the (111) plane
    m = np.zeros((len(crystal), 3))
    ni = np.array([s == "Ni" for s in crystal.symbols])
    m[ni] = 2.0 * ((-1.0) ** plane[ni])[:, None] * direction
    return crystal, m


def cs3v9te13_fm():
    """Cs3V9Te13 with all V moments along +c (illustration of the V sublattice only)."""
    from magview.core.structure import CrystalStructure

    crystal = CrystalStructure.from_file(DATA / "Cs3V9Te13_300K.cif")
    m = np.zeros((len(crystal), 3))
    m[[i for i, s in enumerate(crystal.symbols) if s == "V"]] = [0.0, 0.0, 1.5]
    return crystal, m


def gui_screenshot(path: Path) -> bool:
    if shutil.which("xvfb-run") is None:
        print("skip GUI screenshot: xvfb-run not installed")
        return False
    proc = subprocess.run(
        ["xvfb-run", "-a", "-s", "-screen 0 1400x900x24", sys.executable, "-m", "magview",
         str(DATA / "Cs3V9Te13_300K.cif"), "--gui-screenshot", str(path)],
        capture_output=True, text=True, timeout=300)
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def contact_sheet(items: list[tuple[Path, str]], path: Path, cols: int = 3, cell_w: int = 600) -> None:
    font = ImageFont.load_default(size=22)
    thumbs = []
    for p, title in items:
        im = Image.open(p).convert("RGB")
        im.thumbnail((cell_w, cell_w))
        thumbs.append((im, title))
    cell_h = max(im.height for im, _ in thumbs) + 40
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for k, (im, title) in enumerate(thumbs):
        x, y = (k % cols) * cell_w, (k // cols) * cell_h
        sheet.paste(im, (x + (cell_w - im.width) // 2, y + 36))
        draw.text((x + 10, y + 6), title, fill="black", font=font)
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(200, 200, 200))
    sheet.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gui", action="store_true", help="skip the Xvfb GUI screenshot")
    args = ap.parse_args()

    from magview.core.structure import CrystalStructure
    from magview.render.offscreen import render_png
    from magview.style.appearance import Appearance

    OUT.mkdir(parents=True, exist_ok=True)
    appearance = Appearance.load()
    print("appearance:", appearance.source)
    items: list[tuple[Path, str]] = []

    for name in STRUCTURES:
        crystal = CrystalStructure.from_file(DATA / f"{name}.cif")
        for view in ("oblique", "c"):
            path = OUT / f"{name}_{view}.png"
            render_png(crystal, path, view=view, size=SIZE, appearance=appearance)
            label = "3D" if view == "oblique" else "along c"
            items.append((path, f"{crystal.formula} {crystal.symmetry.international} - {label}"))
            print("wrote", path.relative_to(ROOT))

    crystal, m = nio_afm2()
    for view, label in (("oblique", "3D"), ("b", "along b")):
        path = OUT / f"nio_afm2_arrows{'' if view == 'oblique' else '_b'}.png"
        render_png(crystal, path, view=view, size=SIZE, site_moments=m, moment_view=True,
                   appearance=appearance)
        items.append((path, f"NiO AFM-II (2x2x2), moment view - {label}"))
        print("wrote", path.relative_to(ROOT))

    crystal, m = cs3v9te13_fm()
    path = OUT / "cs3v9te13_v_moments.png"
    render_png(crystal, path, view="oblique", size=SIZE, site_moments=m, moment_view=True,
               appearance=appearance)
    items.append((path, "Cs3V9Te13: V sublattice, moment view"))
    print("wrote", path.relative_to(ROOT))

    if not args.no_gui:
        path = OUT / "gui_shell.png"
        if gui_screenshot(path):
            items.append((path, "GUI shell (Xvfb, Mesa llvmpipe)"))

    sheet = OUT / "contact_sheet.png"
    contact_sheet(items, sheet)
    print("wrote", sheet.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
