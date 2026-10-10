"""2D lattice classification and the canonical collinear pattern library.

A planar set of magnetic sites is classified from its nearest-neighbour graph:

* square / rectangular : coordination 4, two bond directions ~90 deg apart
* honeycomb            : coordination 3, three bond directions ~60 deg apart (mod 180)
* triangular           : coordination 6, three bond directions ~60 deg apart

Anything else is "unsupported", and pattern tools stay silent for it.

Each canonical pattern assigns FM (+1) or AFM (-1) to every bond-direction class:

* square:     FM (++), Neel (--), stripe with FM chains along class 0 (+-) or class 1 (-+)
* honeycomb:  FM (+++), Neel (---), zigzag (one class AFM), stripe (one class FM)
* triangular: FM (+++), stripe (one class FM); Neel is frustrated, so it is not offered
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from magview.core.graph import NeighborGraph, miller_direction

ANGLE_TOL_DEG = 8.0
SQUARE_ASPECT_TOL = 0.02
LAYER_TOL = 0.35  # Angstrom: max out-of-plane component of an in-plane bond (buckling);
# shared with the layer clustering in core.batch, so a buckled layer is never
# accepted as one layer and then rejected here as "unsupported"


@dataclass(frozen=True)
class BondClass:
    index: int
    direction: np.ndarray  # unit vector (in plane)
    label: str  # lattice direction, e.g. "[100]"
    length: float


@dataclass(frozen=True)
class Lattice2D:
    kind: str  # "square" | "rectangular" | "honeycomb" | "triangular" | "unsupported"
    classes: tuple[BondClass, ...]
    bond_class: np.ndarray  # (m,) class index per graph bond (-1 = out of plane / unclassified)
    coordination: int
    reason: str

    @property
    def supported(self) -> bool:
        return self.kind != "unsupported"


@dataclass(frozen=True)
class Pattern2D:
    key: str  # stable id, e.g. "neel", "stripe:[100]", "zigzag:[110]"
    name: str  # family: "FM", "Neel", "stripe", "zigzag"
    label: str  # human-readable
    class_signs: tuple[int, ...]  # +1/-1 per bond class


def _angle_mod_180(v: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> np.ndarray:
    return np.degrees(np.arctan2(v @ e2, v @ e1)) % 180.0


def classify(graph: NeighborGraph, normal: np.ndarray, lattice: np.ndarray,
             plane_tol: float = LAYER_TOL) -> Lattice2D:
    """Classify the planar nearest-neighbour graph.

    Bonds may leave the plane by up to ``plane_tol`` along the normal (buckling);
    direction classes and bond lengths are computed from the in-plane projections.
    """
    normal = np.asarray(normal, float) / np.linalg.norm(normal)
    none = np.full(graph.m, -1, dtype=int)
    if graph.m == 0:
        return Lattice2D("unsupported", (), none, 0, "no nearest-neighbour bonds")
    z_out = graph.vec @ normal
    if np.any(np.abs(z_out) > plane_tol):
        return Lattice2D("unsupported", (), none, 0, "nearest-neighbour bonds leave the plane")
    vec = graph.vec - np.outer(z_out, normal)
    e1 = vec[0] / np.linalg.norm(vec[0])
    e2 = np.cross(normal, e1)
    ang = _angle_mod_180(vec, e1, e2)

    # Cluster bond angles (circular, period 180 deg).
    order = np.argsort(ang)
    centers: list[float] = []
    members: list[list[int]] = []
    for k in order:
        a = ang[k]
        if centers and min(abs(a - centers[-1]), 180 - abs(a - centers[-1])) <= ANGLE_TOL_DEG:
            members[-1].append(k)
        else:
            centers.append(a)
            members.append([k])
    if len(centers) > 1 and min(abs(ang[members[0][0]] - centers[-1]),
                                180 - abs(ang[members[0][0]] - centers[-1])) <= ANGLE_TOL_DEG:
        members[0] += members.pop()  # wrap-around cluster
        centers.pop()
    bond_class = np.full(graph.m, -1, dtype=int)
    classes = []
    for ci, ks in enumerate(members):
        bond_class[ks] = ci
        v = vec[ks[0]]
        classes.append(BondClass(ci, v / np.linalg.norm(v), miller_direction(v, lattice),
                                 float(np.mean(np.linalg.norm(vec[ks], axis=1)))))
    z = int(graph.coordination().max())
    nc = len(classes)
    kind, reason = "unsupported", ""
    if z == 4 and nc == 2:
        dot = abs(classes[0].direction @ classes[1].direction)
        if dot < np.sin(np.radians(ANGLE_TOL_DEG)):
            ratio = classes[0].length / classes[1].length
            kind = "square" if abs(ratio - 1) <= SQUARE_ASPECT_TOL else "rectangular"
    elif z in (3, 6) and nc == 3:
        dots = [abs(classes[a].direction @ classes[b].direction) for a, b in ((0, 1), (1, 2), (0, 2))]
        if all(abs(d - 0.5) < np.sin(np.radians(ANGLE_TOL_DEG)) for d in dots):
            kind = "honeycomb" if z == 3 else "triangular"
    if kind == "unsupported":
        reason = (f"coordination {z} with {nc} bond direction(s) is not a square, honeycomb or "
                  "triangular net")
    else:
        reason = f"{kind}: coordination {z}, bond directions " + ", ".join(c.label for c in classes)
    return Lattice2D(kind, tuple(classes), bond_class, z, reason)


def library(lat: Lattice2D) -> list[Pattern2D]:
    """Canonical patterns for a classified 2D lattice (empty for unsupported)."""
    c = lat.classes
    if lat.kind in ("square", "rectangular"):
        return [
            Pattern2D("fm", "FM", "FM", (1, 1)),
            Pattern2D("neel", "Neel", "Neel (checkerboard)", (-1, -1)),
            Pattern2D(f"stripe:{c[0].label}", "stripe", f"stripe: FM chains along {c[0].label}", (1, -1)),
            Pattern2D(f"stripe:{c[1].label}", "stripe", f"stripe: FM chains along {c[1].label}", (-1, 1)),
        ]
    if lat.kind == "honeycomb":
        out = [Pattern2D("fm", "FM", "FM", (1, 1, 1)), Pattern2D("neel", "Neel", "Neel", (-1, -1, -1))]
        for k in range(3):
            signs = tuple(-1 if q == k else 1 for q in range(3))
            out.append(Pattern2D(f"zigzag:{c[k].label}", "zigzag",
                                 f"zigzag: AFM bonds along {c[k].label}", signs))
        for k in range(3):
            signs = tuple(1 if q == k else -1 for q in range(3))
            out.append(Pattern2D(f"stripe:{c[k].label}", "stripe",
                                 f"stripe: FM bonds along {c[k].label}", signs))
        return out
    if lat.kind == "triangular":
        out = [Pattern2D("fm", "FM", "FM", (1, 1, 1))]
        for k in range(3):
            signs = tuple(1 if q == k else -1 for q in range(3))
            out.append(Pattern2D(f"stripe:{c[k].label}", "stripe",
                                 f"stripe: FM chains along {c[k].label}", signs))
        return out
    return []


def bond_signs(lat: Lattice2D, pattern: Pattern2D) -> np.ndarray:
    return np.array([pattern.class_signs[k] for k in lat.bond_class], dtype=int)
