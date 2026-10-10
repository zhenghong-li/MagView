MagView @VERSION@ for Linux and WSL (x86_64)
==============================================

Crystal-structure editor for magnetic orderings: suggests magnetic sites, lets you
set moments by dragging or layer by layer, finds the minimal magnetic supercell and
writes an AgentDFT handoff. Source and documentation:
https://github.com/zhenghong-li/MagView

Run
---
    ./magview                                  open the GUI
    ./magview examples/NiO.cif                 open an example structure
    ./magview FILE.cif                         open a structure (CIF, POSCAR, .magview.json session)
    ./magview FILE.cif --render out.png --view c   render a PNG without opening a window
    ./magview --help

In the app, press F1 for every key and mouse gesture. The examples/ structures use
approximate literature parameters: fine for trying MagView, not for quantitative work.

Requirements
------------
* x86_64 Linux with glibc 2.39 or newer: Ubuntu 24.04+, Debian 13+, Fedora 40+.
  WSL: Ubuntu 24.04 under WSL 2 with WSLg (Windows 11, or Windows 10 with the
  Microsoft Store version of WSL).
* OpenGL and the windowing libraries of the host. On Ubuntu / Debian / WSL:

      sudo apt install libgl1 libegl1 libgl1-mesa-dri libxcb1 libwayland-client0 fonts-dejavu-core

  For --render on a machine without any display, also: sudo apt install libosmesa6

Everything else (Python, Qt, VTK, pymatgen, spglib, ...) is inside this folder.
Nothing is installed; delete the folder to uninstall.

VESTA appearance (optional)
---------------------------
MagView uses VESTA's element colors, radii and bond rules when it finds a VESTA
installation (they are read at run time and never copied):

    export MAGVIEW_VESTA_DIR=/path/to/VESTA-gtk3-x86_64   # the folder with elements.ini

Without VESTA, MagView falls back to pymatgen's approximate colors and
covalent-radius bonds.

Files MagView writes
--------------------
* Recovery autosaves: ~/.cache/magview/autosave ($XDG_CACHE_HOME respected);
  MAGVIEW_AUTOSAVE_DIR=<dir> moves them, MAGVIEW_AUTOSAVE_DIR=off disables them.
* Mock AgentDFT jobs: ~/.magview/agentdft_jobs (MAGVIEW_AGENTDFT_DIR=<dir>).
* Window layout and arrow settings: ~/.config/MagView/MagView.conf.

Third-party software
--------------------
See licenses/THIRD_PARTY_LICENSES.txt. Qt for Python (PySide6) is LGPL-3.0; its
Qt libraries are the separate files in _internal/ and may be replaced.
