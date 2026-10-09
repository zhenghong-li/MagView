"""Species helpers shared by all layers."""

from __future__ import annotations

import re

_SYMBOL_RE = re.compile(r"[A-Z][a-z]?")


def element_symbol(species: object) -> str:
    """Bare element symbol from a pymatgen Species/Element/str ('Fe2+' -> 'Fe')."""
    sym = getattr(species, "symbol", None)
    if sym is None:
        sym = str(species)
    m = _SYMBOL_RE.match(sym)
    return m.group(0) if m else sym
