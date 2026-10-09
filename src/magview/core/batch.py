"""Batch patterns for a selection of canvas sites (the Patterns popover).

Every option is computed exactly, or reported unavailable with the reason; nothing
is guessed. All patterns are collinear along the document's spin axis.

* FM up / FM down: always available.
* Planar selection (all sites in one plane normal to a, b or c): the 2D library of
  its lattice (core.lattice2d), propagated over the periodic in-plane graph.
* 3D selection:
  - A-type(k): FM layers normal to k, alternating along k (layer parity);
  - C-type(k): AFM nearest-neighbour bonds within layers, FM bonds between layers;
  - G-type: every nearest-neighbour bond AFM (needs a bipartite graph).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from magview.core.graph import neighbor_graph, propagate
from magview.core.lattice2d import bond_signs, classify, library

PLANE_TOL = 0.35  # Angstrom: max deviation from the plane for a "planar" selection
LAYER_TOL = 0.35  # Angstrom: sites closer than this along the axis share a layer


@dataclass(frozen=True)
class PatternOption:
    key: str
    label: str
    signs: np.ndarray | None  # (k,) +1/-1 per selected site (selection order), None if unavailable
    reason: str  # explanation (available) or why it is unavailable

    @property
    def available(self) -> bool:
        return self.signs is not None


def _axis_coords(canvas_struct, sites: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    """Fractional coordinate along canvas axis k (periodic) and the layer spacing scale (A)."""
    frac = canvas_struct.frac_coords[sites, k]
    lat = canvas_struct.lattice_matrix
    normal = np.cross(lat[(k + 1) % 3], lat[(k + 2) % 3])
    period = abs(lat[k] @ normal) / np.linalg.norm(normal)  # canvas thickness along the normal
    return frac, period


def planar_axis(canvas_struct, sites: np.ndarray) -> int | None:
    """Index k of a lattice axis whose normal plane contains all sites (periodic), else None."""
    if len(sites) < 2:
        return None
    for k in (2, 0, 1):
        frac, period = _axis_coords(canvas_struct, sites, k)
        d = (frac - frac[0] + 0.5) % 1.0 - 0.5  # circular difference
        if np.abs(d).max() * period <= PLANE_TOL:
            return k
    return None


def layer_index(canvas_struct, sites: np.ndarray, k: int) -> tuple[np.ndarray, bool]:
    """Layer number of each site along canvas axis k and whether the layers wrap periodically.

    Layers are clusters of the (circular) coordinate along k. They "wrap" when the
    selection spans the whole canvas period with roughly even spacing, in which case
    alternation must be consistent across the periodic boundary.
    """
    frac, period = _axis_coords(canvas_struct, sites, k)
    order = np.argsort(frac)
    xs = frac[order] * period
    labels = np.zeros(len(sites), dtype=int)
    cur = 0
    for a in range(1, len(xs)):
        if xs[a] - xs[a - 1] > LAYER_TOL:
            cur += 1
        labels[order[a]] = cur
    # Merge the last layer into the first if they touch across the boundary.
    sorted_labels = labels[order]
    if cur > 0 and (xs[0] + period) - xs[-1] <= LAYER_TOL:
        xs = np.where(sorted_labels == cur, xs - period, xs)
        labels[labels == cur] = 0
        sorted_labels = labels[order]
        cur -= 1
    n_layers = cur + 1
    centers = np.array([np.mean(xs[sorted_labels == L]) for L in range(n_layers)])
    gaps = np.diff(np.concatenate([centers, [centers[0] + period]]))
    wraps = n_layers > 1 and gaps.max() < 1.5 * np.median(gaps)
    return labels, bool(wraps)


def pattern_options(canvas_struct, sites, input_lattice: np.ndarray) -> list[PatternOption]:
    sites = np.asarray(sorted(set(int(s) for s in sites)), dtype=int)
    n = len(sites)
    opts = [PatternOption("fm_up", "FM up", np.ones(n, int), "all moments along +spin axis"),
            PatternOption("fm_down", "FM down", -np.ones(n, int), "all moments along -spin axis")]
    if n < 2:
        return opts
    lat = canvas_struct.lattice_matrix
    frac = canvas_struct.frac_coords

    k_plane = planar_axis(canvas_struct, sites)
    if k_plane is not None:
        graph = neighbor_graph(lat, frac, sites)
        normal = np.cross(lat[(k_plane + 1) % 3], lat[(k_plane + 2) % 3])
        lat2d = classify(graph, normal, input_lattice)
        if not lat2d.supported:
            opts.append(PatternOption("2d", "2D patterns", None,
                                      f"in-plane net not recognized ({lat2d.reason})"))
            return opts
        for pat in library(lat2d):
            if pat.key == "fm":
                continue
            prop = propagate(graph, bond_signs(lat2d, pat))
            label = f"{pat.label} [{lat2d.kind}]"
            if prop.n_components > 1:
                opts.append(PatternOption(pat.key, label, None,
                                          f"selection splits into {prop.n_components} groups not "
                                          "joined by nearest-neighbour bonds"))
            elif not prop.consistent:
                opts.append(PatternOption(pat.key, label, None,
                                          "does not fit the canvas periodicity; enlarge the canvas"))
            else:
                opts.append(PatternOption(pat.key, label, prop.signs, lat2d.reason))
        return opts

    # 3D selection.
    for k, name in enumerate("abc"):
        labels, wraps = layer_index(canvas_struct, sites, k)
        n_layers = labels.max() + 1
        key, label = f"A:{name}", f"A-type (layers normal to {name}, alternating along {name})"
        if n_layers < 2:
            opts.append(PatternOption(key, label, None, f"all selected sites lie in one {name}-layer"))
        elif wraps and n_layers % 2:
            opts.append(PatternOption(key, label, None,
                                      f"{n_layers} layers per canvas period along {name} (odd): "
                                      f"double the canvas along {name}"))
        else:
            opts.append(PatternOption(key, label, np.where(labels % 2 == 0, 1, -1),
                                      f"{n_layers} layers along {name}"))

    graph = neighbor_graph(lat, frac, sites)
    if graph.m == 0:
        return opts
    g = propagate(graph, -np.ones(graph.m, int))
    if g.n_components > 1:
        opts.append(PatternOption("G", "G-type (all nearest-neighbour bonds AFM)", None,
                                  f"selection splits into {g.n_components} nearest-neighbour groups"))
    elif not g.consistent:
        opts.append(PatternOption("G", "G-type (all nearest-neighbour bonds AFM)", None,
                                  "nearest-neighbour graph is not bipartite (frustrated) or the "
                                  "canvas is too small"))
    else:
        opts.append(PatternOption("G", "G-type (all nearest-neighbour bonds AFM)", g.signs,
                                  f"bipartite, coordination {int(graph.coordination().max())}"))
    for k, name in enumerate("abc"):
        normal = np.cross(lat[(k + 1) % 3], lat[(k + 2) % 3])
        normal = normal / np.linalg.norm(normal)
        inter = np.abs(graph.vec @ normal) > LAYER_TOL
        key, label = f"C:{name}", f"C-type (AFM within {name}-layers, FM between them)"
        if not inter.any() or inter.all():
            opts.append(PatternOption(key, label, None,
                                      f"nearest-neighbour bonds are not split into in-layer and "
                                      f"between-layer bonds along {name}"))
            continue
        prop = propagate(graph, np.where(inter, 1, -1))
        if prop.n_components > 1 or not prop.consistent:
            why = ("selection splits into groups" if prop.n_components > 1
                   else "frustrated within layers or canvas too small")
            opts.append(PatternOption(key, label, None, why))
        else:
            opts.append(PatternOption(key, label, prop.signs, "consistent"))
    return opts
