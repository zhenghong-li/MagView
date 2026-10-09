"""The editable magnetic document: structure, canvas, moments, history.

All edits go through ``History`` as snapshot commands, so each user action is
exactly one undo step and a failed edit (e.g. an impossible canvas resize)
changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from magview.core import magnetic as mg
from magview.core.canvas import DEFAULT_MATRIX, Canvas
from magview.core.history import History
from magview.core.structure import CrystalStructure
from magview.core.suggest import SiteSuggestion

DEFAULT_SPIN_AXIS = (0.0, 0.0, 1.0)
FALLBACK_MAGNITUDE = 1.0  # mu_B, when a site has neither a moment nor a suggested magnitude


class NonCollinearError(ValueError):
    """Switching to the collinear model would change moments; project explicitly instead."""


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
    site_magnitude: np.ndarray | None = None  # (len(crystal),) preferred |m| per input site
    history: History = field(init=False)

    def __post_init__(self) -> None:
        self.history = History(self)
        if self.site_magnitude is None:
            self.site_magnitude = np.zeros(len(self.crystal))

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
        site_mag = self.site_magnitude.copy()
        axis = self.spin_axis / np.linalg.norm(self.spin_axis)
        names = []
        for s in items:
            mag = float(magnitudes.get(s.orbit, s.magnitude))
            flags[list(s.sites)] = True
            site_mag[list(s.sites)] = mag
            on_canvas = np.isin(self.canvas.parent, s.sites)
            moments[on_canvas] = mag * axis
            names.append(s.label)
        self._commit(f"Accept magnetic sites: {', '.join(names)}", magnetic=flags, moments=moments,
                     site_magnitude=site_mag)

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

    # -- moment edits ---------------------------------------------------------------
    def magnitude_for(self, sites) -> np.ndarray:
        """|m| to use when a pattern sets only directions: current |m|, else the
        suggested/accepted magnitude of the site, else FALLBACK_MAGNITUDE."""
        sites = np.asarray(sites, dtype=int)
        cur = np.linalg.norm(self.moments[sites], axis=1)
        pref = self.site_magnitude[self.canvas.parent[sites]]
        return np.where(cur > 0, cur, np.where(pref > 0, pref, FALLBACK_MAGNITUDE))

    def set_moments(self, sites, vectors, label: str = "Set moment") -> None:
        """Set moments of canvas ``sites`` (vectors: (3,) or (k, 3)); one undo step.

        In the collinear model vectors are projected onto the spin axis. Sites that
        receive a nonzero moment get their input site flagged magnetic.
        """
        sites = np.asarray(sorted(set(int(s) for s in np.atleast_1d(sites))), dtype=int)
        if len(sites) == 0:
            return
        vecs = np.broadcast_to(np.asarray(vectors, dtype=float), (len(sites), 3)).copy()
        if self.collinear:
            vecs = mg.project_collinear(vecs, self.spin_axis)
        moments = self.moments.copy()
        moments[sites] = vecs
        flags = self.magnetic.copy()
        nonzero = np.linalg.norm(vecs, axis=1) > 0
        flags[self.canvas.parent[sites[nonzero]]] = True
        self._commit(label, moments=moments, magnetic=flags)

    def apply_signs(self, sites, signs, label: str) -> None:
        """Collinear pattern: m_i = sign_i * |m_i| * spin_axis (see ``magnitude_for``)."""
        sites = np.asarray(sites, dtype=int)
        mags = self.magnitude_for(sites) * np.asarray(signs, dtype=float)
        self.set_moments(sites, np.outer(mags, mg.unit(self.spin_axis)), label)

    def flip(self, sites) -> None:
        sites = np.asarray(sorted(set(sites)), dtype=int)
        self.set_moments(sites, -self.moments[sites], "Flip moments")

    def zero(self, sites) -> None:
        sites = np.asarray(sorted(set(sites)), dtype=int)
        self.set_moments(sites, np.zeros((len(sites), 3)), "Zero moments")

    def set_magnitude(self, sites, magnitude: float) -> None:
        """Keep each direction (spin axis for zero moments), set |m|."""
        sites = np.asarray(sorted(set(sites)), dtype=int)
        cur = self.moments[sites]
        norm = np.linalg.norm(cur, axis=1, keepdims=True)
        dirs = np.where(norm > 0, cur / np.where(norm > 0, norm, 1), mg.unit(self.spin_axis))
        self.set_moments(sites, dirs * float(magnitude), f"Set |m| = {magnitude:g}")

    def set_direction(self, sites, direction) -> None:
        """Keep each |m| (preferred magnitude for zero moments), set the direction."""
        sites = np.asarray(sorted(set(sites)), dtype=int)
        mags = self.magnitude_for(sites)
        self.set_moments(sites, np.outer(mags, mg.unit(direction)), "Set direction")

    def set_spin_axis(self, axis) -> None:
        """Change the collinear axis; collinear moments keep their signed magnitudes."""
        new = mg.unit(axis)
        moments = self.moments
        if self.collinear:
            moments = np.outer(mg.signed_magnitudes(self.moments, self.spin_axis), new)
        self._commit("Change spin axis", spin_axis=new, moments=moments)

    def set_collinear(self, collinear: bool, project: bool = False) -> None:
        """Switch the moment model. Going collinear with non-collinear moments raises
        NonCollinearError unless ``project`` (moments are then projected onto the axis)."""
        if collinear == self.collinear:
            return
        if not collinear:
            self._commit("Non-collinear model", collinear=False)
            return
        if not mg.is_collinear(self.moments, self.spin_axis):
            if not project:
                raise NonCollinearError("moments are not collinear with the spin axis")
            self._commit("Collinear model (projected)", collinear=True,
                         moments=mg.project_collinear(self.moments, self.spin_axis))
            return
        self._commit("Collinear model", collinear=True)
