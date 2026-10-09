"""AgentDFT handoff document: build, validate (JSON Schema + semantics), save.

The handoff is self-contained: it carries the input cell, the chemical reference
(primitive) cell, the magnetic cell with per-site Cartesian moments, the integer
supercell matrix (magnetic = S @ reference) and, when S is not the identity, the
data a band-unfolding step needs. ``dft_hints.vasp`` gives a ready MAGMOM for a
POSCAR written from ``magnetic_cell`` (sites are grouped by species in POSCAR order).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from importlib import resources
from pathlib import Path

import numpy as np

from magview import __version__
from magview.core.document import MagDocument
from magview.core.layers import decompose
from magview.core.magcell import MagneticCell, _periodic_match, minimal_magnetic_cell
from magview.core.patterns import match_layer
from magview.core.stacking import fm_reference, relation, stacking_library

SCHEMA_ID = "magview.handoff"
SCHEMA_VERSION = "1.0"
COLLINEAR_TOL = 1e-6


class HandoffError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def load_schema() -> dict:
    with resources.files("magview.io").joinpath("handoff.schema.json").open() as fh:
        return json.load(fh)


# --- helpers ----------------------------------------------------------------------

def magmom_string(values, decimals: int = 3) -> str:
    """Compact VASP MAGMOM with run-length encoding: '4*2.0 4*-2.0 8*0.0'."""
    flat = [round(float(v), decimals) + 0.0 for v in np.ravel(values)]
    out, i = [], 0
    while i < len(flat):
        j = i
        while j + 1 < len(flat) and flat[j + 1] == flat[i]:
            j += 1
        n = j - i + 1
        out.append(f"{n}*{flat[i]:g}" if n > 1 else f"{flat[i]:g}")
        i = j + 1
    return " ".join(out)


def expand_magmom(s: str) -> list[float]:
    vals: list[float] = []
    for tok in s.split():
        if "*" in tok:
            n, v = tok.split("*")
            vals += [float(v)] * int(n)
        else:
            vals.append(float(tok))
    return vals


def _cell_dict(lattice, species, frac, labels, **extra) -> dict:
    d = {"lattice": np.asarray(lattice, float).tolist(), "species": list(map(str, species)),
         "frac_coords": np.asarray(frac, float).round(10).tolist(), "labels": list(map(str, labels))}
    d.update(extra)
    return d


def _reference_cell(doc: MagDocument, P: np.ndarray) -> dict:
    """Input sites folded into the reference (primitive) lattice."""
    c = doc.crystal
    folded = c.cart_coords @ np.linalg.inv(P)
    keep: list[int] = []
    for i in range(len(c)):
        if not keep or _periodic_match(folded[i:i + 1], folded[keep], P)[0] < 0:
            keep.append(i)
    frac = np.mod(folded[keep], 1.0)
    frac[np.isclose(frac, 1.0, atol=1e-8)] = 0.0
    desc = ("input cell (already primitive)" if np.allclose(P, c.lattice_matrix)
            else "chemical primitive cell (spglib, input orientation)")
    return _cell_dict(P, [c.symbols[i] for i in keep], frac, [c.labels[i] for i in keep],
                      description=desc, parent_input_site=[int(i) for i in keep])


def detect_patterns(doc: MagDocument, axis: str = "c") -> dict:
    """Label each layer's in-plane order and each element's stacking (exact matches only)."""
    sites = np.nonzero(doc.canvas_magnetic & (np.linalg.norm(doc.moments, axis=1) > 0))[0]
    out = {"labels": [], "stacking_axis": axis, "layers": []}
    if len(sites) == 0:
        out["labels"].append("no moments")
        return out
    from magview.core import magnetic as mg

    if not doc.collinear or not mg.is_collinear(doc.moments, doc.spin_axis):
        out["labels"].append("non-collinear (no canonical label)")
        return out
    dec = decompose(doc.canvas.structure, sites, doc.crystal.lattice_matrix, axis)
    for element in sorted({L.element for L in dec.layers}):
        group = dec.by_element(element)
        in_plane = []
        for L in group:
            spins = doc.spin_sign(L.sites)
            label = None
            if L.lattice.supported and np.all(spins != 0):
                res = match_layer(L, {i: int(s) for i, s in enumerate(spins)})
                label = res.candidates[0].pattern.label if res.candidates else "non-canonical"
            in_plane.append(label)
            out["layers"].append({"element": element, "index": L.index, "height_A": round(L.height, 4),
                                  "lattice": L.lattice.kind, "pattern": label,
                                  "relation_to_layer0": None})
        names = sorted({p for p in in_plane if p})
        if len(names) == 1:
            out["labels"].append(f"{element} layers ({group[0].lattice.kind}): {names[0]}")
        elif names:
            out["labels"].append(f"{element} layers: mixed in-plane orders {names}")
        else:
            out["labels"].append(f"{element} layers: unclassified net ({group[0].lattice.kind})")
        base = doc.spin_sign(group[0].sites)
        ref = fm_reference(doc.canvas.structure, group, dec.normal, dec.period, base)
        if ref.ok:
            rels = [relation(doc.spin_sign(G.sites), r) for G, r in zip(group, ref.signs)]
            for entry, r in zip([e for e in out["layers"] if e["element"] == element], rels):
                entry["relation_to_layer0"] = r
            exact = [p.label for p, inv in stacking_library(len(group))
                     if inv is None and list(p.flips) == rels]
            if exact:
                out["labels"].append(f"{element} stacking along {axis}: {exact[0]}")
            elif len(group) > 1:
                out["labels"].append(f"{element} stacking along {axis}: custom {rels}")
        else:
            out["labels"].append(f"{element} stacking along {axis}: undefined ({ref.reason})")
    return out


# --- build / validate / save ----------------------------------------------------------------

def build_handoff(doc: MagDocument, cell: MagneticCell | None = None, axis: str = "c") -> dict:
    cell = cell or minimal_magnetic_cell(doc)
    c = doc.crystal
    sha = None
    if c.source and Path(c.source).is_file():
        sha = hashlib.sha256(Path(c.source).read_bytes()).hexdigest()
    axis_u = doc.spin_axis / np.linalg.norm(doc.spin_axis)
    collinear = bool(doc.collinear)
    signed = (cell.moments @ axis_u).round(6).tolist() if collinear else None
    species_order = list(dict.fromkeys(cell.species))
    counts = [cell.species.count(s) for s in species_order]
    if collinear:
        vasp = {"ISPIN": 2, "LNONCOLLINEAR": False, "MAGMOM": magmom_string(signed),
                "note": "MAGMOM entries follow magnetic_cell site order (POSCAR order). For "
                        "spin-orbit runs set SAXIS to moments.spin_axis."}
    else:
        vasp = {"ISPIN": 2, "LNONCOLLINEAR": True, "MAGMOM": magmom_string(cell.moments),
                "SAXIS": [0.0, 0.0, 1.0],
                "note": "Three components per site in the SAXIS = (0,0,1) frame, i.e. Cartesian x, y, z."}
    vasp.update({"species_order": species_order, "species_counts": counts})
    S = cell.supercell_matrix
    unfolding = None
    if cell.requires_supercell:
        unfolding = {"supercell_matrix": S.tolist(), "reference": "reference_cell",
                     "note": "Unfold bands from magnetic_cell onto reference_cell: "
                             "A_super = S @ A_ref; reference k-points K map to supercell k = S K "
                             "(fractional, mod reciprocal lattice)."}
    return {
        "schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "generator": {"name": "MagView", "version": __version__},
        "source": {"file": str(c.source) if c.source else None, "sha256": sha, "formula": c.formula,
                   "space_group": {"number": c.symmetry.number, "symbol": c.symmetry.international}},
        "input_cell": _cell_dict(c.lattice_matrix, c.symbols, c.frac_coords, c.labels),
        "reference_cell": _reference_cell(doc, cell.reference_lattice),
        "magnetic_cell": _cell_dict(cell.lattice, cell.species, cell.frac_coords, cell.labels,
                                    parent_input_site=[int(p) for p in cell.parent]),
        "supercell_matrix": S.tolist(),
        "supercell_matrix_input": None if cell.supercell_matrix_input is None
        else cell.supercell_matrix_input.tolist(),
        "multiplicity": int(cell.multiplicity),
        "canvas_matrix": doc.canvas.matrix.tolist(),
        "moments": {"frame": "cartesian", "unit": "mu_B", "vectors": cell.moments.round(6).tolist(),
                    "collinear": collinear, "spin_axis": axis_u.tolist(), "signed_magnitudes": signed},
        "patterns": detect_patterns(doc, axis),
        "magnetic_symmetry": cell.msg,
        "requires_unfolding": bool(cell.requires_supercell),
        "unfolding": unfolding,
        "explanation": list(cell.explanation),
        "dft_hints": {"vasp": vasp},
    }


def validate_handoff(data: dict) -> list[str]:
    """All problems found (empty list = valid): JSON Schema first, then consistency."""
    import jsonschema

    errors = [f"schema: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
              for e in sorted(jsonschema.Draft202012Validator(load_schema()).iter_errors(data),
                              key=lambda e: list(e.path))]
    if errors:
        return errors
    mc, ref = data["magnetic_cell"], data["reference_cell"]
    n = len(mc["species"])
    for key in ("frac_coords", "labels", "parent_input_site"):
        if len(mc[key]) != n:
            errors.append(f"magnetic_cell.{key} has {len(mc[key])} entries for {n} sites")
    mom = data["moments"]
    if len(mom["vectors"]) != n:
        errors.append(f"moments.vectors has {len(mom['vectors'])} entries for {n} sites")
    S = np.array(data["supercell_matrix"], float)
    det = int(round(abs(np.linalg.det(S))))
    if not np.allclose(np.array(mc["lattice"]), S @ np.array(ref["lattice"]), atol=1e-4):
        errors.append("magnetic_cell.lattice != supercell_matrix @ reference_cell.lattice")
    if det != data["multiplicity"]:
        errors.append(f"multiplicity {data['multiplicity']} != |det(supercell_matrix)| = {det}")
    if n != det * len(ref["species"]):
        errors.append(f"{n} magnetic sites != multiplicity x {len(ref['species'])} reference sites")
    if data["requires_unfolding"] != (det > 1):
        errors.append("requires_unfolding must be true exactly when the supercell is larger than "
                      "the reference cell")
    if data["requires_unfolding"] and data["unfolding"] is None:
        errors.append("unfolding data missing although requires_unfolding is true")
    n_in = len(data["input_cell"]["species"])
    if any(p >= n_in for p in mc["parent_input_site"]):
        errors.append("magnetic_cell.parent_input_site out of range")
    vec = np.array(mom["vectors"], float).reshape(-1, 3)
    if mom["collinear"]:
        u = np.array(mom["spin_axis"], float)
        if not np.allclose(np.outer(vec @ u, u), vec, atol=COLLINEAR_TOL):
            errors.append("collinear moments are not all along spin_axis")
        if mom.get("signed_magnitudes") is None or not np.allclose(mom["signed_magnitudes"], vec @ u,
                                                                   atol=1e-5):
            errors.append("signed_magnitudes inconsistent with vectors")
    vasp = data["dft_hints"].get("vasp")
    if vasp:
        want = n * (3 if vasp["LNONCOLLINEAR"] else 1)
        got = len(expand_magmom(vasp["MAGMOM"]))
        if got != want:
            errors.append(f"MAGMOM has {got} values, expected {want}")
        if sum(vasp.get("species_counts", [n])) != n:
            errors.append("species_counts do not add up to the number of sites")
    return errors


def save_handoff(data: dict, path: str | Path) -> Path:
    errors = validate_handoff(data)
    if errors:
        raise HandoffError(errors)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)
    return path
