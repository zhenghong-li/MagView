"""Bond search with VESTA ``SBOND`` semantics.

A rule ``(A1, A2, d_min, d_max, boundary_mode, style)`` draws a bond between an
A1 atom and an A2 atom whose distance lies in [d_min, d_max]. Boundary modes:

* 0 - only bonds between atoms already displayed;
* 1 - every displayed A1 atom also pulls in its bonded A2 neighbors that lie
  outside the boundary (this is how VESTA completes coordination polyhedra);
* 2 - VESTA's recursive/molecule search. Not implemented yet: treated as 1.

After the displayed set is final, bonds are drawn between every displayed pair
that matches any rule (first matching rule in table order wins).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from magview.core.images import DisplayAtoms

LENGTH_TOL = 1e-6


@dataclass(frozen=True)
class BondRule:
    a1: str
    a2: str
    d_min: float
    d_max: float
    boundary_mode: int = 1
    style: int = 1  # VESTA bond-style code (see style.theme.BondStyle)

    def matches(self, s1: str, s2: str, d: float) -> bool:
        if not (self.d_min - LENGTH_TOL <= d <= self.d_max + LENGTH_TOL):
            return False
        return (s1 == self.a1 and s2 == self.a2) or (s1 == self.a2 and s2 == self.a1)


@dataclass(frozen=True)
class BondSet:
    atoms: DisplayAtoms  # displayed atoms, possibly augmented by boundary mode 1
    pairs: np.ndarray  # (m, 2) indices into atoms, i < j
    lengths: np.ndarray  # (m,)
    rule_index: np.ndarray  # (m,) index into ``rules``
    n_boundary: int  # atoms[:n_boundary] came from the boundary box; the rest were added
    rules: tuple[BondRule, ...] = ()  # the rules relevant to this structure

    def coordination(self) -> np.ndarray:
        """Number of bonds per displayed atom."""
        cn = np.zeros(len(self.atoms), dtype=int)
        np.add.at(cn, self.pairs.ravel(), 1)
        return cn


def rules_from_vesta(vesta_rules) -> list[BondRule]:
    """Convert ``style.vesta_ini.VestaBondRule`` rows (label-based rows are skipped)."""
    return [BondRule(r.a1, r.a2, r.d_min, r.d_max, r.boundary_mode, r.style)
            for r in vesta_rules if not r.search_by_label]


ANION_LIKE = frozenset("H B C N O F P S Cl As Se Br Te I Sb".split())


def default_bond_rules(symbols, scale: float = 1.2) -> list[BondRule]:
    """Fallback when no VESTA style.ini is available.

    Pairs involving at least one anion-like element bond up to ``scale`` times the
    sum of covalent radii (pymatgen table). Metal-metal pairs get no rule, like VESTA.
    """
    rules = []
    uniq = sorted(set(symbols))
    for i, a in enumerate(uniq):
        for b in uniq[i:]:
            if a not in ANION_LIKE and b not in ANION_LIKE:
                continue
            ra, rb = _covalent_radius(a), _covalent_radius(b)
            if ra is None or rb is None:
                continue
            first, second = (b, a) if (a in ANION_LIKE and b not in ANION_LIKE) else (a, b)
            rules.append(BondRule(first, second, 0.0, scale * (ra + rb), 1, 1))
    return rules


@lru_cache(maxsize=None)
def _covalent_radius(symbol: str) -> float | None:
    from pymatgen.analysis.molecule_structure_comparator import CovalentRadius

    r = CovalentRadius.radius.get(symbol)
    return float(r) if r is not None else None


def find_bonds(structure, atoms: DisplayAtoms, symbols, rules: list[BondRule]) -> BondSet:
    """Find bonds among displayed atoms, adding boundary-mode-1 neighbors first.

    ``structure`` is the pymatgen ``Structure`` of the input cell (frac coords in
    [0, 1)); ``symbols`` are its per-site element symbols.
    """
    symbols = list(symbols)
    present = set(symbols)
    rules = [r for r in rules if r.a1 in present and r.a2 in present]
    lattice = np.array(structure.lattice.matrix)
    frac = np.array(structure.frac_coords)
    n_boundary = len(atoms)
    if not rules or len(atoms) == 0:
        return BondSet(atoms, np.zeros((0, 2), int), np.zeros(0), np.zeros(0, int), n_boundary,
                       tuple(rules))

    r_max = max(r.d_max for r in rules) + 1e-4
    centers, points, offsets, dists = structure.get_neighbor_list(r_max)
    offsets = np.rint(offsets).astype(int)
    neighbors: dict[int, list[tuple[int, tuple[int, int, int], float]]] = defaultdict(list)
    for c, p, o, d in zip(centers, points, offsets, dists):
        neighbors[int(c)].append((int(p), (int(o[0]), int(o[1]), int(o[2])), float(d)))

    def match(s1: str, s2: str, d: float) -> int:
        for k, rule in enumerate(rules):
            if rule.matches(s1, s2, d):
                return k
        return -1

    keys = atoms.keys()
    index = {k: i for i, k in enumerate(keys)}

    # Pass 1: boundary mode >= 1 -> each displayed A1 pulls in bonded A2 outside the box.
    for p, sx, sy, sz in list(keys):
        s1 = symbols[p]
        for q, (ox, oy, oz), d in neighbors[p]:
            key = (q, sx + ox, sy + oy, sz + oz)
            if key in index:
                continue
            k = match(s1, symbols[q], d)
            if k < 0:
                continue
            rule = rules[k]
            if rule.boundary_mode >= 1 and rule.a1 == s1 and rule.a2 == symbols[q]:
                index[key] = len(keys)
                keys.append(key)

    atoms = DisplayAtoms.from_keys(keys, frac, lattice)

    # Pass 2: bonds between all displayed atoms.
    pairs, lengths, rule_idx = [], [], []
    for i, (p, sx, sy, sz) in enumerate(keys):
        for q, (ox, oy, oz), d in neighbors[p]:
            j = index.get((q, sx + ox, sy + oy, sz + oz))
            if j is None or j <= i:
                continue
            k = match(symbols[p], symbols[q], d)
            if k >= 0:
                pairs.append((i, j))
                lengths.append(d)
                rule_idx.append(k)
    return BondSet(
        atoms,
        np.array(pairs, dtype=int).reshape(-1, 2),
        np.array(lengths, dtype=float),
        np.array(rule_idx, dtype=int),
        n_boundary,
        tuple(rules),
    )
