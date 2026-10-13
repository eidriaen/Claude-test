# Chisel — a lightweight 3D editor

Working name; rename it when something better turns up.

A small, fast mesh editor for the jobs that currently mean opening 3ds Max: fix
an import, unwrap something simple, push a few verts, sculpt a bit of detail,
bake a tiling texture, send it to Unreal. Not a Blender replacement. The goal
is that it opens in under a second and does ten things well.

## Why Rust

The Craft apps ([storytold](https://github.com/storytold) — PhotoCraft,
VectorCraft and the rest) are the direct inspiration, and they are all Rust
with [egui](https://github.com/emilk/egui) frontends and a shared workspace of
small libraries. The same shape fits here, with one extra reason that comes
straight from this repo's own history: the last project spent days fighting
Python installs, PATH, winget and TLS on three Windows machines. A compiled
binary has none of those problems. You copy one `.exe` and it runs.

- **egui + wgpu** — immediate-mode UI over a modern graphics API, one codebase
  for Windows, macOS, Linux, and WebAssembly if that ever matters.
- **A workspace of libraries, a thin app on top.** Geometry lives in
  `mesh-core` and is tested without a window; the viewport is a view of the
  data, never the owner of it.

## The honest feasibility table

Your list, with what each one actually costs. The two in bold are the ones
worth deciding early, because they shape everything around them.

| Feature | Verdict |
|---|---|
| OBJ, STL import/export | **Done**, hand-written. PLY and glTF next |
| **FBX** | **Import yes, export no.** `fbxcel` reads the binary format. Writing FBX properly needs Autodesk's SDK, which is C++, proprietary, and cannot be shipped in an MIT binary. Recommend: import FBX, export glTF — Unreal and Unity both take glTF happily |
| **STEP / CAD** | **Possible but heavy.** `truck` has a Rust STEP reader and a tessellator. This is a real B-rep kernel, not a file format: expect weeks, not days, and a large dependency. Worth its own phase |
| Fix flipped normals | **Done** — see below |
| Flatten/planar UV | Easy. Planar, box and cylindrical projection are a few hundred lines |
| Modifiers (3ds Max style) | The stack is the interesting part, not the modifiers. Non-destructive, reorderable, collapsible. Start with four: Subdivide, Smooth, Normalise, Transform |
| Sculpting | Brushes on a dense mesh are straightforward; **dynamic topology is not**. Phase 1: fixed topology with a subdivide step. Phase 2: dynamic tessellation under the brush |
| Back to "regular model flow" | Falls out of the modifier stack if sculpting is a stack entry with its own cached mesh |
| Edit poly tools | Select, move, extrude, bevel, inset, bridge, weld. The selection model and undo are in; the tools are next |
| Texturing | Keep it to assigning and previewing materials. A full paint tool is a different application |
| CrazyBump-style generator | Very doable and satisfying: height → normal is a sobel filter; AO, curvature and cavity are all derivable. No mesh needed, pure image work |
| Seamless tiling | Offset-and-blend gets you most of the way; a proper wrap-aware inpaint is better. Half a day for the first version |

## What exists now

`crates/mesh-core` — the mesh, and the first operation, with tests that run in
under a second and no window anywhere:

- `Mesh` — triangles in a flat index buffer, with `validate()` and `compact()`
- `recompute_vertex_normals` — area-weighted, NaN-safe on degenerate faces
- `orient` — the flipped-normals fix

### Why "fix flipped normals" took real work

It is two problems sharing one name, and a tool that solves only one looks
broken half the time:

1. **Faces disagree with their neighbours.** Fixed by walking the adjacency
   graph and flipping whatever contradicts the face it was reached from.
2. **Every face agrees, and they all point inwards.** Step 1 finds nothing to
   do, because the winding is already consistent. Caught by the sign of the
   enclosed volume instead.

Both are handled, in that order — the volume test is meaningless until the
winding agrees. On top of that:

- **Shells are oriented independently**, so one mirrored object in an imported
  scene does not drag the rest with it.
- **Open surfaces are made consistent but never turned over.** A plane has no
  inside; "fixing" it by volume would flip it on the accident of where the
  origin sits.
- **Non-manifold edges are reported, not guessed at.** Three faces on one edge
  has no single right answer, so it says so instead of calling the mesh fixed.

## Order of work

1. ~~**Viewport**~~ — done.
2. **Import/export** — ~~OBJ, STL~~ done; glTF next, then FBX import.
3. ~~**Selection and undo**~~ — done. Snapshot-based, and every mesh edit
   goes through one path so nothing can quietly become un-undoable.
4. **Edit poly** — the basic set.
5. **Modifier stack** — non-destructive, reorderable.
6. **UV** — planar/box/cylindrical, plus a UV view.
7. **Sculpt** — fixed topology first.
8. **Texture generator** — height/normal/AO/curvature, and seamless tiling.
9. **STEP** — on its own, when the rest stands up.

## Navigation and keys

3ds Max layout, since that is the muscle memory to respect:

| | |
|---|---|
| Orbit | Alt + middle-drag |
| Pan | Middle-drag |
| Zoom | Wheel |
| Frame selection | Z |
| Sub-object modes | 1 vertex · 2 edge · 3 border · 4 polygon · 5 element |
| Move / rotate / scale | W · E · R |
| Wireframe toggle | F3 |
| Wire-on-shaded | F4 |

Blender's habits are not mixed in. Two half-remembered layouts is worse than
either one of them.

## Deliberately out

Animation, rigging, rendering, simulation, a node graph, a full paint tool.
Each is a product of its own, and the point of this one is that it starts fast
and does a few things well.
