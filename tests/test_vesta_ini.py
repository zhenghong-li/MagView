"""Parsers for VESTA's elements.ini / style.ini, run against the local installation."""

import numpy as np
import pytest

from magview.style.theme import BondStyle, RenderTheme
from magview.style.vesta_ini import parse_elements_ini, parse_style_ini

pytestmark = pytest.mark.vesta


@pytest.fixture(scope="module")
def elements(vesta_dir):
    return parse_elements_ini(vesta_dir / "elements.ini")


@pytest.fixture(scope="module")
def style(vesta_dir):
    return parse_style_ini(vesta_dir / "style.ini")


def test_elements_all_rows(elements):
    assert len(elements) == 97  # Z = 1..95, plus D and the XX fallback
    assert {"H", "D", "XX", "Am"} <= set(elements)
    for e in elements.values():
        assert all(0.0 <= c <= 1.0 for c in e.color)
        assert e.r_atomic > 0 and e.r_vdw > 0 and e.r_ionic > 0


def test_elements_fe_row(elements):
    fe = elements["Fe"]
    assert fe.z == 26
    assert (fe.r_atomic, fe.r_vdw, fe.r_ionic) == (1.26, 1.88, 0.78)
    assert fe.color == pytest.approx((0.71051, 0.44662, 0.00136))


def test_sbond_table(style):
    assert style.version == "3.5.0"
    assert len(style.bond_rules) == 914
    fe_o = next(r for r in style.bond_rules if (r.a1, r.a2) == ("Fe", "O"))
    assert fe_o.d_max == pytest.approx(2.44693) and fe_o.d_min == 0.0
    assert fe_o.boundary_mode == 1 and fe_o.style == BondStyle.BICOLOR
    h_o = next(r for r in style.bond_rules if (r.a1, r.a2) == ("H", "O"))
    assert (h_o.d_min, h_o.d_max, h_o.style) == (1.2, 2.1, 5)  # hydrogen bond, line style
    c_o = next(r for r in style.bond_rules if (r.a1, r.a2) == ("C", "O"))
    assert c_o.boundary_mode == 2  # recursive search for molecules


def test_style_defaults(style):
    assert style.bond_radius == 0.25 and style.bond_segments == 16
    assert style.bond_rgb == (127, 127, 127) and style.bond_style == 1
    assert style.atom_sphere_resolution == (24, 24)
    assert style.atom_ball_scale == 0.5
    assert style.vector_scale == 1.0
    assert style.background_rgb == (255, 255, 255)
    assert style.cell_line_rgb == (0, 0, 0) and style.cell_line_width == 1.0
    assert style.projection == 0
    light = style.light(0)
    assert light.enabled and light.ambient[:3] == (26, 26, 26) and light.diffuse[:3] == (179,) * 3
    assert light.direction == (0.0, 0.0, -1.0)
    mat = style.material("ATOMM")
    assert mat.specular[:3] == (204, 204, 204) and mat.shininess == 25.6


def test_theme_from_vesta_matches_builtin_defaults(style):
    """Built-in defaults were transcribed from style.ini; keep them in sync."""
    assert RenderTheme.from_vesta(style) == RenderTheme()


def test_elements_ini_skips_malformed_rows(tmp_path):
    """One malformed row must not abort the whole elements.ini load."""
    path = tmp_path / "elements.ini"
    path.write_text("1 H 0.31 1.09 0.18 1.0 0.5 0.5\n"
                    "26 Fe 1.26 1.88\n"  # too few fields
                    "27 bogus row with non-numeric fields here\n"
                    "29 Cu 1.38 1.87 0.73 0.78 0.50 0.20\n")
    with pytest.warns(UserWarning, match="skipping malformed"):
        table = parse_elements_ini(path)
    assert set(table) == {"H", "Cu"}
    assert table["Cu"].z == 29


def test_pymatgen_fallback_colors_vs_vesta(elements):
    """pymatgen's 'VESTA' scheme is only a rough stand-in (documented finding)."""
    from magview.style.elements import _pymatgen_vesta_colors

    pm = _pymatgen_vesta_colors()
    diff = {s: np.abs(np.subtract(e.color, pm[s])).max() * 255
            for s, e in elements.items() if s in pm}
    for sym in ("H", "O", "Fe", "Ni", "Cu", "V", "La"):
        assert diff[sym] <= 3, sym
    assert diff["Cs"] > 100 and diff["C"] > 40  # known large discrepancies
