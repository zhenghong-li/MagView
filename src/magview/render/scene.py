"""Draw a ``SceneModel`` into a pyvista plotter (onscreen or offscreen, no Qt).

``StructureScene`` owns the actors for one structure: atoms (one glyph actor),
bonds (one tube actor), unit cell, moment arrows and an a/b/c axes triad in a
corner overlay. The GUI and the offscreen renderer both use this class.

Moment edits are the hot path: ``set_moments`` rewrites the arrow mesh's points,
normals and colors in place (topology is fixed per arrow set, see arrows.py) and
refreshes the atom glyphs' colors/opacity, so a drag never rebuilds geometry.
"""

from __future__ import annotations

import logging

import numpy as np
import pyvista as pv
import vtk
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

from magview.render.arrows import (
    ArrowStyle,
    arrow_faces,
    arrow_points,
    arrow_rows,
    moment_colors,
    points_per_arrow,
)
from magview.render.camera import fit_parallel_scale, view_basis
from magview.render.model import SceneModel, magnetic_opacity
from magview.style.theme import RenderTheme

log = logging.getLogger(__name__)

DEFAULT_SPIN_AXIS = np.array([0.0, 0.0, 1.0])

# Level of detail: above these displayed-atom counts the sphere/bond resolution
# is capped (large structures are otherwise dominated by glyph tessellation).
LOD_THRESHOLDS = ((10000, 8), (2000, 12))


def lod_resolution(n_atoms: int, base: int) -> int:
    """``base`` resolution capped by the LOD thresholds for ``n_atoms`` atoms."""
    for limit, res in LOD_THRESHOLDS:
        if n_atoms > limit:
            return min(base, res)
    return base


def _to_uint8(rgb: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(np.asarray(rgb) * 255), 0, 255).astype(np.uint8)


def _set_normals(mesh: pv.PolyData, normals: np.ndarray) -> None:
    """Attach analytic per-vertex normals as the mesh's active normals attribute."""
    arr = numpy_to_vtk(np.ascontiguousarray(normals, dtype=float))
    arr.SetName("Normals")
    mesh.GetPointData().SetNormals(arr)


class StructureScene:
    def __init__(self, plotter: pv.Plotter, theme: RenderTheme, flat_shading: bool = False,
                 show_axes: bool = True, show_cell: bool = True) -> None:
        self.plotter = plotter
        self.theme = theme
        self.flat = flat_shading  # unlit colors; used by color-accuracy tests
        self.show_axes = show_axes
        self.show_cell = show_cell
        self.model: SceneModel | None = None
        self.actors: dict[str, object] = {}
        self._triad: AxesTriad | None = None
        self._view = "oblique"
        self._selection = np.zeros(0, int)
        self.arrow_style = ArrowStyle.from_theme(theme)
        self.color_mode = "sign"
        self.spin_axis = DEFAULT_SPIN_AXIS.copy()
        self._arrow_mesh: pv.PolyData | None = None
        self._arrow_rows: np.ndarray | None = None  # displayed atoms that currently have arrows
        self._arrow_resolution: int | None = None
        self._atom_sels: dict[str, np.ndarray] = {}  # atom actor name -> displayed-atom rows
        self._atom_meshes: dict[str, pv.PolyData] = {}  # strong refs (mapper holds a weakref)
        self._atom_opacity: np.ndarray | None = None  # opacity array the glyphs were built with
        self._atom_rgb_built: np.ndarray | None = None  # model.atom_rgb the glyphs show
        self._sphere_npts = 0  # vertices per sphere glyph (glyph blocks are consecutive)
        self._sphere_res = theme.sphere_resolution
        self._bond_sides = theme.bond_sides
        plotter.set_background(theme.background)
        # Moment view fades non-magnetic atoms and the selection halo is translucent;
        # depth peeling keeps overlapping translucency correct (no ghosting).
        plotter.enable_depth_peeling()
        self._setup_lighting()

    # -- lighting --------------------------------------------------------------
    def _setup_lighting(self) -> None:
        self.plotter.remove_all_lights()
        # VESTA LIGHT0: one light fixed to the viewer, shining along the view direction.
        light = pv.Light(light_type="headlight", intensity=1.0)
        self.plotter.add_light(light)

    def _material(self) -> dict:
        if self.flat:
            return dict(ambient=1.0, diffuse=0.0, specular=0.0, lighting=True)
        t = self.theme
        return dict(ambient=t.ambient, diffuse=t.diffuse, specular=t.specular,
                    specular_power=t.specular_power, smooth_shading=True)

    # -- content ----------------------------------------------------------------
    def set_model(self, model: SceneModel, reset_camera: bool = True) -> None:
        for name in list(self.actors):
            self.plotter.remove_actor(self.actors.pop(name), render=False)
        self._bond_mesh = None
        self.model = model
        t = self.theme
        n_atoms = len(model.atoms)
        self._sphere_res = lod_resolution(n_atoms, t.sphere_resolution)
        self._bond_sides = lod_resolution(n_atoms, t.bond_sides)
        if self._sphere_res != t.sphere_resolution or self._bond_sides != t.bond_sides:
            log.info("LOD: %d displayed atoms -> sphere resolution %d, bond sides %d",
                     n_atoms, self._sphere_res, self._bond_sides)

        self._build_atom_actors()

        if len(model.bond_segments):
            n = len(model.bond_segments)
            lines = pv.PolyData()
            lines.points = model.bond_segments.reshape(-1, 3)
            lines.lines = np.column_stack([np.full(n, 2), 2 * np.arange(n), 2 * np.arange(n) + 1]).ravel()
            # Point data, not cell data: cell rgb on the tube filter's triangle strips
            # renders as hairlines/garbage on some GL drivers (seen on WSLg's Mesa).
            lines.point_data["rgb"] = _to_uint8(np.repeat(model.bond_rgb, 2, axis=0))
            tubes = lines.tube(radius=t.bond_radius, n_sides=self._bond_sides, capping=False)
            self._bond_mesh = tubes  # strong ref: pyvista mappers weakref their input
            self.actors["bonds"] = self.plotter.add_mesh(
                tubes, scalars="rgb", rgb=True, name="bonds", **self._material())

        if self.show_cell:
            edges = pv.PolyData()
            edges.points = model.cell_edges.reshape(-1, 3)
            edges.lines = np.column_stack([np.full(12, 2), 2 * np.arange(12),
                                           2 * np.arange(12) + 1]).ravel()
            self.actors["cell"] = self.plotter.add_mesh(
                edges, color=t.cell_line_color, line_width=t.cell_line_width, lighting=False,
                name="cell")
        if model.grid_edges is not None and len(model.grid_edges):
            k = len(model.grid_edges)
            grid = pv.PolyData()
            grid.points = model.grid_edges.reshape(-1, 3)
            grid.lines = np.column_stack([np.full(k, 2), 2 * np.arange(k), 2 * np.arange(k) + 1]).ravel()
            self.actors["grid"] = self.plotter.add_mesh(
                grid, color=(0.72, 0.72, 0.72), line_width=1.0, lighting=False, name="grid")

        self._arrow_mesh = None
        self._arrow_rows = None
        self.set_moments(model.moments)
        self._selection: np.ndarray = np.zeros(0, int)

        if self.show_axes:
            if self._triad is None:
                self._triad = AxesTriad(self.plotter, t)
            self._triad.set_lattice(model.lattice)
        if reset_camera:
            self.set_view(self._view)

    def _build_atom_actors(self) -> None:
        """(Re)create the per-opacity-level atom glyph actors from the model."""
        for name in [n for n in self.actors if n == "atoms" or n.startswith("atoms_")]:
            self.plotter.remove_actor(self.actors.pop(name), render=False)
        self._atom_sels = {}
        self._atom_meshes = {}
        model = self.model
        if len(model.atoms) == 0:
            self._atom_opacity = None
            self._atom_rgb_built = None
            return
        opacity = model.atom_opacity if model.atom_opacity is not None else np.ones(len(model.atoms))
        sphere = pv.Sphere(radius=1.0, theta_resolution=self._sphere_res,
                           phi_resolution=self._sphere_res)
        self._sphere_npts = sphere.n_points
        # One actor per opacity level (VTK sorts translucent geometry per actor).
        for level in np.unique(opacity)[::-1]:
            sel = np.nonzero(opacity == level)[0]
            cloud = pv.PolyData(model.atoms.cart[sel])
            cloud["radius"] = model.atom_radius[sel]
            cloud["rgb"] = _to_uint8(model.atom_rgb[sel])
            glyphs = cloud.glyph(geom=sphere, scale="radius", orient=False, factor=1.0)
            # Scaling is done; keep rgb/normals but don't carry the radius per vertex.
            del glyphs.point_data["radius"]
            name = "atoms" if level == 1.0 else f"atoms_{level:.2f}"
            self.actors[name] = self.plotter.add_mesh(
                glyphs, scalars="rgb", rgb=True, name=name, opacity=float(level),
                **self._material())
            self._atom_sels[name] = sel
            self._atom_meshes[name] = glyphs
        self._atom_opacity = opacity.copy()
        self._atom_rgb_built = model.atom_rgb.copy()

    def _sync_atom_actors(self) -> None:
        """Refresh atom glyph colors/opacity after a moment-only edit.

        Colors are rewritten in place (and skipped when unchanged); the
        per-level glyph actors are regrouped only when the set of faded atoms
        changed (an atom gained/lost its moment).
        """
        model = self.model
        if model is None or not self._atom_sels:
            return
        if model.atom_opacity is not None:
            opacity = magnetic_opacity(model.moments, self.arrow_style.scale, len(model.atoms),
                                       self.theme.moment_view_nonmagnetic_opacity)
            if not np.array_equal(opacity, self._atom_opacity):
                model.atom_opacity = opacity
                self._build_atom_actors()
                return
        if np.array_equal(model.atom_rgb, self._atom_rgb_built):
            return
        for name, sel in self._atom_sels.items():
            mesh = self._atom_meshes[name]
            rgb = np.repeat(_to_uint8(model.atom_rgb[sel]), self._sphere_npts, axis=0)
            mesh.point_data["rgb"][:] = rgb
            mesh.GetPointData().GetArray("rgb").Modified()
            mesh.Modified()
        self._atom_rgb_built = model.atom_rgb.copy()

    def set_moments(self, moments: np.ndarray | None, spin_axis: np.ndarray | None = None,
                    style: ArrowStyle | None = None) -> None:
        """Set per-displayed-atom Cartesian moments (mu_B).

        True in-place update: when the set of atoms with arrows is unchanged,
        the existing arrow mesh's points, normals and colors are rewritten
        without rebuilding geometry (the drag fast path); atom glyph colors and
        moment-view opacity are refreshed as well. Raises ValueError if
        ``moments`` is not shaped (n_displayed_atoms, 3).
        """
        if spin_axis is not None:
            self.spin_axis = np.asarray(spin_axis, dtype=float)
        if style is not None:
            self.arrow_style = style
        if self.model is None:
            return
        if moments is None:
            self.model.moments = None
            self._remove_arrows()
            self._sync_atom_actors()
            return
        moments = np.asarray(moments, dtype=float)
        n = len(self.model.atoms)
        if moments.shape != (n, 3):
            raise ValueError(f"moments must be ({n}, 3), got {moments.shape}")
        self.model.moments = moments
        self._sync_atom_actors()
        rows = arrow_rows(moments, self.arrow_style.scale)
        if len(rows) == 0:
            self._remove_arrows()
            return
        if (self._arrow_mesh is not None and self._arrow_rows is not None
                and self._arrow_resolution == self.arrow_style.resolution
                and np.array_equal(rows, self._arrow_rows)):
            self._rewrite_arrows(moments, rows)
        else:
            self._rebuild_arrows(moments, rows)

    def update_moments(self, moments: np.ndarray) -> None:
        """Alias of ``set_moments`` kept for the GUI's drag callbacks."""
        self.set_moments(moments)

    def _remove_arrows(self) -> None:
        if "arrows" in self.actors:
            self.plotter.remove_actor(self.actors.pop("arrows"), render=False)
        self._arrow_mesh = None
        self._arrow_rows = None
        self._arrow_resolution = None

    def _arrow_rgb(self, moments: np.ndarray, rows: np.ndarray) -> np.ndarray:
        """Per-arrow uint8 colors, (len(rows), 3)."""
        rgb = moment_colors(self.color_mode, moments[rows], self.spin_axis, self.theme,
                            self.model.atom_rgb[rows])
        return _to_uint8(rgb)

    def _rebuild_arrows(self, moments: np.ndarray, rows: np.ndarray) -> None:
        self._remove_arrows()
        res = self.arrow_style.resolution
        points, normals = arrow_points(self.model.atoms.cart[rows], moments[rows],
                                       self.arrow_style)
        mesh = pv.PolyData(points, arrow_faces(len(rows), res))
        mesh["rgb"] = np.repeat(self._arrow_rgb(moments, rows), points_per_arrow(res), axis=0)
        _set_normals(mesh, normals)
        self._arrow_mesh = mesh
        self._arrow_rows = rows
        self._arrow_resolution = res
        self.actors["arrows"] = self.plotter.add_mesh(
            mesh, scalars="rgb", rgb=True, name="arrows", **self._material())

    def _rewrite_arrows(self, moments: np.ndarray, rows: np.ndarray) -> None:
        """In-place drag update: same topology, new coordinates, normals, colors."""
        mesh = self._arrow_mesh
        points, normals = arrow_points(self.model.atoms.cart[rows], moments[rows],
                                       self.arrow_style)
        mesh.points[:] = points
        mesh.GetPoints().Modified()
        vtk_to_numpy(mesh.GetPointData().GetNormals())[:] = normals
        mesh.GetPointData().GetNormals().Modified()
        per = points_per_arrow(self.arrow_style.resolution)
        # Broadcast per-arrow colors into the per-vertex array (no big repeat/copy).
        mesh.point_data["rgb"].reshape(len(rows), per, 3)[:] = self._arrow_rgb(moments, rows)[:, None, :]
        mesh.GetPointData().GetArray("rgb").Modified()
        mesh.Modified()

    def set_visible(self, on: bool) -> None:
        """Show/hide every actor of this scene (the axes triad stays)."""
        for actor in self.actors.values():
            actor.SetVisibility(bool(on))

    def set_selection(self, display_indices) -> None:
        """Translucent halo plus an opaque wireframe rim around the given atoms."""
        for name in ("selection", "selection_edge"):
            if name in self.actors:
                self.plotter.remove_actor(self.actors.pop(name), render=False)
        idx = np.asarray(sorted(set(int(i) for i in display_indices)), dtype=int)
        self._selection = idx
        if self.model is None or len(idx) == 0:
            return
        cloud = pv.PolyData(self.model.atoms.cart[idx])
        cloud["radius"] = self.model.atom_radius[idx] * 1.35 + 0.08
        sphere = pv.Sphere(radius=1.0, theta_resolution=24, phi_resolution=12)
        halo = cloud.glyph(geom=sphere, scale="radius", orient=False, factor=1.0)
        del halo.point_data["radius"]
        self.actors["selection"] = self.plotter.add_mesh(
            halo, color=self.theme.selection_color, opacity=0.45, name="selection",
            ambient=0.5, diffuse=0.5, specular=0.0, smooth_shading=True)
        # The translucent fill alone washes out on a light background; the opaque
        # contrasting wireframe rim keeps the selection visible.
        self.actors["selection_edge"] = self.plotter.add_mesh(
            halo, style="wireframe", color=self.theme.selection_edge_color, opacity=1.0,
            line_width=2.0, lighting=False, name="selection_edge")

    @property
    def selection(self) -> np.ndarray:
        return self._selection

    # -- camera -----------------------------------------------------------------
    def set_view(self, view: str = "oblique") -> None:
        if self.model is None:
            return
        self._view = view
        d, up = view_basis(self.model.lattice, view)
        center = self.model.center
        cam = self.plotter.camera
        dist = 10.0 * float(np.linalg.norm(self.model.lattice, axis=1).max())
        cam.focal_point = tuple(center)
        cam.position = tuple(center + dist * d)
        cam.up = tuple(up)
        cam.parallel_projection = self.theme.parallel_projection
        w, h = self.plotter.window_size
        pts = self.model.all_points()
        if self.model.moments is not None:
            m = np.where(np.isfinite(self.model.moments), self.model.moments, 0.0)
            half = 0.5 * np.linalg.norm(m, axis=1, keepdims=True) * self.arrow_style.scale
            m_dir = np.divide(m, np.linalg.norm(m, axis=1, keepdims=True),
                              out=np.zeros_like(m), where=half > 0)
            pts = np.vstack([pts, self.model.atoms.cart + half * m_dir, self.model.atoms.cart - half * m_dir])
        cam.parallel_scale = fit_parallel_scale(pts, center, d, up, w / h)
        if not self.theme.parallel_projection:
            self.plotter.reset_camera()
        self.plotter.renderer.ResetCameraClippingRange()


class AxesTriad:
    """a/b/c arrows in a bottom-left overlay whose camera follows the main camera."""

    def __init__(self, plotter: pv.Plotter, theme: RenderTheme, size: float = 0.2) -> None:
        self.plotter = plotter
        self.theme = theme
        self.renderer = vtk.vtkRenderer()
        self.renderer.SetViewport(0.0, 0.0, size, size)
        self.renderer.SetLayer(1)
        self.renderer.InteractiveOff()
        self.renderer.GetActiveCamera().ParallelProjectionOn()
        rw = plotter.render_window
        rw.SetNumberOfLayers(max(2, rw.GetNumberOfLayers()))
        rw.AddRenderer(self.renderer)
        plotter.renderer.AddObserver("StartEvent", self._sync)
        self._actors: list[vtk.vtkProp] = []

    def set_lattice(self, lattice: np.ndarray) -> None:
        for a in self._actors:
            self.renderer.RemoveViewProp(a)
        self._actors = []
        for i, (vec, name) in enumerate(zip(lattice, "abc")):
            u = vec / np.linalg.norm(vec)
            arrow = pv.Arrow(start=(0, 0, 0), direction=u, scale=1.0, tip_length=0.25,
                             tip_radius=0.08, shaft_radius=0.03)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(arrow)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(*self.theme.axis_colors[i])
            prop.SetAmbient(0.3)
            prop.SetDiffuse(0.7)
            self.renderer.AddActor(actor)
            label = vtk.vtkBillboardTextActor3D()
            label.SetInput(name)
            label.SetPosition(*(1.28 * u))
            tp = label.GetTextProperty()
            tp.SetFontSize(18)
            tp.SetBold(True)
            tp.SetColor(*self.theme.axis_colors[i])
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToCentered()
            self.renderer.AddActor(label)
            self._actors += [actor, label]
        light = vtk.vtkLight()
        light.SetLightTypeToHeadlight()
        self.renderer.RemoveAllLights()
        self.renderer.AddLight(light)

    def _sync(self, *_args) -> None:
        main = self.plotter.renderer.GetActiveCamera()
        cam = self.renderer.GetActiveCamera()
        pos = np.array(main.GetPosition())
        foc = np.array(main.GetFocalPoint())
        d = pos - foc
        d /= np.linalg.norm(d)
        cam.SetFocalPoint(0.0, 0.0, 0.0)
        cam.SetPosition(*(5.0 * d))
        cam.SetViewUp(*main.GetViewUp())
        cam.SetParallelScale(1.55)
        self.renderer.ResetCameraClippingRange()
