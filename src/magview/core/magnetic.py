"""Moment math: drag-to-moment mapping, constraints, snapping, batch operations.

All functions are pure (numpy in, numpy out). Moments are Cartesian mu_B.
See docs/ux_spec.md section 3 for the interaction rules implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEAD_ZONE = 0.2  # mu_B: shorter drags count as clicks
MAX_MOMENT = 10.0  # mu_B
SNAP_MAGNITUDE = 0.1  # mu_B
SNAP_ANGLE_DEG = 15.0
AXIS_AT_VIEWER = 0.15  # projected-axis length below which an axis "points at the viewer"

CARTESIAN = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("zero vector has no direction")
    return v / n


def axis_vector(name: str, lattice: np.ndarray) -> np.ndarray:
    """Unit vector for 'x', 'y', 'z', 'a', 'b' or 'c' (lattice rows)."""
    name = name.lower()
    if name in CARTESIAN:
        return CARTESIAN[name]
    if name in "abc" and len(name) == 1:
        return unit(np.asarray(lattice)["abc".index(name)])
    raise ValueError(f"unknown axis {name!r}")


@dataclass(frozen=True)
class DragFrame:
    """Geometry of a drag: the atom and the camera at press time."""

    anchor: np.ndarray  # world position of the dragged atom (Angstrom)
    view_dir: np.ndarray  # unit, direction of projection (into the screen)
    up: np.ndarray  # unit screen-up vector, perpendicular to view_dir
    scale: float  # Angstrom per mu_B
    lattice: np.ndarray  # (3, 3) for a/b/c locks
    centered: bool = False  # arrows drawn through the atom: the tip is at half the length

    @property
    def right(self) -> np.ndarray:
        return np.cross(self.up, -self.view_dir)

    @property
    def tip_scale(self) -> float:
        """Angstrom between the atom and the arrow tip per mu_B: the drag maps the
        cursor distance to |m| so that the visible tip stays under the cursor."""
        return self.scale * (0.5 if self.centered else 1.0)


@dataclass(frozen=True)
class DragResult:
    moment: np.ndarray  # mu_B
    magnitude: float
    below_dead_zone: bool
    lock: str | None  # effective lock ("spin axis", "z", "a", ...)
    note: str  # e.g. "axis points at you: drag up/down"


def plane_point(frame: DragFrame, ray_origin: np.ndarray, ray_dir: np.ndarray) -> np.ndarray:
    """Intersection of the mouse ray with the plane through the anchor, normal to the view."""
    n = frame.view_dir
    denom = ray_dir @ n
    if abs(denom) < 1e-12:
        return frame.anchor.copy()
    t = ((frame.anchor - ray_origin) @ n) / denom
    return ray_origin + t * ray_dir


def drag_moment(frame: DragFrame, point: np.ndarray, lock: str | None = None, snap: bool = False,
                collinear_axis: np.ndarray | None = None, max_moment: float = MAX_MOMENT) -> DragResult:
    """Moment for a drag from the anchor to ``point`` (on the drag plane).

    * free: m = (point - anchor) / tip_scale, in the screen plane (the arrow tip
      stays under the cursor for both centered and tail-anchored arrows);
    * ``lock`` ('x','y','z','a','b','c') or a collinear axis: m = s * u, where s makes
      the arrow tip's screen projection follow the cursor along u's projection;
      if u points nearly at the viewer, vertical mouse motion sets s instead;
    * ``snap``: direction to the nearest axis or 15-degree step, |m| to 0.1 mu_B.
    """
    v = np.asarray(point, float) - frame.anchor
    v = v - (v @ frame.view_dir) * frame.view_dir  # keep it exactly in the plane
    note = ""
    if collinear_axis is not None or lock:
        u = unit(collinear_axis) if collinear_axis is not None else axis_vector(lock, frame.lattice)
        name = "spin axis" if collinear_axis is not None else lock
        u_s = u - (u @ frame.view_dir) * frame.view_dir
        if np.linalg.norm(u_s) < AXIS_AT_VIEWER:
            s = (v @ frame.up) / frame.tip_scale
            if u @ -frame.view_dir < 0:
                s = -s  # keep "drag up = toward the viewer-facing direction"
            note = f"{name} points at you: drag up/down"
        else:
            s = (v @ u_s) / (u_s @ u_s) / frame.tip_scale
        if snap:
            s = round(s / SNAP_MAGNITUDE) * SNAP_MAGNITUDE
        s = float(np.clip(s, -max_moment, max_moment))
        m = s * u
        return DragResult(m, abs(s), abs(s) < DEAD_ZONE, name, note)

    m = v / frame.tip_scale
    mag = float(np.linalg.norm(m))
    if snap and mag > 0:
        mag = round(mag / SNAP_MAGNITUDE) * SNAP_MAGNITUDE
        m = mag * snap_direction(m, frame)
    if mag > max_moment:
        m = m / mag * max_moment
        mag = max_moment
    return DragResult(m, mag, mag < DEAD_ZONE, None, note)


def snap_direction(m: np.ndarray, frame: DragFrame) -> np.ndarray:
    """Nearest of: +-x, +-y, +-z, +-a, +-b, +-c, and 15-degree steps in the drag plane."""
    cands = [CARTESIAN[k] for k in "xyz"] + [unit(r) for r in frame.lattice]
    cands = cands + [-c for c in cands]
    for k in range(int(round(360 / SNAP_ANGLE_DEG))):
        t = np.radians(k * SNAP_ANGLE_DEG)
        cands.append(np.cos(t) * frame.right + np.sin(t) * frame.up)
    cands = np.array(cands)
    return cands[int(np.argmax(cands @ unit(m)))]


# --- batch operations (return new moment arrays) ------------------------------------

def project_collinear(moments: np.ndarray, axis: np.ndarray) -> np.ndarray:
    u = unit(axis)
    return np.outer(np.asarray(moments, float) @ u, u)


def is_collinear(moments: np.ndarray, axis: np.ndarray, tol: float = 1e-6) -> bool:
    m = np.asarray(moments, float)
    return bool(np.allclose(project_collinear(m, axis), m, atol=tol))


def signed_magnitudes(moments: np.ndarray, axis: np.ndarray) -> np.ndarray:
    return np.asarray(moments, float) @ unit(axis)


def spherical(m: np.ndarray) -> tuple[float, float, float]:
    """(|m|, theta, phi) in degrees: theta from +z, phi from +x toward +y."""
    m = np.asarray(m, float)
    r = float(np.linalg.norm(m))
    if r == 0:
        return 0.0, 0.0, 0.0
    theta = float(np.degrees(np.arccos(np.clip(m[2] / r, -1, 1))))
    phi = float(np.degrees(np.arctan2(m[1], m[0])) % 360.0)
    return r, theta, phi


def from_spherical(r: float, theta: float, phi: float) -> np.ndarray:
    t, p = np.radians(theta), np.radians(phi)
    return r * np.array([np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)])


def crystal_components(m: np.ndarray, lattice: np.ndarray) -> np.ndarray:
    """Components along the unit vectors a/|a|, b/|b|, c/|c| (VESTA/mCIF convention)."""
    lat = np.asarray(lattice, float)
    basis = lat / np.linalg.norm(lat, axis=1, keepdims=True)
    return np.asarray(m, float) @ np.linalg.inv(basis)
