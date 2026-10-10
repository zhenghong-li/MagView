"""Offscreen rendering: images are produced, colors are exact, geometry is right."""

import numpy as np
import pytest
import vtk

from magview.render.arrows import ArrowStyle, arrow_colors, arrow_geometry
from magview.render.camera import fit_parallel_scale, view_basis
from magview.render.model import build_scene_model
from magview.render.offscreen import render_png

pytestmark = pytest.mark.render


# --- camera (pure numpy) ----------------------------------------------------------

@pytest.mark.parametrize("view", ["a", "b", "c", "a*", "b*", "c*", "oblique"])
def test_view_basis_orthonormal(load, view):
    lat = load("Cs3V9Te13_300K").lattice_matrix
    d, up = view_basis(lat, view)
    assert np.linalg.norm(d) == pytest.approx(1) and np.linalg.norm(up) == pytest.approx(1)
    assert d @ up == pytest.approx(0, abs=1e-12)


def test_view_along_c_conventions(load):
    lat = load("Cs3V9Te13_300K").lattice_matrix
    d, up = view_basis(lat, "c")
    assert np.allclose(d, lat[2] / np.linalg.norm(lat[2]))  # c points at the viewer
    right = np.cross(up, d)
    a = lat[0] / np.linalg.norm(lat[0])
    assert np.allclose(right, a)  # a horizontal, pointing right


def test_fit_parallel_scale():
    pts = np.array([[0, 0, 0], [0, 4, 0], [2, 2, 0]], float)
    s = fit_parallel_scale(pts, np.array([0, 2, 0.0]), np.array([0, 0, 1.0]),
                           np.array([0, 1, 0.0]), aspect=1.0, margin=1.0)
    assert s == pytest.approx(2.0)


# --- arrows (pure numpy) ----------------------------------------------------------

def test_arrow_constant_thickness_and_extent():
    style = ArrowStyle(scale=1.0, shaft_radius=0.1, centered=True, resolution=12)
    origins = np.zeros((2, 3))
    moments = np.array([[0, 0, 1.0], [3.0, 0, 0]])  # 1 and 3 mu_B
    pts, nrms, faces, idx = arrow_geometry(origins, moments, style)
    n = style.resolution
    per = 6 * n + 2
    assert len(pts) == len(nrms) == 2 * per
    for k, (m, L) in enumerate(zip(moments, [1.0, 3.0])):
        p = pts[idx == k]
        d = m / np.linalg.norm(m)
        ring0 = p[:n]
        radial = ring0 - np.outer(ring0 @ d, d)
        assert np.allclose(np.linalg.norm(radial, axis=1), 0.1)  # same shaft radius
        assert np.allclose(p[5 * n], 0.5 * L * d)  # tip
        assert np.allclose(p[6 * n + 1], -0.5 * L * d)  # tail (centered on the atom)


def test_arrow_tail_anchor_and_zero_skipped():
    style = ArrowStyle(scale=0.5, centered=False, resolution=8)
    pts, nrms, faces, idx = arrow_geometry(np.array([[1.0, 2, 3], [0, 0, 0]]),
                                           np.array([[0, 0, 0.0], [0, 4.0, 0]]), style)
    assert set(idx) == {1}
    assert np.allclose(pts[5 * 8], [0, 2.0, 0])  # tip = origin + 4 * 0.5
    assert np.allclose(pts[6 * 8 + 1], [0, 0, 0])


def test_arrow_mesh_closed_and_outward():
    import pyvista as pv

    pts, nrms, faces, _ = arrow_geometry(np.zeros((1, 3)), np.array([[0.3, -0.2, 2.0]]),
                                         ArrowStyle(resolution=16))
    # Vertices on sharp edges are duplicated (one normal each); merge them to
    # check the underlying surface is watertight and wound outward.
    mesh = pv.PolyData(pts, faces).triangulate().clean()
    assert mesh.n_open_edges == 0
    assert mesh.volume > 0
    oriented = mesh.compute_normals(auto_orient_normals=True, cell_normals=True, point_normals=False)
    raw = mesh.compute_normals(cell_normals=True, point_normals=False)
    agree = np.einsum("ij,ij->i", oriented.cell_data["Normals"], raw.cell_data["Normals"])
    assert np.all(agree > 0)  # our winding already points outward


def test_arrow_analytic_normals_known_arrow():
    """Finding-1 regression: analytic normals on a known arrow, no VTK filter."""
    style = ArrowStyle(scale=1.0, shaft_radius=0.1, resolution=16)
    pts, nrms, faces, idx = arrow_geometry(np.zeros((1, 3)), np.array([[0.0, 0, 2.0]]), style)
    n = style.resolution
    assert pts.shape == (6 * n + 2, 3) and nrms.shape == pts.shape
    assert np.allclose(np.linalg.norm(nrms, axis=1), 1.0)
    d = np.array([0.0, 0.0, 1.0])
    radial_unit = lambda p: (lambda r: r / np.linalg.norm(r, axis=1, keepdims=True))(  # noqa: E731
        p - np.outer(p @ d, d))
    # shaft rings: radial normals, perpendicular to the arrow axis
    assert np.allclose(nrms[:2 * n], radial_unit(pts[:2 * n]))
    # head underside annulus and bottom cap: flat, pointing back along -d
    assert np.allclose(nrms[2 * n:4 * n], -d)
    assert np.allclose(nrms[5 * n + 1:6 * n + 2], -d)
    # cone side: n = (h * radial + r_head * d) / hypot(h, r_head); tip: d
    h = style.head_length_ratio * 2.0
    rh = style.shaft_radius * style.head_radius_ratio
    assert np.allclose(nrms[4 * n:5 * n],
                       (h * radial_unit(pts[4 * n:5 * n]) + rh * d) / np.hypot(h, rh))
    assert np.allclose(nrms[5 * n], d)
    # every face's winding agrees with its vertex normals (all outward)
    pos = 0
    while pos < len(faces):
        k = int(faces[pos])
        ids = faces[pos + 1:pos + 1 + k]
        pos += k + 1
        p = pts[ids]
        face_n = np.cross(p[1] - p[0], p[2] - p[1])
        assert face_n @ nrms[ids].mean(axis=0) > 0


def test_arrow_faces_cached():
    from magview.render.arrows import arrow_faces

    f = arrow_faces(3, 16)
    assert f is arrow_faces(3, 16)  # cached: topology depends only on count + resolution
    other = arrow_faces(4, 16)
    assert len(other) > len(f)


def test_arrow_colors_by_sign():
    rgb = arrow_colors(np.array([[0, 0, 2.0], [0, 0, -1.0], [1.0, 0, 0]]), [0, 0, 1],
                       (1, 0, 0), (0, 0, 1), (0.5, 0.5, 0.5))
    assert np.allclose(rgb, [[1, 0, 0], [0, 0, 1], [0.5, 0.5, 0.5]])


# --- rendering ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["NiO", "Cs3V9Te13_300K", "Fe_bcc"])
def test_render_produces_image(load, name, tmp_path):
    out = tmp_path / f"{name}.png"
    img = render_png(load(name), out, size=(320, 240))
    assert out.is_file() and img.shape == (240, 320, 3)
    non_bg = np.any(img < 250, axis=2).mean()
    assert 0.05 < non_bg < 0.95


def _project(renderer, xyz):
    coord = vtk.vtkCoordinate()
    coord.SetCoordinateSystemToWorld()
    coord.SetValue(*map(float, xyz))
    return np.array(coord.GetComputedDoubleDisplayValue(renderer))


@pytest.mark.parametrize("name, view", [("Cs3V9Te13_300K", "c"), ("La2CuO4", "a"),
                                        ("FeSe", "oblique")])
def test_atom_colors_exact(load, appearance, name, view):
    """Flat-lit render: the pixel at each unoccluded atom center is the element color."""
    c = load(name)
    w, h = 640, 480
    img, plotter, scene = render_png(c, None, view=view, size=(w, h), flat_shading=True,
                                     show_axes=False, show_bonds=False, show_cell=False,
                                     return_scene=True)
    try:
        model = scene.model
        ren = plotter.renderer
        cam = plotter.camera
        px_per_A = (h / 2) / cam.parallel_scale
        xy = np.array([_project(ren, p) for p in model.atoms.cart])
        d = np.array(cam.position) - np.array(cam.focal_point)
        depth = model.atoms.cart @ (d / np.linalg.norm(d))
        r_px = model.atom_radius * px_per_A
        checked = 0
        for i in range(len(model.atoms)):
            dist = np.linalg.norm(xy - xy[i], axis=1)
            occluders = (depth > depth[i] + 1e-6) & (dist < r_px + 2)
            occluders[i] = False
            x, y = int(round(xy[i, 0])), int(round(xy[i, 1]))
            if occluders.any() or not (2 <= x < w - 2 and 2 <= y < h - 2):
                continue
            pixel = img[h - 1 - y, x].astype(int)
            expected = np.rint(np.array(appearance.elements[model.atom_symbols[i]].color) * 255)
            assert np.abs(pixel - expected).max() <= 2, (model.atom_symbols[i], pixel, expected)
            checked += 1
        assert checked >= 5
    finally:
        plotter.close()


def test_moment_view_arrows_render(load, appearance):
    c = load("NiO")
    m = np.zeros((len(c), 3))
    m[[i for i, s in enumerate(c.symbols) if s == "Ni"]] = [0, 0, 2.0]
    model_mv = build_scene_model(c, appearance, site_moments=m, moment_view=True)
    assert len(model_mv.bond_segments) == 0
    assert set(np.unique(model_mv.atom_opacity)) == {0.25, 1.0}
    img = render_png(c, None, view="b", size=(400, 300), site_moments=m, moment_view=True)
    red = (img[..., 0] > 150) & (img[..., 1] < 90) & (img[..., 2] < 90)
    assert red.mean() > 0.002  # spin-up arrows visible


def test_update_moments_in_place_and_rebuild(load, appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    c = load("NiO")
    m = np.zeros((len(c), 3))
    ni = [i for i, s in enumerate(c.symbols) if s == "Ni"]
    m[ni] = [0, 0, 2.0]
    model = build_scene_model(c, appearance, site_moments=m, moment_view=True)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        mesh = scene._arrow_mesh
        before = np.array(mesh.points)
        disp = model.moments.copy()
        disp[model.moments[:, 2] > 0] *= -1  # flip all arrows: same atoms, new geometry
        scene.update_moments(disp)
        assert scene._arrow_mesh is mesh  # in place
        assert not np.allclose(mesh.points, before)
        disp[0] = 0.0 if np.any(model.moments[0]) else [0, 0, 1.0]  # arrow set changes
        scene.update_moments(disp)
        assert scene._arrow_mesh is not mesh  # rebuilt
    finally:
        p.close()


@pytest.mark.parametrize("mode", ["sign", "direction", "element", "uniform"])
def test_color_modes(mode, appearance):
    from magview.render.arrows import moment_colors

    m = np.array([[0, 0, 1.0], [0, 0, -1.0], [1.0, 0, 0], [0, 1.0, 0]])
    rgb = moment_colors(mode, m, [0, 0, 1], appearance.theme, atom_rgb=np.full((4, 3), 0.5))
    assert rgb.shape == (4, 3) and np.all((rgb >= 0) & (rgb <= 1))
    if mode == "sign":
        assert np.allclose(rgb[0], appearance.theme.spin_up_color)
    if mode == "direction":
        assert not np.allclose(rgb[2], rgb[3])  # different azimuths -> different hues
        assert rgb[0].sum() > rgb[1].sum()  # up lighter than down


def test_moment_view_desaturates_atoms(load, appearance):
    c = load("Cs3V9Te13_300K")
    plain = build_scene_model(c, appearance, show_bonds=False)
    mv = build_scene_model(c, appearance, moment_view=True)
    v = plain.atom_symbols.index("V")
    sat = lambda rgb: rgb.max() - rgb.min()  # noqa: E731
    assert sat(mv.atom_rgb[v]) < 0.6 * sat(plain.atom_rgb[v])


def _nio_moment_model(load, appearance):
    """NiO moment-view model with all Ni moments along +c."""
    c = load("NiO")
    m = np.zeros((len(c), 3))
    m[[i for i, s in enumerate(c.symbols) if s == "Ni"]] = [0, 0, 2.0]
    return build_scene_model(c, appearance, site_moments=m, moment_view=True)


def _synthetic_model(n_atoms: int):
    """Minimal SceneModel: Fe atoms on a 3 A grid, no bonds, no moments."""
    from magview.core.bonds import BondSet
    from magview.core.images import DisplayAtoms
    from magview.render.model import SceneModel, cell_edges

    side = int(np.ceil(n_atoms ** (1 / 3)))
    cart = np.indices((side, side, side)).reshape(3, -1).T[:n_atoms] * 3.0
    lat = np.eye(3) * (side * 3.0 + 2.0)
    atoms = DisplayAtoms(np.arange(n_atoms), np.zeros((n_atoms, 3), int),
                         (cart + 1.0) / lat.diagonal(), cart)
    bonds = BondSet(atoms, np.zeros((0, 2), int), np.zeros(0), np.zeros(0, int), n_atoms)
    return SceneModel(lat, atoms, ["Fe"] * n_atoms, np.full(n_atoms, 0.6),
                      np.tile([0.7, 0.4, 0.1], (n_atoms, 1)), bonds,
                      np.zeros((0, 2, 3)), np.zeros((0, 3)), cell_edges(lat))


# --- in-place moment updates (the set_moments contract) ---------------------------

def test_set_moments_in_place_matches_rebuild(load, appearance):
    """The drag fast path produces exactly the rebuilt mesh, without rebuilding it."""
    from vtk.util.numpy_support import vtk_to_numpy

    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = _nio_moment_model(load, appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        mesh = scene._arrow_mesh
        flipped = model.moments.copy()
        flipped[model.moments[:, 2] > 0] *= -1.3  # same arrow set, new geometry + colors
        scene.set_moments(flipped)
        assert scene._arrow_mesh is mesh
        # reference: a fresh scene rebuilt from the same moments (model.moments is updated)
        p2 = make_plotter((300, 200))
        try:
            ref = StructureScene(p2, appearance.theme)
            ref.set_model(model)
            rmesh = ref._arrow_mesh
            assert np.allclose(mesh.points, rmesh.points)
            assert np.allclose(vtk_to_numpy(mesh.GetPointData().GetNormals()),
                               vtk_to_numpy(rmesh.GetPointData().GetNormals()))
            assert np.array_equal(mesh.point_data["rgb"], rmesh.point_data["rgb"])
        finally:
            p2.close()
    finally:
        p.close()


def test_set_moments_rebuilds_on_resolution_change(load, appearance):
    from dataclasses import replace

    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = _nio_moment_model(load, appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        mesh = scene._arrow_mesh
        scene.set_moments(model.moments, style=replace(scene.arrow_style, resolution=8))
        assert scene._arrow_mesh is not mesh  # topology changed: rebuilt
    finally:
        p.close()


def test_set_moments_shape_validation(load, appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = _nio_moment_model(load, appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        n = len(model.atoms)
        with pytest.raises(ValueError, match="moments must be"):
            scene.set_moments(np.zeros((n, 2)))
        with pytest.raises(ValueError, match="moments must be"):
            scene.set_moments(np.zeros(3))
        with pytest.raises(ValueError, match="moments must be"):
            scene.set_moments(np.zeros((n + 1, 3)))
    finally:
        p.close()


def test_set_moments_regroups_faded_atoms(load, appearance):
    """Moment-only edits keep the atom opacity grouping in sync with the arrows."""
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = _nio_moment_model(load, appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        assert set(scene._atom_sels) == {"atoms", "atoms_0.25"}
        scene.set_moments(np.zeros_like(model.moments))
        assert scene._arrow_mesh is None  # no arrows left
        assert set(scene._atom_sels) == {"atoms_0.25"}  # all atoms faded
        back = np.zeros_like(model.moments)
        back[0] = [0, 0, 1.0]
        scene.set_moments(back)
        assert scene._arrow_mesh is not None
        assert set(scene._atom_sels) == {"atoms", "atoms_0.25"}
    finally:
        p.close()


# --- glyph memory (finding 2) ------------------------------------------------------

def test_glyph_point_data_stripped(load, appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = build_scene_model(load("NiO"), appearance)
    p = make_plotter((64, 48))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        assert scene._sphere_res == appearance.theme.sphere_resolution  # 57 atoms: no LOD
        for name in scene._atom_sels:
            mesh = scene._atom_meshes[name]
            assert "display_index" not in mesh.point_data
            assert "radius" not in mesh.point_data
            assert "rgb" in mesh.point_data and "Normals" in mesh.point_data
    finally:
        p.close()


def test_lod_thresholds():
    from magview.render.scene import lod_resolution

    assert lod_resolution(10, 24) == 24
    assert lod_resolution(2000, 24) == 24
    assert lod_resolution(2001, 24) == 12
    assert lod_resolution(10001, 24) == 8
    assert lod_resolution(2001, 16) == 12 and lod_resolution(10001, 16) == 8


def test_lod_triggers_for_large_structures(caplog):
    import logging

    import pyvista as pv

    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene
    from magview.style.theme import RenderTheme

    model = _synthetic_model(2001)
    p = make_plotter((64, 48))
    try:
        scene = StructureScene(p, RenderTheme())
        with caplog.at_level(logging.INFO, logger="magview.render.scene"):
            scene.set_model(model)
        assert scene._sphere_res == 12 and scene._bond_sides == 12
        assert "LOD" in caplog.text
        mesh = scene._atom_meshes["atoms"]
        sphere12 = pv.Sphere(radius=1.0, theta_resolution=12, phi_resolution=12).n_points
        assert mesh.n_points == 2001 * sphere12
        assert "radius" not in mesh.point_data
    finally:
        p.close()


# --- translucency and selection (findings 3 and 4) ---------------------------------

def test_depth_peeling_enabled(appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    p = make_plotter((64, 48))
    try:
        assert p.renderer.GetUseDepthPeeling()  # make_plotter
        StructureScene(p, appearance.theme)
        assert p.renderer.GetUseDepthPeeling()
    finally:
        p.close()


def test_selection_has_opaque_wireframe_rim(load, appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = build_scene_model(load("NiO"), appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        scene.set_selection([0, 1])
        assert {"selection", "selection_edge"} <= set(scene.actors)
        halo = scene.actors["selection"].GetProperty()
        assert halo.GetOpacity() == pytest.approx(0.45)  # translucent fill kept
        edge = scene.actors["selection_edge"].GetProperty()
        assert edge.GetRepresentation() == 1  # VTK wireframe
        assert edge.GetOpacity() == 1.0
        assert edge.GetColor() == pytest.approx(appearance.theme.selection_edge_color,
                                                abs=1 / 255 + 1e-6)
    finally:
        p.close()


# --- minor findings -----------------------------------------------------------------

def test_pick_sphere_near_plane_clipped():
    """Atoms intersected by the near clipping plane stay pickable (t_mid may be < 0)."""
    from magview.render.picking import pick_sphere

    origin, direction = np.zeros(3), np.array([0.0, 0, 1])
    assert pick_sphere(origin, direction, np.array([[0, 0, -0.3]]), [1.0]) == 0
    assert pick_sphere(origin, direction, np.array([[0, 0, -5.0]]), [1.0]) is None
    hit = pick_sphere(origin, direction, np.array([[0, 0, -0.3], [0, 0, 3.0]]), [1.0, 1.0])
    assert hit == 0  # the clipped (closer) sphere wins


def test_direction_colors_match_colorsys():
    import colorsys

    from magview.render.arrows import direction_colors

    rng = np.random.default_rng(7)
    m = rng.normal(size=(64, 3))
    m[0] = 0.0
    axis = np.array([0.3, -0.2, 1.0])
    got = direction_colors(m, axis)
    u = axis / np.linalg.norm(axis)
    helper = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(u, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    ref = np.zeros((len(m), 3))
    for k, v in enumerate(m):
        nrm = np.linalg.norm(v)
        if nrm == 0:
            continue
        v = v / nrm
        hue = (np.arctan2(v @ e2, v @ e1) / (2 * np.pi)) % 1.0
        ref[k] = colorsys.hls_to_rgb(hue, 0.5 + 0.3 * (v @ u),
                                     0.35 + 0.6 * np.sqrt(max(0.0, 1 - (v @ u) ** 2)))
    assert np.allclose(got, ref, atol=1e-12)


def test_has_moment_matches_arrow_threshold(load, appearance):
    """The atom fade and the arrow drop share one threshold: norm * scale > MIN_LENGTH."""
    from magview.render.arrows import MIN_LENGTH, arrow_rows

    c = load("NiO")
    scale = appearance.theme.vector_scale
    m = np.zeros((len(c), 3))
    m[0] = [0, 0, 0.5 * MIN_LENGTH / scale]  # below the draw threshold
    model = build_scene_model(c, appearance, site_moments=m, moment_view=True)
    assert len(arrow_rows(model.moments, scale)) == 0
    assert set(np.unique(model.atom_opacity)) == {appearance.theme.moment_view_nonmagnetic_opacity}
    m[0] = [0, 0, 2 * MIN_LENGTH / scale]
    model = build_scene_model(c, appearance, site_moments=m, moment_view=True)
    assert len(arrow_rows(model.moments, scale)) > 0
    assert 1.0 in model.atom_opacity


def test_set_view_uses_current_arrow_style(load, appearance):
    """Camera fit must follow the live arrow style, not a fresh one from the theme."""
    from dataclasses import replace

    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    model = _nio_moment_model(load, appearance)
    p = make_plotter((300, 200))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        scene.arrow_style = replace(scene.arrow_style, scale=8.0)
        scene.set_view("oblique")
        big = p.camera.parallel_scale
        scene.arrow_style = replace(scene.arrow_style, scale=0.5)
        scene.set_view("oblique")
        assert p.camera.parallel_scale < big
    finally:
        p.close()


def test_set_view_survives_nan_moments(load, appearance):
    from magview.render.offscreen import make_plotter
    from magview.render.scene import StructureScene

    c = load("NiO")
    m = np.zeros((len(c), 3))
    m[0] = np.nan
    model = build_scene_model(c, appearance, site_moments=m, moment_view=True)
    p = make_plotter((64, 48))
    try:
        scene = StructureScene(p, appearance.theme)
        scene.set_model(model)
        scene.set_view("oblique")
        assert np.isfinite(p.camera.parallel_scale)
        assert scene._arrow_mesh is None  # a NaN moment draws no arrow
    finally:
        p.close()


def test_mode_border_is_four_quads():
    """The mode border is drawn as quads (wide glLineWidth is unreliable)."""
    from magview.render.offscreen import make_plotter
    from magview.render.overlay import ModeOverlay

    p = make_plotter((200, 100))
    try:
        ov = ModeOverlay(p.renderer)
        ov.show("moment mode", (0.9, 0.5, 0.1))
        poly = ov._border_poly
        assert poly.GetPolys().GetNumberOfCells() == 4
        assert poly.GetNumberOfPoints() == 16
        assert poly.GetLines().GetNumberOfCells() == 0
    finally:
        p.close()


def test_per_atom_styles_vectorized_correctly(load, appearance):
    """np.unique/return_inverse expansion must reproduce the per-atom lookups."""
    model = build_scene_model(load("Cs3V9Te13_300K"), appearance)
    scale = appearance.theme.ball_scale
    for i, sym in enumerate(model.atom_symbols):
        st = appearance.elements[sym]
        assert model.atom_radius[i] == pytest.approx(st.radius * scale)
        assert model.atom_rgb[i] == pytest.approx(st.color)


def test_configure_offscreen_only_without_display(monkeypatch):
    import pyvista as pv

    from magview.render import offscreen

    monkeypatch.setattr(pv, "OFF_SCREEN", False)
    monkeypatch.setenv("DISPLAY", ":99")
    offscreen.configure_offscreen()
    assert pv.OFF_SCREEN is False  # display present: pyvista's default untouched
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    offscreen.configure_offscreen()
    assert pv.OFF_SCREEN is True
