//! Selection: converting between levels, growing, and not outliving the mesh.

use mesh_core::mesh::Mesh;
use mesh_core::primitives::{cube, plane, sphere};
use mesh_core::selection::{edge_id, Level, Selection};
use glam::Vec3;

/// Two separate cubes in one mesh.
fn two_cubes() -> Mesh {
    let mut m = cube(2.0);
    let mut other = cube(2.0);
    for p in &mut other.positions {
        *p += Vec3::new(10.0, 0.0, 0.0);
    }
    let offset = m.positions.len() as u32;
    m.positions.extend(other.positions);
    m.indices.extend(other.indices.iter().map(|i| i + offset));
    mesh_core::normals::recompute_vertex_normals(&mut m);
    m
}

#[test]
fn select_all_counts_the_right_things_at_each_level() {
    let m = cube(2.0);
    for (level, expected) in [
        (Level::Vertex, 8),
        (Level::Face, 12),
        (Level::Edge, 18), // 12 cube edges plus one diagonal per face
    ] {
        let mut s = Selection::new(level);
        s.select_all(&m);
        assert_eq!(s.len(), expected, "{level:?}");
    }
}

#[test]
fn inverting_twice_gets_back_what_you_started_with() {
    let m = sphere(1.0, 12, 8);
    let mut s = Selection::new(Level::Vertex);
    s.add(3);
    s.add(7);
    let original = s.items.clone();
    s.invert(&m);
    assert!(!s.items.contains(&3));
    s.invert(&m);
    assert_eq!(s.items, original);
}

#[test]
fn faces_convert_down_to_their_corners() {
    let m = cube(2.0);
    let mut s = Selection::new(Level::Face);
    s.add(0);
    s.convert(&m, Level::Vertex);
    assert_eq!(s.level, Level::Vertex);
    assert_eq!(s.items.len(), 3, "one triangle has three corners");
}

#[test]
fn vertices_convert_up_only_to_faces_they_fully_enclose() {
    // The other rule -- any corner selected -- turns one picked vertex into a
    // fistful of faces, which is never what anyone means.
    let m = cube(2.0);
    let mut s = Selection::new(Level::Vertex);
    for v in m.tri(0) {
        s.add(v);
    }
    s.convert(&m, Level::Face);
    assert!(s.items.contains(&0));
    assert_eq!(s.items.len(), 1, "only the triangle whose corners are all in");

    let mut one = Selection::new(Level::Vertex);
    one.add(m.tri(0)[0]);
    one.convert(&m, Level::Face);
    assert!(one.is_empty(), "a single vertex encloses no face");
}

#[test]
fn an_element_selection_takes_the_whole_connected_piece() {
    let m = two_cubes();
    let mut s = Selection::new(Level::Vertex);
    s.add(0);
    s.convert(&m, Level::Element);
    assert_eq!(s.items.len(), 8, "the cube it touched, and not the other one");
    assert!(s.items.iter().all(|&v| v < 8));
}

#[test]
fn growing_a_vertex_selection_reaches_its_neighbours() {
    let m = plane(2.0);
    let mut s = Selection::new(Level::Vertex);
    s.add(0);
    let before = s.items.len();
    s.grow(&m);
    assert!(s.items.len() > before);
    assert!(s.items.contains(&0), "growing never drops what you had");
}

#[test]
fn shrinking_drops_the_boundary() {
    let m = sphere(1.0, 16, 12);
    let mut s = Selection::new(Level::Vertex);
    s.select_all(&m);
    let all = s.items.len();
    s.shrink(&m);
    assert_eq!(s.items.len(), all, "a fully selected mesh has no boundary to lose");

    // Shrink means: keep only what is interior to the selection. Asserted as
    // the property rather than a vertex count, because which vertices qualify
    // depends on the mesh -- a vertex whose every neighbour happens to be
    // selected is interior even if it sits on the mesh border.
    let mut part = Selection::new(Level::Vertex);
    for v in m.tri(0) {
        part.add(v);
    }
    let before = part.items.clone();
    part.shrink(&m);
    assert!(part.items.is_subset(&before), "shrink never adds");

    let mut neighbours = std::collections::HashMap::<u32, Vec<u32>>::new();
    for t in 0..m.tri_count() {
        let tri = m.tri(t);
        for &a in &tri {
            for &b in &tri {
                if a != b {
                    neighbours.entry(a).or_default().push(b);
                }
            }
        }
    }
    for v in &before {
        let interior = neighbours[v].iter().all(|n| before.contains(n));
        assert_eq!(
            part.items.contains(v),
            interior,
            "vertex {v} should be kept only when all its neighbours were selected"
        );
    }
}

#[test]
fn grow_then_shrink_is_not_destructive_on_a_closed_mesh() {
    let m = sphere(1.0, 16, 12);
    let mut s = Selection::new(Level::Vertex);
    s.add(10);
    s.grow(&m);
    let grown = s.items.len();
    s.shrink(&m);
    assert!(s.items.len() <= grown);
    assert!(!s.is_empty(), "shrinking a grown patch should not empty it");
}

#[test]
fn edges_have_one_identity_whichever_face_names_them() {
    assert_eq!(edge_id(7, 2), edge_id(2, 7));
    let mut s = Selection::new(Level::Edge);
    s.add_edge(edge_id(2, 7));
    s.toggle_edge(edge_id(7, 2));
    assert!(s.is_empty(), "the same edge, so it toggled off");
}

#[test]
fn pruning_drops_indices_the_mesh_no_longer_has() {
    // The case that matters: an operation removed geometry and the selection
    // still points at it. Left alone, the next operation indexes past the end.
    let m = cube(2.0);
    let mut s = Selection::new(Level::Vertex);
    s.add(3);
    s.add(99);
    s.prune(&m);
    assert!(s.items.contains(&3));
    assert!(!s.items.contains(&99));

    let mut f = Selection::new(Level::Face);
    f.add(0);
    f.add(500);
    f.prune(&m);
    assert_eq!(f.items.len(), 1);
}

#[test]
fn converting_to_the_level_you_are_already_at_changes_nothing() {
    let m = cube(2.0);
    let mut s = Selection::new(Level::Vertex);
    s.add(1);
    s.add(2);
    let before = s.clone();
    s.convert(&m, Level::Vertex);
    assert_eq!(s, before);
}

#[test]
fn to_vertices_works_from_every_level() {
    let m = cube(2.0);
    for level in [Level::Vertex, Level::Face, Level::Edge] {
        let mut s = Selection::new(level);
        s.select_all(&m);
        let verts = s.to_vertices(&m);
        assert_eq!(verts.len(), 8, "{level:?} covering everything covers all 8");
    }
}
