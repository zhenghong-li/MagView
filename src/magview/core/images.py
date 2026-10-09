"""Periodic-boundary images for display.

VESTA draws every atom whose fractional coordinates fall inside the boundary box
(default 0..1 on each axis, inclusive), so an atom at x = 0 also appears at
x = 1. Each displayed atom is a *(parent site, integer lattice shift)* pair; the
parent index is what selection and moment editing operate on.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

DEFAULT_BOUNDS = ((0.0, 1.0), (0.0, 1.0), (0.0, 1.0))
BOUNDARY_TOL = 1e-4  # fractional


@dataclass(frozen=True)
class DisplayAtoms:
    """Atoms as displayed: parent site index + lattice shift for each."""

    parent: np.ndarray  # (n,) int, index into the input cell
    shift: np.ndarray  # (n, 3) int, lattice translation added to the parent's frac coords
    frac: np.ndarray  # (n, 3) float
    cart: np.ndarray  # (n, 3) float

    def __len__(self) -> int:
        return len(self.parent)

    def keys(self) -> list[tuple[int, int, int, int]]:
        return [(int(p), int(s[0]), int(s[1]), int(s[2])) for p, s in zip(self.parent, self.shift)]

    def is_image(self) -> np.ndarray:
        """True for copies that are not the parent's home position (shift != 0)."""
        return np.any(self.shift != 0, axis=1)

    @staticmethod
    def from_keys(keys: list[tuple[int, int, int, int]], frac_coords: np.ndarray,
                  lattice: np.ndarray) -> "DisplayAtoms":
        if keys:
            arr = np.array(keys, dtype=int)
            parent, shift = arr[:, 0], arr[:, 1:]
        else:
            parent, shift = np.zeros(0, int), np.zeros((0, 3), int)
        frac = frac_coords[parent] + shift
        return DisplayAtoms(parent, shift, frac, frac @ lattice)


def boundary_images(frac_coords: np.ndarray, lattice: np.ndarray,
                    bounds=DEFAULT_BOUNDS, tol: float = BOUNDARY_TOL) -> DisplayAtoms:
    """All (site, shift) pairs with lo - tol <= frac + shift <= hi + tol on every axis.

    ``frac_coords`` must already be wrapped into [0, 1) (``CrystalStructure`` does this).
    Atoms are ordered by parent index, then by shift, so parents come first per site.
    """
    frac_coords = np.asarray(frac_coords, dtype=float)
    keys: list[tuple[int, int, int, int]] = []
    for i, f in enumerate(frac_coords):
        ranges = []
        for axis in range(3):
            lo, hi = bounds[axis]
            n_lo = int(np.ceil(lo - tol - f[axis]))
            n_hi = int(np.floor(hi + tol - f[axis]))
            ranges.append(range(n_lo, n_hi + 1))
        shifts = sorted(itertools.product(*ranges), key=lambda s: (s != (0, 0, 0), s))
        keys.extend((i, *s) for s in shifts)
    return DisplayAtoms.from_keys(keys, frac_coords, np.asarray(lattice, dtype=float))
