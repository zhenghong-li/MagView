"""Inter-layer stacking of one element's layers, with rule-based completion.

Reference ("FM stacking") configuration: layer 0's spins are transported layer
by layer along the shortest inter-layer nearest-neighbour bonds, each bond
demanding parallel spins. If a site receives conflicting demands (e.g. a Neel
plane stacked body-centred over another: two up and two down neighbours) the
stacking is *frustrated* and no reference exists. If the transport around the
canvas period does not return layer 0 unchanged, it is *inconsistent with the
canvas*. Both cases are reported, never guessed.

Every layer k then has a relation r_k to its reference: +1 (same), -1 (flipped),
0 (a different in-plane order) or None (nothing assigned yet). Stacking patterns
are flip sequences f_k relative to the reference:

* FM stacking          f = + + + + ...
* AFM stacking         f = + - + - ...            (needs an even number of layers)
* double stacking      f = + + - - ... / + - - +  (needs a multiple of 4)

Completion uses the same rule as the in-plane matcher: a pattern matches if it
agrees with every layer whose relation is known; nothing is applied automatically.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

REL_TOL = 0.08  # bonds up to (1 + tol) x the shortest inter-layer distance
HEIGHT_TOL = 0.35  # Angstrom


@dataclass(frozen=True)
class StackingPattern:
    key: str
    label: str
    flips: tuple[int, ...]


@dataclass(frozen=True)
class Reference:
    ok: bool
    signs: list[np.ndarray] | None  # per layer (local order), +1/-1
    reason: str


@dataclass(frozen=True)
class StackingMatch:
    status: str  # "idle" | "no_match" | "matches"
    candidates: tuple[StackingPattern, ...]
    excluded: tuple[tuple[StackingPattern, str], ...]
    relations: tuple[int | None, ...]  # per layer
    reason: str


def stacking_library(n: int) -> list[tuple[StackingPattern, str | None]]:
    """All stacking patterns for n layers per canvas period, with a reason if invalid."""
    out = [(StackingPattern("fm", "FM stacking", tuple([1] * n)), None)]
    afm = tuple(1 if k % 2 == 0 else -1 for k in range(n))
    out.append((StackingPattern("afm", "AFM stacking", afm),
                None if n % 2 == 0 else f"{n} layer(s) per canvas period (odd): double the canvas "
                "along the stacking axis"))
    for phase, name in ((0, "double stacking (+ + - -)"), (1, "double stacking (+ - - +)")):
        flips = tuple(1 if ((k + phase) // 2) % 2 == 0 else -1 for k in range(n))
        if phase == 1:
            flips = tuple(f * flips[0] for f in flips)  # layer 0 is always the reference (+)
        out.append((StackingPattern(f"uudd:{phase}", name, flips),
                    None if n % 4 == 0 else f"needs a multiple of 4 layers per canvas period ({n})"))
    return out


def interlayer_bonds(canvas_struct, sites_a: np.ndarray, sites_b: np.ndarray, normal: np.ndarray,
                     dh: float) -> tuple[np.ndarray, np.ndarray]:
    """Shortest bonds from layer A to layer B lying ``dh`` above it (periodic).

    Returns (ia, ib): indices into sites_a and sites_b for every bond.
    """
    lat = canvas_struct.lattice_matrix
    fa = canvas_struct.frac_coords[sites_a]
    fb = canvas_struct.frac_coords[sites_b]
    shifts = np.array(list(itertools.product((-1, 0, 1), repeat=3)))
    # (na, nb, ns, 3) Cartesian vectors a -> b + shift
    d = (fb[None, :, None, :] + shifts[None, None, :, :] - fa[:, None, None, :]) @ lat
    up = d @ normal
    ok = np.abs(up - dh) <= HEIGHT_TOL
    dist = np.linalg.norm(d, axis=-1)
    dist = np.where(ok, dist, np.inf)
    dmin = dist.min()
    if not np.isfinite(dmin):
        return np.zeros(0, int), np.zeros(0, int)
    ia, ib, _ = np.nonzero(dist <= dmin * (1 + REL_TOL) + 1e-6)
    pairs = sorted(set(zip(ia.tolist(), ib.tolist())))
    return np.array([p[0] for p in pairs], int), np.array([p[1] for p in pairs], int)


def fm_reference(canvas_struct, layers, normal: np.ndarray, period: float,
                 base: np.ndarray) -> Reference:
    """Transport layer 0's spins (``base``, all nonzero) through the stack with FM bonds."""
    n = len(layers)
    if np.any(np.asarray(base) == 0):
        return Reference(False, None, "layer 0 is not fully assigned yet")
    signs = [np.asarray(base, int)]
    for k in range(n):
        a, b = layers[k], layers[(k + 1) % n]
        dh = (b.height - a.height) % period
        if dh < HEIGHT_TOL:
            dh += period  # same layer (n = 1) or wrap: the next copy above
        ia, ib = interlayer_bonds(canvas_struct, a.sites, b.sites, normal, dh)
        if len(ia) == 0:
            return Reference(False, None, f"no inter-layer bonds between layers {k} and "
                                          f"{(k + 1) % n}")
        nxt = np.zeros(len(b.sites), int)
        for i, j in zip(ia, ib):
            want = signs[k][i]
            if nxt[j] == 0:
                nxt[j] = want
            elif nxt[j] != want:
                return Reference(False, None, f"frustrated: a site in layer {(k + 1) % n} has "
                                              f"inter-layer neighbours with both spins")
        if np.any(nxt == 0):
            return Reference(False, None, f"some sites of layer {(k + 1) % n} have no "
                                          f"neighbour in layer {k}")
        if k + 1 < n:
            signs.append(nxt)
        elif not np.array_equal(nxt, signs[0]):
            return Reference(False, None, "inconsistent with the canvas periodicity: transporting "
                                          "layer 0 around the stack does not return it unchanged")
    return Reference(True, signs, f"reference built from layer 0 through {n} layer(s)")


def relation(spins: np.ndarray, ref: np.ndarray) -> int | None:
    """+1 same as reference, -1 flipped, 0 different in-plane order, None unassigned."""
    spins = np.asarray(spins, int)
    assigned = spins != 0
    if not assigned.any():
        return None
    if np.all(spins[assigned] == ref[assigned]):
        return 1
    if np.all(spins[assigned] == -ref[assigned]):
        return -1
    return 0


def match_stacking(relations: list[int | None]) -> StackingMatch:
    n = len(relations)
    rels = tuple(relations)
    lib = stacking_library(n)
    known = {k: r for k, r in enumerate(relations) if k > 0 and r is not None}
    if any(r == 0 for r in known.values()):
        bad = [k for k, r in known.items() if r == 0]
        return StackingMatch("no_match", (), (), rels,
                             f"layer(s) {bad} have a different in-plane order than layer 0")
    if not known:
        return StackingMatch("idle", (), (), rels, "assign a spin in another layer to get a "
                                                   "stacking suggestion")
    cands, excl = [], []
    for pat, invalid in lib:
        if invalid:
            excl.append((pat, invalid))
        elif all(pat.flips[k] == r for k, r in known.items()):
            cands.append(pat)
        else:
            k = next(k for k, r in known.items() if pat.flips[k] != r)
            excl.append((pat, f"layer {k} would be {'same' if pat.flips[k] > 0 else 'flipped'}"))
    if not cands:
        return StackingMatch("no_match", (), tuple(excl), rels,
                             "no stacking pattern agrees with the assigned layers")
    return StackingMatch("matches", tuple(cands), tuple(excl), rels,
                         f"{len(cands)} stacking pattern(s) agree with {len(known)} assigned layer(s)")
