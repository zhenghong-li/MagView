"""Neighbour graphs, sign propagation, 2D lattice classification, batch pattern options."""

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure

from magview.core.batch import layer_index, pattern_options, planar_axis
from magview.core.canvas import Canvas
from magview.core.graph import miller_direction, neighbor_graph, propagate
from magview.core.lattice2d import bond_signs, classify, library
from magview.core.structure import CrystalStructure

MAGNETIC = {"Cu", "Cr", "Co", "Ni", "Fe", "V", "Mn"}


def canvas(load_or_crystal, matrix=(2, 2, 2)):
    c = load_or_crystal
    cv = Canvas.build(c, matrix)
    sym = np.array(c.symbols)[cv.parent]
    return c, cv, np.nonzero(np.isin(sym, list(MAGNETIC)))[0]


def layer0(cv, sites, k=2):
    return sites[np.isclose(cv.structure.frac_coords[sites, k], cv.structure.frac_coords[sites, k].min())]


@pytest.fixture(scope="module")
def cubic():
    """Simple-cubic Mn toy (bipartite in 3D)."""
    return CrystalStructure.from_pymatgen(Structure(Lattice.cubic(4.0), ["Mn"], [[0, 0, 0]]))


# --- graph ----------------------------------------------------------------------

def test_graph_coordination(load):
    c, cv, mag = canvas(load("toy_honeycomb_Cr"))
    lay = layer0(cv, mag)
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, lay)
    assert g.d_nn == pytest.approx(3.40, abs=1e-3)
    assert set(g.coordination()) == {3}
    assert g.m == 3 * len(lay) // 2  # each bond once


def test_self_image_bond_on_tiny_canvas(cubic):
    cv = Canvas.build(cubic, (1, 1, 1))
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, [0])
    assert g.m == 3 and np.all(g.i == g.j)  # bonds to its own images along a, b, c
    assert not propagate(g, -np.ones(3, int)).consistent  # AFM impossible in 1x1x1
    assert propagate(g, np.ones(3, int)).consistent


def test_propagate_detects_frustration(load):
    c, cv, mag = canvas(load("toy_triangular_Co"))
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, layer0(cv, mag))
    prop = propagate(g, -np.ones(g.m, int))
    assert not prop.consistent and len(prop.conflicts) > 0


def test_propagate_anchor():
    from magview.core.graph import NeighborGraph

    g = NeighborGraph(np.arange(3), np.zeros((3, 3)), np.array([0, 1]), np.array([1, 2]),
                      np.zeros((2, 3)), 1.0)
    p = propagate(g, np.array([-1, -1]), anchors={2: -1})
    assert list(p.signs) == [-1, 1, -1] and p.consistent


@pytest.mark.parametrize("vec, label", [((1, 0, 0), "[100]"), ((0.5, 0.5, 0), "[110]"),
                                        ((1, -1, 0), "[1-10]"), ((-2, 0, 0), "[100]")])
def test_miller_direction(vec, label):
    assert miller_direction(np.array(vec) * 3.0, np.eye(3) * 3.0) == label


# --- 2D classification ------------------------------------------------------------

@pytest.mark.parametrize("name, kind, n_patterns", [
    ("toy_square_CuO2", "square", 4), ("toy_honeycomb_Cr", "honeycomb", 8),
    ("toy_triangular_Co", "triangular", 4), ("FeSe", "square", 4),
    ("Cs3V9Te13_300K", "unsupported", 0)])
def test_classify(load, name, kind, n_patterns):
    c, cv, mag = canvas(load(name))
    if name.startswith("Cs3"):
        lay = mag[np.isclose(cv.structure.frac_coords[mag, 2], 0.25)]
    else:
        lay = layer0(cv, mag)
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, lay)
    lat = classify(g, cv.structure.lattice_matrix[2], c.lattice_matrix)
    assert lat.kind == kind, lat.reason
    assert len(library(lat)) == n_patterns


def test_fese_bond_directions_are_diagonal(load):
    c, cv, mag = canvas(load("FeSe"))
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, layer0(cv, mag))
    lat = classify(g, [0, 0, 1], c.lattice_matrix)
    assert sorted(cl.label for cl in lat.classes) == ["[1-10]", "[110]"]


def test_honeycomb_patterns_all_fit_2x2(load):
    c, cv, mag = canvas(load("toy_honeycomb_Cr"))
    lay = layer0(cv, mag)
    g = neighbor_graph(cv.structure.lattice_matrix, cv.structure.frac_coords, lay)
    lat = classify(g, [0, 0, 1], c.lattice_matrix)
    for pat in library(lat):
        prop = propagate(g, bond_signs(lat, pat))
        assert prop.consistent, pat.key
        if pat.name == "zigzag":  # every site: 2 FM neighbours, 1 AFM neighbour
            s = prop.signs
            same = s[g.i] == s[g.j]
            per_site_fm = np.bincount(g.i[same], minlength=g.n) + np.bincount(g.j[same], minlength=g.n)
            assert set(per_site_fm) == {2}


# --- batch options ----------------------------------------------------------------

def available(opts):
    return {o.key for o in opts if o.available}


def test_square_layer_options(load):
    c, cv, mag = canvas(load("toy_square_CuO2"))
    lay = layer0(cv, mag)
    opts = pattern_options(cv.structure, lay, c.lattice_matrix)
    assert available(opts) == {"fm_up", "fm_down", "neel", "stripe:[100]", "stripe:[010]"}
    neel = next(o for o in opts if o.key == "neel")
    xy = cv.translation[lay][:, :2]
    parity = np.where((xy.sum(axis=1) % 2) == 0, 1, -1)
    assert np.array_equal(neel.signs, parity * neel.signs[0] * parity[0])


def test_square_1x1_canvas_cannot_hold_neel(load):
    c, cv, mag = canvas(load("toy_square_CuO2"), (2, 1, 1))
    opts = pattern_options(cv.structure, mag, c.lattice_matrix)
    reasons = {o.key: o.reason for o in opts}
    assert "neel" not in available(opts) and "periodicity" in reasons["neel"]
    assert "stripe:[010]" in available(opts)  # FM chains along b, alternating along a: fits 2x1


def test_cubic_3d_options(cubic):
    _, cv, mag = canvas(cubic)
    keys = available(pattern_options(cv.structure, mag, cubic.lattice_matrix))
    assert {"G", "A:a", "A:b", "A:c", "C:a", "C:b", "C:c"} <= keys


def test_odd_layer_count_refused(cubic):
    _, cv, mag = canvas(cubic, (2, 2, 3))  # 3 layers along c per canvas period
    opts = {o.key: o for o in pattern_options(cv.structure, mag, cubic.lattice_matrix)}
    assert not opts["A:c"].available and "odd" in opts["A:c"].reason
    assert not opts["G"].available


def test_nio_fcc_frustration(load):
    c, cv, mag = canvas(load("NiO"))
    opts = {o.key: o for o in pattern_options(cv.structure, mag, c.lattice_matrix)}
    assert opts["A:c"].available and not opts["G"].available
    assert "bipartite" in opts["G"].reason


def test_cs3v9te13_layer_gets_no_2d_patterns(load):
    """Negative control: the V trimer net is none of the canonical lattices."""
    c, cv, mag = canvas(load("Cs3V9Te13_300K"))
    lay = mag[np.isclose(cv.structure.frac_coords[mag, 2], 0.25)]
    opts = pattern_options(cv.structure, lay, c.lattice_matrix)
    assert available(opts) == {"fm_up", "fm_down"}
    assert "not recognized" in next(o for o in opts if o.key == "2d").reason


def test_planar_axis_and_layers(load):
    c, cv, mag = canvas(load("La2CuO4"))
    assert planar_axis(cv.structure, layer0(cv, mag)) == 2
    assert planar_axis(cv.structure, mag) is None
    labels, wraps = layer_index(cv.structure, mag, 2)
    assert labels.max() + 1 == 4 and wraps  # Cu at z = 0, 1/2 per cell -> 4 per 2x2x2 canvas
