"""Draw a ``SceneModel`` into a pyvista plotter (onscreen or offscreen, no Qt).

``StructureScene`` owns the actors for one structure: atoms (one glyph actor),
bonds (one tube actor), unit cell, moment arrows and an a/b/c axes triad in a
corner overlay. The GUI and the offscreen renderer both use this class.
"""

from __future__ import annotations

import numpy as np
import pyvista as pv
import vtk

from magview.render.arrows import ArrowStyle, arrow_geometry, moment_colors
from magview.render.camera import fit_parallel_scale, view_basis
from magview.render.model import SceneModel
from magview.style.theme import RenderTheme

DEFAULT_SPIN_AXIS = np.array([0.0, 0.0, 1.0])


def _to_uint8(rgb: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(np.asarray(rgb) * 255), 0, 255).astype(np.uint8)


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
        plotter.set_background(theme.background)
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
        self.model = model
        t = self.theme

        if len(model.atoms):
            opacity = model.atom_opacity if model.atom_opacity is not None else np.ones(len(model.atoms))
            sphere = pv.Sphere(radius=1.0, theta_resolution=t.sphere_resolution * 2,
                               phi_resolution=t.sphere_resolution)
            # One actor per opacity level (VTK sorts translucent geometry per actor).
            for level in np.unique(opacity)[::-1]:
                sel = np.nonzero(opacity == level)[0]
                cloud = pv.PolyData(model.atoms.cart[sel])
                cloud["radius"] = model.atom_radius[sel]
                cloud["rgb"] = _to_uint8(model.atom_rgb[sel])
                cloud["display_index"] = sel
                glyphs = cloud.glyph(geom=sphere, scale="radius", orient=False, factor=1.0)
                name = "atoms" if level == 1.0 else f"atoms_{level:.2f}"
                self.actors[name] = self.plotter.add_mesh(
                    glyphs, scalars="rgb", rgb=True, name=name, opacity=float(level),
                    **self._material())

        if len(model.bond_segments):
            n = len(model.bond_segments)
            lines = pv.PolyData()
            lines.points = model.bond_segments.reshape(-1, 3)
            lines.lines = np.column_stack([np.full(n, 2), 2 * np.arange(n), 2 * np.arange(n) + 1]).ravel()
            lines.cell_data["rgb"] = _to_uint8(model.bond_rgb)
            tubes = lines.tube(radius=t.bond_radius, n_sides=t.bond_sides, capping=False)
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

        self.set_moments(model.moments)
        self._selection: np.ndarray = np.zeros(0, int)

        if self.show_axes:
            if self._triad is None:
                self._triad = AxesTriad(self.plotter, t)
            self._triad.set_lattice(model.lattice)
        if reset_camera:
            self.set_view(self._view)

    def set_moments(self, moments: np.ndarray | None, spin_axis: np.ndarray | None = None,
                    style: ArrowStyle | None = None) -> None:
        """(Re)draw arrows for per-displayed-atom Cartesian moments (mu_B)."""
        if spin_axis is not None:
            self.spin_axis = np.asarray(spin_axis, dtype=float)
        if style is not None:
            self.arrow_style = style
        if "arrows" in self.actors:
            self.plotter.remove_actor(self.actors.pop("arrows"), render=False)
        self._arrow_mesh = None
        self._arrow_rows = None
        if self.model is None or moments is None:
            return
        self.model.moments = np.asarray(moments, dtype=float)
        mesh = self._arrow_polydata(self.model.moments)
        if mesh is None:
            return
        self._arrow_mesh = mesh
        self.actors["arrows"] = self.plotter.add_mesh(
            mesh, scalars="rgb", rgb=True, name="arrows", **self._material())

    def _arrow_polydata(self, moments: np.ndarray) -> pv.PolyData | None:
        points, faces, idx = arrow_geometry(self.model.atoms.cart, moments, self.arrow_style)
        if len(points) == 0:
            return None
        rgb = moment_colors(self.color_mode, moments, self.spin_axis, self.theme, self.model.atom_rgb)
        mesh = pv.PolyData(points, faces)
        mesh["rgb"] = _to_uint8(rgb[idx])
        self._arrow_rows = np.unique(idx)
        return mesh.compute_normals(split_vertices=True, feature_angle=50.0, auto_orient_normals=False)

    def update_moments(self, moments: np.ndarray) -> None:
        """Fast path for live dragging: rewrite the arrow mesh in place when the set of
        atoms with arrows is unchanged; otherwise rebuild the actor."""
        moments = np.asarray(moments, dtype=float)
        if self.model is None:
            return
        rows = np.nonzero(np.linalg.norm(moments, axis=1) * self.arrow_style.scale > 1e-6)[0]
        if self._arrow_mesh is None or self._arrow_rows is None or not np.array_equal(rows, self._arrow_rows):
            self.set_moments(moments)
            return
        self.model.moments = moments
        new = self._arrow_polydata(moments)
        self._arrow_mesh.copy_from(new)

    def set_visible(self, on: bool) -> None:
        """Show/hide every actor of this scene (the axes triad stays)."""
        for actor in self.actors.values():
            actor.SetVisibility(bool(on))

    def set_selection(self, display_indices) -> None:
        """Translucent halo around the given displayed atoms."""
        if "selection" in self.actors:
            self.plotter.remove_actor(self.actors.pop("selection"), render=False)
        idx = np.asarray(sorted(set(int(i) for i in display_indices)), dtype=int)
        self._selection = idx
        if self.model is None or len(idx) == 0:
            return
        cloud = pv.PolyData(self.model.atoms.cart[idx])
        cloud["radius"] = self.model.atom_radius[idx] * 1.35 + 0.08
        sphere = pv.Sphere(radius=1.0, theta_resolution=24, phi_resolution=12)
        halo = cloud.glyph(geom=sphere, scale="radius", orient=False, factor=1.0)
        self.actors["selection"] = self.plotter.add_mesh(
            halo, color=self.theme.selection_color, opacity=0.45, name="selection",
            ambient=0.5, diffuse=0.5, specular=0.0, smooth_shading=True)

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
            style = ArrowStyle.from_theme(self.theme)
            half = 0.5 * np.linalg.norm(self.model.moments, axis=1, keepdims=True) * style.scale
            m_dir = np.divide(self.model.moments, np.linalg.norm(self.model.moments, axis=1, keepdims=True),
                              out=np.zeros_like(self.model.moments), where=half > 0)
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
