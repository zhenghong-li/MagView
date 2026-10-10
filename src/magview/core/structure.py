"""Crystal structure loading and symmetry analysis.

``CrystalStructure`` wraps a pymatgen ``Structure`` (the *input cell*, exactly as
loaded, with fractional coordinates wrapped into [0, 1)) together with its spglib
symmetry dataset. Everything downstream (images, bonds, canvas, moments) refers
to sites by their index in this input cell.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import spglib
import spglib.error
from pymatgen.core import Structure

from magview.core.species import element_symbol

DEFAULT_SYMPREC = 1e-3  # Angstrom
WRAP_TOL = 1e-8

# Opt in to spglib's exception-based error handling (the legacy None-return mode
# is deprecated and emits a warning on every call).
spglib.error.OLD_ERROR_HANDLING = False


class StructureLoadError(RuntimeError):
    pass


class DisorderedStructureError(StructureLoadError):
    pass


def _reject_magview_json(path: Path) -> None:
    """MagView's own JSON files are not structures; say what they are instead of
    letting pymatgen fail with an opaque KeyError."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return  # not JSON we know: let pymatgen report it
    if not isinstance(data, dict):
        return
    if data.get("format") == "magview-project":
        raise StructureLoadError(
            f"{path.name} is a MagView session, not a crystal structure: open it in the MagView "
            "GUI (File > Open), which recognizes sessions by the .magview.json extension")
    if data.get("schema") == "magview.handoff":
        raise StructureLoadError(
            f"{path.name} is a MagView AgentDFT handoff, not a crystal structure; its structures "
            "are in the input_cell, reference_cell and magnetic_cell fields")


@dataclass(frozen=True)
class SymmetryInfo:
    number: int
    international: str
    hall: str
    wyckoffs: tuple[str, ...]  # per site of the input cell
    equivalent_atoms: tuple[int, ...]  # per site: representative index of its orbit
    site_symmetry: tuple[str, ...]
    primitive_lattice: np.ndarray  # (3, 3) rows = primitive lattice vectors (Angstrom)
    symprec: float


@dataclass
class CrystalStructure:
    structure: Structure
    labels: tuple[str, ...]
    symmetry: SymmetryInfo
    source: Path | None = None

    # -- construction --------------------------------------------------------
    @classmethod
    def from_file(cls, path: str | Path, symprec: float = DEFAULT_SYMPREC) -> "CrystalStructure":
        path = Path(path)
        if not path.is_file():
            raise StructureLoadError(f"file not found: {path}")
        if path.suffix.lower() == ".json":
            _reject_magview_json(path)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if path.suffix.lower() == ".cif":
                    from pymatgen.io.cif import CifParser

                    parser = CifParser(path, occupancy_tolerance=1.0, site_tolerance=1e-4)
                    structures = parser.parse_structures(primitive=False)
                    if not structures:
                        raise StructureLoadError(f"no structure found in {path}")
                    struct = structures[0]
                else:
                    struct = Structure.from_file(path)
        except StructureLoadError:
            raise
        except Exception as exc:  # pymatgen raises many exception types
            if isinstance(exc, KeyError):  # str(KeyError) is just the quoted key name
                raise StructureLoadError(f"could not read {path}: not a structure file pymatgen "
                                         f"recognizes (missing field {exc})") from exc
            raise StructureLoadError(f"could not read {path}: {exc}") from exc
        return cls.from_pymatgen(struct, source=path, symprec=symprec)

    @classmethod
    def from_pymatgen(cls, struct: Structure, source: Path | None = None,
                      symprec: float = DEFAULT_SYMPREC) -> "CrystalStructure":
        if not struct.is_ordered:
            raise DisorderedStructureError(
                "structure has partial occupancies; order it first (e.g. pick one "
                "configuration or build a supercell) before magnetic editing")
        struct = struct.copy()
        frac = np.mod(struct.frac_coords, 1.0)
        frac[np.abs(frac - 1.0) < WRAP_TOL] = 0.0
        labels = []
        for i, site in enumerate(struct):
            label = getattr(site, "label", None)
            labels.append(str(label) if label else f"{element_symbol(site.specie)}{i + 1}")
        struct = Structure(struct.lattice, struct.species, frac,
                           coords_are_cartesian=False, labels=labels,
                           site_properties=struct.site_properties or None)
        return cls(struct, tuple(labels), _symmetry(struct, symprec), source)

    # -- convenience ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self.structure)

    @property
    def lattice_matrix(self) -> np.ndarray:
        """(3, 3), rows are a, b, c in Cartesian Angstrom."""
        return np.array(self.structure.lattice.matrix)

    @property
    def frac_coords(self) -> np.ndarray:
        return np.array(self.structure.frac_coords)

    @property
    def cart_coords(self) -> np.ndarray:
        return np.array(self.structure.cart_coords)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(element_symbol(s.specie) for s in self.structure)

    @property
    def formula(self) -> str:
        return self.structure.composition.reduced_formula

    @property
    def name(self) -> str:
        return self.source.stem if self.source else self.formula

    def summary(self) -> str:
        lat = self.structure.lattice
        s = self.symmetry
        return (f"{self.formula}  {s.international} (#{s.number})  "
                f"a={lat.a:.4f} b={lat.b:.4f} c={lat.c:.4f} "
                f"alpha={lat.alpha:.2f} beta={lat.beta:.2f} gamma={lat.gamma:.2f}  "
                f"{len(self)} sites")


def _symmetry(struct: Structure, symprec: float) -> SymmetryInfo:
    numbers = [site.specie.Z for site in struct]
    cell = (np.array(struct.lattice.matrix), np.array(struct.frac_coords), numbers)
    try:
        ds = spglib.get_symmetry_dataset(cell, symprec=symprec)
    except spglib.error.SpglibError as exc:
        raise StructureLoadError(f"spglib failed to find symmetry: {exc}") from exc
    if ds is None:
        raise StructureLoadError("spglib failed to find symmetry")
    return SymmetryInfo(
        number=int(ds.number),
        international=str(ds.international),
        hall=str(ds.hall),
        wyckoffs=tuple(str(w) for w in ds.wyckoffs),
        equivalent_atoms=tuple(int(i) for i in ds.equivalent_atoms),
        site_symmetry=tuple(str(s) for s in ds.site_symmetry_symbols),
        primitive_lattice=np.array(ds.primitive_lattice),
        symprec=symprec,
    )
