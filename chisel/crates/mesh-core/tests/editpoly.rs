//! Edit poly. These change topology, so the tests check the mesh is still a
//! mesh afterwards, not just that the numbers moved.

use mesh_core::editpoly::*;
use mesh_core::normals::{orient, signed_volume};
use mesh_core::primitives::{cube, grid, plane, sphere};
use mesh_core::Mesh;

/// A mesh is in one piece, closed, and wound outwards.
fn assert_solid(m: &Mesh, what: &str) {
    assert!(m.validate().is_ok(), "{what}: {:?}", m.validate());
    let mut copy = m.clone();
    let report = orient(&mut copy);
    assert_eq!(report.non_manifold_edges, 0, "{what} has non-manifold edges");
    assert!(!report.changed(), "{what} came out needing Fix normals: {}", report.summary());
}

#[test]
fn extruding_a_face_adds_walls_and_moves_it_out() {
    let mut m = cube(2.0);
    let before_volume = signed_volume(&m);
    let before_faces = m.tri_count();

    // One side of the cube is two triangles sharing a diagonal.
    let (report, new) = extrude(&mut m, &[0, 1], 1.0, 0.0);

    assert_eq!(new.len(), 2, "the extruded faces come back to stay selected");
    assert!(report.faces_added > 0, "{}", report.summary());
    assert!(m.tri_count() > before_faces);
    assert!(signed_volume(&m) > before_volume, "extruding outward should add volume");
    assert_solid(&m, "extruded cube");
}

#[test]
fn an_interior_edge_gets_no_wall() {
    // The two triangles of one cube side share a diagonal. A wall there would
    // bury a sheet of geometry through the middle of the extrusion.
    let mut m = cube(2.0);
    let (report, _) = extrude(&mut m, &[0, 1], 1.0, 0.0);
    // Four boundary edges, two triangles each.
    assert_eq!(report.faces_added, 8, "{}", report.summary());
    assert_solid(&m, "extrusion with a shared diagonal");
}

#[test]
fn extruding_inward_removes_volume() {
    let mut m = cube(2.0);
    let before = signed_volume(&m);
    extrude(&mut m, &[0, 1], -0.5, 0.0);
    assert!(signed_volume(&m) < before);
    assert_solid(&m, "inward extrusion");
}

#[test]
fn extruding_by_zero_leaves_the_shape_alone() {
    let mut m = cube(2.0);
    let before = signed_volume(&m);
    extrude(&mut m, &[0, 1], 0.0, 0.0);
    assert!((signed_volume(&m) - before).abs() < 1e-4);
}

#[test]
fn extruding_every_face_of_a_sphere_grows_it() {
    let mut m = sphere(1.0, 16, 12);
    let faces: Vec<u32> = (0..m.tri_count() as u32).collect();
    let before = signed_volume(&m);
    let (_, new) = extrude(&mut m, &faces, 0.25, 0.0);
    assert_eq!(new.len(), faces.len());
    assert!(signed_volume(&m) > before);
    // A closed region has no boundary, so there is nothing to build walls on.
    assert_eq!(m.tri_count(), faces.len(), "no walls on a closed surface");
}

#[test]
fn inset_shrinks_a_face_without_moving_it_outward() {
    let mut m = plane(4.0);
    let before = m.positions.clone();
    let (report, _) = inset(&mut m, &[0], 0.5);
    assert!(report.faces_added > 0);
    // Nothing should have left the plane.
    for p in &m.positions {
        assert!(p.y.abs() < 1e-5, "inset lifted a vertex to {p:?}");
    }
    assert!(m.positions.len() > before.len());
}

#[test]
fn an_inset_larger_than_the_face_does_not_turn_it_inside_out() {
    // Clamped short of the centre; without that the face crosses itself and
    // the result is a crumpled mess that still passes a vertex count check.
    let mut m = plane(2.0);
    let (_, _) = inset(&mut m, &[0, 1], 50.0);
    assert!(m.positions.iter().all(|p| p.is_finite()));
    let spread = m
        .positions
        .iter()
        .map(|p| p.length())
        .fold(0.0f32, f32::max);
    assert!(spread > 0.0, "everything collapsed onto the centre");
}

#[test]
fn bevel_is_inset_and_height_together() {
    let mut m = cube(2.0);
    let (report, _) = extrude(&mut m, &[0, 1], 0.5, 0.3);
    assert!(report.faces_added > 0, "{}", report.summary());
    assert_solid(&m, "bevelled cube");
}

#[test]
fn extruding_with_nothing_selected_says_so_rather_than_doing_nothing_quietly() {
    let mut m = cube(2.0);
    let before = m.clone();
    let (report, new) = extrude(&mut m, &[], 1.0, 0.0);
    assert!(new.is_empty());
    assert!(report.summary().contains("no faces"), "{}", report.summary());
    assert_eq!(m, before);
}

#[test]
fn opposing_faces_both_extrude_outward() {
    // Each vertex moves along the average of the selected faces touching it,
    // not along one direction for the whole region. With a single region
    // normal these two cancel exactly and nothing moves -- which is also why
    // extruding a whole closed mesh does nothing under that scheme.
    let mut m = cube(2.0);
    let n0 = m.face_normal(0);
    let other = (1..m.tri_count())
        .find(|&t| m.face_normal(t).dot(n0) < -0.99)
        .expect("a cube has opposing faces") as u32;

    let before = signed_volume(&m);
    let (report, new) = extrude(&mut m, &[0, other], 0.5, 0.0);
    assert_eq!(new.len(), 2, "{}", report.summary());
    assert!(signed_volume(&m) > before, "both should have moved outward");
    assert_solid(&m, "opposing extrusions");
}

#[test]
fn welding_two_vertices_merges_them_and_drops_the_collapsed_face() {
    let mut m = grid(2.0, 2.0, 1, 1);
    let before_faces = m.tri_count();
    let report = weld_selected(&mut m, &[0, 1]);

    assert_eq!(report.vertices_removed, 1, "{}", report.summary());
    assert!(m.tri_count() < before_faces, "the face between them had to go");
    assert!(m.validate().is_ok());
}

#[test]
fn welding_puts_the_merged_vertex_at_the_average_position() {
    let mut m = grid(2.0, 2.0, 1, 1);
    let a = m.positions[0];
    let b = m.positions[1];
    weld_selected(&mut m, &[0, 1]);
    let expected = (a + b) / 2.0;
    assert!(
        m.positions.iter().any(|p| (*p - expected).length() < 1e-4),
        "no vertex at the midpoint {expected:?}"
    );
}

#[test]
fn welding_one_vertex_is_refused_with_an_explanation() {
    let mut m = cube(2.0);
    let before = m.clone();
    let report = weld_selected(&mut m, &[3]);
    assert!(report.summary().contains("at least two"), "{}", report.summary());
    assert_eq!(m, before);
}

#[test]
fn welding_by_distance_closes_a_split_seam() {
    // Two separate planes meeting edge to edge: the classic imported-model
    // problem, where it looks solid and behaves as two pieces.
    let mut m = plane(2.0);
    let mut other = plane(2.0);
    for p in &mut other.positions {
        p.x += 2.0;
    }
    let offset = m.positions.len() as u32;
    m.positions.extend(other.positions);
    m.indices.extend(other.indices.iter().map(|i| i + offset));

    let mut before = m.clone();
    assert_eq!(orient(&mut before).shells, 2, "two pieces to start");

    let report = weld_by_distance(&mut m, 1e-4);
    assert!(report.vertices_removed > 0, "{}", report.summary());
    let mut after = m.clone();
    assert_eq!(orient(&mut after).shells, 1, "should be one piece now");
}

#[test]
fn deleting_faces_leaves_a_hole_and_tidies_up_after_itself() {
    let mut m = cube(2.0);
    let report = delete_faces(&mut m, &[0, 1]);
    assert_eq!(report.faces_removed, 2);
    assert_eq!(m.tri_count(), 10);
    assert!(m.validate().is_ok());
    // The corners are still used by the neighbouring sides, so nothing goes.
    assert_eq!(report.vertices_removed, 0);
}

#[test]
fn deleting_a_vertex_takes_every_face_that_used_it() {
    let mut m = cube(2.0);
    let report = delete_vertices(&mut m, &[0]);
    assert!(report.faces_removed > 0, "{}", report.summary());
    assert!(m.validate().is_ok());
    assert!(
        !m.indices.is_empty(),
        "deleting one corner should not empty the mesh"
    );
}

#[test]
fn deleting_everything_leaves_an_empty_mesh_not_a_broken_one() {
    let mut m = cube(2.0);
    let all: Vec<u32> = (0..m.tri_count() as u32).collect();
    delete_faces(&mut m, &all);
    assert_eq!(m.tri_count(), 0);
    assert_eq!(m.vertex_count(), 0, "orphaned vertices should be swept up");
    assert!(m.validate().is_ok());
}

#[test]
fn tessellate_splits_each_face_into_three() {
    let mut m = cube(2.0);
    let before = m.tri_count();
    let report = tessellate(&mut m, &[0, 1]);
    assert_eq!(m.tri_count(), before + 4, "{}", report.summary());
    assert_eq!(report.vertices_added, 2);
    assert_solid(&m, "tessellated cube");
}

#[test]
fn tessellating_does_not_move_the_surface() {
    let mut m = cube(2.0);
    let before = signed_volume(&m);
    let all: Vec<u32> = (0..m.tri_count() as u32).collect();
    tessellate(&mut m, &all);
    assert!((signed_volume(&m) - before).abs() < 1e-4, "the shape changed");
}

#[test]
fn an_out_of_range_face_index_is_ignored_not_a_panic() {
    // Selections can outlive the mesh they were made on.
    let mut m = cube(2.0);
    let (_, _) = extrude(&mut m, &[0, 9999], 1.0, 0.0);
    assert!(m.validate().is_ok());
    let r = delete_faces(&mut m, &[9999]);
    assert!(m.validate().is_ok(), "{}", r.summary());
}

#[test]
fn repeated_extrusion_builds_a_stack_as_it_does_in_max() {
    // Extrude hands back the new faces, which is what makes this work.
    let mut m = cube(2.0);
    let mut faces = vec![0u32, 1];
    for _ in 0..4 {
        let (_, next) = extrude(&mut m, &faces, 0.4, 0.1);
        assert!(!next.is_empty());
        faces = next;
    }
    assert_solid(&m, "four stacked extrusions");
    // 12 to start, plus 8 wall triangles per extrusion.
    assert_eq!(m.tri_count(), 12 + 4 * 8);
}
