//! Primitives: closed where they should be, and wound outwards.

use mesh_core::normals::{orient, signed_volume};
use mesh_core::primitives::*;

/// Every closed primitive must enclose a positive volume and need no fixing.
#[test]
fn closed_primitives_are_already_correct() {
    let cases: Vec<(&str, mesh_core::Mesh)> = vec![
        ("cube", cube(2.0)),
        ("box", box_mesh(2.0, 1.0, 3.0, 2, 3, 4)),
        ("sphere", sphere(1.0, 24, 16)),
        ("cylinder", cylinder(1.0, 2.0, 16, true)),
        ("cone", cone(1.0, 0.0, 2.0, 16)),
        ("truncated cone", cone(1.0, 0.5, 2.0, 16)),
        ("torus", torus(2.0, 0.5, 12, 24)),
    ];
    for (name, mesh) in cases {
        assert!(signed_volume(&mesh) > 0.0, "{name} is inside out");
        let mut m = mesh.clone();
        let report = orient(&mut m);
        assert!(!report.changed(), "{name} needed fixing: {}", report.summary());
        assert_eq!(report.shells, 1, "{name} should be one connected piece");
        assert_eq!(report.non_manifold_edges, 0, "{name} has non-manifold edges");
        assert!(mesh.validate().is_ok(), "{name} failed validation");
    }
}

#[test]
fn the_sphere_seam_is_welded_shut() {
    // The duplicate wrap-around column lights as a visible crease all the way
    // down the sphere, which is what it looked like in the first screenshot.
    let m = sphere(1.0, 32, 24);
    let mut seen = std::collections::HashMap::<[i64; 3], usize>::new();
    for p in &m.positions {
        let key = [
            (p.x * 1e4).round() as i64,
            (p.y * 1e4).round() as i64,
            (p.z * 1e4).round() as i64,
        ];
        *seen.entry(key).or_default() += 1;
    }
    let duplicates: Vec<_> = seen.values().filter(|&&n| n > 1).collect();
    assert!(duplicates.is_empty(), "{} positions appear twice", duplicates.len());
}

#[test]
fn an_uncapped_cylinder_is_open_and_says_so() {
    let m = cylinder(1.0, 2.0, 12, false);
    let mut copy = m.clone();
    let report = orient(&mut copy);
    assert_eq!(report.shells_flipped, 0, "an open tube has no outward to find");
}

#[test]
fn a_cone_has_one_apex_not_a_ring_of_them() {
    let m = cone(1.0, 0.0, 2.0, 16);
    let apexes = m.positions.iter().filter(|p| p.y > 0.99).count();
    assert_eq!(apexes, 1, "the tip should be a single welded vertex");
}

#[test]
fn segment_counts_are_honoured_and_never_zero() {
    let a = grid(2.0, 2.0, 4, 4);
    assert_eq!(a.tri_count(), 4 * 4 * 2);
    // Zero segments would make an empty mesh; clamped to one instead.
    let b = grid(2.0, 2.0, 0, 0);
    assert_eq!(b.tri_count(), 2);
    let c = cylinder(1.0, 1.0, 1, true);
    assert!(c.tri_count() > 0, "a cylinder needs at least three sides");
}
