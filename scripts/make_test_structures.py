"""Generate the synthetic test structures in tests/data/ (run once; outputs are committed).

Real compounds use approximate literature lattice parameters and positions; they
are for visual and algorithmic tests, not for quantitative work. Toy lattices
isolate one magnetic 2D net each for the layer/pattern phases.
"""

from pathlib import Path

from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter

OUT = Path(__file__).resolve().parents[1] / "tests" / "data"


def fe_bcc() -> Structure:  # Im-3m (229)
    return Structure(Lattice.cubic(2.8665), ["Fe", "Fe"], [[0, 0, 0], [0.5, 0.5, 0.5]])


def nio() -> Structure:  # Fm-3m (225), rocksalt
    fcc = [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]]
    o = [[(x + 0.5) % 1, y, z] for x, y, z in fcc]
    return Structure(Lattice.cubic(4.1771), ["Ni"] * 4 + ["O"] * 4, fcc + o)


def la2cuo4() -> Structure:  # I4/mmm (139), high-temperature tetragonal phase
    zla, zo2 = 0.3607, 0.1824
    base = [("Cu", [0, 0, 0]), ("La", [0, 0, zla]), ("La", [0, 0, -zla]),
            ("O", [0, 0.5, 0]), ("O", [0.5, 0, 0]), ("O", [0, 0, zo2]), ("O", [0, 0, -zo2])]
    species, coords = [], []
    for sp, (x, y, z) in base:
        for t in ([0, 0, 0], [0.5, 0.5, 0.5]):  # body centering
            species.append(sp)
            coords.append([(x + t[0]) % 1, (y + t[1]) % 1, (z + t[2]) % 1])
    return Structure(Lattice.tetragonal(3.78, 13.23), species, coords)


def fese() -> Structure:  # P4/nmm (129), origin choice 2
    z = 0.2672
    return Structure(Lattice.tetragonal(3.7734, 5.5258), ["Fe", "Fe", "Se", "Se"],
                     [[0.75, 0.25, 0], [0.25, 0.75, 0], [0.25, 0.25, z], [0.75, 0.75, 1 - z]])


def toy_square() -> Structure:  # CuO2 square net, P4/mmm
    return Structure(Lattice.tetragonal(3.9, 8.0), ["Cu", "O", "O"],
                     [[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0]])


def toy_honeycomb() -> Structure:  # Cr honeycomb net, P6/mmm (Cr-Cr = 3.40 A)
    return Structure(Lattice.hexagonal(3.40 * 3 ** 0.5, 8.0), ["Cr", "Cr"],
                     [[1 / 3, 2 / 3, 0], [2 / 3, 1 / 3, 0]])


def toy_triangular() -> Structure:  # Co triangular net, P6/mmm
    return Structure(Lattice.hexagonal(3.5, 6.0), ["Co"], [[0, 0, 0]])


STRUCTURES = {
    "Fe_bcc": fe_bcc,
    "NiO": nio,
    "La2CuO4": la2cuo4,
    "FeSe": fese,
    "toy_square_CuO2": toy_square,
    "toy_honeycomb_Cr": toy_honeycomb,
    "toy_triangular_Co": toy_triangular,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in STRUCTURES.items():
        path = OUT / f"{name}.cif"
        CifWriter(make(), symprec=1e-3).write_file(path)
        print("wrote", path.relative_to(OUT.parents[1]))


if __name__ == "__main__":
    main()
