"""Rule-based magnetic-site suggestions."""

import pytest
from pymatgen.core import Lattice, Structure

from magview.core.structure import CrystalStructure
from magview.core.suggest import ElementRuleSuggester

S = ElementRuleSuggester()


def rows(crystal):
    return {r.element + ("" if r.candidate else "-x"): r for r in S.suggest(crystal).items}


def test_nio(load):
    r = S.suggest(load("NiO"))
    (ni,) = r.items
    assert (ni.element, ni.magnitude, ni.confidence, ni.candidate, ni.default_on) == \
        ("Ni", 2.0, "high", True, True)
    assert len(ni.sites) == 4 and "d8" in ni.rationale
    assert r.oxidation_states == {"Ni": 2.0, "O": -2.0}


def test_la2cuo4_lists_excluded_la(load):
    r = rows(load("La2CuO4"))
    assert r["Cu"].magnitude == 1.0 and r["Cu"].confidence == "high"
    assert not r["La-x"].candidate and "f0" in r["La-x"].rationale  # shown, not dropped


def test_fese_spin_state_ambiguity(load):
    fe = rows(load("FeSe"))["Fe"]
    assert fe.magnitude == 4.0 and fe.confidence == "medium" and "ambiguous" in fe.rationale


def test_metal_uses_default(load):
    fe = rows(load("Fe_bcc"))["Fe"]
    assert fe.magnitude == 3.0 and fe.confidence == "medium" and "metal" in fe.rationale


def test_cs3v9te13_mixed_valence(load):
    """V averages +2.56 (Cs+, Te2-): flagged low confidence; one row per orbit."""
    r = S.suggest(load("Cs3V9Te13_300K"))
    assert [s.label for s in r.items] == ["V1 (3g)", "V2 (6k)"]
    assert [len(s.sites) for s in r.items] == [3, 6]
    for s in r.items:
        assert s.candidate and s.default_on and s.confidence == "low"
        assert "mixed valence" in s.rationale and "uncertain" in s.rationale
        assert s.magnitude == pytest.approx(2.4)
    assert r.oxidation_states["V"] == pytest.approx(23 / 9)


def _crystal(lat, species, coords):
    return CrystalStructure.from_pymatgen(Structure(lat, species, coords))


def test_d0_excluded():
    sto = _crystal(Lattice.cubic(3.9), ["Sr", "Ti", "O", "O", "O"],
                   [[0, 0, 0], [.5, .5, .5], [.5, .5, 0], [.5, 0, .5], [0, .5, .5]])
    (ti,) = S.suggest(sto).items
    assert not ti.candidate and "d0" in ti.rationale


def test_rare_earth_and_4d():
    gdn = _crystal(Lattice.cubic(4.97), ["Gd", "N"], [[0, 0, 0], [.5, .5, .5]])
    (gd,) = S.suggest(gdn).items
    assert gd.magnitude == 7.0 and "4f7" in gd.rationale
    ruo2 = _crystal(Lattice.tetragonal(4.49, 3.11), ["Ru", "Ru", "O", "O", "O", "O"],
                    [[0, 0, 0], [.5, .5, .5], [.3, .3, 0], [.7, .7, 0], [.8, .2, .5], [.2, .8, .5]])
    (ru,) = S.suggest(ruo2).items
    assert ru.confidence == "low" and not ru.default_on and ru.magnitude == 2.0


def test_no_candidates():
    nacl = _crystal(Lattice.cubic(5.64), ["Na", "Cl"], [[0, 0, 0], [.5, .5, .5]])
    assert S.suggest(nacl).items == ()


def test_markdown_table(load):
    md = S.suggest(load("La2CuO4")).as_markdown()
    assert md.count("\n") == 3 and "| Cu1 (2a) |" in md
