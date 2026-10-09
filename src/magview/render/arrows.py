"""Constant-thickness moment arrows, built vectorized in numpy.

VTK's glyph filters scale an arrow uniformly, so long arrows get fat. Magnetic
figures (and VESTA) keep the shaft radius fixed and vary only the length, so the
geometry is generated here directly. Topology depends only on the number of
arrows, so during a drag the GUI can rewrite point coordinates in place.
"""

from __future__ import annotations

from dataclasses import dataclass

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


def _template_faces(n: int) -> np.ndarray:
    """Face connectivity of one arrow, in pyvista's flat [k, i0, ..., ik-1] format.

    Point layout per arrow: ring0 (shaft bottom, n), ring1 (shaft top, n),
    ring2 (head base, n), tip (1), bottom center (1).
    """
    r0, r1, r2 = 0, n, 2 * n
    tip, bottom = 3 * n, 3 * n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [4, r0 + i, r0 + j, r1 + j, r1 + i]  # shaft side
        faces += [3, bottom, r0 + j, r0 + i]  # bottom cap
        faces += [4, r1 + i, r1 + j, r2 + j, r2 + i]  # head underside annulus
        faces += [3, r2 + i, r2 + j, tip]  # cone
    return np.array(faces, dtype=np.int64)


def _orthonormal_frames(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors perpendicular to each row of unit vectors ``d``."""
    helper = np.where(np.abs(d[:, [0]]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
    u = np.cross(d, helper)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    w = np.cross(d, u)
    return u, w


def arrow_geometry(origins: np.ndarray, moments: np.ndarray, style: ArrowStyle):
    """Return ``(points, faces, arrow_index)`` for arrows at ``origins``.

    ``moments`` are Cartesian mu_B; length = |m| * style.scale. Arrows shorter than
    MIN_LENGTH are omitted; ``arrow_index`` maps each output point to its input row.
    """
    origins = np.asarray(origins, dtype=float).reshape(-1, 3)
    moments = np.asarray(moments, dtype=float).reshape(-1, 3)
    lengths = np.linalg.norm(moments, axis=1) * style.scale
    keep = np.nonzero(lengths > MIN_LENGTH)[0]
    n = style.resolution
    if len(keep) == 0:
        return np.zeros((0, 3)), np.zeros(0, np.int64), np.zeros(0, np.int64)

    L = lengths[keep][:, None]  # (N, 1)
    d = moments[keep] / np.linalg.norm(moments[keep], axis=1, keepdims=True)
    u, w = _orthonormal_frames(d)
    base = origins[keep] - (0.5 * L * d if style.centered else 0.0)

    rs = style.shaft_radius
    rh = rs * style.head_radius_ratio
    h = np.minimum(style.head_length_ratio * L, L)  # head length
    theta = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    circ = np.cos(theta)[None, :, None] * u[:, None, :] + np.sin(theta)[None, :, None] * w[:, None, :]

    b = base[:, None, :]
    dd = d[:, None, :]
    ring0 = b + rs * circ
    ring1 = b + (L - h)[:, :, None] * dd + rs * circ
    ring2 = b + (L - h)[:, :, None] * dd + rh * circ
    tip = base + L * d
    bottom = base
    pts = np.concatenate([ring0, ring1, ring2, tip[:, None, :], bottom[:, None, :]], axis=1)
    per = 3 * n + 2
    points = pts.reshape(-1, 3)

    tmpl = _template_faces(n)
    is_index = np.ones_like(tmpl, dtype=bool)
    pos = 0
    while pos < len(tmpl):  # mark the size entries, which must not be offset
        is_index[pos] = False
        pos += tmpl[pos] + 1
    offsets = (np.arange(len(keep), dtype=np.int64) * per)[:, None]
    faces = np.where(is_index[None, :], tmpl[None, :] + offsets, tmpl[None, :]).ravel()
    arrow_index = np.repeat(keep, per)
    return points, faces, arrow_index


def arrow_colors(moments: np.ndarray, axis: np.ndarray, up_rgb, down_rgb, neutral_rgb,
                 tol: float = 1e-6) -> np.ndarray:
    """Color by sign of the projection on the collinear axis (neutral if perpendicular)."""
    proj = np.asarray(moments, dtype=float) @ (np.asarray(axis, float) / np.linalg.norm(axis))
    out = np.tile(np.asarray(neutral_rgb, float), (len(proj), 1))
    out[proj > tol] = up_rgb
    out[proj < -tol] = down_rgb
    return out
