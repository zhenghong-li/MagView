"""Scene model: everything to draw, as plain numpy arrays (no VTK).

Building the model is separated from drawing it so geometry and color logic can
be tested without an OpenGL context, and so the GUI and offscreen paths share it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from magview.core.bonds import BondSet, find_bonds
from magview.core.images import DEFAULT_BOUNDS, DisplayAtoms, boundary_images
from magview.core.structure import CrystalStructure
from magview.style.appearance import Appearance
from magview.style.theme import BondStyle

# Unit-cube edges as pairs of corner indices; corners are the binary (i, j, k) triples.
_CORNERS = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)], dtype=float)
_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8)
          if np.abs(_CORNERS[a] - _CORNERS[b]).sum() == 1]


@dataclass
class SceneModel:
    lattice: np.ndarray  # (3, 3) rows a, b, c
    atoms: DisplayAtoms
    atom_symbols: list[str]
    atom_radius: np.ndarray  # (n,) display radius (Angstrom, already scaled)
    atom_rgb: np.ndarray  # (n, 3) in [0, 1]
    bonds: BondSet
    bond_segments: np.ndarray  # (k, 2, 3) drawn segments (two halves per bond if bicolor)
    bond_rgb: np.ndarray  # (k, 3)
    cell_edges: np.ndarray  # (12, 2, 3)
    moments: np.ndarray | None = None  # (n, 3) Cartesian mu_B per displayed atom
    atom_opacity: np.ndarray | None = None  # (n,) in [0, 1]; None means fully opaque

    @property
    def center(self) -> np.ndarray:
        return 0.5 * self.lattice.sum(axis=0)

    def all_points(self) -> np.ndarray:
        """Points that should fit in view (atom surfaces approximated by centers +- r)."""
        pts = [self.cell_edges.reshape(-1, 3)]
        if len(self.atoms):
            r = self.atom_radius[:, None]
            pts += [self.atoms.cart - r, self.atoms.cart + r]
        return np.vstack(pts)


def cell_edges(lattice: np.ndarray, origin=(0.0, 0.0, 0.0)) -> np.ndarray:
    corners = (_CORNERS + np.asarray(origin)) @ lattice
    return np.array([[corners[a], corners[b]] for a, b in _EDGES])


def build_scene_model(crystal: CrystalStructure, appearance: Appearance,
                      bounds=DEFAULT_BOUNDS, site_moments: np.ndarray | None = None,
                      show_bonds: bool = True, moment_view: bool = False) -> SceneModel:
    """Assemble atoms (with boundary images), bonds, cell edges and optional moments.

    ``site_moments`` is (n_sites, 3) Cartesian mu_B for the sites of ``crystal``;
    every displayed image inherits its parent's moment. ``moment_view`` applies the
    moment-editing look: atoms shrunk, atoms without a moment faded, no bonds.
    """
    if moment_view:
        show_bonds = False
    lattice = crystal.lattice_matrix
    symbols = crystal.symbols
    atoms = boundary_images(crystal.frac_coords, lattice, bounds)
    theme = appearance.theme

    if show_bonds:
        bonds = find_bonds(crystal.structure, atoms, symbols, appearance.bond_rules(symbols))
    else:
        bonds = BondSet(atoms, np.zeros((0, 2), int), np.zeros(0), np.zeros(0, int), len(atoms))
    atoms = bonds.atoms

    atom_symbols = [symbols[p] for p in atoms.parent]
    styles = [appearance.elements[s] for s in atom_symbols]
    radius = np.array([st.radius for st in styles]) * theme.ball_scale
    rgb = np.array([st.color for st in styles], dtype=float).reshape(-1, 3)

    segs, seg_rgb = _bond_geometry(bonds, atoms, rgb, theme)

    moments = None
    if site_moments is not None:
        site_moments = np.asarray(site_moments, dtype=float)
        if site_moments.shape != (len(crystal), 3):
            raise ValueError(f"site_moments must be ({len(crystal)}, 3), got {site_moments.shape}")
        moments = site_moments[atoms.parent]

    opacity = None
    if moment_view:
        radius = radius * theme.moment_view_atom_scale
        has_moment = (np.linalg.norm(moments, axis=1) > 0 if moments is not None
                      else np.zeros(len(atoms), bool))
        opacity = np.where(has_moment, 1.0, theme.moment_view_nonmagnetic_opacity)

    return SceneModel(lattice, atoms, atom_symbols, radius, rgb, bonds, segs, seg_rgb,
                      cell_edges(lattice), moments, opacity)


def _bond_geometry(bonds: BondSet, atoms: DisplayAtoms, atom_rgb: np.ndarray, theme):
    if len(bonds.pairs) == 0:
        return np.zeros((0, 2, 3)), np.zeros((0, 3))
    p0 = atoms.cart[bonds.pairs[:, 0]]
    p1 = atoms.cart[bonds.pairs[:, 1]]
    if theme.bond_style == BondStyle.BICOLOR:
        mid = 0.5 * (p0 + p1)
        segs = np.concatenate([np.stack([p0, mid], 1), np.stack([mid, p1], 1)])
        rgb = np.concatenate([atom_rgb[bonds.pairs[:, 0]], atom_rgb[bonds.pairs[:, 1]]])
    else:
        segs = np.stack([p0, p1], 1)
        rgb = np.tile(np.asarray(theme.bond_color, dtype=float), (len(p0), 1))
    return segs, rgb
