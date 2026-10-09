"""Inter-layer stacking: reference transport, frustration, relations, completion."""

import numpy as np
import pytest

from magview.core.document import MagDocument
from magview.core.layers import decompose
from magview.core.patterns import match_layer
from magview.core.stacking import fm_reference, match_stacking, relation, stacking_library
from magview.core.suggest import ElementRuleSuggester


def setup(load, name, matrix=(2, 2, 2)):
    d = MagDocument.new(load(name), matrix)
    d.accept_suggestions(list(ElementRuleSuggester().suggest(d.crystal).candidates))
    dec = decompose(d.canvas.structure, np.nonzero(d.canvas_magnetic)[0], d.crystal.lattice_matrix)
    return d, dec


def neel_signs(L):
    k = int(np.nonzero(L.lattice.bond_class == 0)[0][0])
    res = match_layer(L, {int(L.graph.i[k]): 1, int(L.graph.j[k]): -1})
    return next(c for c in res.candidates if c.pattern.key == "neel").signs


def ref_for(d, dec, base):
    return fm_reference(d.canvas.structure, list(dec.layers), dec.normal, dec.period, base)


@pytest.mark.parametrize("n, valid", [(1, {"fm"}), (2, {"fm", "afm"}), (3, {"fm"}),
                                      (4, {"fm", "afm", "uudd:0", "uudd:1"})])
def test_library_validity(n, valid):
    lib = stacking_library(n)
    assert {p.key for p, invalid in lib if invalid is None} == valid
    for p, _ in lib:
        assert len(p.flips) == n and p.flips[0] == 1  # layer 0 is the reference
    flips = {p.key: p.flips for p, _ in stacking_library(4)}
    assert flips["afm"] == (1, -1, 1, -1)
    assert flips["uudd:0"] == (1, 1, -1, -1) and flips["uudd:1"] == (1, -1, -1, 1)


def test_direct_stacking_transports_neel(load):
    d, dec = setup(load, "toy_square_CuO2")
    base = neel_signs(dec.layers[0])
    ref = ref_for(d, dec, base)
    assert ref.ok and np.array_equal(ref.signs[1], base)  # sites directly above: same spins


@pytest.mark.parametrize("name", ["La2CuO4", "NiO"])
def test_neel_over_centred_stacking_is_frustrated(load, name):
    """Body-centred (La2CuO4) / fcc (NiO) registry: each site sees up and down neighbours."""
    d, dec = setup(load, name)
    ref = ref_for(d, dec, neel_signs(dec.layers[0]))
    assert not ref.ok and "frustrated" in ref.reason
    ref_fm = ref_for(d, dec, np.ones(len(dec.layers[0].sites), int))
    assert ref_fm.ok and all(np.all(s == 1) for s in ref_fm.signs)


def test_single_layer_canvas(load):
    d, dec = setup(load, "toy_square_CuO2", (2, 2, 1))
    assert len(dec.layers) == 1
    ref = ref_for(d, dec, neel_signs(dec.layers[0]))
    assert ref.ok  # the layer above is its own periodic image
    lib = {p.key: invalid for p, invalid in stacking_library(1)}
    assert lib["afm"] is not None and "double the canvas" in lib["afm"]


def test_incomplete_layer0(load):
    d, dec = setup(load, "toy_square_CuO2")
    base = np.ones(len(dec.layers[0].sites), int)
    base[0] = 0
    ref = ref_for(d, dec, base)
    assert not ref.ok and "not fully assigned" in ref.reason


def test_relation():
    ref = np.array([1, -1, 1, -1])
    assert relation(np.array([0, 0, 0, 0]), ref) is None
    assert relation(np.array([1, 0, 0, -1]), ref) == 1
    assert relation(np.array([-1, 1, 0, 0]), ref) == -1
    assert relation(np.array([1, 1, 0, 0]), ref) == 0


def test_match_stacking_rules():
    assert match_stacking([1, None, None, None]).status == "idle"
    m = match_stacking([1, -1, None, None])
    assert {p.key for p in m.candidates} == {"afm", "uudd:1"}
    m = match_stacking([1, 1, None, None])
    assert {p.key for p in m.candidates} == {"fm", "uudd:0"}
    m = match_stacking([1, -1, -1, -1])
    assert m.status == "no_match" and not m.candidates
    m = match_stacking([1, 0, None, None])
    assert m.status == "no_match" and "different in-plane order" in m.reason
    m = match_stacking([1, -1, None])  # 3 layers: AFM invalid
    assert m.status == "no_match"
    assert any("odd" in r for p, r in m.excluded if p.key == "afm")


def test_apply_afm_stacking_and_flip_layer(load):
    d, dec = setup(load, "La2CuO4")
    group = list(dec.layers)
    base = np.ones(len(group[0].sites), int)
    ref = ref_for(d, dec, base)
    afm = next(p for p, _ in stacking_library(len(group)) if p.key == "afm")
    sites = np.concatenate([G.sites for G in group])
    d.apply_completion(sites, np.concatenate([f * r for f, r in zip(afm.flips, ref.signs)]), "afm")
    rels = [relation(d.spin_sign(G.sites), r) for G, r in zip(group, ref.signs)]
    assert rels == [1, -1, 1, -1]
    d.flip_layer(group[2].sites)
    rels = [relation(d.spin_sign(G.sites), r) for G, r in zip(group, ref.signs)]
    assert rels == [1, -1, -1, -1]
    d.history.undo()
    assert [relation(d.spin_sign(G.sites), r) for G, r in zip(group, ref.signs)] == [1, -1, 1, -1]
