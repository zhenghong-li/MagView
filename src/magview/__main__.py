"""Command-line entry point.

    magview [FILE]                         open the GUI
    magview FILE --render OUT.png          render offscreen and exit (no Qt)
    magview FILE --gui-screenshot OUT.png  open the GUI, capture it, exit (Xvfb smoke test)
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from magview.render.camera import VIEWS

    ap = argparse.ArgumentParser(prog="magview", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="structure file (CIF, POSCAR, ...)")
    ap.add_argument("--render", metavar="PNG", help="render offscreen to PNG and exit")
    ap.add_argument("--view", choices=VIEWS, default=None, help="camera view (default: oblique)")
    ap.add_argument("--size", nargs=2, type=int, default=(1200, 900), metavar=("W", "H"))
    ap.add_argument("--gui-screenshot", metavar="PNG", help="open GUI, save a screenshot, exit")
    args = ap.parse_args(argv)

    if args.render:
        if not args.file:
            ap.error("--render needs a structure file")
        from magview.core.structure import CrystalStructure, StructureLoadError
        from magview.render.offscreen import render_png

        try:
            crystal = CrystalStructure.from_file(args.file)
        except StructureLoadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        render_png(crystal, args.render, view=args.view or "oblique", size=tuple(args.size))
        print(f"{crystal.summary()}\nwrote {args.render}")
        return 0

    from magview.gui.app import run

    return run(args.file, screenshot=args.gui_screenshot, view=args.view)


if __name__ == "__main__":
    sys.exit(main())
