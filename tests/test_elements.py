"""Element style lookup and its fallback chain."""

import pytest

from magview.core.species import element_symbol
from magview.style.appearance import Appearance
from magview.style.elements import ElementStyles


@pytest.mark.parametrize("raw, sym", [("Fe", "Fe"), ("Fe2+", "Fe"), ("O2-", "O"), ("V", "V")])
def test_element_symbol(raw, sym):
    assert element_symbol(raw) == sym


def test_species_object():
    from pymatgen.core import Species

    assert element_symbol(Species("Mn", 3)) == "Mn"


def test_vesta_source(vesta_dir):
    styles = ElementStyles.load(vesta_dir)
    fe = styles["Fe"]
    assert fe.source == "vesta" and fe.radius == 1.26


def test_fallback_without_vesta():
    styles = ElementStyles.load(use_vesta=False)
    fe = styles["Fe2+"]
    assert fe.source == "pymatgen"
    assert 1.0 < fe.radius < 1.6
    assert fe.color == pytest.approx((181 / 255, 113 / 255, 0.0))


def test_unknown_symbol_gets_gray():
    st = ElementStyles.load(use_vesta=False)["Xq"]
    assert st.source == "default"


def test_appearance_fallback_has_bond_rules():
    app = Appearance.load(use_vesta=False)
    assert app.vesta_dir is None
    rules = app.bond_rules(["Ni", "O"])
    assert any({r.a1, r.a2} == {"Ni", "O"} for r in rules)
    assert not any({r.a1, r.a2} == {"Ni"} for r in rules)  # no metal-metal rule
