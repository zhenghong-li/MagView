"""Layer decomposition and the rule-based pattern matcher (truth tables)."""

import itertools

import numpy as np
import pytest

from magview.core.document import MagDocument
from magview.core.graph import propagate
from magview.core.lattice2d import bond_signs, library
from magview.core.layers import decompose
from magview.core.patterns import match_layer
from magview.core.suggest import ElementRuleSuggester
from magview.core import project


def make_doc(load, name, matrix=(2, 2, 2)):
    d = MagDocument.new(load(name), matrix)
    d.accept_suggestions(list(ElementRuleSuggester().suggest(d.crystal).candidates))
    return d


def layers_of(doc, axis="c"):
    return decompose(doc.canvas.structure, np.nonzero(doc.canvas_magnetic)[0],
                     doc.crystal.lattice_matrix, axis)


# --- decomposition ----------------------------------------------------------------

@pytest.mark.parametrize("name, n_layers, n_sites, kind", [
    ("toy_square_CuO2", 2, 4, "square"), ("La2CuO4", 4, 4, "square"), ("FeSe", 2, 8, "square"),
    ("toy_honeycomb_Cr", 2, 8, "honeycomb"), ("toy_triangular_Co", 2, 4, "triangular"),
    ("NiO", 4, 8, "square"), ("Cs3V9Te13_300K", 2, 36, "unsupported")])
def test_decompose_along_c(load, name, n_layers, n_sites, kind):
    dec = layers_of(make_doc(load, name))
    assert len(dec.layers) == n_layers
    assert {len(L.sites) for L in dec.layers} == {n_sites}
    assert {L.lattice.kind for L in dec.layers} == {kind}
    heights = [L.height for L in dec.layers]
    assert heights == sorted(heights) and dec.period > 0
    all_sites = np.concatenate([L.sites for L in dec.layers])
    assert len(all_sites) == len(set(all_sites.tolist()))  # a partition


def test_decompose_other_axis(load):
    dec = layers_of(make_doc(load, "toy_square_CuO2"), axis="a")
    # Layers normal to a: Cu chains along b (2 cells) x 2 c-layers -> 1D chains, unsupported
    assert len(dec.layers) == 2 and all(len(L.sites) == 4 for L in dec.layers)


def test_groups_by_element():
    from pymatgen.core import Lattice, Structure

    from magview.core.structure import CrystalStructure

    c = CrystalStructure.from_pymatgen(Structure(Lattice.tetragonal(4.0, 8.0), ["Mn", "Fe"],
                                                 [[0, 0, 0], [0.5, 0.5, 0]]))
    d = MagDocument.new(c, (2, 2, 1))
    d.accept_suggestions(list(ElementRuleSuggester().suggest(c).candidates))
    dec = layers_of(d)
    assert [L.element for L in dec.layers] == ["Fe", "Mn"]  # same plane, separate sublattices
    assert all(L.lattice.kind == "square" for L in dec.layers)


def buckled_honeycomb():
    """Silicene-like buckled honeycomb: NN = 2.28 A, sublattices offset by 0.30 A along c."""
    from pymatgen.core import Lattice, Structure

    from magview.core.structure import CrystalStructure

    lat = Lattice.hexagonal(2.28 * np.sqrt(3), 20.0)
    return CrystalStructure.from_pymatgen(
        Structure(lat, ["Si", "Si"], [[1 / 3, 2 / 3, 0.5 - 0.0075], [2 / 3, 1 / 3, 0.5 + 0.0075]]))


def test_buckled_honeycomb_classifies():
    """Buckling up to LAYER_TOL stays one layer and classifies (regression: the
    out-of-plane bond test used a tighter 0.25 A cutoff than the layer clustering)."""
    from magview.core.canvas import Canvas

    c = buckled_honeycomb()
    cv = Canvas.build(c, (2, 2, 1))
    dec = decompose(cv.structure, np.arange(len(cv)), c.lattice_matrix)
    assert len(dec.layers) == 1
    assert dec.layers[0].lattice.kind == "honeycomb"


def test_decompose_threads_tol():
    from magview.core.canvas import Canvas

    c = buckled_honeycomb()
    cv = Canvas.build(c, (1, 1, 1))
    sites = np.arange(len(cv))
    assert len(decompose(cv.structure, sites, c.lattice_matrix).layers) == 1
    assert len(decompose(cv.structure, sites, c.lattice_matrix, tol=0.1).layers) == 2


# --- matcher: worked examples -------------------------------------------------------

def bond_pair(L, cls):
    k = int(np.nonzero(L.lattice.bond_class == cls)[0][0])
    return int(L.graph.i[k]), int(L.graph.j[k])


def keys(res):
    return {c.pattern.key for c in res.candidates}


def test_users_example_square(load):
    """Two adjacent atoms with the same spin -> FM and the stripe along that bond."""
    L = layers_of(make_doc(load, "toy_square_CuO2")).layers[0]
    i, j = bond_pair(L, 0)
    c0, c1 = (c.label for c in L.lattice.classes)
    assert keys(match_layer(L, {i: 1, j: 1})) == {"fm", f"stripe:{c0}"}
    assert keys(match_layer(L, {i: 1, j: -1})) == {"neel", f"stripe:{c1}"}
    assert keys(match_layer(L, {i: -1, j: -1})) == {"fm", f"stripe:{c0}"}  # global flip


def test_users_example_honeycomb(load):
    L = layers_of(make_doc(load, "toy_honeycomb_Cr")).layers[0]
    labels = [c.label for c in L.lattice.classes]
    i, j = bond_pair(L, 0)
    same = keys(match_layer(L, {i: 1, j: 1}))
    assert same == {"fm", f"zigzag:{labels[1]}", f"zigzag:{labels[2]}", f"stripe:{labels[0]}"}
    opp = keys(match_layer(L, {i: 1, j: -1}))
    assert opp == {"neel", f"zigzag:{labels[0]}", f"stripe:{labels[1]}", f"stripe:{labels[2]}"}


def test_triangular_examples(load):
    L = layers_of(make_doc(load, "toy_triangular_Co")).layers[0]
    labels = [c.label for c in L.lattice.classes]
    i, j = bond_pair(L, 0)
    assert keys(match_layer(L, {i: 1, j: 1})) == {"fm", f"stripe:{labels[0]}"}
    assert keys(match_layer(L, {i: 1, j: -1})) == {f"stripe:{labels[1]}", f"stripe:{labels[2]}"}


def test_idle_rules(load):
    L = layers_of(make_doc(load, "toy_square_CuO2")).layers[0]
    assert match_layer(L, {}).status == "idle"
    assert match_layer(L, {0: 1}).status == "idle"
    V = layers_of(make_doc(load, "Cs3V9Te13_300K")).layers[0]
    res = match_layer(V, {k: (1 if k % 2 else -1) for k in range(10)})
    assert res.status == "idle" and not res.candidates  # never guesses on an unknown net
    assert "no canonical patterns" in res.reason


def test_no_match(load):
    """Square chain along [100] with up, up, down: no canonical pattern fits."""
    L = layers_of(make_doc(load, "toy_square_CuO2", (3, 3, 1))).layers[0]
    g = L.graph
    cls0 = L.lattice.bond_class == 0
    a, b = int(g.i[cls0][0]), int(g.j[cls0][0])
    # next site along the same direction from b
    nxt = [int(j) for i_, j, c in zip(g.i, g.j, cls0) if c and i_ == b and j != a]
    nxt += [int(i_) for i_, j, c in zip(g.i, g.j, cls0) if c and j == b and i_ != a]
    res = match_layer(L, {a: 1, b: 1, nxt[0]: -1})
    assert res.status == "no_match" and not res.candidates
    assert len(res.excluded) == len(library(L.lattice))


def test_canvas_too_small(load):
    L = layers_of(make_doc(load, "toy_square_CuO2", (2, 1, 1))).layers[0]
    i, j = bond_pair(L, 0)
    res = match_layer(L, {i: 1, j: -1})
    reasons = {e.pattern.key: e.reason for e in res.excluded}
    assert "neel" not in keys(res) and "canvas" in reasons["neel"]


# --- matcher: exhaustive soundness/completeness ------------------------------------------

@pytest.mark.parametrize("name", ["toy_square_CuO2", "toy_honeycomb_Cr", "toy_triangular_Co", "FeSe"])
def test_truth_table(load, name):
    """For every pattern P, global sign, and every 2- and 3-site pin set drawn from P:
    the result contains P (completeness), every candidate agrees with the pins
    (soundness), and the candidate set equals a brute-force check over the library."""
    L = layers_of(make_doc(load, name)).layers[0]
    pats = library(L.lattice)
    S = {}
    for p in pats:
        prop = propagate(L.graph, bond_signs(L.lattice, p))
        if prop.consistent and prop.n_components == 1:
            S[p.key] = prop.signs
    n = L.graph.n
    checked = 0
    for key, sp in S.items():
        for g in (1, -1):
            for k in (2, 3):
                for subset in itertools.islice(itertools.combinations(range(n), k), 60):
                    pins = {s: int(g * sp[s]) for s in subset}
                    res = match_layer(L, pins)
                    got = keys(res)
                    assert res.status == "matches" and key in got
                    for c in res.candidates:
                        assert all(c.signs[s] == v for s, v in pins.items())
                    brute = {q for q, sq in S.items()
                             if any(all(h * sq[s] == v for s, v in pins.items()) for h in (1, -1))}
                    assert got == brute
                    checked += 1
    assert checked >= 60  # 4-site layers: C(4,2)+C(4,3) = 10 pin sets per pattern and sign


# --- document layer operations ----------------------------------------------------------

def test_layer_click_cycle_and_ops(load, tmp_path):
    d = make_doc(load, "toy_square_CuO2", (2, 2, 1))
    L = layers_of(d).layers[0]
    s0, s1 = int(L.sites[0]), int(L.sites[1])
    d.clear_layer(L.sites)
    assert not d.pinned[L.sites].any() and not d.spin_sign(L.sites).any()
    assert d.layer_click(s0) == 1 and d.pinned[s0]
    assert d.layer_click(s0) == -1
    assert d.layer_click(s0) == 1
    d.layer_set([s1], -1)
    assert d.spin_sign([s1])[0] == -1 and d.pinned[s1]
    d.apply_completion(L.sites, np.ones(len(L.sites), int), "all up")
    assert d.pinned[s1] and d.spin_sign([s1])[0] == 1  # completion keeps pins as they are
    assert set(np.nonzero(d.pinned)[0]) == {s0, s1}
    path = project.save(d, tmp_path / "p.magview.json")
    assert np.array_equal(project.load(path).pinned, d.pinned)
    d.layer_unset(s0)
    assert not d.pinned[s0] and d.spin_sign([s0])[0] == 0
    for _ in range(4):
        d.history.undo()
    assert d.pinned[s0] and not d.pinned[s1]
