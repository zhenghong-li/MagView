"""Boundary images (VESTA 0..1 inclusive box) and VESTA-rule bond search."""

from collections import Counter

import numpy as np
import pytest

from magview.core.bonds import BondRule, find_bonds
from magview.core.images import boundary_images


def counts(c, atoms):
    sym = np.array(c.symbols)
    return Counter(sym[atoms.parent])


def test_bcc_fe_images(load):
    c = load("Fe_bcc")
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    assert len(a) == 9  # 8 corners + body center
    assert int((~a.is_image()).sum()) == 2  # one home position per site


def test_nio_images(load):
    c = load("NiO")
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    n = counts(c, a)
    assert n["Ni"] == 14 and n["O"] == 13  # corners+faces / edges+body center


def test_cs3v9te13_images(load):
    c = load("Cs3V9Te13_300K")
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    assert counts(c, a) == Counter({"Cs": 10, "V": 11, "Te": 19})


@pytest.mark.parametrize("name", ["NiO", "Cs3V9Te13_300K", "La2CuO4", "FeSe"])
def test_images_map_to_parent(load, name):
    c = load(name)
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    assert np.allclose(a.frac - a.shift, c.frac_coords[a.parent])
    assert np.allclose(a.cart, a.frac @ c.lattice_matrix)
    assert np.all((a.frac > -1e-4) & (a.frac < 1 + 1e-4))
    assert set(a.parent) == set(range(len(c)))


def test_larger_bounds(load):
    c = load("Fe_bcc")
    a = boundary_images(c.frac_coords, c.lattice_matrix, bounds=((0, 2), (0, 2), (0, 2)))
    assert len(a) == 27 + 8  # 3x3x3 corner lattice + 2x2x2 body centers


def bonds_for(c, appearance):
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    return find_bonds(c.structure, a, c.symbols, appearance.bond_rules(c.symbols))


def test_nio_octahedra_completed(load, appearance):
    c = load("NiO")
    bs = bonds_for(c, appearance)
    sym = np.array(c.symbols)[bs.atoms.parent]
    cn = bs.coordination()
    ni_box = (sym == "Ni") & (np.arange(len(sym)) < bs.n_boundary)
    assert np.all(cn[ni_box] == 6)  # boundary mode 1 pulls in the outside O
    assert np.allclose(bs.lengths, 4.1771 / 2)
    assert counts(c, bs.atoms)["O"] > 13


def test_boundary_mode_zero_keeps_box(load):
    c = load("NiO")
    a = boundary_images(c.frac_coords, c.lattice_matrix)
    bs = find_bonds(c.structure, a, c.symbols, [BondRule("Ni", "O", 0.0, 2.3, boundary_mode=0)])
    assert len(bs.atoms) == len(a)
    sym = np.array(c.symbols)[bs.atoms.parent]
    cn = bs.coordination()
    assert cn[sym == "Ni"].min() == 3  # corner Ni keeps only in-box neighbors


def test_bcc_fe_no_bonds(load, appearance):
    bs = bonds_for(load("Fe_bcc"), appearance)
    assert len(bs.pairs) == 0


@pytest.mark.vesta
def test_cs3v9te13_follows_vesta_cutoffs(load, appearance, vesta_dir):
    """VESTA's V-Te cutoff (2.72642 A) hides the 2.740 A Te1-V2 and 2.817 A Te2-V2 bonds."""
    c = load("Cs3V9Te13_300K")
    bs = bonds_for(c, appearance)
    sym = np.array(c.symbols)[bs.atoms.parent]
    lab = np.array(c.labels)[bs.atoms.parent]
    kinds = Counter(tuple(sorted(p)) for p in sym[bs.pairs])
    assert set(kinds) == {("Cs", "Te"), ("Te", "V")}  # no V-V, no Te-Te
    vte = [k for k, p in enumerate(bs.pairs) if set(sym[p]) == {"Te", "V"}]
    assert bs.lengths[vte].max() < 2.72642
    cn = bs.coordination()[: bs.n_boundary]
    assert set(cn[lab[: bs.n_boundary] == "V1"]) == {5}  # 4 Te2 + 1 Te3
    assert set(cn[lab[: bs.n_boundary] == "V2"]) == {2}  # only the two Te3


def test_bond_rule_matching_is_symmetric():
    r = BondRule("Fe", "O", 0.0, 2.0)
    assert r.matches("Fe", "O", 1.9) and r.matches("O", "Fe", 1.9)
    assert not r.matches("Fe", "O", 2.1) and not r.matches("Fe", "Fe", 1.0)
