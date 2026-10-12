# Chisel

A small, fast 3D mesh editor. Working name.

```bash
cargo test -p mesh-core      # the geometry, no window required
```

Design, scope and the honest feasibility table: [SCOPE.md](SCOPE.md).

## Where it is

Early. `crates/mesh-core` holds the mesh and the first real operation — the
flipped-normals fix, which handles inconsistent winding, wholly inside-out
meshes, multiple shells and open surfaces, and reports non-manifold edges
rather than guessing at them. 11 tests, all of them against meshes that break
naive implementations.

No viewport yet. That is next.
