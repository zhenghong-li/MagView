"""Nearest-neighbour graphs over a subset of canvas sites, and sign propagation.

Collinear patterns are expressed as a sign (+1 FM, -1 AFM) per nearest-neighbour
bond. ``propagate`` turns bond signs into site signs by breadth-first search and
reports every bond that contradicts them, so an impossible pattern (frustrated
lattice, canvas too small) is detected exactly instead of being guessed.

Bonds are periodic in the canvas lattice: a site may bond to an image of
another selected site, or even to its own image when the canvas is small.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from pymatgen.core import Lattice, Structure

DEFAULT_REL_TOL = 0.08  # bonds up to (1 + tol) x the shortest distance count as nearest


@dataclass(frozen=True)
class NeighborGraph:
    sites: np.ndarray  # (n,) canvas site indices, in the order given
    positions: np.ndarray  # (n, 3) Cartesian (home positions in the canvas)
    i: np.ndarray  # (m,) local index of the bond start
    j: np.ndarray  # (m,) local index of the bond end (an image of it)
    vec: np.ndarray  # (m, 3) Cartesian bond vector from i to that image of j
    d_nn: float  # shortest distance found (0 if no bonds)

    @property
    def n(self) -> int:
        return len(self.sites)

    @property
    def m(self) -> int:
        return len(self.i)

    def coordination(self) -> np.ndarray:
        z = np.zeros(self.n, dtype=int)
        np.add.at(z, self.i, 1)
        np.add.at(z, self.j, 1)
        return z


def neighbor_graph(lattice: np.ndarray, frac: np.ndarray, sites, rel_tol: float = DEFAULT_REL_TOL,
                   d_nn: float | None = None) -> NeighborGraph:
    """Nearest-neighbour graph among ``sites`` (indices into ``frac``), periodic in ``lattice``.

    ``d_nn`` fixes the reference distance; by default it is the shortest distance
    among the given sites. Each undirected bond appears once.
    """
    sites = np.asarray(sites, dtype=int)
    n = len(sites)
    lat = Lattice(np.asarray(lattice, dtype=float))
    sub = np.asarray(frac, dtype=float)[sites]
    positions = sub @ lat.matrix
    empty = NeighborGraph(sites, positions, np.zeros(0, int), np.zeros(0, int), np.zeros((0, 3)), 0.0)
    if n == 0:
        return empty
    struct = Structure(lat, ["H"] * n, sub)
    if d_nn is None:
        r = 2.0
        max_r = 2.0 * max(lat.abc) + 1.0
        while True:
            c, p, o, d = struct.get_neighbor_list(r)
            if len(d) or r > max_r:
                break
            r *= 1.6
        if not len(d):
            return empty
        d_nn = float(d.min())
    c, p, o, d = struct.get_neighbor_list(d_nn * (1 + rel_tol) + 1e-6)
    keep = []
    for k, (a, b, off) in enumerate(zip(c, p, np.rint(o).astype(int))):
        # Keep each undirected bond once: a < b, or a self-image bond with a positive offset.
        if a < b or (a == b and tuple(off) > (0, 0, 0)):
            keep.append(k)
    keep = np.array(keep, dtype=int)
    i, j = c[keep].astype(int), p[keep].astype(int)
    off = np.rint(o[keep]).astype(int)
    vec = (sub[j] + off - sub[i]) @ lat.matrix if len(keep) else np.zeros((0, 3))
    return NeighborGraph(sites, positions, i, j, vec, d_nn)


@dataclass(frozen=True)
class Propagation:
    signs: np.ndarray  # (n,) +1/-1 per site (relative within each component)
    component: np.ndarray  # (n,) component label
    conflicts: np.ndarray  # (k,) bond indices whose sign is violated

    @property
    def n_components(self) -> int:
        return int(self.component.max()) + 1 if len(self.component) else 0

    @property
    def consistent(self) -> bool:
        return len(self.conflicts) == 0


def propagate(graph: NeighborGraph, bond_sign: np.ndarray, anchors: dict[int, int] | None = None) -> Propagation:
    """Site signs such that s_i * s_j == bond_sign for every bond (where possible).

    Each connected component starts from its anchored site (``anchors``: local
    index -> sign) or from its lowest index with +1. Conflicting bonds are listed.
    """
    n = graph.n
    bond_sign = np.asarray(bond_sign, dtype=int)
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for k, (a, b) in enumerate(zip(graph.i, graph.j)):
        adj[a].append((b, k))
        if a != b:
            adj[b].append((a, k))
    signs = np.zeros(n, dtype=int)
    comp = np.full(n, -1, dtype=int)
    anchors = anchors or {}
    order = sorted(anchors) + [s for s in range(n) if s not in anchors]
    label = 0
    for root in order:
        if comp[root] >= 0:
            continue
        signs[root] = anchors.get(root, 1)
        comp[root] = label
        queue = deque([root])
        while queue:
            a = queue.popleft()
            for b, k in adj[a]:
                if comp[b] < 0:
                    signs[b] = signs[a] * bond_sign[k]
                    comp[b] = label
                    queue.append(b)
        label += 1
    conflicts = np.array([k for k, (a, b) in enumerate(zip(graph.i, graph.j))
                          if signs[a] * signs[b] != bond_sign[k]], dtype=int)
    return Propagation(signs, comp, conflicts)


def miller_direction(vec: np.ndarray, lattice: np.ndarray, max_index: int = 6) -> str:
    """Lattice-direction label [uvw] of a Cartesian vector in the basis of ``lattice`` rows."""
    f = np.asarray(vec, float) @ np.linalg.inv(np.asarray(lattice, float))
    best = None
    for scale in range(1, max_index + 1):
        g = f / np.abs(f).max() * scale
        r = np.rint(g)
        err = np.abs(g - r).max()
        if err < 0.05:
            best = r.astype(int)
            break
    if best is None:
        return "[" + " ".join(f"{x:.2f}" for x in f) + "]"
    from math import gcd

    d = gcd(gcd(abs(best[0]), abs(best[1])), abs(best[2])) or 1
    best = best // d
    if best[np.nonzero(best)[0][0]] < 0:  # sign convention: first nonzero positive
        best = -best
    return "[" + "".join(str(v) if v >= 0 else f"-{-v}" for v in best) + "]"
