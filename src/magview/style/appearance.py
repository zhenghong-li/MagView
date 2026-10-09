"""One-stop loading of everything that decides how a structure looks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from magview.core.bonds import BondRule, default_bond_rules, rules_from_vesta
from magview.style.elements import ElementStyles
from magview.style.theme import RenderTheme
from magview.style.vesta_ini import VestaStyle, find_vesta_dir, parse_style_ini


@dataclass
class Appearance:
    elements: ElementStyles
    theme: RenderTheme
    vesta_rules: list[BondRule] | None  # None -> covalent-radius fallback
    vesta_dir: Path | None = None
    vesta_style: VestaStyle | None = None

    @classmethod
    def load(cls, vesta_dir: Path | None = None, use_vesta: bool = True) -> "Appearance":
        vesta_dir = (vesta_dir or find_vesta_dir()) if use_vesta else None
        style = None
        if vesta_dir is not None and (vesta_dir / "style.ini").is_file():
            style = parse_style_ini(vesta_dir / "style.ini")
        return cls(
            elements=ElementStyles.load(vesta_dir, use_vesta=vesta_dir is not None),
            theme=RenderTheme.from_vesta(style),
            vesta_rules=rules_from_vesta(style.bond_rules) if style else None,
            vesta_dir=vesta_dir,
            vesta_style=style,
        )

    def bond_rules(self, symbols) -> list[BondRule]:
        if self.vesta_rules is not None:
            return self.vesta_rules
        return default_bond_rules(symbols)

    @property
    def source(self) -> str:
        return f"VESTA ({self.vesta_dir})" if self.vesta_dir else "built-in fallback (pymatgen)"
