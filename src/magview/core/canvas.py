"""Editing canvas: the working supercell on which moments are defined.

The canvas is ``W x input cell`` for an integer matrix ``W`` (rows = canvas
lattice vectors in units of the input lattice vectors; same convention as
pymatgen's ``make_supercell``). Every canvas site is an input-cell site ``p``
translated by an input-lattice vector ``R``; that ``(p, R)`` bookkeeping is what
lets moments be carried across canvas resizes and, in Phase 5, lets the minimal
magnetic cell be computed exactly.

Default ``W = diag(2, 2, 2)``: every canonical commensurate pattern has
propagation-vector components in {0, 1/2}, so it fits.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
from pymatgen.core import Structure

from magview.core.structure import CrystalStructure

DEFAULT_MATRIX = ((2, 0, 0), (0, 2, 0), (0, 0, 2))
MOMENT_TOL = 1e-6  # mu_B


class CanvasResizeError(ValueError):
    """Resizing would lose information (moments not periodic in the new canvas)."""


def _as_matrix(matrix) -> np.ndarray:
    m = np.array(matrix, dtype=int)
    if m.shape == (3,):
        m = np.diag(m)
    if m.shape != (3, 3):
        raise ValueError(f"canvas matrix must be 3x3 or a length-3 diagonal, got {m.shape}")
    if round(abs(np.linalg.det(m))) == 0:
        raise ValueError("canvas matrix is singular")
    if np.linalg.det(m) < 0:
        raise ValueError("canvas matrix must be right-handed (det > 0)")
    return m


def lattice_points_in_cell(matrix: np.ndarray) -> np.ndarray:
    """All integer vectors R (input-lattice units) inside the canvas cell spanned by ``matrix``.

    Returns (det, 3) ints with R @ inv(matrix) in [0, 1)^3, sorted for determinism.
    """
    inv = np.linalg.inv(matrix)
    corners = np.array(list(itertools.product((0, 1), repeat=3))) @ matrix
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    grid = np.array(list(itertools.product(*(range(int(a), int(b) + 1) for a, b in zip(lo, hi)))))
    f = grid @ inv
    eps = 1e-9
    inside = np.all((f > -eps) & (f < 1 - eps), axis=1)
    pts = grid[inside]
    det = int(round(abs(np.linalg.det(matrix))))
    if len(pts) != det:
        raise RuntimeError(f"found {len(pts)} lattice points, expected {det}")
    return pts[np.lexsort(pts.T[::-1])]


@dataclass(frozen=True)
class Canvas:
    crystal: CrystalStructure  # the input cell
    matrix: np.ndarray  # (3, 3) int
    structure: CrystalStructure  # the canvas cell as its own structure
    parent: np.ndarray  # (N,) input-cell site index of each canvas site
    translation: np.ndarray  # (N, 3) int: canvas site = input site `parent` + R
    _index: dict = field(repr=False, compare=False, default_factory=dict)

    @classmethod
    def build(cls, crystal: CrystalStructure, matrix=DEFAULT_MATRIX) -> "Canvas":
        m = _as_matrix(matrix)
        cells = lattice_points_in_cell(m)
        n = len(crystal)
        parent = np.tile(np.arange(n), len(cells))
        translation = np.repeat(cells, n, axis=0)
        frac_in = crystal.frac_coords[parent] + translation  # input-lattice units
        frac = np.mod(frac_in @ np.linalg.inv(m), 1.0)
        frac[np.abs(frac - 1.0) < 1e-9] = 0.0
        lattice = m @ crystal.lattice_matrix
        species = [crystal.structure[p].specie for p in parent]
        labels = [crystal.labels[p] for p in parent]
        struct = Structure(lattice, species, frac, labels=labels)
        canvas_struct = CrystalStructure.from_pymatgen(struct, source=crystal.source,
                                                       symprec=crystal.symmetry.symprec)
        index = {(int(p), *map(int, r)): i for i, (p, r) in enumerate(zip(parent, translation))}
        return cls(crystal, m, canvas_struct, parent, translation, index)

    def __len__(self) -> int:
        return len(self.parent)

    @property
    def n_cells(self) -> int:
        return int(round(abs(np.linalg.det(self.matrix))))

    @property
    def is_diagonal(self) -> bool:
        return bool(np.all(self.matrix == np.diag(np.diag(self.matrix))))

    def describe(self) -> str:
        if self.is_diagonal:
            return "x".join(str(int(v)) for v in np.diag(self.matrix))
        return str(self.matrix.tolist())

    def reduce(self, translation: np.ndarray) -> np.ndarray:
        """Reduce input-lattice vectors modulo the canvas lattice (into the canvas cell)."""
        t = np.atleast_2d(np.asarray(translation, dtype=int))
        k = np.floor(t @ np.linalg.inv(self.matrix) + 1e-9).astype(int)
        return t - k @ self.matrix

    def index_of(self, parent: int, translation) -> int:
        """Canvas site index of input site ``parent`` shifted by any lattice vector."""
        r = self.reduce(translation)[0]
        return self._index[(int(parent), int(r[0]), int(r[1]), int(r[2]))]

    def sites_of_parent(self, parent: int) -> np.ndarray:
        return np.nonzero(self.parent == parent)[0]

    def resize_moments(self, new: "Canvas", moments: np.ndarray, tol: float = MOMENT_TOL) -> np.ndarray:
        """Carry ``moments`` (len(self), 3) onto canvas ``new`` (same input cell).

        Every new site (p, R) takes the moment of (p, R mod W_old); this is exact
        because moments on a canvas are periodic in it. Afterwards every old site
        is checked against the new site it maps to: if any differ, the moment
        pattern is not periodic in the new canvas and CanvasResizeError is raised.
        """
        moments = np.asarray(moments, dtype=float)
        if moments.shape != (len(self), 3):
            raise ValueError(f"moments must be ({len(self)}, 3)")
        new_m = np.array([moments[self.index_of(p, r)] for p, r in zip(new.parent, new.translation)])
        back = np.array([new_m[new.index_of(p, r)] for p, r in zip(self.parent, self.translation)])
        bad = np.nonzero(np.linalg.norm(back - moments, axis=1) > tol)[0]
        if len(bad):
            i = int(bad[0])
            raise CanvasResizeError(
                f"canvas {new.describe()} cannot hold the current moments: site "
                f"{self.crystal.labels[self.parent[i]]} at cell {self.translation[i].tolist()} "
                f"has m = {np.round(moments[i], 3).tolist()} but the new canvas would force "
                f"m = {np.round(back[i], 3).tolist()} ({len(bad)} sites conflict)")
        return new_m
