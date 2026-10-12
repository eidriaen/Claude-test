//! Fixing normals, on the meshes that actually break it.

use mesh_core::mesh::Mesh;
use mesh_core::normals::{orient, recompute_vertex_normals, signed_volume};
use mesh_core::primitives::{cube, plane, sphere};
use glam::Vec3;

/// Flip one triangle's winding, as a bad import does to scattered faces.
fn flip_face(mesh: &mut Mesh, t: usize) {
    mesh.indices.swap(t * 3 + 1, t * 3 + 2);
}

fn flip_all(mesh: &mut Mesh) {
    for t in 0..mesh.tri_count() {
        flip_face(mesh, t);
    }
}

/// Every vertex normal points away from the centre.
fn all_outward(mesh: &Mesh) -> bool {
    let centre = mesh.positions.iter().copied().sum::<Vec3>() / mesh.positions.len() as f32;
    mesh.positions
        .iter()
        .zip(&mesh.normals)
        .all(|(p, n)| (*p - centre).dot(*n) > 0.0)
}

#[test]
fn a_clean_cube_is_left_alone() {
    let mut m = cube(2.0);
    let before = m.clone();
    let report = orient(&mut m);
    assert!(!report.changed(), "{}", report.summary());
    assert_eq!(report.shells, 1);
    assert_eq!(m.indices, before.indices, "nothing should have moved");
}

#[test]
fn one_face_wound_backwards_is_rewound() {
    let mut m = cube(2.0);
    flip_face(&mut m, 3);
    let report = orient(&mut m);
    assert_eq!(report.rewound, 1, "{}", report.summary());
    assert_eq!(m.indices, cube(2.0).indices);
}

#[test]
fn half_the_faces_backwards_still_comes_out_right() {
    let mut m = sphere(1.0, 16, 12);
    for t in (0..m.tri_count()).step_by(2) {
        flip_face(&mut m, t);
    }
    orient(&mut m);
    assert!(signed_volume(&m) > 0.0);
    assert!(all_outward(&m), "every normal should point out of the sphere");
}

#[test]
fn a_mesh_turned_entirely_inside_out_is_caught_by_volume() {
    // The case consistency alone cannot see: every face agrees with its
    // neighbours, and all of them are wrong.
    let mut m = cube(2.0);
    flip_all(&mut m);
    assert!(signed_volume(&m) < 0.0, "test setup: should start inside out");

    let report = orient(&mut m);
    assert_eq!(report.rewound, 0, "winding was already consistent");
    assert_eq!(report.shells_flipped, 1, "{}", report.summary());
    assert!(signed_volume(&m) > 0.0);
    assert!(all_outward(&m));
}

#[test]
fn two_separate_shells_are_oriented_independently() {
    // One cube correct, one inside out, in a single mesh -- what you get after
    // importing a scene where only some objects came from a mirrored rig.
    let mut m = cube(2.0);
    let mut bad = cube(2.0);
    flip_all(&mut bad);
    for p in &mut bad.positions {
        *p += Vec3::new(10.0, 0.0, 0.0);
    }
    let offset = m.positions.len() as u32;
    m.positions.extend(bad.positions);
    m.indices.extend(bad.indices.iter().map(|i| i + offset));
    recompute_vertex_normals(&mut m);

    let report = orient(&mut m);
    assert_eq!(report.shells, 2);
    assert_eq!(report.shells_flipped, 1, "only the bad one: {}", report.summary());

    // Each cube is judged against its own centre. A centroid of both would sit
    // in the empty space between them and call every normal on the left cube
    // inward.
    for (range, centre) in [
        (0..8, Vec3::ZERO),
        (8..16, Vec3::new(10.0, 0.0, 0.0)),
    ] {
        for i in range {
            let out = (m.positions[i] - centre).dot(m.normals[i]);
            assert!(out > 0.0, "vertex {i} points inward ({out})");
        }
    }
}

#[test]
fn an_open_surface_is_made_consistent_but_not_turned_over() {
    // A plane has no inside. Consistency is all that can be asked of it, and
    // "fixing" it by volume would flip it on a whim of where the origin is.
    let mut m = plane(2.0);
    flip_face(&mut m, 1);
    let report = orient(&mut m);
    assert_eq!(report.rewound, 1);
    assert_eq!(report.shells_flipped, 0, "an open surface has no outward");

    let n0 = m.face_normal(0);
    let n1 = m.face_normal(1);
    assert!(n0.dot(n1) > 0.9, "the two faces should agree with each other");
}

#[test]
fn a_plane_already_facing_down_is_not_turned_up() {
    // Consistent and open: there is nothing to fix, whatever it looks like.
    let mut m = plane(2.0);
    flip_all(&mut m);
    let down = m.face_normal(0);
    let report = orient(&mut m);
    assert!(!report.changed(), "{}", report.summary());
    assert!(m.face_normal(0).dot(down) > 0.99, "it must stay as the artist left it");
}

#[test]
fn non_manifold_edges_are_reported_not_guessed_at() {
    // Three faces on one edge: there is no single right answer, so say so
    // rather than picking one and calling the mesh fixed.
    let mut m = Mesh {
        positions: vec![
            Vec3::new(0.0, 0.0, 0.0),
            Vec3::new(1.0, 0.0, 0.0),
            Vec3::new(0.0, 1.0, 0.0),
            Vec3::new(0.0, 0.0, 1.0),
            Vec3::new(0.0, -1.0, 0.0),
        ],
        indices: vec![0, 1, 2, 0, 1, 3, 0, 1, 4],
        ..Default::default()
    };
    let report = orient(&mut m);
    assert!(report.non_manifold_edges >= 1, "{}", report.summary());
    assert!(report.summary().contains("non-manifold"));
}

#[test]
fn degenerate_faces_do_not_produce_nan_normals() {
    // A zero-area triangle has no normal. normalize() would hand back NaN and
    // poison every averaged vertex that touches it.
    let mut m = Mesh {
        positions: vec![Vec3::ZERO, Vec3::X, Vec3::X],
        indices: vec![0, 1, 2],
        ..Default::default()
    };
    recompute_vertex_normals(&mut m);
    assert!(m.normals.iter().all(|n| n.is_finite()), "{:?}", m.normals);
}

#[test]
fn an_empty_mesh_is_not_an_error() {
    let mut m = Mesh::new();
    let report = orient(&mut m);
    assert_eq!(report.shells, 0);
    assert!(!report.changed());
}

#[test]
fn validate_catches_an_index_past_the_end() {
    let m = Mesh {
        positions: vec![Vec3::ZERO, Vec3::X, Vec3::Y],
        indices: vec![0, 1, 7],
        ..Default::default()
    };
    let err = m.validate().unwrap_err().to_string();
    assert!(err.contains("7"), "{err}");
}
