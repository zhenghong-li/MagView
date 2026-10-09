"""Per-element display style (radius + color) with a VESTA -> pymatgen fallback chain.

Source priority:
1. ``elements.ini`` of a local VESTA installation (read at runtime, never vendored);
2. pymatgen's bundled "VESTA" color scheme + pymatgen atomic radii;
3. a neutral gray for anything still unknown (VESTA's ``XX`` convention).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

from magview.core.species import element_symbol
from magview.style.vesta_ini import VestaElement, find_vesta_dir, parse_elements_ini

UNKNOWN_COLOR = (0.3, 0.3, 0.3)
UNKNOWN_RADIUS = 0.8


@dataclass(frozen=True)
class ElementStyle:
    symbol: str
    radius: float  # atomic radius in Angstrom (unscaled)
    color: tuple[float, float, float]  # RGB in [0, 1]
    source: str  # "vesta", "pymatgen" or "default"


class ElementStyles:
    """Lookup table ``symbol -> ElementStyle``."""

    def __init__(self, vesta_elements: dict[str, VestaElement] | None = None,
                 vesta_dir: Path | None = None) -> None:
        self.vesta_dir = vesta_dir
        self._vesta = vesta_elements or {}

    @classmethod
    def load(cls, vesta_dir: Path | None = None, use_vesta: bool = True) -> "ElementStyles":
        if use_vesta:
            vesta_dir = vesta_dir or find_vesta_dir()
            if vesta_dir is not None and (vesta_dir / "elements.ini").is_file():
                return cls(parse_elements_ini(vesta_dir / "elements.ini"), vesta_dir)
        return cls()

    @property
    def uses_vesta(self) -> bool:
        return bool(self._vesta)

    def __getitem__(self, species: object) -> ElementStyle:
        return self.get(species)

    def get(self, species: object) -> ElementStyle:
        sym = element_symbol(species)
        v = self._vesta.get(sym)
        if v is not None:
            return ElementStyle(sym, v.r_atomic, v.color, "vesta")
        color = _pymatgen_vesta_colors().get(sym)
        radius = _pymatgen_radius(sym)
        if color is not None and radius is not None:
            return ElementStyle(sym, radius, color, "pymatgen")
        xx = self._vesta.get("XX")
        if xx is not None:
            return ElementStyle(sym, xx.r_atomic, xx.color, "vesta")
        return ElementStyle(sym, radius or UNKNOWN_RADIUS, color or UNKNOWN_COLOR, "default")


@lru_cache(maxsize=1)
def _pymatgen_vesta_colors() -> dict[str, tuple[float, float, float]]:
    from monty.serialization import loadfn

    path = resources.files("pymatgen.core") / "ElementColorSchemes.yaml"
    with resources.as_file(path) as p:
        schemes = loadfn(str(p))
    return {k: (v[0] / 255.0, v[1] / 255.0, v[2] / 255.0) for k, v in schemes["VESTA"].items()}


@lru_cache(maxsize=None)
def _pymatgen_radius(symbol: str) -> float | None:
    from pymatgen.core import Element

    try:
        el = Element(symbol)
    except ValueError:
        return None
    r = el.atomic_radius or el.atomic_radius_calculated
    return float(r) if r is not None else None
