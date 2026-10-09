"""Selection set algebra (selection is view state, not an undoable edit)."""

from __future__ import annotations

from collections.abc import Iterable

from magview.core.interaction import SelectOp


def apply_selection(current: frozenset[int], sites: Iterable[int], op: SelectOp) -> frozenset[int]:
    sites = frozenset(int(s) for s in sites)
    if op is SelectOp.REPLACE:
        return sites
    if op is SelectOp.ADD:
        return current | sites
    if op is SelectOp.SUBTRACT:
        return current - sites
    if op is SelectOp.TOGGLE:
        return current ^ sites
    raise ValueError(op)
