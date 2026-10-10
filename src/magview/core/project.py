"""Save/load a MagView session (``.magview.json``) and recovery autosave.

The file stores the structure itself (lattice, species, fractional coordinates,
labels), so a session opens even if the original CIF moved. Moments are Cartesian
mu_B per canvas site, in canvas-site order, which is a deterministic function of
(input structure, canvas matrix).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
from pymatgen.core import Lattice, Structure

from magview import __version__, osdeps
from magview.core import magnetic as mg
from magview.core.canvas import Canvas
from magview.core.document import MagDocument
from magview.core.structure import CrystalStructure, StructureLoadError

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


def _index_array(data: dict, key: str, upper: int) -> np.ndarray:
    """An index list from the file, validated against the array it indexes into."""
    idx = np.asarray(data.get(key, []), dtype=int)
    if idx.ndim != 1 or (len(idx) and (idx.min() < 0 or idx.max() >= upper)):
        raise ProjectError(f"{key} contains an index out of range 0..{upper - 1}")
    return idx


def from_dict(data: dict) -> MagDocument:
    if data.get("format") != FORMAT:
        raise ProjectError("not a MagView project file")
    if int(data.get("version", 0)) > VERSION:
        raise ProjectError(f"project version {data['version']} is newer than this MagView")
    st = data.get("structure")
    if not isinstance(st, dict):
        raise ProjectError("missing or invalid structure block")
    for key in ("lattice", "species", "frac_coords", "labels"):
        if key not in st:
            raise ProjectError(f"structure.{key} is missing")
    try:
        struct = Structure(Lattice(st["lattice"]), st["species"], st["frac_coords"],
                           labels=st["labels"])
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"invalid structure: {exc}") from exc
    source = Path(data["source_file"]) if data.get("source_file") else None
    try:
        crystal = CrystalStructure.from_pymatgen(struct, source=source,
                                                 symprec=data.get("symprec", 1e-3))
    except StructureLoadError as exc:
        raise ProjectError(f"invalid structure: {exc}") from exc
    try:
        canvas = Canvas.build(crystal, data["canvas_matrix"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(f"invalid canvas_matrix: {exc}") from exc
    try:
        raw = np.asarray(data.get("moments_cartesian_muB", []), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"invalid moments_cartesian_muB: {exc}") from exc
    if raw.size % 3:
        raise ProjectError(f"moments_cartesian_muB has {raw.size} values, not a multiple of 3")
    moments = raw.reshape(-1, 3)
    if not np.isfinite(moments).all():
        raise ProjectError("moments_cartesian_muB contains non-finite values (NaN or infinity)")
    if len(moments) != len(canvas):
        raise ProjectError(f"{len(moments)} moments for a canvas of {len(canvas)} sites")
    magnetic = np.zeros(len(crystal), bool)
    magnetic[_index_array(data, "magnetic_input_sites", len(crystal))] = True
    site_magnitude = np.asarray(data.get("site_magnitude", np.zeros(len(crystal))), dtype=float)
    if site_magnitude.shape != (len(crystal),):
        raise ProjectError(f"site_magnitude has {site_magnitude.size} entries for "
                           f"{len(crystal)} input sites")
    if not (np.isfinite(site_magnitude).all() and (site_magnitude >= 0).all()):
        raise ProjectError("site_magnitude must be finite and non-negative")
    spin_axis = np.asarray(data.get("spin_axis", [0, 0, 1]), dtype=float)
    if spin_axis.shape != (3,):
        raise ProjectError("spin_axis must be a 3-vector")
    if not (np.isfinite(spin_axis).all() and np.linalg.norm(spin_axis) > 0):
        raise ProjectError("spin_axis must be a finite, non-zero 3-vector")
    spin_axis = spin_axis / np.linalg.norm(spin_axis)  # the rest of MagView assumes a unit axis
    collinear = bool(data.get("collinear", True))
    if collinear and not mg.is_collinear(moments, spin_axis):
        raise ProjectError("collinear is true but moments_cartesian_muB are not all along "
                           "spin_axis")
    doc = MagDocument(crystal, canvas, moments, magnetic,
                      spin_axis=spin_axis, collinear=collinear, site_magnitude=site_magnitude)
    doc.pinned[_index_array(data, "pinned_canvas_sites", len(canvas))] = True
    return doc


def save(doc: MagDocument, path: str | Path) -> Path:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_dict(doc), indent=1), encoding="utf-8")
    osdeps.atomic_replace(tmp, path)  # a crash never leaves a half-written project
    return path


def load(path: str | Path) -> MagDocument:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from exc
    return from_dict(data)


def autosave_dir() -> Path:
    """$MAGVIEW_AUTOSAVE_DIR, else ``autosave`` in the per-user cache
    (osdeps.cache_dir: $XDG_CACHE_HOME/magview or ~/.cache/magview on Linux)."""
    env = os.environ.get(ENV_AUTOSAVE_DIR)
    base = Path(env) if env else osdeps.cache_dir() / "autosave"
    base.mkdir(parents=True, exist_ok=True)
    return base


def structure_key(crystal: CrystalStructure) -> str:
    """Short content hash of the input structure (lattice, species, coordinates)."""
    h = hashlib.sha256()
    h.update(np.round(crystal.lattice_matrix, 6).tobytes())
    h.update(np.round(crystal.frac_coords, 6).tobytes())
    h.update("|".join(str(site.specie) for site in crystal.structure).encode())
    return h.hexdigest()[:10]


def autosave_path(crystal: CrystalStructure) -> Path:
    """Recovery file of a structure. The name carries a content hash: files that share
    a name (every POSCAR, every structure.cif) must neither overwrite each other's
    recovery data nor be offered each other's edits on open."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", crystal.name)
    return autosave_dir() / f"{name}-{structure_key(crystal)}.magview.json"


def autosave(doc: MagDocument) -> Path:
    return save(doc, autosave_path(doc.crystal))
