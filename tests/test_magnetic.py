"""Moment math: drag mapping, locks, snapping, dead zone, conversions."""

import numpy as np
import pytest

from magview.core import magnetic as mg

LAT = np.diag([4.0, 5.0, 6.0])


def frame(view=(0, -1, 0), up=(0, 0, 1), scale=1.0, lattice=LAT):
    # Camera looks along +y (into the screen); screen up = +z; screen right = +x.
    return mg.DragFrame(np.zeros(3), mg.unit(view), mg.unit(up), scale, np.asarray(lattice, float))


def test_frame_right_vector():
    assert np.allclose(frame((0, 1, 0)).right, [1, 0, 0])


def test_plane_point():
    f = mg.DragFrame(np.array([1.0, 2.0, 3.0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), 1.0, LAT)
    p = mg.plane_point(f, np.array([5.0, -10.0, 7.0]), np.array([0, 1.0, 0]))
    assert np.allclose(p, [5.0, 2.0, 7.0])


def test_free_drag_is_screen_vector_over_scale():
    f = frame((0, 1, 0), scale=0.5)
    r = mg.drag_moment(f, np.array([1.0, 0.0, 1.0]))
    assert np.allclose(r.moment, [2.0, 0, 2.0]) and r.magnitude == pytest.approx(np.sqrt(8))
    assert r.lock is None and not r.below_dead_zone


def test_centered_arrow_tip_follows_cursor():
    f = mg.DragFrame(np.zeros(3), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), 0.5, LAT,
                     centered=True)
    r = mg.drag_moment(f, np.array([0, 0, 1.0]))
    # tip of a centered arrow sits at anchor + 0.5 * |m| * scale = cursor distance
    assert r.magnitude == pytest.approx(4.0)
    assert 0.5 * r.magnitude * f.scale == pytest.approx(1.0)


def test_drag_point_off_plane_is_projected():
    f = frame((0, 1, 0))
    r = mg.drag_moment(f, np.array([1.0, 3.0, 0.0]))  # y component is along the view: dropped
    assert np.allclose(r.moment, [1.0, 0, 0])


def test_axis_lock_follows_projection():
    f = frame((0, 1, 0))
    r = mg.drag_moment(f, np.array([0.3, 0, 2.0]), lock="z")
    assert np.allclose(r.moment, [0, 0, 2.0]) and r.lock == "z"
    # oblique axis: s chosen so that the tip's screen projection follows the cursor
    u = mg.unit([1.0, 1.0, 0.0])  # projects onto screen x with length 1/sqrt2
    r = mg.drag_moment(f, np.array([1.0, 0, 0]), collinear_axis=u)
    assert np.allclose(r.moment, 2.0 / np.sqrt(2) * u)
    assert r.lock == "spin axis"


def test_lattice_axis_lock_and_negative_drag():
    f = frame((0, 1, 0))
    r = mg.drag_moment(f, np.array([-1.5, 0, 0]), lock="a")
    assert np.allclose(r.moment, [-1.5, 0, 0])


def test_axis_pointing_at_viewer_uses_vertical_motion():
    f = frame((0, 1, 0))  # spin axis y points into the screen
    r = mg.drag_moment(f, np.array([0.0, 0, 1.2]), collinear_axis=np.array([0, 1.0, 0]))
    assert "points at you" in r.note
    # drag up = moment toward the viewer (-y here, since +y points away)
    assert np.allclose(r.moment, [0, -1.2, 0])


def test_snap_magnitude_and_direction():
    f = frame((0, 1, 0))
    r = mg.drag_moment(f, np.array([1.04, 0, 0.03]), snap=True)
    assert np.allclose(r.moment, [1.0, 0, 0])  # direction to +x, |m| to 0.1
    r = mg.drag_moment(f, np.array([1.0, 0, 1.0]), snap=True)
    assert np.allclose(mg.unit(r.moment), mg.unit([1, 0, 1]))  # 45 deg in-plane step
    r = mg.drag_moment(f, np.array([0, 0, 2.06]), lock="z", snap=True)
    assert np.allclose(r.moment, [0, 0, 2.1])


def test_clamp_and_dead_zone():
    f = frame((0, 1, 0))
    assert mg.drag_moment(f, np.array([50.0, 0, 0])).magnitude == mg.MAX_MOMENT
    assert mg.drag_moment(f, np.array([0, 0, -50.0]), lock="z").moment[2] == -mg.MAX_MOMENT
    assert mg.drag_moment(f, np.array([0.1, 0, 0.1])).below_dead_zone
    assert mg.drag_moment(f, np.array([0, 0, 0.15]), lock="z").below_dead_zone


def test_axis_vector_names():
    lat = np.array([[3.0, 0, 0], [-1.5, 2.6, 0], [0, 0, 7]])
    assert np.allclose(mg.axis_vector("b", lat), mg.unit(lat[1]))
    assert np.allclose(mg.axis_vector("Z", lat), [0, 0, 1])
    with pytest.raises(ValueError):
        mg.axis_vector("q", lat)


def test_spherical_round_trip_and_components():
    m = np.array([1.0, -2.0, 0.5])
    r, th, ph = mg.spherical(m)
    assert np.allclose(mg.from_spherical(r, th, ph), m)
    hexa = np.array([[3.0, 0, 0], [-1.5, 1.5 * np.sqrt(3), 0], [0, 0, 7]])
    m = 2.0 * mg.unit(hexa[1])
    assert np.allclose(mg.crystal_components(m, hexa), [0, 2.0, 0])


def test_collinear_helpers():
    m = np.array([[0, 0, 2.0], [0.1, 0, -1.0]])
    assert not mg.is_collinear(m, [0, 0, 1])
    p = mg.project_collinear(m, [0, 0, 1])
    assert np.allclose(p, [[0, 0, 2.0], [0, 0, -1.0]]) and mg.is_collinear(p, [0, 0, 1])
    assert np.allclose(mg.signed_magnitudes(p, [0, 0, 1]), [2.0, -1.0])
