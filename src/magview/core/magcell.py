"""Minimal magnetic supercell of the configuration on the editing canvas.

The magnetic lattice is the set of pure crystal translations that leave every
moment unchanged. It is found exactly:

1. Work in the basis of the chemical primitive lattice P (spglib, input
   orientation), so centring translations of a conventional input cell count.
2. The canvas lattice is K = W * N_A in primitive units (W: canvas matrix,
   N_A: input cell in primitive units). Every primitive translation t modulo K
   is tested: does it map every canvas site onto a site of the same species
   with the same moment?
3. The invariant translations plus K generate the magnetic lattice; a basis is
   obtained by Hermite normal form, then reduced (LLL) and expressed relative to
   the input cell when that is integral (preferring a diagonal matrix).

The result says whether the chemical cell suffices and why not (which input
translations map an up site onto a down site), and spglib's magnetic symmetry
dataset is used as an independent check of the lattice volume.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import spglib
from pymatgen.core import Lattice, Structure

from magview.core.canvas import lattice_points_in_cell
from magview.core.document import MagDocument
from magview.core.structure import CrystalStructure

MOMENT_TOL = 1e-4  # mu_B
POS_TOL_A = 0.01  # Angstrom: two positions closer than this are the same site


@dataclass(frozen=True)
class BrokenTranslation:
    name: str  # e.g. "a", "(1/2,1/2,1/2) centring"
    vector_input: np.ndarray  # in input-cell fractional units
    example: str  # "Cu1 (up) -> Cu1 (down)"


@dataclass(frozen=True)
class MagneticCell:
    reference_lattice: np.ndarray  # (3,3) chemical primitive lattice (rows, Angstrom)
    supercell_matrix: np.ndarray  # (3,3) int: magnetic = S @ reference
    supercell_matrix_input: np.ndarray | None  # (3,3) int: magnetic = S_in @ input, if integral
    lattice: np.ndarray  # (3,3) magnetic cell
    species: list[str]
    frac_coords: np.ndarray  # (n,3) in the magnetic cell
    moments: np.ndarray  # (n,3) Cartesian mu_B
    parent: np.ndarray  # (n,) input-cell site index
    labels: list[str]
    multiplicity: int  # magnetic cell volume / reference (primitive) cell volume
    input_multiplicity: float  # magnetic cell volume / input cell volume
    broken: tuple[BrokenTranslation, ...]  # input-cell translations that are not magnetic
    explanation: tuple[str, ...]
    msg: dict | None  # spglib magnetic symmetry summary

    @property
    def requires_supercell(self) -> bool:
        """True if the magnetic cell is larger than the chemical primitive cell."""
        return self.multiplicity > 1

    def describe_matrix(self) -> str:
        S = self.supercell_matrix_input
        if S is None:
            return "not a supercell of the input cell"
        if np.all(S == np.diag(np.diag(S))):
            return "x".join(str(int(v)) for v in np.diag(S))
        return str(S.tolist())


# --- integer lattice helpers ------------------------------------------------------

def hnf_basis(generators: np.ndarray) -> np.ndarray:
    """Row-style Hermite normal form basis (3x3) of the lattice spanned by integer rows."""
    m = [list(map(int, r)) for r in np.asarray(generators, dtype=np.int64)]
    rows = len(m)
    r = 0
    for col in range(3):
        # Euclid on column `col` among rows r..end
        while True:
            nz = [i for i in range(r, rows) if m[i][col] != 0]
            if not nz:
                break
            piv = min(nz, key=lambda i: abs(m[i][col]))
            m[r], m[piv] = m[piv], m[r]
            done = True
            for i in range(r + 1, rows):
                if m[i][col]:
                    q = m[i][col] // m[r][col]
                    m[i] = [a - q * b for a, b in zip(m[i], m[r])]
                    if m[i][col]:
                        done = False
            if done:
                break
        if r < rows and m[r][col] != 0:
            if m[r][col] < 0:
                m[r] = [-a for a in m[r]]
            for i in range(r):  # reduce entries above the pivot
                q = m[i][col] // m[r][col]
                m[i] = [a - q * b for a, b in zip(m[i], m[r])]
            r += 1
    basis = np.array(m[:3], dtype=np.int64)
    if r < 3 or round(abs(np.linalg.det(basis))) == 0:
        raise ValueError("generators do not span a full-rank lattice")
    return basis


def same_lattice(b1: np.ndarray, b2: np.ndarray) -> bool:
    """Two integer bases generate the same lattice iff b1 @ inv(b2) is unimodular."""
    t = np.asarray(b1, float) @ np.linalg.inv(np.asarray(b2, float))
    return bool(np.allclose(t, np.rint(t), atol=1e-8) and round(abs(np.linalg.det(t))) == 1)


def _nice_input_matrix(mag_lattice: np.ndarray, input_lattice: np.ndarray) -> np.ndarray | None:
    """Integer S with S @ input == a basis of the magnetic lattice, if one exists.

    Preference: diagonal matrix; otherwise an LLL-reduced basis ordered along a, b, c.
    """
    S0 = mag_lattice @ np.linalg.inv(input_lattice)
    if not np.allclose(S0, np.rint(S0), atol=1e-6):
        return None
    S0 = np.rint(S0).astype(np.int64)
    det = int(round(abs(np.linalg.det(S0))))
    for diag in itertools.product(range(1, det + 1), repeat=3):
        if np.prod(diag) == det and same_lattice(np.diag(diag), S0):
            return np.diag(diag).astype(np.int64)
    red = Lattice(mag_lattice).get_lll_reduced_lattice().matrix
    S = red @ np.linalg.inv(input_lattice)
    if not np.allclose(S, np.rint(S), atol=1e-6):
        return S0
    S = np.rint(S).astype(np.int64)
    # order rows to follow a, b, c and make each point "forward"
    unit_in = input_lattice / np.linalg.norm(input_lattice, axis=1, keepdims=True)
    unit_s = (S @ input_lattice) / np.linalg.norm(S @ input_lattice, axis=1, keepdims=True)
    best, best_score = S, -1.0
    for perm in itertools.permutations(range(3)):
        P = S[list(perm)]
        U = unit_s[list(perm)]
        signs = np.sign(np.einsum("ij,ij->i", U, unit_in))
        signs[signs == 0] = 1
        P = (P.T * signs).T
        if np.linalg.det(P) < 0:
            P[2] = -P[2]
        score = float(np.abs(np.einsum("ij,ij->i", U, unit_in)).sum())
        if score > best_score:
            best, best_score = P, score
    return np.rint(best).astype(np.int64)


def _periodic_match(query: np.ndarray, ref: np.ndarray, lattice: np.ndarray) -> np.ndarray:
    """For each fractional ``query`` point, the index of the ``ref`` point at the same
    position (periodic, within POS_TOL_A), or -1. Tolerance-based on purpose: hashing
    rounded coordinates splits sites whose coordinates sit on rounding boundaries
    (CIF values such as 0.1555)."""
    d = query[:, None, :] - ref[None, :, :]
    d -= np.rint(d)
    dist = np.linalg.norm(d @ lattice, axis=-1)
    j = np.argmin(dist, axis=1)
    return np.where(dist[np.arange(len(query)), j] <= POS_TOL_A, j, -1)


def _fraction(x: float) -> str:
    from fractions import Fraction

    fr = Fraction(x).limit_denominator(12)
    return str(fr.numerator) if fr.denominator == 1 else f"{fr.numerator}/{fr.denominator}"


# --- main analysis ------------------------------------------------------------------

def minimal_magnetic_cell(doc: MagDocument) -> MagneticCell:
    crystal: CrystalStructure = doc.crystal
    canvas = doc.canvas
    A = crystal.lattice_matrix
    P = np.array(crystal.symmetry.primitive_lattice, dtype=float)
    NA = A @ np.linalg.inv(P)  # input cell in primitive units
    if not np.allclose(NA, np.rint(NA), atol=1e-5):
        raise ValueError("input cell is not a superlattice of the spglib primitive lattice")
    NA = np.rint(NA).astype(np.int64)
    if round(abs(np.linalg.det(NA))) == 1:
        # The input cell is primitive: use it (not spglib's re-ordered copy) as the reference.
        P, NA = A.copy(), np.eye(3, dtype=np.int64)
    K = canvas.matrix.astype(np.int64) @ NA  # canvas lattice in primitive units
    C = canvas.structure.lattice_matrix

    frac = canvas.structure.frac_coords
    species = np.array(canvas.structure.symbols)
    moments = doc.moments

    def maps_to(t_cart: np.ndarray) -> np.ndarray | None:
        out = _periodic_match(frac + t_cart @ np.linalg.inv(C), frac, C)
        if np.any(out < 0) or np.any(species[out] != species):
            return None
        return out

    invariant = []
    for t in lattice_points_in_cell(K):
        m = maps_to(t @ P)
        if m is None:
            raise ValueError(f"primitive translation {t} does not map the canvas onto itself")
        if np.all(np.linalg.norm(moments[m] - moments, axis=1) <= MOMENT_TOL):
            invariant.append(t)
    gens = np.vstack([K, np.array(invariant, dtype=np.int64)])
    B = hnf_basis(gens)  # magnetic lattice in primitive units (rows)
    mult = int(round(abs(np.linalg.det(B))))
    mag_lattice = Lattice(B @ P).get_lll_reduced_lattice().matrix
    S_in = _nice_input_matrix(mag_lattice, A)
    if S_in is not None:
        mag_lattice = S_in @ A
    S_ref = mag_lattice @ np.linalg.inv(P)
    S_ref = np.rint(S_ref).astype(np.int64)
    if np.linalg.det(mag_lattice) < 0:
        mag_lattice = -mag_lattice
        S_ref = -S_ref
        S_in = None if S_in is None else -S_in

    # Sites of the magnetic cell: canvas sites folded into it (duplicates are equivalent).
    inv_m = np.linalg.inv(mag_lattice)
    cart = frac @ C
    folded = cart @ inv_m
    order = []
    for i in range(len(frac)):
        if not order or _periodic_match(folded[i:i + 1], folded[order], mag_lattice)[0] < 0:
            order.append(i)
    # group by species in order of first appearance in the input cell (POSCAR convention)
    sp_order = list(dict.fromkeys(crystal.symbols))
    order.sort(key=lambda i: (sp_order.index(species[i]), i))
    order = np.array(order, dtype=int)
    mfrac = np.mod(cart[order] @ inv_m, 1.0)
    mfrac[np.isclose(mfrac, 1.0, atol=1e-8)] = 0.0
    parent = canvas.parent[order]
    expected_n = len(crystal) * mult / int(round(abs(np.linalg.det(NA))))
    if len(order) != round(expected_n):
        raise RuntimeError(f"magnetic cell has {len(order)} sites, expected {expected_n:g}")

    broken = _broken_input_translations(doc, P, NA, maps_to)
    explanation = _explain(crystal, mult, S_in, int(round(abs(np.linalg.det(NA)))), broken)
    cell = MagneticCell(P, S_ref, S_in, mag_lattice, [str(s) for s in species[order]], mfrac,
                        moments[order].copy(), parent, [crystal.labels[p] for p in parent], mult,
                        abs(np.linalg.det(mag_lattice) / np.linalg.det(A)), tuple(broken),
                        tuple(explanation), None)
    return _with_msg(cell, doc)


def _broken_input_translations(doc, P, NA, maps_to) -> list[BrokenTranslation]:
    """Input-cell translations (a, b, c and centrings) that change the moments."""
    crystal = doc.crystal
    A = crystal.lattice_matrix
    cands = [("a", np.array([1.0, 0, 0])), ("b", np.array([0, 1.0, 0])), ("c", np.array([0, 0, 1.0]))]
    # centring translations: primitive lattice points inside the input cell (excluding 0;
    # the zero vector does not sort first when interior points have negative coordinates)
    for t in lattice_points_in_cell(NA):
        if not t.any():
            continue
        f = (t @ P) @ np.linalg.inv(A)
        f = np.mod(f, 1.0)
        cands.append(("(" + ", ".join(_fraction(x) for x in f) + ") centring", f))
    out = []
    axis = doc.spin_axis / np.linalg.norm(doc.spin_axis)
    for name, f in cands:
        m = maps_to(f @ A)
        if m is None:
            continue
        diff = np.linalg.norm(doc.moments[m] - doc.moments, axis=1) > MOMENT_TOL
        if diff.any():
            i = int(np.nonzero(diff)[0][0])
            j = int(m[i])

            def tag(k):
                v = doc.moments[k]
                s = v @ axis
                d = "up" if s > 1e-6 else "down" if s < -1e-6 else f"{np.round(v, 2).tolist()}"
                return f"{crystal.labels[doc.canvas.parent[k]]} ({d})"

            out.append(BrokenTranslation(name, f, f"{tag(i)} -> {tag(j)}"))
    return out


def _explain(crystal, mult: int, S_in, n_prim_in_input: int, broken) -> list[str]:
    lines = []
    if mult == 1:
        if n_prim_in_input > 1:
            lines.append(f"The magnetic cell is the chemical primitive cell (1/{n_prim_in_input} of the "
                         "input cell); no supercell is needed.")
        else:
            lines.append("The chemical cell suffices: every lattice translation leaves all moments "
                         "unchanged.")
        return lines
    lines.append(f"A magnetic supercell is required: {mult} chemical primitive cells.")
    if S_in is not None:
        if round(abs(np.linalg.det(S_in))) == 1:
            lines.append("The input (conventional) cell already contains it, but its centring "
                         "translations are not magnetic translations.")
        else:
            lines.append(f"Relative to the input cell: {S_in.tolist()}.")
    else:
        lines.append("It is not a supercell of the input cell (it fits the primitive lattice only).")
    for b in broken:
        lines.append(f"Translation {b.name} is not a magnetic translation: it maps {b.example}.")
    return lines


def _with_msg(cell: MagneticCell, doc: MagDocument) -> MagneticCell:
    """Attach spglib's magnetic symmetry (independent check of the translation lattice)."""
    from pymatgen.core import Element

    nums = [Element(s).Z for s in cell.species]
    if doc.collinear:
        mags = cell.moments @ (doc.spin_axis / np.linalg.norm(doc.spin_axis))
    else:
        mags = cell.moments
    try:
        ds = spglib.get_magnetic_symmetry_dataset((cell.lattice, cell.frac_coords, nums, mags),
                                                  symprec=1e-3, mag_symprec=1e-3)
    except Exception as exc:  # spglib raises on failure in the new error mode
        msg = {"error": str(exc)}
    else:
        msg = None if ds is None else {
            "uni_number": int(ds.uni_number),
            "msg_type": int(ds.msg_type),
            "n_operations": int(ds.n_operations),
            "primitive_volume": float(abs(np.linalg.det(ds.primitive_lattice))),
        }
    return MagneticCell(**{**cell.__dict__, "msg": msg})


def as_structure(cell: MagneticCell) -> Structure:
    return Structure(Lattice(cell.lattice), cell.species, cell.frac_coords, labels=cell.labels)
