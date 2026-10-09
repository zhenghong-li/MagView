"""Top view of one magnetic layer for click-to-toggle up/down editing.

Moments along the viewing axis would be seen end-on, so spins are drawn as
colored discs: red = up (+spin axis), blue = down, light gray = unset. A dark
ring marks pinned sites (set by a click); unpinned sites show the current or
previewed completion. Nearest-neighbour bonds of the layer are drawn as lines.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyvista as pv

from magview.core.images import DisplayAtoms, boundary_images
from magview.render.camera import fit_parallel_scale, view_basis

UNSET_RGB = (0.80, 0.80, 0.80)


@dataclass
class LayerModel:
    """Duck-types the parts of SceneModel used by picking (atoms, atom_radius)."""

    atoms: DisplayAtoms  # displayed copies; .parent = canvas site
    atom_radius: np.ndarray
    local: np.ndarray  # (n_display,) index of each copy in layer.sites
    lattice: np.ndarray
    normal: np.ndarray
    outline: np.ndarray | None = None  # (5, 3) canvas outline in the layer plane

    @property
    def center(self) -> np.ndarray:
        return self.atoms.cart.mean(axis=0) if len(self.atoms) else 0.5 * self.lattice.sum(axis=0)


def _blend(rgb, k: float, to=(1.0, 1.0, 1.0)) -> np.ndarray:
    return (1 - k) * np.asarray(rgb, float) + k * np.asarray(to, float)


class LayerScene:
    def __init__(self, plotter, theme) -> None:
        self.plotter = plotter
        self.theme = theme
        self.actors: dict[str, object] = {}
        self.model: LayerModel | None = None
        self.layer = None
        self.axis = "c"

    def clear(self) -> None:
        for name in list(self.actors):
            self.plotter.remove_actor(self.actors.pop(name), render=False)
        self.model = None

    def show(self, canvas_struct, layer, axis: str, signs: np.ndarray, pinned: np.ndarray,
             preview: np.ndarray | None = None, reset_camera: bool = True,
             selected: np.ndarray | None = None) -> None:
        """Draw ``layer`` (core.layers.Layer). ``signs``/``pinned``/``preview`` are per
        layer site (local order); ``preview`` replaces the signs of unpinned sites."""
        self.clear()
        self.layer, self.axis = layer, axis
        lat = canvas_struct.lattice_matrix
        all_img = boundary_images(canvas_struct.frac_coords, lat)
        k_ax = "abc".index(axis)
        # In-plane periodic copies only: the copy at frac = 1 along the axis is the same layer.
        keep = np.nonzero(np.isin(all_img.parent, layer.sites) & (all_img.shift[:, k_ax] == 0))[0]
        atoms = DisplayAtoms(all_img.parent[keep], all_img.shift[keep], all_img.frac[keep],
                             all_img.cart[keep])
        local = np.searchsorted(layer.sites, atoms.parent)
        d_nn = layer.graph.d_nn if layer.graph.d_nn > 0 else 3.0
        radius = np.full(len(atoms), 0.28 * d_nn)
        normal = np.cross(lat[("abc".index(axis) + 1) % 3], lat[("abc".index(axis) + 2) % 3])
        normal = normal / np.linalg.norm(normal)
        self.model = LayerModel(atoms, radius, local, lat, normal)
        t = self.theme

        shown = np.asarray(signs, int).copy()
        is_preview = np.zeros(len(shown), bool)
        if preview is not None:
            mask = ~np.asarray(pinned, bool)
            is_preview = mask & (np.asarray(preview) != shown)
            shown[mask] = np.asarray(preview, int)[mask]
        rgb = np.array([t.spin_up_color if s > 0 else t.spin_down_color if s < 0 else UNSET_RGB
                        for s in shown])
        rgb[is_preview] = _blend(rgb[is_preview], 0.45)
        disc_rgb = rgb[local]

        # Discs (flat spheres would occlude bonds less, but spheres read well from any tilt).
        cloud = pv.PolyData(atoms.cart)
        cloud["radius"] = radius
        cloud["rgb"] = np.clip(np.rint(disc_rgb * 255), 0, 255).astype(np.uint8)
        sphere = pv.Sphere(radius=1.0, theta_resolution=32, phi_resolution=16)
        glyphs = cloud.glyph(geom=sphere, scale="radius", orient=False, factor=1.0)
        self.actors["discs"] = self.plotter.add_mesh(
            glyphs, scalars="rgb", rgb=True, ambient=0.35, diffuse=0.65, specular=0.2,
            smooth_shading=True, name="layer_discs")

        # Rings around pinned sites.
        pin_disp = np.nonzero(np.asarray(pinned, bool)[local])[0]
        if len(pin_disp):
            ring = pv.Disc(center=(0, 0, 0), inner=1.12, outer=1.42, normal=tuple(normal), c_res=48)
            pc = pv.PolyData(atoms.cart[pin_disp] + 0.05 * normal * radius[pin_disp, None])
            pc["radius"] = radius[pin_disp]
            rings = pc.glyph(geom=ring, scale="radius", orient=False, factor=1.0)
            self.actors["rings"] = self.plotter.add_mesh(rings, color=(0.15, 0.15, 0.15),
                                                         lighting=False, name="layer_rings")

        # Selection (for "Selected: up/down"): translucent yellow halo.
        if selected is not None and np.any(selected):
            sel_disp = np.nonzero(np.asarray(selected, bool)[local])[0]
            sc = pv.PolyData(atoms.cart[sel_disp])
            sc["radius"] = radius[sel_disp] * 1.6
            halo = sc.glyph(geom=pv.Sphere(radius=1.0), scale="radius", orient=False, factor=1.0)
            self.actors["selection"] = self.plotter.add_mesh(halo, color=t.selection_color, opacity=0.4,
                                                             name="layer_selection")

        # Up/down symbols: a short bar pointing along the spin axis projection is not
        # visible end-on, so use +/- text labels on the discs.
        labels = ["+" if s > 0 else "-" if s < 0 else "" for s in shown[local]]
        lab_pts = atoms.cart + 1.2 * normal * radius[:, None]
        if any(labels):
            idx = [i for i, l in enumerate(labels) if l]
            self.actors["signs"] = self.plotter.add_point_labels(
                lab_pts[idx], [labels[i] for i in idx], font_size=18, text_color="white",
                shape=None, show_points=False, always_visible=True, bold=True, name="layer_signs")

        # Nearest-neighbour bonds of the layer (home positions + bond vectors).
        g = layer.graph
        if g.m:
            starts = g.positions[g.i]
            segs = np.stack([starts, starts + g.vec], axis=1).reshape(-1, 3)
            lines = pv.PolyData()
            lines.points = segs
            n = g.m
            lines.lines = np.column_stack([np.full(n, 2), 2 * np.arange(n), 2 * np.arange(n) + 1]).ravel()
            self.actors["bonds"] = self.plotter.add_mesh(lines, color=(0.55, 0.55, 0.55),
                                                         line_width=2.0, lighting=False,
                                                         name="layer_bonds")
        # Canvas outline in the layer plane.
        k = "abc".index(axis)
        u, v = lat[(k + 1) % 3], lat[(k + 2) % 3]
        h = (layer.height / (abs(lat[k] @ normal))) * lat[k]
        corners = np.array([h, h + u, h + u + v, h + v, h])
        self.model.outline = corners
        outline = pv.lines_from_points(corners)
        self.actors["outline"] = self.plotter.add_mesh(outline, color=(0.2, 0.2, 0.2),
                                                       line_width=1.0, lighting=False,
                                                       name="layer_outline")
        if reset_camera:
            self.set_view()

    def set_view(self) -> None:
        if self.model is None:
            return
        d, up = view_basis(self.model.lattice, self.axis + "*")
        cam = self.plotter.camera
        center = self.model.center
        dist = 10.0 * float(np.linalg.norm(self.model.lattice, axis=1).max())
        cam.focal_point = tuple(center)
        cam.position = tuple(center + dist * d)
        cam.up = tuple(up)
        cam.parallel_projection = True
        w, h = self.plotter.window_size
        pts = [self.model.atoms.cart - self.model.atom_radius[:, None] * 1.5,
               self.model.atoms.cart + self.model.atom_radius[:, None] * 1.5]
        if self.model.outline is not None:
            pts.append(self.model.outline)
        pts = np.vstack(pts)
        center = 0.5 * (pts.min(axis=0) + pts.max(axis=0))
        cam.focal_point = tuple(center)
        cam.position = tuple(center + dist * d)
        cam.parallel_scale = fit_parallel_scale(pts, center, d, up, w / h, margin=1.12)
        self.plotter.renderer.ResetCameraClippingRange()
