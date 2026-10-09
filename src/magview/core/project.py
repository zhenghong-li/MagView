"""Save/load a MagView session (``.magview.json``) and recovery autosave.

The file stores the structure itself (lattice, species, fractional coordinates,
labels), so a session opens even if the original CIF moved. Moments are Cartesian
mu_B per canvas site, in canvas-site order, which is a deterministic function of
(input structure, canvas matrix).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
from pymatgen.core import Lattice, Structure

from magview import __version__
from magview.core.canvas import Canvas
from magview.core.document import MagDocument
from magview.core.structure import CrystalStructure

FORMAT = "magview-project"
VERSION = 1
ENV_AUTOSAVE_DIR = "MAGVIEW_AUTOSAVE_DIR"


class ProjectError(ValueError):
    pass


def to_dict(doc: MagDocument) -> dict:
    c = doc.crystal
    return {
        "format": FORMAT,
        "version": VERSION,
        "magview_version": __version__,
        "source_file": str(c.source) if c.source else None,
        "structure": {
            "lattice": c.lattice_matrix.tolist(),
            "species": [str(site.specie) for site in c.structure],
            "frac_coords": c.frac_coords.tolist(),
            "labels": list(c.labels),
        },
        "symprec": c.symmetry.symprec,
        "canvas_matrix": doc.canvas.matrix.tolist(),
        "moments_cartesian_muB": doc.moments.tolist(),
        "magnetic_input_sites": [int(i) for i in np.nonzero(doc.magnetic)[0]],
        "site_magnitude": doc.site_magnitude.tolist(),
        "spin_axis": doc.spin_axis.tolist(),
        "pinned_canvas_sites": [int(i) for i in np.nonzero(doc.pinned)[0]],
        "collinear": bool(doc.collinear),
    }


def from_dict(data: dict) -> MagDocument:
    if data.get("format") != FORMAT:
        raise ProjectError("not a MagView project file")
    if int(data.get("version", 0)) > VERSION:
        raise ProjectError(f"project version {data['version']} is newer than this MagView")
    st = data["structure"]
    struct = Structure(Lattice(st["lattice"]), st["species"], st["frac_coords"], labels=st["labels"])
    source = Path(data["source_file"]) if data.get("source_file") else None
    crystal = CrystalStructure.from_pymatgen(struct, source=source, symprec=data.get("symprec", 1e-3))
    canvas = Canvas.build(crystal, data["canvas_matrix"])
    moments = np.array(data["moments_cartesian_muB"], dtype=float).reshape(-1, 3)
    if len(moments) != len(canvas):
        raise ProjectError(f"{len(moments)} moments for a canvas of {len(canvas)} sites")
    magnetic = np.zeros(len(crystal), bool)
    magnetic[data.get("magnetic_input_sites", [])] = True
    doc = MagDocument(crystal, canvas, moments, magnetic,
                      spin_axis=np.array(data.get("spin_axis", [0, 0, 1]), dtype=float),
                      collinear=bool(data.get("collinear", True)),
                      site_magnitude=np.array(data.get("site_magnitude", np.zeros(len(crystal)))))
    doc.pinned[data.get("pinned_canvas_sites", [])] = True
    return doc


def save(doc: MagDocument, path: str | Path) -> Path:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_dict(doc), indent=1))
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written project
    return path


def load(path: str | Path) -> MagDocument:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from exc
    return from_dict(data)


def autosave_dir() -> Path:
    env = os.environ.get(ENV_AUTOSAVE_DIR)
    base = Path(env) if env else Path.home() / ".cache" / "magview" / "autosave"
    base.mkdir(parents=True, exist_ok=True)
    return base


def autosave(doc: MagDocument) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", doc.crystal.name)
    return save(doc, autosave_dir() / f"{name}.magview.json")
