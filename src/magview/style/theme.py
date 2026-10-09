"""Render theme: VESTA-derived defaults plus MagView's magnetic-editing colors.

Every numeric default below matches VESTA 3.x's shipped ``style.ini`` (see
``docs/vesta_reference.md``); ``RenderTheme.from_vesta`` re-reads them from the
user's installation when available so the two stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from magview.style.vesta_ini import VestaStyle

RGB = tuple[float, float, float]


class BondStyle(IntEnum):
    """VESTA bond-style codes (0-based; inferred, see vesta_ini.VestaBondRule)."""

    UNICOLOR = 0
    BICOLOR = 1
    COLOR_LINE = 2
    GRADIENT_LINE = 3
    DOTTED_LINE = 4
    DASHED_LINE = 5


def _rgb255(c: tuple[int, int, int]) -> RGB:
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


@dataclass(frozen=True)
class RenderTheme:
    # --- scene (VESTA STYLE block) ---
    background: RGB = (1.0, 1.0, 1.0)  # BKGRC 255 255 255
    ball_scale: float = 0.5  # ATOMP field 4 = 50 (%), inferred meaning
    sphere_resolution: int = 24  # ATOMP 24 24
    bond_radius: float = 0.25  # BONDP 0.250 Angstrom
    bond_sides: int = 16  # BONDP 16
    bond_style: BondStyle = BondStyle.BICOLOR  # BONDP 1
    bond_color: RGB = (127 / 255, 127 / 255, 127 / 255)  # BONDP 127 127 127 (unicolor)
    cell_line_color: RGB = (0.0, 0.0, 0.0)  # UCOLP 0 0 0
    cell_line_width: float = 1.0  # UCOLP 1.000
    parallel_projection: bool = True  # PROJT 0 (inferred: parallel)

    # --- lighting / material (LIGHT0 + ATOMM, OpenGL fixed-function terms) ---
    ambient: float = 26 / 255  # LIGHT0 ambient 26
    diffuse: float = 179 / 255  # LIGHT0 diffuse 179
    specular: float = 204 / 255  # ATOMM specular 204 (light specular is 255)
    specular_power: float = 25.6  # ATOMM shininess

    # --- axes triad (VESTA convention: a red, b green, c blue) ---
    axis_colors: tuple[RGB, RGB, RGB] = ((0.85, 0.0, 0.0), (0.0, 0.6, 0.0), (0.0, 0.0, 0.9))

    # --- magnetic moments (MagView) ---
    vector_scale: float = 1.0  # Angstrom per mu_B (VESTA VECTS 1.0)
    arrow_shaft_radius: float = 0.12  # Angstrom, constant regardless of length
    arrow_head_radius_ratio: float = 2.0  # head radius / shaft radius
    arrow_head_length_ratio: float = 0.25  # head length / total length
    arrow_anchor_centered: bool = True  # VESTA "penetrate" style
    arrow_resolution: int = 16
    spin_up_color: RGB = (0.84, 0.19, 0.12)
    spin_down_color: RGB = (0.13, 0.40, 0.75)
    arrow_neutral_color: RGB = (0.85, 0.55, 0.0)
    # Moment view: shrink atoms so arrows stay visible, fade non-magnetic atoms, hide bonds.
    moment_view_atom_scale: float = 0.6
    moment_view_nonmagnetic_opacity: float = 0.25

    # --- interaction feedback (MagView) ---
    selection_color: RGB = (1.0, 0.85, 0.0)
    moment_mode_color: RGB = (0.96, 0.62, 0.04)  # amber border
    layer_mode_color: RGB = (0.05, 0.6, 0.6)  # teal border

    @classmethod
    def from_vesta(cls, style: VestaStyle | None, base: "RenderTheme | None" = None) -> "RenderTheme":
        theme = base or cls()
        if style is None:
            return theme
        kw: dict[str, object] = {}
        try:
            kw["background"] = _rgb255(style.background_rgb)
            kw["ball_scale"] = style.atom_ball_scale
            kw["sphere_resolution"] = style.atom_sphere_resolution[0]
            kw["bond_radius"] = style.bond_radius
            kw["bond_sides"] = style.bond_segments
            kw["bond_style"] = BondStyle(style.bond_style)
            kw["bond_color"] = _rgb255(style.bond_rgb)
            kw["cell_line_color"] = _rgb255(style.cell_line_rgb)
            kw["cell_line_width"] = style.cell_line_width
            kw["parallel_projection"] = style.projection == 0
            light = style.light(0)
            kw["ambient"] = light.ambient[0] / 255
            kw["diffuse"] = light.diffuse[0] / 255
            mat = style.material("ATOMM")
            kw["specular"] = mat.specular[0] / 255 * light.specular[0] / 255
            kw["specular_power"] = mat.shininess
            kw["vector_scale"] = style.vector_scale
        except (KeyError, IndexError, ValueError):
            # Partial/unknown style.ini: keep built-in defaults for missing fields.
            pass
        return replace(theme, **kw)
