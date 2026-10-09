"""Runtime parsers for VESTA's ``elements.ini`` and ``style.ini``.

VESTA's license forbids redistributing its files, so MagView never copies them.
These parsers read them from the user's own VESTA installation at runtime.
Field meanings are documented in ``docs/vesta_reference.md``; fields whose
meaning is inferred rather than certain are marked as such there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_VESTA_DIR = "MAGVIEW_VESTA_DIR"
VESTA_DIR_NAME = "VESTA-gtk3-x86_64"


@dataclass(frozen=True)
class VestaElement:
    """One row of ``elements.ini``."""

    z: int
    symbol: str
    r_atomic: float
    r_vdw: float
    r_ionic: float
    color: tuple[float, float, float]  # RGB in [0, 1]


@dataclass(frozen=True)
class VestaBondRule:
    """One row of the ``SBOND`` table in ``style.ini``.

    ``a1``/``a2`` are element symbols (or labels when ``search_by_label`` is set).
    ``style`` is VESTA's bond-style code; 0-based meaning (inferred):
    0 unicolor cylinder, 1 bicolor cylinder, 2 color line, 3 gradient line,
    4 dotted line, 5 dashed line. The H...O hydrogen-bond rows use 5.
    """

    index: int
    a1: str
    a2: str
    d_min: float
    d_max: float
    search_mode: int
    boundary_mode: int
    show_polyhedra: bool
    search_by_label: bool
    style: int


@dataclass(frozen=True)
class VestaLight:
    enabled: bool
    matrix: tuple[tuple[float, ...], ...]  # 4x4 orientation matrix
    position: tuple[float, ...]
    direction: tuple[float, ...]
    ambient: tuple[int, int, int, int]  # RGBA 0..255
    diffuse: tuple[int, int, int, int]
    specular: tuple[int, int, int, int]


@dataclass(frozen=True)
class VestaMaterial:
    specular: tuple[int, int, int, int]  # RGBA 0..255
    shininess: float


@dataclass
class VestaStyle:
    """Parsed ``style.ini``: bond rules plus the raw STYLE block."""

    version: str
    header: list[list[str]]
    bond_rules: list[VestaBondRule]
    blocks: dict[str, list[list[str]]] = field(default_factory=dict)

    # -- typed accessors for the STYLE fields MagView uses ------------------
    def _first(self, key: str) -> list[str]:
        rows = self.blocks.get(key)
        if not rows:
            raise KeyError(f"STYLE block {key!r} not present")
        return rows[0]

    @property
    def background_rgb(self) -> tuple[int, int, int]:
        r, g, b = (int(v) for v in self.blocks["BKGRC"][1][:3])
        return (r, g, b)

    @property
    def bond_radius(self) -> float:
        return float(self.blocks["BONDP"][1][2])

    @property
    def bond_segments(self) -> int:
        return int(self.blocks["BONDP"][1][1])

    @property
    def bond_style(self) -> int:
        return int(self.blocks["BONDP"][1][0])

    @property
    def bond_line_width(self) -> float:
        return float(self.blocks["BONDP"][1][3])

    @property
    def bond_rgb(self) -> tuple[int, int, int]:
        r, g, b = (int(v) for v in self.blocks["BONDP"][1][4:7])
        return (r, g, b)

    @property
    def atom_sphere_resolution(self) -> tuple[int, int]:
        row = self.blocks["ATOMP"][1]
        return int(row[0]), int(row[1])

    @property
    def atom_ball_scale(self) -> float:
        """Ball-and-stick radius scale. Inferred: ATOMP field 4 is a percentage."""
        return float(self.blocks["ATOMP"][1][3]) / 100.0

    @property
    def vector_scale(self) -> float:
        return float(self._first("VECTS")[1])

    @property
    def cell_line_width(self) -> float:
        return float(self.blocks["UCOLP"][1][2])

    @property
    def cell_line_rgb(self) -> tuple[int, int, int]:
        r, g, b = (int(v) for v in self.blocks["UCOLP"][1][3:6])
        return (r, g, b)

    @property
    def projection(self) -> int:
        return int(self._first("PROJT")[1])

    def light(self, i: int = 0) -> VestaLight:
        rows = self.blocks[f"LIGHT{i}"]
        enabled = len(rows[0]) > 1 and rows[0][1] == "1"
        nums = rows[1:]
        matrix = tuple(tuple(float(v) for v in r) for r in nums[0:4])
        position = tuple(float(v) for v in nums[4])
        direction = tuple(float(v) for v in nums[5])

        def rgba(r: list[str]) -> tuple[int, int, int, int]:
            a, b, c, d = (int(v) for v in r[:4])
            return (a, b, c, d)

        return VestaLight(enabled, matrix, position, direction,
                          rgba(nums[6]), rgba(nums[7]), rgba(nums[8]))

    def material(self, key: str = "ATOMM") -> VestaMaterial:
        rows = self.blocks[key]
        spec = tuple(int(v) for v in rows[1][:4])
        return VestaMaterial(spec, float(rows[2][0]))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_elements_ini(path: str | os.PathLike) -> dict[str, VestaElement]:
    """Parse ``elements.ini``: ``Z symbol r_atomic r_vdW r_ionic R G B`` per line."""
    table: dict[str, VestaElement] = {}
    for lineno, line in enumerate(Path(path).read_text(encoding="latin-1").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 8:
            raise ValueError(f"{path}:{lineno}: expected 8 fields, got {len(parts)}")
        z, sym = int(parts[0]), parts[1]
        r_at, r_vdw, r_ion, r, g, b = (float(v) for v in parts[2:])
        table[sym] = VestaElement(z, sym, r_at, r_vdw, r_ion, (r, g, b))
    return table


# Keywords that start a block inside the STYLE section. A block runs until the
# next keyword line; its first row is the keyword line itself.
def _is_keyword(token: str) -> bool:
    return token[:1].isalpha() and token.upper() == token


def parse_style_ini(path: str | os.PathLike) -> VestaStyle:
    lines = Path(path).read_text(encoding="latin-1").splitlines()
    if not lines or not lines[0].startswith("#VESTA_STYLE_VER"):
        raise ValueError(f"{path}: not a VESTA style.ini (missing #VESTA_STYLE_VER)")
    version = lines[0].split()[1]

    i = 1
    header: list[list[str]] = []
    while i < len(lines) and lines[i].strip() != "SBOND":
        header.append(lines[i].split())
        i += 1
    if i == len(lines):
        raise ValueError(f"{path}: SBOND section not found")
    i += 1

    rules: list[VestaBondRule] = []
    while i < len(lines):
        parts = lines[i].split()
        i += 1
        if parts == ["0", "0", "0", "0"]:
            break
        if len(parts) < 10:
            raise ValueError(f"{path}:{i}: malformed SBOND row: {parts}")
        rules.append(VestaBondRule(
            index=int(parts[0]), a1=parts[1], a2=parts[2],
            d_min=float(parts[3]), d_max=float(parts[4]),
            search_mode=int(parts[5]), boundary_mode=int(parts[6]),
            show_polyhedra=parts[7] == "1", search_by_label=parts[8] == "1",
            style=int(parts[9]),
        ))

    blocks: dict[str, list[list[str]]] = {}
    current: str | None = None
    for line in lines[i:]:
        parts = line.split()
        if not parts:
            continue
        if _is_keyword(parts[0]) and not _looks_numeric(parts[0]):
            current = parts[0]
            blocks[current] = [parts]
        elif current is not None:
            blocks[current].append(parts)
    return VestaStyle(version=version, header=header, bond_rules=rules, blocks=blocks)


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_vesta_dir(start: str | os.PathLike | None = None) -> Path | None:
    """Locate a VESTA installation containing ``elements.ini``.

    Order: ``$MAGVIEW_VESTA_DIR``; then ``<ancestor>/vesta_tools/VESTA-gtk3-x86_64``
    for every ancestor of ``start`` (default: cwd) and of this package.
    """
    env = os.environ.get(ENV_VESTA_DIR)
    if env:
        p = Path(env).expanduser()
        return p if (p / "elements.ini").is_file() else None

    roots = [Path(start) if start else Path.cwd(), Path(__file__).resolve()]
    seen: set[Path] = set()
    for root in roots:
        for anc in [root, *root.parents]:
            if anc in seen:
                continue
            seen.add(anc)
            cand = anc / "vesta_tools" / VESTA_DIR_NAME
            if (cand / "elements.ini").is_file():
                return cand
    return None
