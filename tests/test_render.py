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
    pts, faces, idx = arrow_geometry(origins, moments, style)
    n = style.resolution
    per = 3 * n + 2
    assert len(pts) == 2 * per
    for k, (m, L) in enumerate(zip(moments, [1.0, 3.0])):
        p = pts[idx == k]
        d = m / np.linalg.norm(m)
        ring0 = p[:n]
        radial = ring0 - np.outer(ring0 @ d, d)
        assert np.allclose(np.linalg.norm(radial, axis=1), 0.1)  # same shaft radius
        assert np.allclose(p[3 * n], 0.5 * L * d)  # tip
        assert np.allclose(p[3 * n + 1], -0.5 * L * d)  # tail (centered on the atom)


def test_arrow_tail_anchor_and_zero_skipped():
    style = ArrowStyle(scale=0.5, centered=False, resolution=8)
    pts, faces, idx = arrow_geometry(np.array([[1.0, 2, 3], [0, 0, 0]]),
                                     np.array([[0, 0, 0.0], [0, 4.0, 0]]), style)
    assert set(idx) == {1}
    assert np.allclose(pts[3 * 8], [0, 2.0, 0])  # tip = origin + 4 * 0.5
    assert np.allclose(pts[3 * 8 + 1], [0, 0, 0])


def test_arrow_mesh_closed_and_outward():
    import pyvista as pv

    pts, faces, _ = arrow_geometry(np.zeros((1, 3)), np.array([[0.3, -0.2, 2.0]]),
                                   ArrowStyle(resolution=16))
    mesh = pv.PolyData(pts, faces).triangulate()
    assert mesh.n_open_edges == 0
    assert mesh.volume > 0
    oriented = mesh.compute_normals(auto_orient_normals=True, cell_normals=True, point_normals=False)
    raw = mesh.compute_normals(cell_normals=True, point_normals=False)
    agree = np.einsum("ij,ij->i", oriented.cell_data["Normals"], raw.cell_data["Normals"])
    assert np.all(agree > 0)  # our winding already points outward


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
