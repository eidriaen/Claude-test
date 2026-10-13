//! Transforms, and the modifier stack.

use glam::Vec3;
use mesh_core::mesh::Mesh;
use mesh_core::modifier::{FixNormals, Modifier, Smooth, Stack, Subdivide};
use mesh_core::normals::signed_volume;
use mesh_core::primitives::{cube, sphere};
use mesh_core::selection::{Level, Selection};
use mesh_core::transform::{affected, centre_of, pivot_point, rotate, scale, translate, Axes, Pivot};

#[test]
fn moving_a_selection_leaves_everything_else_where_it_was() {
    let mut m = cube(2.0);
    let before = m.positions.clone();
    let mut sel = Selection::new(Level::Vertex);
    sel.add(0);

    let verts = affected(&m, &sel);
    translate(&mut m, &verts, Vec3::new(5.0, 0.0, 0.0));

    assert_eq!(m.positions[0], before[0] + Vec3::new(5.0, 0.0, 0.0));
    for i in 1..before.len() {
        assert_eq!(m.positions[i], before[i], "vertex {i} should not have moved");
    }
}

#[test]
fn nothing_selected_at_a_sub_object_level_transforms_nothing() {
    // "Move with nothing selected" quietly moving the whole model is a nasty
    // surprise, and the kind of thing you only notice after saving.
    let m = cube(2.0);
    let sel = Selection::new(Level::Vertex);
    assert!(affected(&m, &sel).is_empty());
}

#[test]
fn at_object_level_a_transform_moves_the_whole_mesh() {
    let m = cube(2.0);
    let sel = Selection::new(Level::Object);
    assert_eq!(affected(&m, &sel).len(), m.vertex_count());
}

#[test]
fn rotating_about_the_selection_centre_keeps_it_in_place() {
    let mut m = sphere(1.0, 16, 12);
    let sel = Selection::new(Level::Object);
    let verts = affected(&m, &sel);
    let centre = centre_of(&m, &verts);

    rotate(&mut m, &verts, centre, Vec3::Y, 1.0);

    let after = centre_of(&m, &verts);
    assert!((after - centre).length() < 1e-4, "the centre drifted to {after:?}");
}

#[test]
fn rotating_about_the_origin_swings_an_offset_model_around() {
    let mut m = cube(1.0);
    for p in &mut m.positions {
        p.x += 5.0;
    }
    let sel = Selection::new(Level::Object);
    let verts = affected(&m, &sel);

    let pivot = pivot_point(&m, &verts, Pivot::Origin);
    rotate(&mut m, &verts, pivot, Vec3::Y, std::f32::consts::PI);

    let centre = centre_of(&m, &verts);
    assert!((centre.x + 5.0).abs() < 1e-3, "should have swung to -5, got {centre:?}");
}

#[test]
fn a_full_turn_puts_everything_back() {
    let mut m = cube(2.0);
    let before = m.positions.clone();
    let verts: Vec<u32> = (0..m.vertex_count() as u32).collect();
    rotate(&mut m, &verts, Vec3::ZERO, Vec3::new(1.0, 2.0, 3.0), std::f32::consts::TAU);
    for (a, b) in m.positions.iter().zip(&before) {
        assert!((*a - *b).length() < 1e-4, "{a:?} vs {b:?}");
    }
}

#[test]
fn scaling_by_one_changes_nothing() {
    let mut m = sphere(1.0, 12, 8);
    let before = m.positions.clone();
    let verts: Vec<u32> = (0..m.vertex_count() as u32).collect();
    scale(&mut m, &verts, Vec3::ZERO, Vec3::ONE);
    for (a, b) in m.positions.iter().zip(&before) {
        assert!((*a - *b).length() < 1e-6);
    }
}

#[test]
fn scaling_to_zero_is_clamped_so_it_can_be_undone_by_scaling_back() {
    // A true zero flattens every vertex onto a plane, and no amount of scaling
    // up recovers the shape -- the information is gone, not hidden.
    let mut m = cube(2.0);
    let verts: Vec<u32> = (0..m.vertex_count() as u32).collect();
    scale(&mut m, &verts, Vec3::ZERO, Vec3::new(0.0, 1.0, 1.0));
    let widths: Vec<f32> = m.positions.iter().map(|p| p.x).collect();
    assert!(widths.iter().any(|x| x.abs() > 0.0), "everything collapsed to x = 0");

    scale(&mut m, &verts, Vec3::ZERO, Vec3::new(1.0 / 1e-4 / 1.0, 1.0, 1.0));
    assert!(m.positions.iter().any(|p| p.x.abs() > 0.5), "the shape did not come back");
}

#[test]
fn axis_masks_say_what_they_mean() {
    assert_eq!(Axes::X.mask(), Vec3::new(1.0, 0.0, 0.0));
    assert_eq!(Axes::X.axis(), Some(Vec3::X));
    assert_eq!(Axes::ALL.axis(), None, "all three is not a single axis");
}

// --- the modifier stack ----------------------------------------------------

#[test]
fn a_subdivide_quadruples_the_triangles_without_changing_the_shape() {
    let base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    let out = stack.evaluate(&base, 1);

    assert_eq!(out.tri_count(), base.tri_count() * 4);
    // Linear subdivision keeps the surface exactly where it was, so the volume
    // is unchanged -- that is what makes it safe to put under a Smooth.
    assert!((signed_volume(&out) - signed_volume(&base)).abs() < 1e-3);
}

#[test]
fn subdivide_shares_its_midpoints_between_neighbouring_faces() {
    // One new vertex per edge, not per face. Getting this wrong splits the
    // mesh along every edge, which looks fine until you smooth it.
    let base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    let out = stack.evaluate(&base, 1);

    let mut shared = out.clone();
    let removed = shared.weld(1e-5);
    assert_eq!(removed, 0, "{removed} vertices were duplicates of another");
}

#[test]
fn the_base_mesh_is_never_touched() {
    // The whole point of a stack: what you built stays as you built it.
    let base = cube(2.0);
    let before = base.clone();
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 2 });
    stack.push(Smooth { iterations: 3, strength: 0.5 });
    let _ = stack.evaluate(&base, 1);
    assert_eq!(base, before);
}

#[test]
fn order_changes_the_result() {
    let base = cube(2.0);
    let mut a = Stack::default();
    a.push(Subdivide { iterations: 1 });
    a.push(Smooth { iterations: 2, strength: 0.5 });
    let smooth_on_top = a.evaluate(&base, 1);

    let mut b = Stack::default();
    b.push(Smooth { iterations: 2, strength: 0.5 });
    b.push(Subdivide { iterations: 1 });
    let subdivide_on_top = b.evaluate(&base, 1);

    assert_ne!(
        smooth_on_top.positions, subdivide_on_top.positions,
        "smoothing before and after subdividing should not agree"
    );
}

#[test]
fn turning_a_modifier_off_and_on_again_gets_the_same_mesh_back() {
    let base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    stack.push(Smooth { iterations: 2, strength: 0.4 });

    let full = stack.evaluate(&base, 1);
    stack.set_enabled(1, false);
    let without = stack.evaluate(&base, 1);
    stack.set_enabled(1, true);
    let again = stack.evaluate(&base, 1);

    assert_ne!(full.positions, without.positions);
    assert_eq!(full.positions, again.positions, "a stale cache was served");
}

#[test]
fn editing_the_base_invalidates_the_cache_above_it() {
    let mut base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    let first = stack.evaluate(&base, 1);

    base.positions[0].x += 10.0;
    // A different signature is what tells the stack the base moved. Passing
    // the same one would serve the old shape forever.
    let second = stack.evaluate(&base, 2);
    assert_ne!(first.positions, second.positions);
}

#[test]
fn collapsing_bakes_the_result_and_empties_the_stack() {
    let mut base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    stack.collapse(&mut base, 1);

    assert!(stack.is_empty());
    assert_eq!(base.tri_count(), 48, "the subdivision is part of the mesh now");
}

#[test]
fn a_modifier_can_fix_normals_like_the_menu_item_does() {
    let mut base = cube(2.0);
    for t in 0..base.tri_count() {
        base.indices.swap(t * 3 + 1, t * 3 + 2);
    }
    assert!(signed_volume(&base) < 0.0);

    let mut stack = Stack::default();
    stack.push(FixNormals);
    let out = stack.evaluate(&base, 1);
    assert!(signed_volume(&out) > 0.0);
}

#[test]
fn moving_an_entry_reorders_and_invalidates() {
    let base = cube(2.0);
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 1 });
    stack.push(Smooth { iterations: 2, strength: 0.5 });
    let before = stack.evaluate(&base, 1);

    stack.move_entry(1, 0);
    let after = stack.evaluate(&base, 1);
    assert_ne!(before.positions, after.positions);
}

#[test]
fn an_empty_stack_hands_the_base_straight_back() {
    let base = sphere(1.0, 12, 8);
    let mut stack = Stack::default();
    assert_eq!(stack.evaluate(&base, 1), base);
}

#[test]
fn smoothing_shrinks_a_sphere_which_is_what_this_algorithm_does() {
    // Laplacian smoothing pulls every vertex toward its neighbours, so a
    // closed surface loses volume. Worth asserting so nobody later "fixes"
    // the shrinkage by scaling the result and hides a real bug.
    let base = sphere(1.0, 24, 16);
    let mut stack = Stack::default();
    stack.push(Smooth { iterations: 5, strength: 0.5 });
    let out = stack.evaluate(&base, 1);
    assert!(signed_volume(&out) < signed_volume(&base));
    assert!(signed_volume(&out) > 0.0, "it should shrink, not collapse");
}

#[test]
fn a_modifier_on_an_empty_mesh_is_not_a_crash() {
    let base = Mesh::new();
    let mut stack = Stack::default();
    stack.push(Subdivide { iterations: 2 });
    stack.push(Smooth { iterations: 2, strength: 0.5 });
    stack.push(FixNormals);
    let out = stack.evaluate(&base, 1);
    assert_eq!(out.vertex_count(), 0);
}

#[test]
fn a_modifier_names_itself_for_the_stack_list() {
    assert!(Subdivide { iterations: 2 }.name().contains('2'));
    assert_eq!(FixNormals.name(), "Fix normals");
}
