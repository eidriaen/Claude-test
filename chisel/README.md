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
| Sub-object level | 1 vertex · 2 edge · 3 border · 4 polygon · 5 element |

Pressing the sub-object level you are already in leaves it, as Max does.

**Files** — OBJ and STL, in and out. Ctrl+O to open, Ctrl+S to save. Every
import reports what it had to decide: n-gons triangulated, normals computed
because the file carried none, directives skipped. A failed import says why
and leaves the model you had alone.

**Mesh menu** — Fix normals, Recompute normals, Remove unused vertices, and a
Flip all normals that exists to break the mesh so Fix normals has something to
do.

## What is tested

49 tests, none of which need a window:

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

The viewport itself has not been looked at by human eyes yet. It compiles and
the maths behind it is checked, but the first run may well need adjusting.

## Not yet

glTF and FBX import, selection, undo, edit poly, modifiers, UV, sculpt, the
texture generator. Order and reasoning in [SCOPE.md](SCOPE.md).
