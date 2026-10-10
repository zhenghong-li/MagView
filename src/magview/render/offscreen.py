"""Offscreen PNG rendering (no display, no Qt).

Call ``configure_offscreen()`` before the first plotter is created. Without a
display, VTK is pointed at OSMesa explicitly (deterministic llvmpipe software
rendering); otherwise VTK's own default is kept.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from magview.osdeps import has_display

OSMESA_WINDOW = "vtkOSOpenGLRenderWindow"


def configure_offscreen() -> None:
    """Force OSMesa/offscreen only when no display is present; with a display,
    pyvista/VTK defaults are left alone (interactive windows stay possible)."""
    if has_display():
        return
    os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", OSMESA_WINDOW)
    import pyvista as pv

    pv.OFF_SCREEN = True


def make_plotter(size=(1200, 900)):
    configure_offscreen()
    import pyvista as pv

    plotter = pv.Plotter(off_screen=True, window_size=list(size))
    # Translucent content (moment-view fades, selection halos) needs sorted blending.
    plotter.enable_depth_peeling()
    return plotter


def render_png(crystal, path: str | Path | None = None, view: str = "oblique",
               size=(1200, 900), site_moments: np.ndarray | None = None, appearance=None,
               flat_shading: bool = False, show_axes: bool = True, show_bonds: bool = True,
               anti_alias: bool = True, bounds=None, moment_view: bool = False,
               show_cell: bool = True, return_scene: bool = False):
    """Render ``crystal`` to ``path`` (if given) and return the RGB image array.

    With ``return_scene=True`` returns ``(image, plotter, scene)`` and leaves the
    plotter open (caller must ``plotter.close()``); used by projection tests.
    """
    from magview.core.images import DEFAULT_BOUNDS
    from magview.render.model import build_scene_model
    from magview.render.scene import StructureScene
    from magview.style.appearance import Appearance

    appearance = appearance or Appearance.load()
    model = build_scene_model(crystal, appearance, bounds or DEFAULT_BOUNDS,
                              site_moments=site_moments, show_bonds=show_bonds,
                              moment_view=moment_view)
    plotter = make_plotter(size)
    if anti_alias and not flat_shading:
        plotter.enable_anti_aliasing("ssaa")
    scene = StructureScene(plotter, appearance.theme, flat_shading=flat_shading,
                           show_axes=show_axes, show_cell=show_cell)
    scene.set_model(model)
    scene.set_view(view)
    image = plotter.screenshot(str(path) if path else None, return_img=True)
    if return_scene:
        return image, plotter, scene
    plotter.close()
    return image
