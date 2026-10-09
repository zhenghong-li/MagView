"""Camera orientations in crystallographic terms (pure numpy).

``view_basis`` returns (D, up): D is the unit vector from the focal point toward
the camera, so the viewer looks along -D. Conventions:

* view along c (or c*): c points at the viewer, a is horizontal pointing right;
* view along a (or a*): b is horizontal right; view along b (or b*): c right;
* "oblique": a VESTA-like perspective-free 3/4 view with c up.
"""

from __future__ import annotations

import numpy as np

VIEWS = ("a", "b", "c", "a*", "b*", "c*", "oblique")
_RIGHT_AXIS = {"a": 1, "b": 2, "c": 0}  # horizontal axis for each viewing axis


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def reciprocal_directions(lattice: np.ndarray) -> np.ndarray:
    """Unit vectors along a*, b*, c* (rows), i.e. normals of the bc, ca, ab planes."""
    a, b, c = lattice
    return np.array([_unit(np.cross(b, c)), _unit(np.cross(c, a)), _unit(np.cross(a, b))])


def view_basis(lattice: np.ndarray, view: str = "oblique") -> tuple[np.ndarray, np.ndarray]:
    lattice = np.asarray(lattice, dtype=float)
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; choose from {VIEWS}")
    if view == "oblique":
        rec = reciprocal_directions(lattice)
        d = _unit(rec[0] + 0.45 * rec[1] + 0.32 * rec[2])
        c = lattice[2]
        up = _unit(c - (c @ d) * d)
        return d, up
    axis = view[0]
    idx = "abc".index(axis)
    d = reciprocal_directions(lattice)[idx] if view.endswith("*") else _unit(lattice[idx])
    right = lattice[_RIGHT_AXIS[axis]]
    up = _unit(np.cross(d, right))
    return d, up


def fit_parallel_scale(points: np.ndarray, center: np.ndarray, d: np.ndarray, up: np.ndarray,
                       aspect: float, margin: float = 1.08) -> float:
    """Half-height of the parallel view that fits all points (aspect = width / height)."""
    right = np.cross(up, d)
    rel = np.asarray(points) - center
    half_h = np.abs(rel @ up).max()
    half_w = np.abs(rel @ right).max()
    return float(max(half_h, half_w / aspect) * margin)
