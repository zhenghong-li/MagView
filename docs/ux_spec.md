# MagView moment-editing UX specification

Status: **frozen at Phase 2** and implemented (`core/interaction.py`, `core/magnetic.py`, `core/batch.py`, `gui/`). Changes made during implementation are recorded in §8 with their reasons. The goal is that every interaction works out of the box, without requiring a later redesign.

## 1. Modes

| Mode | Enter | Exit | Visual cue | Purpose |
|---|---|---|---|---|
| **View** (default) | start-up, `Esc` from other modes | n/a | none | VESTA-like browsing; click shows atom info |
| **Moment edit** | toolbar "Moments" toggle, `M` | `M`, or `Esc` when no gesture is active | amber viewport border, banner "MOMENT EDIT: drag from an atom to set its moment · long-press to box-select · Esc to exit", moment view on | direct manipulation and batch operations |
| **Layer** | toolbar "Layers" toggle, `L` | `L` / `Esc` | teal border, banner, main view becomes the layer top view, reference view highlights the current layer | layer-by-layer up/down assignment (Phase 3) |

Rules:
- Only one mode is active at a time. Switching modes **cancels** an uncommitted gesture (a drag in progress, an armed box select) and never commits it.
- `Esc` has two levels: first it cancels the active gesture, and a second press exits the mode.
- The mode is always visible in three ways: the banner, the border color and a status-bar chip. The cursor also changes: an open hand over an atom, a closed hand while dragging, a crosshair when box-select is armed.

## 2. Pointer gestures

These gestures are recognized by a pure state machine (`core/interaction.py`). The GUI feeds it timestamped events, so tests can drive it with synthetic events.

| Gesture | View mode | Moment-edit mode |
|---|---|---|
| Click an atom (press + release, < 4 px of movement) | select it and show its info | select it; `Shift` adds to the selection, `Ctrl` toggles |
| Click empty space | clear the selection | clear the selection |
| Press on an atom, then move ≥ 4 px | rotate the camera | **draw the moment** (see §3) |
| Press on empty space, then move ≥ 4 px | rotate the camera | rotate the camera |
| **Long press** (≥ 350 ms held, < 4 px of movement), then drag | rubber-band select | rubber-band select. `Shift` adds, `Ctrl` subtracts |
| `B`, then drag | rubber-band select (touchpad fallback) | same |
| Middle button drag, or `Shift` + left drag on empty space | pan | pan |
| Wheel | zoom | zoom |
| Right-click | context menu (info, select equivalent atoms) | context menu: Flip, Zero, Set \|m\|..., Set direction..., Patterns..., Select equivalent |

- Long-press feedback: after 350 ms the cursor becomes a crosshair and a faint ring appears at the press point, so the user knows box mode is armed before moving.
- Picking an image atom always resolves to its **parent site**. Selecting or editing an image affects the parent and every copy of it.
- **Hover:** in Moment-edit mode, the atom under the cursor gets a thin outline before it is clicked, so the user knows what a press will grab.

## 3. Drawing a moment

- **Plane:** the arrow lies in the plane through the atom center, perpendicular to the view direction. The mouse ray is intersected with that plane.
- **Direction and length:** the moment points from the atom center toward the cursor, and the **arrow tip stays under the cursor**: |m| = (distance in Å) / (scale × ½) for centered arrows, or / scale for tail-anchored ones. The scale is in Å per μB; the default comes from VESTA's `VECTS` (1.0 Å/μB).
- **Dead zone:** below 0.2 μB the drag counts as a click, so no tiny accidental moments are created.
- **Range:** |m| is clamped to [0, 10] μB (configurable) and shown in the tooltip.
- **Constraints** apply while a key is held:
  - `X`/`Y`/`Z`: lock to the Cartesian axis. The signed length is the drag projected onto that axis's screen direction.
  - `A`/`B`/`C`: lock to the lattice axis.
  - `Shift`: snap the direction to the nearest of ±a, ±b, ±c, ±x, ±y, ±z or a 15° step in the drag plane, and snap |m| to 0.1 μB.
- **Collinear model:** drags are always locked to ±n̂, the global spin axis (default +z, configurable as a/b/c, x/y/z or custom). The sign follows the drag along n̂'s screen projection. If n̂ points almost straight at the viewer (screen projection under 15% of its length), vertical mouse motion sets the signed magnitude instead, and the banner says "spin axis points at you: drag up/down".
- **Selection-wide drag:** dragging a *selected* atom applies the same moment to the whole selection, with a live preview on all of them. Dragging an unselected atom first makes it the only selection.
- **Live feedback:** the arrow follows the cursor every frame; only the arrow mesh points are rewritten. A tooltip next to the cursor shows `|m| = 3.2 μB`, the Cartesian components, the (a, b, c) components and θ/φ, plus the active constraint.
- **Commit and cancel:** releasing the mouse commits exactly **one undo step**. `Esc` during a drag restores the previous moments.
- **Exact entry:** the Moments panel always shows the selection's moment(s) as editable fields (|m|, θ/φ, or components). A mixed selection shows "mixed".

## 4. Batch operations

These apply to the current selection. Each one is a single undo step.
- FM ↑ / FM ↓ along n̂, Flip (m → −m), Zero, Set |m| (keeps each direction), Set direction (keeps each |m|).
- **Patterns popover** (`P` or the context menu), placed next to the selection:
  - A **planar selection** (all selected atoms within the layer tolerance of one plane) gets the 2D library for the detected lattice: square → FM, Néel, stripe-a, stripe-b; honeycomb → FM, Néel, zigzag ×3, stripe ×3; triangular → FM, stripe ×3.
  - A **3D selection** gets FM, A-type (choose the stacking axis), C-type and G-type. G-type is offered only if the nearest-neighbor graph of the selection is bipartite.
  - Entries that don't apply are **grayed out with the reason**, e.g. "G-type: nearest-neighbor graph is not bipartite (frustrated)".
  - Hovering an entry shows ghost arrows; clicking applies it. Nothing is ever applied without a click.

## 5. Display options (persisted per user via QSettings)

| Option | Default | Choices |
|---|---|---|
| Arrow anchor | centered on the atom (VESTA "penetrate") | centered / tail at atom |
| Arrow scale | 1.0 Å/μB, fixed during editing; "Auto-fit" button on request | any > 0 |
| Shaft radius | 0.12 Å, constant regardless of length | any > 0 |
| Head | radius 2× shaft, length 25% of the arrow | ratios |
| Arrow color | by sign along n̂ (up red, down blue) in collinear mode; by direction hue in non-collinear mode | by sign / by direction / by element / single color |
| Moment view | on in Moment-edit mode: atoms 0.6×, atoms without a moment at 25% opacity, bonds hidden | toggle; opacity slider |
| Magnitude labels | off | on / off |
| Arrows on periodic images | on | on / off |

**Resolved (Phase 2):** spin-up red used to clash with VESTA's red for O and V. In moment view, atom colors are now blended 55% toward light gray, so element identity stays visible while arrows remain the most saturated objects. See `artifacts/phase2/cs3v9te13_{before,after}_fix.png`.

## 6. Data and safety rails

- Moments are stored as Cartesian μB vectors, one per site of the **editing canvas**. Collinear vs non-collinear is a model setting, not a separate data type.
- Switching from non-collinear to collinear with non-collinear moments present opens a dialog: project onto n̂, or cancel. Moments are never changed silently.
- Undo/redo (`Ctrl+Z`, `Ctrl+Y` / `Ctrl+Shift+Z`) covers every edit: drag, batch operation, pattern application, layer toggle, suggestion acceptance and canvas resize.
- Resizing the canvas: growing it extends moments periodically. Shrinking it is refused, with an explanation, when the moments are not periodic in the smaller canvas.
- The session saves to `.magview.json`, which holds the structure, canvas, moments and settings. A recovery autosave happens after each committed edit.

## 7. Keyboard summary

`M` moment mode · `L` layer mode · `Esc` cancel/exit · `B` arm box select · `P` patterns · `F` flip selection · `0` zero selection · `Ctrl+A` select all magnetic sites · `Ctrl+Z`/`Ctrl+Y` undo/redo · hold `X Y Z A B C` axis lock · hold `Shift` snap.

## 8. Changes made during implementation (Phase 2)

| Change | Reason |
|---|---|
| The arrow **tip** follows the cursor (for centered arrows, \|m\| uses half the scale) | With centered arrows the tip reached only halfway to the cursor, which felt like the arrow "lagged" (visible in the first drag replay). Grabbing the tip is the natural mental model. Drag length still maps linearly to \|m\|. |
| Moment view desaturates atom colors (55% toward light gray) | Spin-up red merged with red V/O atoms (§5). |
| Letter keys (B, M, F, 0, P, E, and the held X/Y/Z/A/B/C locks) are handled by a key filter on the 3D viewport, not by window shortcuts | `B` is both "box select" (when idle) and "lock to b" (while dragging). Shortcuts would consume the key before the drag sees it. Ctrl combinations and Esc stay window shortcuts. |
| Redo is bound to both Ctrl+Y and Ctrl+Shift+Z | Qt's platform-standard Redo on Linux is Ctrl+Shift+Z only. |
| Right-click without movement opens the context menu; right-drag keeps VTK's zoom | Both behaviors are kept without a mode switch. |
| Overlay text avoids the `\|` character | VTK's text renderer treats `\|` specially, so "\|m\| = ..." rendered garbled. |
| Patterns popover: entries that don't apply stay visible but disabled, with the reason as a tooltip | Required by §4 ("grayed out with the reason"). |
