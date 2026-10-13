# Chisel

A small, fast 3D mesh editor. Working name.

```bash
cargo run -p chisel-app --release    # the editor
cargo test -p mesh-core              # the geometry, no window required
```

Design, scope and the honest feasibility table: [SCOPE.md](SCOPE.md).

## Where it is

A window that opens, a mesh you can orbit, and the first real tool.

**Viewport** — wgpu behind an egui paint callback, so the 3D view is a widget
inside the normal UI rather than a separate window to dodge around. Grid,
shaded and wireframe drawing, depth-tested, no back-face culling (half the
point of this app is finding faces that point the wrong way — culling them
hides exactly what you are looking for).

**Navigation**, 3ds Max bindings:

| | |
|---|---|
| Orbit | Alt + middle-drag |
| Pan | Middle-drag |
| Zoom | Wheel |
| Frame selection | Z |
| Wireframe only / wire-on-shaded | F3 / F4 |
| Select · Move · Rotate · Scale | Q · W · E · R |
| Sub-object level | 1 vertex · 2 edge · 3 face · 4 element |
| Select / add to selection | Click / Ctrl+click |
| Select all · none · invert | Ctrl+A · Selection menu · Ctrl+I |
| Undo / redo | Ctrl+Z / Ctrl+Y |

Pressing the sub-object level you are already in leaves it, as Max does, and
changing level **converts** the selection rather than dropping it — pick four
verts, press 3, and you get the faces they bound.

**Files** — OBJ and STL, in and out. Ctrl+O to open, Ctrl+S to save. Every
import reports what it had to decide: n-gons triangulated, normals computed
because the file carried none, directives skipped. A failed import says why
and leaves the model you had alone.

**Mesh menu** — Fix normals, Recompute normals, Remove unused vertices, and a
Flip all normals that exists to break the mesh so Fix normals has something to
do.

**Command panel** — Max's right-hand column. **Create** makes primitives with
their parameters still editable (box, sphere, cylinder, cone, torus, plane,
all with segment counts). **Modify** holds the modifier stack and the
transform type-ins.

**Modifier stack** — non-destructive, reorderable, with per-entry caching so
changing the top modifier does not re-run the four below it. The base mesh is
never touched until you collapse. Subdivide, Smooth and Fix normals to start.

**Transform gizmo** — axis bars with arrowheads for Move, rings for Rotate,
boxes for Scale, plus plane handles and a centre box. Drawn over the model,
because a gizmo behind it is a gizmo you cannot grab, and held at a constant
size on screen. The handle under the cursor brightens before you commit. A
whole drag is one undo step.

**Transforms** — move, rotate and scale a selection about its centre or the
origin, by gizmo or by typing an exact number.

**Selection** — vertex, edge, face and element, with grow, shrink and invert.
Selected geometry is tinted in the viewport.

**Undo** — snapshot-based, and everything that changes the mesh goes through
one path, so nothing can quietly become un-undoable. The selection is part of
each snapshot: undoing a delete has to put back the selection too, or the next
operation indexes past the end of an array that just got shorter. A drag
collapses into a single step, and the stack is bounded by memory rather than
by a step count.

## What is tested

129 tests, none of which need a window:

- **Normals** — inconsistent winding, wholly inside-out meshes, two shells
  where only one is wrong, open surfaces that must not be turned over,
  non-manifold edges, degenerate faces, empty meshes.
- **Camera** — orbit holding its target and distance, pitch stopping short of
  the poles, pan covering the same screen distance at any zoom, zoom staying
  proportional and never reaching the pivot, framing fitting horizontally as
  well as vertically, and the basis staying orthonormal all the way round.
- **OBJ** — the same position with two normals becoming two vertices (merging
  them smooths every hard edge), negative indices, n-gons, 1-D texture
  coordinates, and errors that name the line and the value.
- **STL** — 36 loose corners welding back into 8 vertices, a binary file whose
  header happens to say "solid", truncation, and face normals being recomputed
  rather than trusted.
- **Selection** — level conversion both ways, grow and shrink as properties
  rather than counts, element selection taking a whole connected piece, and
  pruning indices the mesh no longer has.
- **Picking** — Möller–Trumbore against known geometry, the nearest face
  winning over the one behind it, back faces staying pickable, and a click on
  empty space picking nothing.
- **Undo** — redo branches discarded on a new edit, a 200-step drag collapsing
  to one, the memory budget holding, and one step always surviving however
  heavy the mesh.
- **Primitives** — every closed one encloses a positive volume, is a single
  shell, and needs no fixing. This test found four real bugs: box, sphere,
  cylinder and torus were all wound inward, which the two-sided shading in the
  viewport had been hiding.
- **Transforms** — a selection moving while nothing else does, an empty
  sub-object selection transforming *nothing* rather than everything, rotation
  holding its pivot, and scale-to-zero being clamped so it can be undone.
- **Modifier stack** — the base mesh never changing, order mattering, a
  disabled modifier not serving a stale cache, and subdivide sharing midpoints
  between neighbouring faces.
- **Gizmo** — grabbing each handle, an axis drag landing exactly on the axis
  and never creeping into the other two, looking straight down an axis being
  refused rather than guessed at, a rotation taking the short way round the
  seam, and the gizmo holding its screen size at any zoom. One of these caught
  a sign error that dragged the selection the wrong way down the axis.

The viewport itself has not been looked at by human eyes yet. It compiles and
the maths behind it is checked, but the first run may well need adjusting.

## Not yet

Edit poly, glTF and FBX import, UV, sculpt, the texture generator. Order and reasoning in [SCOPE.md](SCOPE.md).
