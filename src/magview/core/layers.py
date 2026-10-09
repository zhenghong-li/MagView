"""Decompose magnetic sites into sublattice layers.

Sites are grouped by element first, then clustered into layers along a chosen
lattice axis of the canvas (a, b or c; default c). Each layer gets its periodic
in-plane nearest-neighbour graph and a 2D lattice classification
(core.lattice2d), which the pattern matcher (core.patterns) works on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from magview.core.batch import LAYER_TOL, layer_index
from magview.core.graph import NeighborGraph, neighbor_graph
from magview.core.lattice2d import Lattice2D, classify

AXES = "abc"


@dataclass(frozen=True)
class Layer:
    element: str
    index: int  # order along the axis within this element's group (0 = lowest)
    sites: np.ndarray  # canvas site indices, sorted
    height: float  # Angstrom along the axis normal (layer center, in [0, period))
    graph: NeighborGraph
    lattice: Lattice2D

    @property
    def label(self) -> str:
        return (f"{self.element} layer {self.index}  h = {self.height:.2f} A  "
                f"({len(self.sites)} sites, {self.lattice.kind})")


@dataclass(frozen=True)
class LayerDecomposition:
    axis: str
    normal: np.ndarray  # unit normal of the layers (Cartesian)
    period: float  # canvas thickness along the normal (Angstrom)
    layers: tuple[Layer, ...]  # ordered by element, then height

    def by_element(self, element: str) -> list[Layer]:
        return [L for L in self.layers if L.element == element]

    def layer_of(self, site: int) -> Layer | None:
        for L in self.layers:
            if site in L.sites:
                return L
        return None


def decompose(canvas_struct, sites, input_lattice: np.ndarray, axis: str = "c",
              tol: float = LAYER_TOL) -> LayerDecomposition:
    """Layers of ``sites`` (canvas indices) along canvas axis ``axis``."""
    k = AXES.index(axis)
    lat = canvas_struct.lattice_matrix
    normal = np.cross(lat[(k + 1) % 3], lat[(k + 2) % 3])
    normal = normal / np.linalg.norm(normal)
    period = float(abs(lat[k] @ normal))
    sites = np.asarray(sorted(set(int(s) for s in sites)), dtype=int)
    symbols = np.array(canvas_struct.symbols)
    layers = []
    for element in sorted(set(symbols[sites])):
        el_sites = sites[symbols[sites] == element]
        if len(el_sites) == 1:
            labels = np.zeros(1, int)
        else:
            labels, _ = layer_index(canvas_struct, el_sites, k)
        frac = canvas_struct.frac_coords[el_sites, k]
        for L in range(labels.max() + 1):
            members = el_sites[labels == L]
            f = frac[labels == L]
            # circular mean of the fractional coordinate -> height
            ang = 2 * np.pi * f
            mean = (np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2 * np.pi)) % 1.0
            graph = neighbor_graph(lat, canvas_struct.frac_coords, members)
            lat2d = classify(graph, normal, input_lattice)
            layers.append((element, mean * period, members, graph, lat2d))
    out = []
    for element in sorted(set(e for e, *_ in layers)):
        group = sorted((x for x in layers if x[0] == element), key=lambda x: x[1])
        for i, (el, h, members, graph, lat2d) in enumerate(group):
            out.append(Layer(el, i, np.sort(members), h, graph, lat2d))
    return LayerDecomposition(axis, normal, period, tuple(out))
