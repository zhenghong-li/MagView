"""Constant-thickness moment arrows, built vectorized in numpy.

VTK's glyph filters scale an arrow uniformly, so long arrows get fat. Magnetic
figures (and VESTA) keep the shaft radius fixed and vary only the length, so the
geometry is generated here directly. Topology depends only on the number of
arrows and the resolution, so during a drag the GUI rewrites point coordinates
and normals in place (``arrow_points`` + the cached ``arrow_faces``) instead of
rebuilding the mesh.

Vertices on sharp edges (shaft/cap/cone junctions) are duplicated so each one
carries a single analytically computed normal; no VTK normals filter is needed.
Point layout per arrow (n = style.resolution, 6n+2 points, 4n faces):

    shaft ring0 (n), shaft ring1 (n)     normals = radial circle vectors
    annulus ring1 (n), annulus ring2 (n) normals = -direction
    cone ring2 (n), cone tip (1)         normals = cone side / direction
    cap ring0 (n), cap center (1)        normals = -direction
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

MIN_LENGTH = 1e-6  # Angstrom; shorter arrows are dropped


@dataclass(frozen=True)
class ArrowStyle:
    scale: float = 1.0  # Angstrom per mu_B
    shaft_radius: float = 0.12
    head_radius_ratio: float = 2.0
    head_length_ratio: float = 0.25
    centered: bool = True  # True: arrow passes through the atom; False: tail at the atom
    resolution: int = 16

    @classmethod
    def from_theme(cls, theme) -> "ArrowStyle":
        return cls(theme.vector_scale, theme.arrow_shaft_radius, theme.arrow_head_radius_ratio,
                   theme.arrow_head_length_ratio, theme.arrow_anchor_centered,
                   theme.arrow_resolution)


def points_per_arrow(resolution: int) -> int:
    return 6 * resolution + 2


def arrow_rows(moments: np.ndarray, scale: float) -> np.ndarray:
    """Rows of ``moments`` long enough to draw: |m| * scale > MIN_LENGTH."""
    m = np.asarray(moments, dtype=float).reshape(-1, 3)
    return np.nonzero(np.linalg.norm(m, axis=1) * scale > MIN_LENGTH)[0]


def _template_faces(n: int) -> np.ndarray:
    """Face connectivity of one arrow, in pyvista's flat [k, i0, ..., ik-1] format.

    See the module docstring for the point layout.
    """
    s0, s1, a1, a2, c2 = 0, n, 2 * n, 3 * n, 4 * n
    tip, cap0, capc = 5 * n, 5 * n + 1, 6 * n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [4, s0 + i, s0 + j, s1 + j, s1 + i]  # shaft side
        faces += [4, a1 + i, a1 + j, a2 + j, a2 + i]  # head underside annulus
        faces += [3, c2 + i, c2 + j, tip]  # cone
        faces += [3, capc, cap0 + j, cap0 + i]  # bottom cap
    return np.array(faces, dtype=np.int64)


@lru_cache(maxsize=64)
def arrow_faces(n_arrows: int, resolution: int) -> np.ndarray:
    """Connectivity for ``n_arrows`` arrows of the given resolution, cached:
    the topology is identical for every drag frame of the same arrow set."""
    tmpl = _template_faces(resolution)
    is_index = np.ones_like(tmpl, dtype=bool)
    pos = 0
    while pos < len(tmpl):  # mark the size entries, which must not be offset
        is_index[pos] = False
        pos += tmpl[pos] + 1
    offsets = (np.arange(n_arrows, dtype=np.int64) * points_per_arrow(resolution))[:, None]
    return np.where(is_index[None, :], tmpl[None, :] + offsets, tmpl[None, :]).ravel()


def _orthonormal_frames(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors perpendicular to each row of unit vectors ``d``."""
    helper = np.where(np.abs(d[:, [0]]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
    u = np.cross(d, helper)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    w = np.cross(d, u)
    return u, w


def arrow_points(origins: np.ndarray, moments: np.ndarray, style: ArrowStyle):
    """Points and analytic per-vertex normals for one arrow per row.

    Every row must pass ``arrow_rows`` (norm * scale > MIN_LENGTH and finite).
    Used for the initial build and for the in-place drag update: the output
    shape is fixed by ``(len(origins), style.resolution)``.
    """
    origins = np.asarray(origins, dtype=float).reshape(-1, 3)
    moments = np.asarray(moments, dtype=float).reshape(-1, 3)
    n = style.resolution
    lengths = np.linalg.norm(moments, axis=1) * style.scale
    L = lengths[:, None]  # (N, 1)
    d = moments / np.linalg.norm(moments, axis=1, keepdims=True)
    u, w = _orthonormal_frames(d)
    base = origins - (0.5 * L * d if style.centered else 0.0)

    rs = style.shaft_radius
    rh = rs * style.head_radius_ratio
    h = np.minimum(style.head_length_ratio * lengths, lengths)  # (N,) head length
    theta = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    circ = np.cos(theta)[None, :, None] * u[:, None, :] + np.sin(theta)[None, :, None] * w[:, None, :]

    b = base[:, None, :]
    shaft_top = b + (L - h[:, None])[:, :, None] * d[:, None, :]
    ring0 = b + rs * circ
    ring1 = shaft_top + rs * circ
    ring2 = shaft_top + rh * circ
    tip = base + L * d

    # Normals: radial on the shaft, flat (-d) on the annulus and the bottom cap,
    # and the cone side normal (h * radial + rh * d) / |..| (smooth around the
    # cone; the tip normal is d, the cone's average).
    down = np.broadcast_to(-d[:, None, :], (len(d), n, 3))
    cone = (h[:, None, None] * circ + rh * d[:, None, :]) / np.sqrt(h ** 2 + rh ** 2)[:, None, None]

    points = np.concatenate([ring0, ring1, ring1, ring2, ring2, tip[:, None, :],
                             ring0, base[:, None, :]], axis=1)
    normals = np.concatenate([circ, circ, down, down, cone, d[:, None, :],
                              down, -d[:, None, :]], axis=1)
    return points.reshape(-1, 3), normals.reshape(-1, 3)


def arrow_geometry(origins: np.ndarray, moments: np.ndarray, style: ArrowStyle):
    """Return ``(points, normals, faces, arrow_index)`` for arrows at ``origins``.

    ``moments`` are Cartesian mu_B; length = |m| * style.scale. Arrows shorter than
    MIN_LENGTH are omitted; ``arrow_index`` maps each output point to its input row.
    """
    origins = np.asarray(origins, dtype=float).reshape(-1, 3)
    moments = np.asarray(moments, dtype=float).reshape(-1, 3)
    keep = arrow_rows(moments, style.scale)
    n = style.resolution
    if len(keep) == 0:
        empty3 = np.zeros((0, 3))
        return empty3, empty3.copy(), np.zeros(0, np.int64), np.zeros(0, np.int64)
    points, normals = arrow_points(origins[keep], moments[keep], style)
    faces = arrow_faces(len(keep), n)
    arrow_index = np.repeat(keep, points_per_arrow(n))
    return points, normals, faces, arrow_index


def arrow_colors(moments: np.ndarray, axis: np.ndarray, up_rgb, down_rgb, neutral_rgb,
                 tol: float = 1e-6) -> np.ndarray:
    """Color by sign of the projection on the collinear axis (neutral if perpendicular)."""
    proj = np.asarray(moments, dtype=float) @ (np.asarray(axis, float) / np.linalg.norm(axis))
    out = np.tile(np.asarray(neutral_rgb, float), (len(proj), 1))
    out[proj > tol] = up_rgb
    out[proj < -tol] = down_rgb
    return out


COLOR_MODES = ("sign", "direction", "element", "uniform")


def _hls_to_rgb(h: np.ndarray, l: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Vectorized ``colorsys.hls_to_rgb``."""
    m2 = np.where(l <= 0.5, l * (1.0 + s), l + s - l * s)
    m1 = 2.0 * l - m2

    def channel(hue: np.ndarray) -> np.ndarray:
        hue = np.mod(hue, 1.0)
        return np.select(
            [hue < 1.0 / 6.0, hue < 0.5, hue < 2.0 / 3.0],
            [m1 + (m2 - m1) * hue * 6.0, m2, m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0],
            default=m1)

    return np.stack([channel(h + 1.0 / 3.0), channel(h), channel(h - 1.0 / 3.0)], axis=-1)


def direction_colors(moments: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Hue = azimuth around the spin axis, lightness = polar angle (up light, down dark)."""
    m = np.asarray(moments, dtype=float)
    u = np.asarray(axis, float) / np.linalg.norm(axis)
    helper = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(u, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    out = np.zeros((len(m), 3))
    norms = np.linalg.norm(m, axis=1)
    nz = np.nonzero(norms > 0)[0]
    if len(nz) == 0:
        return out
    v = m[nz] / norms[nz, None]
    hue = np.mod(np.arctan2(v @ e2, v @ e1) / (2 * np.pi), 1.0)
    cos = v @ u
    light = 0.5 + 0.3 * cos
    sat = 0.35 + 0.6 * np.sqrt(np.clip(1.0 - cos ** 2, 0.0, None))
    out[nz] = _hls_to_rgb(hue, light, sat)
    return out


def moment_colors(mode: str, moments: np.ndarray, axis: np.ndarray, theme,
                  atom_rgb: np.ndarray | None = None) -> np.ndarray:
    if mode == "sign":
        return arrow_colors(moments, axis, theme.spin_up_color, theme.spin_down_color,
                            theme.arrow_neutral_color)
    if mode == "direction":
        return direction_colors(moments, axis)
    if mode == "element" and atom_rgb is not None:
        return np.asarray(atom_rgb, float) * 0.75
    return np.tile(np.asarray(theme.arrow_neutral_color, float), (len(moments), 1))
