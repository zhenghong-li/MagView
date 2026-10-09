"""Analytic picking and screen projection against a real (offscreen) camera."""

import numpy as np
import pytest
import vtk

from magview.core.canvas import Canvas
from magview.core import interaction as ia
from magview.core.selection import apply_selection
from magview.render.overlay import BoxOverlay
from magview.render.picking import (
    atoms_in_box,
    display_ray,
    pick_atom,
    pick_sphere,
    project_to_display,
)
from magview.render.offscreen import render_png

pytestmark = pytest.mark.render


def test_pick_sphere_nearest_hit():
    centers = np.array([[0, 0, 5.0], [0, 0, 2.0], [3, 0, 0]])
    radii = np.array([1.0, 1.0, 1.0])
    assert pick_sphere(np.zeros(3), np.array([0, 0, 1.0]), centers, radii) == 1
    assert pick_sphere(np.zeros(3), np.array([0, 0, -1.0]), centers, radii) is None  # behind
    assert pick_sphere(np.array([0, 0.99, 0]), np.array([0, 0, 1.0]), centers, radii) == 1
    assert pick_sphere(np.array([0, 1.01, 0]), np.array([0, 0, 1.0]), centers, radii) is None


@pytest.fixture
def scene_nio(load):
    canvas = Canvas.build(load("NiO"))
    img, plotter, scene = render_png(canvas.structure, None, size=(640, 480), return_scene=True)
    yield plotter, scene
    plotter.close()


def test_projection_matches_vtk(scene_nio):
    plotter, scene = scene_nio
    pts = scene.model.atoms.cart[:20]
    ours = project_to_display(plotter.renderer, pts)
    for p, xy in zip(pts, ours):
        c = vtk.vtkCoordinate()
        c.SetCoordinateSystemToWorld()
        c.SetValue(*p)
        assert np.allclose(c.GetComputedDoubleDisplayValue(plotter.renderer), xy, atol=1e-6)


def test_ray_goes_through_projected_point(scene_nio):
    plotter, scene = scene_nio
    p = scene.model.atoms.cart[5]
    x, y = project_to_display(plotter.renderer, [p])[0]
    o, d = display_ray(plotter.renderer, x, y)
    closest = o + ((p - o) @ d) * d
    assert np.linalg.norm(closest - p) < 1e-6


def test_pick_front_atoms_and_empty(scene_nio):
    plotter, scene = scene_nio
    ren, model = plotter.renderer, scene.model
    xy = project_to_display(ren, model.atoms.cart)
    cam = plotter.camera
    d = np.array(cam.position) - np.array(cam.focal_point)
    front = int(np.argmax(model.atoms.cart @ d))  # the atom nearest the viewer
    assert pick_atom(ren, model, *xy[front]) == front
    assert pick_atom(ren, model, 2, 2) is None  # empty corner


def test_box_selection(scene_nio):
    plotter, scene = scene_nio
    ren, model = plotter.renderer, scene.model
    xy = project_to_display(ren, model.atoms.cart)
    w, h = ren.GetSize()
    idx = atoms_in_box(ren, model, 0, 0, w / 2, h)
    assert set(idx) == set(np.nonzero(xy[:, 0] <= w / 2)[0])
    assert len(atoms_in_box(ren, model, 0, 0, 1, 1)) == 0


def test_offscreen_gesture_pipeline(scene_nio):
    """Machine + picking + overlay without Qt: click, then long-press box with Shift."""
    plotter, scene = scene_nio
    ren, model = plotter.renderer, scene.model
    machine = ia.InteractionMachine()
    sel = frozenset()
    xy = project_to_display(ren, model.atoms.cart)
    cam = plotter.camera
    d = np.array(cam.position) - np.array(cam.focal_point)
    front = int(np.argmax(model.atoms.cart @ d))
    x, y = xy[front]
    hit = model.atoms.parent[pick_atom(ren, model, x, y)]
    for a in machine.handle(ia.Press(x, y, 0.0, int(hit))) + machine.handle(ia.Release(x, y, 0.1)):
        if isinstance(a, ia.Select):
            sel = apply_selection(sel, a.sites, a.op)
    assert sel == {int(model.atoms.parent[front])}
    plotter.render()  # pyvista's screenshot() reuses the last frame otherwise
    before = plotter.screenshot(return_img=True).astype(int)
    box = BoxOverlay(ren)
    machine.handle(ia.Press(200, 150, 1.0))  # clear of the axes triad (bottom-left)
    machine.handle(ia.Tick(1.5))
    for a in machine.handle(ia.Move(450, 400, 1.6)):
        box.update(a.x0, a.y0, a.x1, a.y1)
    plotter.render()
    after = plotter.screenshot(return_img=True).astype(int)
    changed = np.abs(after - before).sum(axis=2) > 30
    edge = changed[480 - 150 - 2: 480 - 150 + 2, 205:445].any(axis=0)
    assert edge.all()  # the rubber band's bottom edge is drawn across its whole width
    assert not changed[:, :190].any()  # nothing outside the box changed
    inside = np.abs(after - before)[480 - 390: 480 - 160, 210:440].sum(axis=2) > 0
    assert inside.mean() > 0.9  # translucent fill tints the whole rectangle
    (fin,) = machine.handle(ia.Release(450, 400, 1.7, ia.Mods(shift=True)))
    in_box = {int(model.atoms.parent[i]) for i in atoms_in_box(ren, model, 200, 150, 450, 400)}
    sel = apply_selection(sel, in_box, fin.op)
    assert sel == in_box | {int(model.atoms.parent[front])}
