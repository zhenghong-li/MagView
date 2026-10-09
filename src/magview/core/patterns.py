"""Rule-based completion of a partially assigned layer (deterministic, no guessing).

Input: one layer (its nearest-neighbour graph + 2D lattice classification) and
the spins the user pinned by clicking (+1 up / -1 down). For every canonical
pattern of that lattice (core.lattice2d.library):

1. propagate the pattern's bond signs over the layer graph -> site signs S_P
   (skipped if the pattern does not fit the canvas, i.e. the propagation has
   conflicts, or the layer is not connected);
2. it MATCHES if one global sign g gives s_i = g * S_P(i) for every pinned site i.

Status rules:
* "idle": fewer than MIN_PINNED pinned sites, or the lattice is not one of the
  supported nets -> show nothing;
* "no_match": no canonical pattern agrees with the pins -> say so, do nothing;
* "matches": list every matching pattern with its completion; the user picks one.
  Nothing is ever applied automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from magview.core.graph import propagate
from magview.core.lattice2d import Pattern2D, bond_signs, library

MIN_PINNED = 2


@dataclass(frozen=True)
class Candidate:
    pattern: Pattern2D
    signs: np.ndarray  # (n,) completed spins for the whole layer (local order)
    n_new: int  # sites whose spin the completion would set or change (not pinned)
    explanation: str


@dataclass(frozen=True)
class Exclusion:
    pattern: Pattern2D
    reason: str


@dataclass(frozen=True)
class MatchResult:
    status: str  # "idle" | "no_match" | "matches"
    candidates: tuple[Candidate, ...]
    excluded: tuple[Exclusion, ...]
    reason: str


def match_layer(layer, pinned: dict[int, int], labels: list[str] | None = None) -> MatchResult:
    """Match pinned spins (local index -> +1/-1) of ``layer`` against its pattern library."""
    lat = layer.lattice
    if not lat.supported:
        return MatchResult("idle", (), (), f"no canonical patterns: {lat.reason}")
    if len(pinned) < MIN_PINNED:
        return MatchResult("idle", (), (), f"pin at least {MIN_PINNED} sites to get suggestions "
                           f"({len(pinned)} pinned)")
    labels = labels or [str(i) for i in range(layer.graph.n)]
    idx = np.array(sorted(pinned), dtype=int)
    want = np.array([pinned[i] for i in idx], dtype=int)
    cands, excl = [], []
    for pat in library(lat):
        prop = propagate(layer.graph, bond_signs(lat, pat))
        if prop.n_components > 1:
            excl.append(Exclusion(pat, "layer is not connected by nearest-neighbour bonds"))
            continue
        if not prop.consistent:
            excl.append(Exclusion(pat, "does not fit the canvas periodicity (enlarge the canvas)"))
            continue
        g = want[0] * prop.signs[idx[0]]
        got = g * prop.signs[idx]
        bad = np.nonzero(got != want)[0]
        if len(bad):
            j = idx[bad[0]]
            excl.append(Exclusion(pat, f"site {labels[j]} would be {'up' if -want[bad[0]] > 0 else 'down'}"))
            continue
        signs = g * prop.signs
        n_new = int(layer.graph.n - len(idx))
        cands.append(Candidate(pat, signs, n_new,
                               f"agrees with all {len(idx)} pinned sites; sets {n_new} more"))
    if not cands:
        return MatchResult("no_match", (), tuple(excl),
                           "no canonical pattern agrees with the pinned spins")
    return MatchResult("matches", tuple(cands), tuple(excl),
                       f"{len(cands)} pattern{'s' if len(cands) > 1 else ''} agree with "
                       f"{len(idx)} pinned site{'s' if len(idx) > 1 else ''}")
