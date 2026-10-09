"""Magnetic-site suggestion (Phase 1 MVP: deterministic element rules).

``SiteSuggester`` is the interface that a future agent-backed suggester (e.g. an
LLM reading the literature) will implement; ``ElementRuleSuggester`` is the
rule-based MVP:

* candidates: 3d transition metals Ti-Cu, lanthanides Ce-Yb (high/medium
  confidence); 4d/5d metals and actinides (low confidence, unchecked by default);
* every symmetry orbit (spglib equivalent atoms) is one suggestion row;
* the starting moment is the spin-only value 2S (in mu_B) from the electron
  count implied by pymatgen's best charge-balanced oxidation-state guess:
  high spin for 3d, low spin for 4d/5d, Hund's rule for 4f/5f;
* rows that end up with no unpaired electrons (d0, d10, f0, f14) are reported as
  excluded with the reason, never dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pymatgen.core import Composition, Element

from magview.core.structure import CrystalStructure

D3 = ("Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu")
D3_NONMAGNETIC = ("Sc", "Zn")
D45 = ("Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt")
F4 = ("Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb")
F4_NONMAGNETIC = ("La", "Lu")
F5 = ("U", "Np", "Pu", "Am")

# Starting moments (mu_B) when no oxidation state is usable (metals, failed guess).
ELEMENT_DEFAULT = {"Ti": 1.0, "V": 2.0, "Cr": 3.0, "Mn": 4.0, "Fe": 3.0, "Co": 2.0,
                   "Ni": 1.0, "Cu": 1.0}


@dataclass(frozen=True)
class SiteSuggestion:
    orbit: int  # representative input-site index
    sites: tuple[int, ...]  # all input-site indices of the orbit
    label: str  # e.g. "V1 (3g)"
    element: str
    magnitude: float  # suggested starting |m| in mu_B (0 for excluded rows)
    confidence: str  # "high" | "medium" | "low"
    rationale: str
    candidate: bool  # False: excluded, shown with the reason
    default_on: bool  # pre-checked in the panel


@dataclass(frozen=True)
class SuggestionReport:
    items: tuple[SiteSuggestion, ...]
    oxidation_states: dict[str, float] | None
    method: str

    @property
    def candidates(self) -> tuple[SiteSuggestion, ...]:
        return tuple(s for s in self.items if s.candidate)

    @property
    def excluded(self) -> tuple[SiteSuggestion, ...]:
        return tuple(s for s in self.items if not s.candidate)

    def as_markdown(self) -> str:
        rows = ["| Orbit | Sites | Candidate | Pre-checked | Start mu_B | Confidence | Rationale |",
                "|---|---|---|---|---|---|---|"]
        for s in self.items:
            rows.append(f"| {s.label} | {len(s.sites)} | {'yes' if s.candidate else 'no'} | "
                        f"{'yes' if s.default_on else 'no'} | {s.magnitude:.1f} | {s.confidence} | "
                        f"{s.rationale} |")
        return "\n".join(rows)


class SiteSuggester(Protocol):
    name: str

    def suggest(self, crystal: CrystalStructure) -> SuggestionReport: ...


def _unpaired_d(n: float, high_spin: bool) -> float:
    n = min(max(n, 0.0), 10.0)
    if high_spin:
        return n if n <= 5 else 10 - n
    # Low spin, octahedral-like: t2g (6) fills before eg (4).
    if n <= 3:
        return n
    if n <= 6:
        return 6 - n
    if n <= 8:
        return n - 6
    return 10 - n


def _unpaired_f(n: float) -> float:
    n = min(max(n, 0.0), 14.0)
    return n if n <= 7 else 14 - n


def _fmt_ox(ox: float) -> str:
    return f"{ox:+.0f}" if abs(ox - round(ox)) < 1e-6 else f"{ox:+.2f}"


class ElementRuleSuggester:
    name = "element-rules-v1"

    def suggest(self, crystal: CrystalStructure) -> SuggestionReport:
        oxi = self._oxidation_states(crystal)
        items = []
        eq = crystal.symmetry.equivalent_atoms
        for rep in sorted(set(eq)):
            sites = tuple(i for i, e in enumerate(eq) if e == rep)
            el = crystal.symbols[rep]
            label = f"{crystal.labels[rep]} ({len(sites)}{crystal.symmetry.wyckoffs[rep]})"
            row = self._rule(el, oxi)
            if row is None:
                continue  # not a magnetic element at all (O, Te, Cs, ...): not listed
            magnitude, confidence, rationale, candidate, default_on = row
            items.append(SiteSuggestion(rep, sites, label, el, magnitude, confidence, rationale,
                                        candidate, default_on))
        return SuggestionReport(tuple(items), oxi, self.name)

    @staticmethod
    def _oxidation_states(crystal: CrystalStructure) -> dict[str, float] | None:
        try:
            guesses = Composition(crystal.structure.composition.formula).oxi_state_guesses(max_sites=-1)
        except Exception:
            return None
        return dict(guesses[0]) if guesses else None

    def _rule(self, el: str, oxi: dict[str, float] | None):
        """Return (magnitude, confidence, rationale, candidate, default_on) or None."""
        if el in D3_NONMAGNETIC:
            return (0.0, "high", f"{el} is normally d0/d10 (non-magnetic)", False, False)
        if el in F4_NONMAGNETIC:
            return (0.0, "high", f"{el}3+ is f0/f14 (non-magnetic)", False, False)
        if el not in D3 + D45 + F4 + F5:
            return None
        ox = None if oxi is None else oxi.get(el)
        if el in D3:
            return self._d_shell(el, ox, high_spin=True)
        if el in D45:
            row = self._d_shell(el, ox, high_spin=False)
            mag, _conf, why, cand, _on = row
            return (mag, "low", why + "; 4d/5d moments are often quenched", cand, False)
        if el in F4:
            if ox is None or ox == 0:
                ox = 3.0
                src = "Ln3+ assumed"
            else:
                src = f"oxidation {_fmt_ox(ox)}"
            nf = Element(el).Z - 54 - ox
            unp = _unpaired_f(nf)
            if unp < 0.05:
                return (0.0, "high", f"{el}: {src} gives 4f{nf:.0f} (no unpaired f)", False, False)
            return (round(unp, 1), "high",
                    f"4f rare earth, {src}: 4f{nf:g}, spin-only 2S = {unp:.1f} mu_B "
                    "(orbital moment not included)", True, True)
        # 5f actinides
        if ox is None or ox == 0:
            return (2.0, "low", "5f actinide, oxidation state unknown: default 2 mu_B", True, False)
        nf = Element(el).Z - 86 - ox
        unp = _unpaired_f(nf)
        return (round(unp, 1), "low", f"5f actinide, oxidation {_fmt_ox(ox)}: 5f{nf:g}, "
                f"2S = {unp:.1f} mu_B; 5f moments are often itinerant/reduced", unp > 0.05, False)

    @staticmethod
    def _d_shell(el: str, ox: float | None, high_spin: bool):
        group = Element(el).group  # valence electrons (s + d) of the neutral atom
        spin = "high spin" if high_spin else "low spin"
        if ox is None:
            mag = ELEMENT_DEFAULT.get(el, 1.0)
            return (mag, "low", f"{el}: no charge-balanced oxidation state found; "
                    f"default {mag:.0f} mu_B", True, True)
        if abs(ox) < 1e-9:
            mag = ELEMENT_DEFAULT.get(el, 1.0)
            return (mag, "medium", f"{el} in a metal/intermetallic (oxidation 0): itinerant "
                    f"moment, default {mag:.0f} mu_B", True, True)
        nd = group - ox
        unp = _unpaired_d(nd, high_spin)
        mixed = abs(ox - round(ox)) > 1e-6
        base = f"{el}{_fmt_ox(ox)}: d{nd:.2f}".rstrip("0").rstrip(".") if mixed else \
            f"{el}{_fmt_ox(ox)}: d{nd:.0f}"
        if unp < 0.05:
            return (0.0, "high", f"{base} has no unpaired d electrons", False, False)
        if mixed:
            return (round(unp, 1), "low",
                    f"{base} (mixed valence, average from charge balance), {spin} "
                    f"2S = {unp:.1f} mu_B; magnitude uncertain", True, True)
        ambiguous = high_spin and 4 <= nd <= 7
        conf = "medium" if ambiguous else "high"
        extra = "; spin state ambiguous (high spin assumed)" if ambiguous else ""
        return (round(unp, 1), conf, f"{base}, {spin} 2S = {unp:.0f} mu_B{extra}", True, True)
