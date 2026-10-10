"""Structure loading and symmetry."""

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure

from magview.core.structure import CrystalStructure, DisorderedStructureError, StructureLoadError

from conftest import DATA

EXPECTED_SG = {
    "Fe_bcc": (229, "Im-3m"),
    "NiO": (225, "Fm-3m"),
    "La2CuO4": (139, "I4/mmm"),
    "FeSe": (129, "P4/nmm"),
    "Cs3V9Te13_300K": (189, "P-62m"),
    "toy_square_CuO2": (123, "P4/mmm"),
    "toy_honeycomb_Cr": (191, "P6/mmm"),
    "toy_triangular_Co": (191, "P6/mmm"),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SG))
def test_space_groups(load, name):
    c = load(name)
    assert (c.symmetry.number, c.symmetry.international) == EXPECTED_SG[name]
    assert np.all((c.frac_coords >= 0) & (c.frac_coords < 1))


def test_cs3v9te13_experimental_cif(load):
    c = load("Cs3V9Te13_300K")
    comp, z = c.structure.composition.get_reduced_composition_and_factor()
    assert comp.reduced_formula == "Cs3V9Te13" and z == 1
    assert len(c) == 25
    lat = c.structure.lattice
    assert (lat.a, lat.c, lat.gamma) == pytest.approx((10.089, 8.212, 120.0))
    # CIF labels survive symmetry expansion.
    assert sorted(set(c.labels)) == ["Cs1", "Te1", "Te2", "Te3", "V1", "V2"]
    assert c.labels.count("V1") == 3 and c.labels.count("V2") == 6
    v = [i for i, s in enumerate(c.symbols) if s == "V"]
    assert np.allclose(c.frac_coords[v, 2], 0.5)  # single V layer at z = 1/2
    by_label = {lab: c.symmetry.wyckoffs[c.labels.index(lab)] for lab in set(c.labels)}
    assert by_label == {"Cs1": "f", "V1": "g", "V2": "k", "Te1": "h", "Te2": "i", "Te3": "g"}
    # V1 and V2 are distinct orbits.
    eq = np.array(c.symmetry.equivalent_atoms)
    assert len(set(eq[v])) == 2


def test_primitive_lattice_of_centered_cell(load):
    c = load("NiO")  # fcc: primitive volume = conventional / 4
    vol_prim = abs(np.linalg.det(c.symmetry.primitive_lattice))
    assert vol_prim == pytest.approx(c.structure.volume / 4)


def test_disordered_rejected():
    s = Structure(Lattice.cubic(4.0), [{"Fe": 0.5, "Co": 0.5}], [[0, 0, 0]])
    with pytest.raises(DisorderedStructureError):
        CrystalStructure.from_pymatgen(s)


def test_missing_file(tmp_path):
    with pytest.raises(StructureLoadError):
        CrystalStructure.from_file(tmp_path / "nope.cif")


def test_garbage_file(tmp_path):
    p = tmp_path / "bad.cif"
    p.write_text("this is not a cif\n")
    with pytest.raises(StructureLoadError):
        CrystalStructure.from_file(p)


def test_poscar_roundtrip(load, tmp_path):
    p = tmp_path / "POSCAR"
    load("NiO").structure.to(filename=str(p), fmt="poscar")
    c = CrystalStructure.from_file(p)
    assert c.symmetry.number == 225 and len(c) == 8


def test_wraps_coordinates():
    s = Structure(Lattice.cubic(3.0), ["Fe"], [[1.0, -0.25, 2.5]])
    c = CrystalStructure.from_pymatgen(s)
    assert np.allclose(c.frac_coords, [[0.0, 0.75, 0.5]])


def test_magview_json_files_get_a_clear_error(tmp_path):
    """A session or handoff given where a structure is expected used to fail with
    pymatgen's bare KeyError text ("could not read x.json: 'lattice'")."""
    import json

    from magview.core import project
    from magview.core.document import MagDocument

    doc = MagDocument.new(CrystalStructure.from_file(DATA / "NiO.cif"))
    session = project.save(doc, tmp_path / "nio.magview.json")
    with pytest.raises(StructureLoadError, match="is a MagView session"):
        CrystalStructure.from_file(session)
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps({"schema": "magview.handoff", "input_cell": {}}))
    with pytest.raises(StructureLoadError, match="AgentDFT handoff"):
        CrystalStructure.from_file(handoff)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"foo": 1}))
    with pytest.raises(StructureLoadError, match=r"not a structure file .*missing field 'lattice'"):
        CrystalStructure.from_file(other)


NON_FINITE_PROBE = """
import sys
from pymatgen.core import Lattice, Structure
from magview.core.structure import CrystalStructure, StructureLoadError
nan, inf = float("nan"), float("inf")
cases = {
    "lattice NaN": (Lattice([[nan, 0, 0], [0, 4, 0], [0, 0, 4]]), [[0, 0, 0]], 1e-3),
    "degenerate lattice": (Lattice([[1, 0, 0], [2, 0, 0], [0, 0, 4]]), [[0, 0, 0]], 1e-3),
    "frac NaN": (Lattice.cubic(4), [[nan, 0, 0]], 1e-3),
    "frac inf": (Lattice.cubic(4), [[inf, 0, 0]], 1e-3),
    "symprec NaN": (Lattice.cubic(4), [[0, 0, 0]], nan),
    "symprec negative": (Lattice.cubic(4), [[0, 0, 0]], -1.0),
}
for name, (lat, frac, symprec) in cases.items():
    try:
        CrystalStructure.from_pymatgen(Structure(lat, ["Fe"] * len(frac), frac), symprec=symprec)
        print(name, "ACCEPTED", flush=True)
    except StructureLoadError as exc:
        print(name, "rejected:", exc, flush=True)
"""


def test_non_finite_geometry_rejected_instead_of_crashing():
    """NaN/inf lattices, coordinates or tolerances (e.g. a corrupt session) reached
    spglib's C code and killed the process (SIGSEGV), GUI included. Run in a
    subprocess so a regression fails this test instead of the whole session."""
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-c", NON_FINITE_PROBE], capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, f"crashed with exit code {proc.returncode}: {proc.stdout}"
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 6 and all("rejected:" in line for line in lines), proc.stdout
