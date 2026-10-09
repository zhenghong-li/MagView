"""The editable magnetic document: structure, canvas, moments, history.

All edits go through ``History`` as snapshot commands, so each user action is
exactly one undo step and a failed edit (e.g. an impossible canvas resize)
changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from magview.core.canvas import DEFAULT_MATRIX, Canvas
from magview.core.history import History
from magview.core.structure import CrystalStructure
from magview.core.suggest import SiteSuggestion

DEFAULT_SPIN_AXIS = (0.0, 0.0, 1.0)


@dataclass
class SnapshotCommand:
    """Set document attributes to new values; revert restores the old ones."""

    label: str
    before: dict[str, Any]
    after: dict[str, Any]

    def apply(self, doc: "MagDocument") -> None:
        for k, v in self.after.items():
            setattr(doc, k, _copy(v))

    def revert(self, doc: "MagDocument") -> None:
        for k, v in self.before.items():
            setattr(doc, k, _copy(v))


def _copy(v):
    return v.copy() if isinstance(v, np.ndarray) else v


@dataclass(eq=False)
class MagDocument:
    crystal: CrystalStructure
    canvas: Canvas
    moments: np.ndarray  # (len(canvas), 3) Cartesian mu_B
    magnetic: np.ndarray  # (len(crystal),) bool: input sites flagged magnetic
    spin_axis: np.ndarray = field(default_factory=lambda: np.array(DEFAULT_SPIN_AXIS))
    collinear: bool = True
    history: History = field(init=False)

    def __post_init__(self) -> None:
        self.history = History(self)

    @classmethod
    def new(cls, crystal: CrystalStructure, canvas_matrix=DEFAULT_MATRIX) -> "MagDocument":
        canvas = Canvas.build(crystal, canvas_matrix)
        return cls(crystal, canvas, np.zeros((len(canvas), 3)), np.zeros(len(crystal), bool))

    # -- derived ------------------------------------------------------------------
    @property
    def canvas_magnetic(self) -> np.ndarray:
        return self.magnetic[self.canvas.parent]

    def site_label(self, canvas_site: int) -> str:
        p = int(self.canvas.parent[canvas_site])
        r = self.canvas.translation[canvas_site].tolist()
        wy = self.crystal.symmetry.wyckoffs[p]
        return f"{self.crystal.labels[p]} ({wy}) cell {r}"

    # -- edits (each is one undo step) -------------------------------------------
    def _commit(self, label: str, **after: Any) -> None:
        before = {k: _copy(getattr(self, k)) for k in after}
        self.history.push(SnapshotCommand(label, before, {k: _copy(v) for k, v in after.items()}))

    def accept_suggestions(self, items: list[SiteSuggestion],
                           magnitudes: dict[int, float] | None = None) -> None:
        """Flag the orbits as magnetic and start them ferromagnetic along the spin axis.

        ``magnitudes`` optionally overrides the suggested |m| per orbit representative.
        """
        magnitudes = magnitudes or {}
        flags = self.magnetic.copy()
        moments = self.moments.copy()
        axis = self.spin_axis / np.linalg.norm(self.spin_axis)
        names = []
        for s in items:
            mag = float(magnitudes.get(s.orbit, s.magnitude))
            flags[list(s.sites)] = True
            on_canvas = np.isin(self.canvas.parent, s.sites)
            moments[on_canvas] = mag * axis
            names.append(s.label)
        self._commit(f"Accept magnetic sites: {', '.join(names)}", magnetic=flags, moments=moments)

    def clear_magnetic(self) -> None:
        self._commit("Clear magnetic sites", magnetic=np.zeros_like(self.magnetic),
                     moments=np.zeros_like(self.moments))

    def resize_canvas(self, matrix) -> None:
        """Change the canvas. Raises CanvasResizeError (and changes nothing) if the
        current moments are not periodic in the new canvas."""
        new = Canvas.build(self.crystal, matrix)
        moments = self.canvas.resize_moments(new, self.moments)
        self._commit(f"Canvas {self.canvas.describe()} -> {new.describe()}", canvas=new,
                     moments=moments)
