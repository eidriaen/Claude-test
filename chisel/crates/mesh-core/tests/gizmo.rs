//! The gizmo: grabbing handles and what dragging them means.

use glam::{Vec2, Vec3, Vec4Swizzles};
use mesh_core::camera::Camera;
use mesh_core::gizmo::*;
use mesh_core::transform::Tool;

fn cam() -> Camera {
    Camera { target: Vec3::ZERO, distance: 8.0, yaw: 0.7, pitch: 0.5, ..Default::default() }
}

fn to_ndc(c: &Camera, aspect: f32, p: Vec3) -> Vec2 {
    let clip = c.view_projection(aspect) * p.extend(1.0);
    clip.xy() / clip.w
}

#[test]
fn clicking_an_axis_bar_grabs_that_axis() {
    let c = cam();
    let aspect = 16.0 / 9.0;
    let origin = Vec3::ZERO;
    let scale = screen_scale(&c, origin, 0.12);

    for (axis, want) in [
        (Vec3::X, Handle::X),
        (Vec3::Y, Handle::Y),
        (Vec3::Z, Handle::Z),
    ] {
        // Two thirds along the bar: clear of the centre box and of the plane
        // handles that sit near the origin.
        let point = origin + axis * scale * 0.7;
        let got = pick_handle(&c, aspect, origin, scale, to_ndc(&c, aspect, point), Tool::Move);
        assert_eq!(got, Some(want), "clicking along {axis:?}");
    }
}

#[test]
fn clicking_the_centre_grabs_the_uniform_handle() {
    let c = cam();
    let origin = Vec3::new(1.0, 2.0, -1.0);
    let scale = screen_scale(&c, origin, 0.12);
    let ndc = to_ndc(&c, 1.5, origin);
    assert_eq!(
        pick_handle(&c, 1.5, origin, scale, ndc, Tool::Scale),
        Some(Handle::Uniform)
    );
}

#[test]
fn clicking_well_away_from_the_gizmo_grabs_nothing() {
    let c = cam();
    let scale = screen_scale(&c, Vec3::ZERO, 0.12);
    assert_eq!(
        pick_handle(&c, 1.5, Vec3::ZERO, scale, Vec2::new(0.95, -0.95), Tool::Move),
        None
    );
}

#[test]
fn the_select_tool_has_no_handles_to_grab() {
    // Otherwise the gizmo would eat clicks meant for selection.
    let c = cam();
    let scale = screen_scale(&c, Vec3::ZERO, 0.12);
    let ndc = to_ndc(&c, 1.5, Vec3::ZERO);
    assert_eq!(pick_handle(&c, 1.5, Vec3::ZERO, scale, ndc, Tool::Select), None);
}

#[test]
fn the_gizmo_keeps_its_size_on_screen_however_far_away_it_is() {
    // In world units it shrinks to nothing zoomed out and swallows the model
    // zoomed in -- useless at exactly the moments you reach for it.
    let aspect = 1.0;
    let near = Camera { distance: 2.0, ..cam() };
    let far = Camera { distance: 200.0, ..cam() };

    let measure = |c: &Camera| {
        let scale = screen_scale(c, Vec3::ZERO, 0.12);
        let a = to_ndc(c, aspect, Vec3::ZERO);
        let b = to_ndc(c, aspect, Vec3::Y * scale);
        (b - a).length()
    };
    let ratio = measure(&near) / measure(&far);
    assert!((0.95..1.05).contains(&ratio), "near {} far {}", measure(&near), measure(&far));
}

#[test]
fn dragging_an_axis_follows_the_cursor_along_that_axis() {
    let c = cam();
    let aspect = 1.6;
    let origin = Vec3::ZERO;

    // Aim at a point three units along X and check the drag lands there.
    let target = Vec3::new(3.0, 0.0, 0.0);
    let ndc = to_ndc(&c, aspect, target);
    let got = closest_point_on_axis(&c, aspect, origin, Vec3::X, ndc).expect("on screen");

    assert!((got.x - 3.0).abs() < 0.05, "landed at {got:?}");
    assert!(got.y.abs() < 1e-4 && got.z.abs() < 1e-4, "drifted off the axis: {got:?}");
}

#[test]
fn an_axis_drag_never_moves_in_the_other_two() {
    // The bug this exists for: a drag that creeps sideways, which looks almost
    // right and quietly ruins alignment.
    let c = cam();
    let aspect = 1.6;
    for ndc in [Vec2::new(0.3, 0.2), Vec2::new(-0.5, 0.4), Vec2::new(0.1, -0.7)] {
        let p = closest_point_on_axis(&c, aspect, Vec3::ZERO, Vec3::Y, ndc).expect("on screen");
        assert!(p.x.abs() < 1e-4 && p.z.abs() < 1e-4, "{p:?} is off the Y axis");
    }
}

#[test]
fn looking_straight_down_an_axis_refuses_to_guess() {
    // Every point on the axis projects to the same pixel, so there is no
    // answer; returning one sends the selection to infinity.
    let c = Camera { target: Vec3::ZERO, distance: 5.0, yaw: 0.0, pitch: 0.0, ..Default::default() };
    // The camera looks along -Z, so dragging the Z axis is the degenerate case.
    assert_eq!(closest_point_on_axis(&c, 1.0, Vec3::ZERO, Vec3::Z, Vec2::ZERO), None);
}

#[test]
fn a_plane_drag_stays_in_its_plane() {
    let c = cam();
    let aspect = 1.6;
    let hit = ray_plane(&c, aspect, Vec3::ZERO, Vec3::Y, Vec2::new(0.2, -0.3))
        .expect("the ground plane is in view");
    assert!(hit.y.abs() < 1e-4, "left the plane at {hit:?}");
}

#[test]
fn a_plane_seen_edge_on_has_no_answer() {
    let c = Camera { target: Vec3::ZERO, distance: 5.0, yaw: 0.0, pitch: 0.0, ..Default::default() };
    assert_eq!(ray_plane(&c, 1.0, Vec3::ZERO, Vec3::Z, Vec2::ZERO).is_none(), false);
    // Looking level at the ground plane: the ray never descends to meet it.
    assert!(ray_plane(&c, 1.0, Vec3::ZERO, Vec3::Y, Vec2::new(0.0, 0.5)).is_none());
}

#[test]
fn rotating_a_quarter_turn_reads_as_a_quarter_turn() {
    let c = Camera { target: Vec3::ZERO, distance: 10.0, yaw: 0.0, pitch: 1.2, ..Default::default() };
    let aspect = 1.0;
    let (u, v) = basis_for(Vec3::Y);
    let from = to_ndc(&c, aspect, u * 3.0);
    let to = to_ndc(&c, aspect, v * 3.0);

    let angle = drag_angle(&c, aspect, Vec3::ZERO, Vec3::Y, from, to).expect("both on screen");
    assert!(
        (angle.abs() - std::f32::consts::FRAC_PI_2).abs() < 0.05,
        "got {angle} radians"
    );
}

#[test]
fn a_rotation_drag_takes_the_short_way_round() {
    // Crossing the seam at pi must not read as a full turn backwards.
    let c = Camera { target: Vec3::ZERO, distance: 10.0, yaw: 0.0, pitch: 1.3, ..Default::default() };
    let aspect = 1.0;
    let (u, v) = basis_for(Vec3::Y);
    let just_before = (u * (-0.99f32).cos() + v * (-0.99f32).sin()) * 3.0;
    let just_after = (u * 0.99f32.cos() + v * 0.99f32.sin()) * 3.0;
    let angle = drag_angle(
        &c,
        aspect,
        Vec3::ZERO,
        Vec3::Y,
        to_ndc(&c, aspect, just_before),
        to_ndc(&c, aspect, just_after),
    )
    .unwrap();
    assert!(angle.abs() <= std::f32::consts::PI, "{angle} is more than half a turn");
}

#[test]
fn scaling_out_grows_and_scaling_in_shrinks() {
    let c = cam();
    let aspect = 1.6;
    let centre = to_ndc(&c, aspect, Vec3::ZERO);
    let near = centre + Vec2::new(0.1, 0.0);
    let far = centre + Vec2::new(0.3, 0.0);

    let out = drag_scale_factor(&c, aspect, Vec3::ZERO, near, far).unwrap();
    let in_ = drag_scale_factor(&c, aspect, Vec3::ZERO, far, near).unwrap();
    assert!(out > 1.0, "dragging away should grow, got {out}");
    assert!(in_ < 1.0, "dragging in should shrink, got {in_}");
    assert!((out * in_ - 1.0).abs() < 1e-3, "they should be inverses");
}

#[test]
fn a_scale_drag_that_starts_on_the_centre_is_refused() {
    let c = cam();
    let centre = to_ndc(&c, 1.6, Vec3::ZERO);
    assert_eq!(drag_scale_factor(&c, 1.6, Vec3::ZERO, centre, centre + Vec2::new(0.2, 0.0)), None);
}

#[test]
fn every_handle_has_a_colour_and_the_axes_differ() {
    let x = Handle::X.colour();
    let y = Handle::Y.colour();
    let z = Handle::Z.colour();
    assert_ne!(x, y);
    assert_ne!(y, z);
    assert_eq!(Handle::X.axis(), Some(Vec3::X));
    assert_eq!(Handle::XY.plane_normal(), Some(Vec3::Z));
    assert_eq!(Handle::Uniform.axis(), None);
}

#[test]
fn the_perpendicular_basis_never_collapses() {
    // Picking a fixed helper axis gives a zero cross product exactly when the
    // axis lines up with it.
    for axis in [Vec3::X, Vec3::Y, Vec3::Z, Vec3::new(1.0, 1.0, 1.0).normalize()] {
        let (u, v) = basis_for(axis);
        assert!((u.length() - 1.0).abs() < 1e-4, "u collapsed for {axis:?}");
        assert!((v.length() - 1.0).abs() < 1e-4, "v collapsed for {axis:?}");
        assert!(u.dot(axis).abs() < 1e-4 && v.dot(axis).abs() < 1e-4);
    }
}
