"""Minimal magnetic cell, handoff document, and the mock AgentDFT bridge."""

import copy
import json

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure

from magview.bridge.agentdft import HandoffRejected, MockAgentDFTBridge
from magview.core.batch import pattern_options
from magview.core.document import MagDocument
from magview.core.layers import decompose
from magview.core.magcell import (
    POS_TOL_A,
    PeriodicIndex,
    as_structure,
    hnf_basis,
    minimal_magnetic_cell,
    same_lattice,
)
from magview.core.patterns import match_layer
from magview.core.stacking import fm_reference, stacking_library
from magview.core.structure import CrystalStructure
from magview.core.suggest import ElementRuleSuggester
from magview.io.handoff import (HandoffError, build_handoff, expand_magmom, magmom_string,
                                save_handoff, validate_handoff)


# --- helpers ---------------------------------------------------------------------

def doc_for(load, name, matrix=(2, 2, 2)):
    c = load(name) if isinstance(name, str) else name
    d = MagDocument.new(c, matrix)
    d.accept_suggestions(list(ElementRuleSuggester().suggest(c).candidates))
    return d


def mag_sites(d):
    return np.nonzero(d.canvas_magnetic)[0]


def apply_opt(d, key, sites=None):
    sites = mag_sites(d) if sites is None else sites
    opts = {o.key: o for o in pattern_options(d.canvas.structure, sites, d.crystal.lattice_matrix)}
    assert opts[key].available, opts[key].reason
    d.apply_signs(sites, opts[key].signs, key)
    return d


def layer_pattern(d, kind):
    L = decompose(d.canvas.structure, mag_sites(d), d.crystal.lattice_matrix).layers[0]
    k = int(np.nonzero(L.lattice.bond_class == 0)[0][0])
    i, j = int(L.graph.i[k]), int(L.graph.j[k])
    res = match_layer(L, {i: 1, j: 1 if kind in ("fm", "zigzag") else -1})
    cand = next(c for c in res.candidates if c.pattern.name.lower().startswith(kind))
    d.apply_completion(L.sites, cand.signs, kind)
    return d


def afm_stack(d):
    dec = decompose(d.canvas.structure, mag_sites(d), d.crystal.lattice_matrix)
    group = list(dec.layers)
    ref = fm_reference(d.canvas.structure, group, dec.normal, dec.period, d.spin_sign(group[0].sites))
    assert ref.ok, ref.reason
    pat = next(p for p, inv in stacking_library(len(group)) if p.key == "afm")
    d.apply_completion(np.concatenate([G.sites for G in group]),
                       np.concatenate([f * r for f, r in zip(pat.flips, ref.signs)]), "afm")
    return d


def nio_afm2(load):
    d = doc_for(load, "NiO")
    s = mag_sites(d)
    plane = np.rint((d.canvas.structure.frac_coords[s] * 2).sum(axis=1)).astype(int)
    d.apply_signs(s, np.where(plane % 2 == 0, 1, -1), "AFM-II")
    return d


def cubic():
    return CrystalStructure.from_pymatgen(Structure(Lattice.cubic(4.0), ["Mn"], [[0, 0, 0]]))


def triangular_120(load):
    d = doc_for(load, "toy_triangular_Co", (3, 3, 1))
    d.set_collinear(False)
    s = mag_sites(d)
    t = d.canvas.translation[s]
    ph = np.radians(((t[:, 0] - t[:, 1]) % 3) * 120.0)
    d.set_moments(s, np.column_stack([np.cos(ph), np.sin(ph), np.zeros(len(s))]) * 2.0, "120")
    return d


SQRT2 = [[1, 1, 0], [-1, 1, 0], [0, 0, 1]]
CASES = {
    # name: (builder, multiplicity, expected S relative to the input cell or None)
    "Fe bcc FM (primitive cell)": (lambda load: doc_for(load, "Fe_bcc"), 1, None),
    "square Neel -> sqrt2 x sqrt2": (lambda load: layer_pattern(doc_for(load, "toy_square_CuO2", (2, 2, 1)), "neel"), 2, SQRT2),
    "square stripe -> 1x2": (lambda load: apply_opt(doc_for(load, "toy_square_CuO2", (2, 2, 1)), "stripe:[100]"), 2, np.diag([1, 2, 1])),
    "square stripe -> 2x1": (lambda load: apply_opt(doc_for(load, "toy_square_CuO2", (2, 2, 1)), "stripe:[010]"), 2, np.diag([2, 1, 1])),
    "NiO AFM-II (rhombohedral)": (nio_afm2, 2, None),
    "NiO AFM-I": (lambda load: apply_opt(doc_for(load, "NiO"), "A:c"), 2, None),
    "La2CuO4 A-type (conventional cell)": (lambda load: afm_stack(doc_for(load, "La2CuO4")), 2, np.eye(3, dtype=int)),
    "Cs3V9Te13 FM -> 1x1x1": (lambda load: doc_for(load, "Cs3V9Te13_300K"), 1, np.eye(3, dtype=int)),
    "Cs3V9Te13 AFM stacking -> 1x1x2": (lambda load: afm_stack(doc_for(load, "Cs3V9Te13_300K")), 2, np.diag([1, 1, 2])),
    "honeycomb zigzag": (lambda load: layer_pattern(doc_for(load, "toy_honeycomb_Cr", (2, 2, 1)), "zigzag"), 2, None),
    "simple cubic G-type": (lambda load: apply_opt(doc_for(load, cubic()), "G"), 2, None),
    "triangular 120 deg (non-collinear)": (triangular_120, 3, None),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_minimal_cell(load, case):
    build, mult, S_expected = CASES[case]
    d = build(load)
    cell = minimal_magnetic_cell(d)
    assert cell.multiplicity == mult
    assert cell.requires_supercell == (mult > 1)
    if S_expected is not None:
        assert cell.supercell_matrix_input is not None
        assert same_lattice(cell.supercell_matrix_input, np.asarray(S_expected))
    if case.startswith(("Fe bcc", "NiO")):
        assert cell.supercell_matrix_input is None  # primitive-lattice cells, not input supercells
    # geometry: magnetic lattice = S @ reference, site count = mult x reference sites
    assert np.allclose(cell.lattice, cell.supercell_matrix @ cell.reference_lattice, atol=1e-6)
    n_ref = len(d.crystal) / round(abs(np.linalg.det(d.crystal.lattice_matrix)
                                       / np.linalg.det(cell.reference_lattice)))
    assert len(cell.species) == round(n_ref * mult)
    # every canvas moment equals the magnetic-cell moment at the same (folded) position
    C = d.canvas.structure.lattice_matrix
    folded = (d.canvas.structure.frac_coords @ C) @ np.linalg.inv(cell.lattice)
    for i in range(0, len(folded), 3):
        dd = folded[i] - cell.frac_coords
        dd -= np.rint(dd)
        j = int(np.argmin(np.linalg.norm(dd @ cell.lattice, axis=1)))
        assert np.allclose(cell.moments[j], d.moments[i], atol=1e-6)
    # independent check: spglib's magnetic primitive lattice has the same volume
    assert cell.msg and "primitive_volume" in cell.msg
    assert cell.msg["primitive_volume"] == pytest.approx(abs(np.linalg.det(cell.lattice)), rel=1e-6)


def test_explanations(load):
    cell = minimal_magnetic_cell(afm_stack(doc_for(load, "Cs3V9Te13_300K")))
    assert [b.name for b in cell.broken] == ["c"]
    assert "V1 (up) -> V1 (down)" in cell.broken[0].example
    cell = minimal_magnetic_cell(afm_stack(doc_for(load, "La2CuO4")))
    assert any("centring" in b.name for b in cell.broken)
    assert any("centring translations are not magnetic" in e for e in cell.explanation)
    cell = minimal_magnetic_cell(doc_for(load, "Fe_bcc"))
    assert "primitive cell" in cell.explanation[0]
    cell = minimal_magnetic_cell(doc_for(load, "Cs3V9Te13_300K"))
    assert "chemical cell suffices" in cell.explanation[0]


def test_nio_afm2_breaks_every_fcc_centring(load):
    """fcc has three face centrings; AFM-II breaks all of them (regression: the zero
    primitive translation does not sort first, so slicing it away dropped (0,1/2,1/2))."""
    cell = minimal_magnetic_cell(nio_afm2(load))
    names = [b.name for b in cell.broken]
    for f in ("(0, 1/2, 1/2)", "(1/2, 0, 1/2)", "(1/2, 1/2, 0)"):
        assert f"{f} centring" in names
    for f in ("(0, 1/2, 1/2)", "(1/2, 0, 1/2)", "(1/2, 1/2, 0)"):
        assert any(f in e for e in cell.explanation)


def _bruteforce_match(query, ref, lattice):
    d = query[:, None, :] - ref[None, :, :]
    d -= np.rint(d)
    dist = np.linalg.norm(d @ lattice, axis=-1)
    j = np.argmin(dist, axis=1)
    return np.where(dist[np.arange(len(query)), j] <= POS_TOL_A, j, -1)


def test_periodic_index_matches_bruteforce():
    """The KD-tree matcher agrees with the exhaustive periodic search, including a skewed
    triclinic cell, coordinates sitting on 1e-3 rounding half-points (the Cs3V9Te13 case),
    points across the periodic boundary and near-misses just outside the tolerance."""
    rng = np.random.default_rng(7)
    lattice = Lattice.from_parameters(7.1, 9.3, 12.4, 61.0, 78.0, 112.0).matrix
    ref = rng.random((400, 3))
    ref[:20] = np.round(ref[:20], 4) + 5e-4  # 0.xxx5: exactly on rounding half-points
    ref[20:30, 0] = 1e-12  # on the boundary
    inv = np.linalg.inv(lattice)
    jitter = rng.normal(size=(400, 3))
    jitter /= np.linalg.norm(jitter, axis=1, keepdims=True)
    # half the queries are matches (0.3 x tolerance away), half near-misses (2 x tolerance)
    scale = np.where(np.arange(400) % 2 == 0, 0.3, 2.0)[:, None] * POS_TOL_A
    query = ref + (jitter * scale) @ inv + rng.integers(-2, 3, size=(400, 3))  # + lattice shifts
    query[20:30, 0] -= 2e-12  # just below 0 -> wraps to ~1
    got = PeriodicIndex(ref, lattice).match(query)
    assert np.array_equal(got, _bruteforce_match(query, ref, lattice))
    assert (got[::2] == np.arange(0, 400, 2)).all() and (got[1::2] == -1).all()


def test_periodic_index_first_occurrences():
    lattice = np.diag([4.0, 5.0, 6.0])
    pts = np.array([[0.1, 0.2, 0.3], [0.5, 0.5, 0.5], [1.1, 0.2, 0.3 + 1e-4],
                    [0.9999999, 0.0, 0.0], [0.0, 1.0, 1.0], [0.5, 0.5, 0.5 + 0.01]])
    assert PeriodicIndex(pts, lattice).first_occurrences().tolist() == [0, 1, 3, 5]


def test_minimal_cell_large_canvas_is_fast(load):
    """5400-site canvas (Cs3V9Te13, 6x6x6) with AFM stacking: the dense N x N matcher
    took ~4 minutes and 2.6 GB here; the periodic KD-tree takes about a second."""
    import time

    d = afm_stack(doc_for(load, "Cs3V9Te13_300K", (6, 6, 6)))
    t0 = time.perf_counter()
    cell = minimal_magnetic_cell(d)
    assert time.perf_counter() - t0 < 30
    assert cell.multiplicity == 2 and len(cell.species) == 50
    assert same_lattice(cell.supercell_matrix_input, np.diag([1, 1, 2]))


def test_hnf_and_lattice_equality():
    B = hnf_basis(np.array([[2, 0, 0], [0, 2, 0], [0, 0, 1], [1, 1, 0]]))
    assert round(abs(np.linalg.det(B))) == 2
    assert same_lattice(B, [[1, 1, 0], [1, -1, 0], [0, 0, 1]])
    assert not same_lattice(B, np.diag([2, 1, 1]))


def test_as_structure(load):
    cell = minimal_magnetic_cell(layer_pattern(doc_for(load, "toy_square_CuO2", (2, 2, 1)), "neel"))
    s = as_structure(cell)
    assert len(s) == 6 and s.composition.reduced_formula == "CuO2"


# --- handoff ------------------------------------------------------------------------

@pytest.fixture
def handoff(load):
    d = afm_stack(layer_pattern(doc_for(load, "toy_square_CuO2"), "neel"))
    return build_handoff(d)


def test_handoff_valid_and_labels(handoff):
    assert validate_handoff(handoff) == []
    assert validate_handoff(json.loads(json.dumps(handoff))) == []
    labels = " | ".join(handoff["patterns"]["labels"])
    assert "Neel" in labels and "AFM stacking" in labels
    assert handoff["requires_unfolding"] and handoff["unfolding"]["supercell_matrix"]
    assert handoff["multiplicity"] == 2
    vasp = handoff["dft_hints"]["vasp"]
    assert vasp["LNONCOLLINEAR"] is False and len(expand_magmom(vasp["MAGMOM"])) == 6
    assert vasp["species_order"] == ["Cu", "O"] and vasp["species_counts"] == [2, 4]


@pytest.mark.parametrize("tamper, needle", [
    (lambda h: h.__setitem__("multiplicity", 3), "multiplicity"),
    (lambda h: h["magnetic_cell"]["lattice"][0].__setitem__(0, 99.0), "supercell_matrix @ reference"),
    (lambda h: h.__setitem__("supercell_matrix_input", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
     "supercell_matrix_input"),
    (lambda h: h["unfolding"]["supercell_matrix"][0].__setitem__(0, 5), "unfolding.supercell_matrix"),
    (lambda h: h["reference_cell"]["parent_input_site"].__setitem__(0, 99),
     "reference_cell.parent_input_site"),
    (lambda h: h.__setitem__("requires_unfolding", False), "requires_unfolding"),
    (lambda h: h["dft_hints"]["vasp"].__setitem__("MAGMOM", "1 2"), "MAGMOM"),
    (lambda h: h["moments"]["vectors"][0].__setitem__(0, 0.7), "collinear"),
    (lambda h: h.pop("moments"), "schema"),
    (lambda h: h.__setitem__("schema_version", "9"), "schema"),
])
def test_handoff_tampering_detected(handoff, tamper, needle):
    h = copy.deepcopy(handoff)
    tamper(h)
    errors = validate_handoff(h)
    assert errors and any(needle in e for e in errors), errors


def test_nio_handoff_valid_with_null_input_matrix(load):
    """NiO AFM-II: no integral matrix vs the input cell (supercell_matrix_input is null);
    the handoff must still validate, and the explanation names all three fcc centrings."""
    h = build_handoff(nio_afm2(load))
    assert h["supercell_matrix_input"] is None
    assert validate_handoff(h) == []
    text = " | ".join(h["explanation"])
    for f in ("(0, 1/2, 1/2)", "(1/2, 0, 1/2)", "(1/2, 1/2, 0)"):
        assert f in text


def test_noncollinear_handoff(load):
    h = build_handoff(triangular_120(load))
    assert validate_handoff(h) == []
    vasp = h["dft_hints"]["vasp"]
    assert vasp["LNONCOLLINEAR"] and vasp["SAXIS"] == [0.0, 0.0, 1.0]
    assert len(expand_magmom(vasp["MAGMOM"])) == 3 * len(h["magnetic_cell"]["species"])
    assert h["moments"]["signed_magnitudes"] is None
    assert any("non-collinear" in s for s in h["patterns"]["labels"])


def test_magmom_compression_round_trip():
    vals = [2.0, 2.0, -2.0, 0.0, 0.0, 0.0, 1.5]
    s = magmom_string(vals)
    assert s == "2*2 -2 3*0 1.5" and expand_magmom(s) == vals


def test_vasp_consumer_smoke(handoff, tmp_path):
    """A DFT backend can write POSCAR + MAGMOM straight from the handoff."""
    from pymatgen.io.vasp import Poscar

    mc = handoff["magnetic_cell"]
    s = Structure(Lattice(mc["lattice"]), mc["species"], mc["frac_coords"])
    Poscar(s).write_file(tmp_path / "POSCAR")
    back = Poscar.from_file(tmp_path / "POSCAR").structure
    assert [str(x) for x in back.species] == mc["species"]  # grouped, so MAGMOM order holds
    assert len(expand_magmom(handoff["dft_hints"]["vasp"]["MAGMOM"])) == len(back)


# --- bridge ---------------------------------------------------------------------

def test_mock_bridge(handoff, tmp_path):
    bridge = MockAgentDFTBridge(tmp_path / "jobs")
    ticket = bridge.submit(handoff)
    assert ticket.status == "started (simulated)"
    job = tmp_path / "jobs" / ticket.job_id
    assert json.loads((job / "handoff.json").read_text())["multiplicity"] == 2
    assert bridge.status(ticket.job_id).status == "started (simulated)"
    assert "unfold" in ticket.history[-1]["detail"]
    bad = copy.deepcopy(handoff)
    bad["multiplicity"] = 7
    with pytest.raises(HandoffRejected):
        bridge.submit(bad)
    assert len(list((tmp_path / "jobs").iterdir())) == 1  # nothing stored for the rejected one
    with pytest.raises(KeyError):
        bridge.status("nope")


def test_bridge_env_root(handoff, tmp_path, monkeypatch):
    monkeypatch.setenv("MAGVIEW_AGENTDFT_DIR", str(tmp_path / "envjobs"))
    t = MockAgentDFTBridge().submit(handoff)
    assert (tmp_path / "envjobs" / t.job_id / "job.json").is_file()


def test_handoff_rejects_non_finite_numbers(load, tmp_path):
    """NaN in coordinates or the input lattice passed validation (every tolerance
    comparison is False for NaN); elsewhere it was caught only by accident."""
    import copy

    doc = MagDocument.new(load("NiO"))
    doc.accept_suggestions([s for s in ElementRuleSuggester().suggest(doc.crystal).items
                            if s.candidate], {})
    h = build_handoff(doc, minimal_magnetic_cell(doc))
    assert validate_handoff(h) == []
    for keys in (("magnetic_cell", "frac_coords", 0, 1), ("input_cell", "lattice", 2, 2),
                 ("reference_cell", "frac_coords", 0, 0), ("moments", "vectors", 0, 2)):
        for bad in (float("nan"), float("inf")):
            x = copy.deepcopy(h)
            node = x
            for k in keys[:-1]:
                node = node[k]
            node[keys[-1]] = bad
            errors = validate_handoff(x)
            assert errors == [f"non-finite number (NaN or infinity) at {'/'.join(map(str, keys))}"]
            with pytest.raises(HandoffError):
                save_handoff(x, tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()
