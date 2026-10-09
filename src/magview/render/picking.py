"""Screen <-> world helpers for picking and rubber-band selection.

Picking is analytic (mouse ray against the atom spheres) rather than a VTK
render-buffer pick: it is exact, independent of rendering, and therefore
testable offscreen. The camera transforms come from VTK so parallel and
perspective projections are both handled.
"""

from __future__ import annotations

import numpy as np


def _world_point(renderer, x: float, y: float, z: float) -> np.ndarray:
    renderer.SetDisplayPoint(float(x), float(y), float(z))
    renderer.DisplayToWorld()
    w = np.array(renderer.GetWorldPoint(), dtype=float)
    return w[:3] / w[3]


def display_ray(renderer, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
    """Ray (origin on the near plane, unit direction into the scene) under display pixel (x, y).

    Display coordinates are VTK's: origin at the bottom-left of the render window.
    """
    near = _world_point(renderer, x, y, 0.0)
    far = _world_point(renderer, x, y, 1.0)
    d = far - near
    return near, d / np.linalg.norm(d)


def pick_sphere(origin: np.ndarray, direction: np.ndarray, centers: np.ndarray,
                radii: np.ndarray) -> int | None:
    """Index of the first sphere hit by the ray, or None."""
    if len(centers) == 0:
        return None
    oc = centers - origin
    t_mid = oc @ direction  # closest approach along the ray
    d2 = np.einsum("ij,ij->i", oc, oc) - t_mid ** 2
    r2 = np.asarray(radii, dtype=float) ** 2
    hit = (d2 <= r2) & (t_mid >= 0)
    if not hit.any():
        return None
    t_entry = t_mid - np.sqrt(np.clip(r2 - d2, 0.0, None))
    t_entry[~hit] = np.inf
    return int(np.argmin(t_entry))


def project_to_display(renderer, points: np.ndarray) -> np.ndarray:
    """World points -> display (x, y) pixels, vectorized."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    cam = renderer.GetActiveCamera()
    aspect = renderer.GetTiledAspectRatio()
    m = cam.GetCompositeProjectionTransformMatrix(aspect, -1.0, 1.0)
    mat = np.array([[m.GetElement(i, j) for j in range(4)] for i in range(4)])
    h = np.column_stack([pts, np.ones(len(pts))]) @ mat.T
    ndc = h[:, :2] / h[:, 3:4]
    vx, vy = renderer.GetOrigin()
    w, hgt = renderer.GetSize()
    return np.column_stack([vx + (ndc[:, 0] + 1.0) * 0.5 * w, vy + (ndc[:, 1] + 1.0) * 0.5 * hgt])


def in_rect(xy: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    lo_x, hi_x = sorted((x0, x1))
    lo_y, hi_y = sorted((y0, y1))
    return (xy[:, 0] >= lo_x) & (xy[:, 0] <= hi_x) & (xy[:, 1] >= lo_y) & (xy[:, 1] <= hi_y)


def pick_atom(renderer, model, x: float, y: float) -> int | None:
    """Displayed-atom index under display pixel (x, y), or None."""
    origin, direction = display_ray(renderer, x, y)
    return pick_sphere(origin, direction, model.atoms.cart, model.atom_radius)


def atoms_in_box(renderer, model, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Displayed-atom indices whose centers project inside the rectangle (through all depths)."""
    xy = project_to_display(renderer, model.atoms.cart)
    return np.nonzero(in_rect(xy, x0, y0, x1, y1))[0]
