//! Viewport navigation, checked without a viewport.

use glam::{Vec2, Vec3, Vec4Swizzles};
use mesh_core::camera::Camera;
use mesh_core::primitives::{cube, sphere};

/// Where a world point lands in normalised device coordinates.
/// Inside [-1, 1] on both axes means it is on screen.
fn project(cam: &Camera, aspect: f32, p: Vec3) -> Vec3 {
    let clip = cam.view_projection(aspect) * p.extend(1.0);
    clip.xyz() / clip.w
}

fn on_screen(cam: &Camera, aspect: f32, p: Vec3) -> bool {
    let ndc = project(cam, aspect, p);
    ndc.x.abs() <= 1.0 && ndc.y.abs() <= 1.0 && (0.0..=1.0).contains(&ndc.z)
}

#[test]
fn the_camera_looks_at_its_target() {
    let cam = Camera::default();
    let to_target = (cam.target - cam.eye()).normalize();
    assert!((to_target - cam.forward()).length() < 1e-5);
    assert!((cam.eye() - cam.target).length() - cam.distance < 1e-4);
}

#[test]
fn orbiting_keeps_the_target_and_the_distance() {
    // Orbit moves the camera around the model. If either of these drifts, the
    // model slides away as you turn it -- the classic broken orbit.
    let mut cam = Camera::default();
    let (target, distance) = (cam.target, cam.distance);
    for _ in 0..40 {
        cam.orbit(Vec2::new(17.0, 9.0), 0.01);
    }
    assert!((cam.target - target).length() < 1e-5);
    assert!((cam.distance - distance).abs() < 1e-4);
}

#[test]
fn pitch_stops_short_of_the_poles() {
    // At exactly straight up the view matrix degenerates and the picture
    // snaps over on the last pixel of travel.
    let mut cam = Camera::default();
    for _ in 0..500 {
        cam.orbit(Vec2::new(0.0, 50.0), 0.01);
    }
    assert!(cam.pitch < std::f32::consts::FRAC_PI_2);
    assert!(cam.view().is_finite());

    for _ in 0..1000 {
        cam.orbit(Vec2::new(0.0, -50.0), 0.01);
    }
    assert!(cam.pitch > -std::f32::consts::FRAC_PI_2);
    assert!(cam.view().is_finite());
}

#[test]
fn panning_moves_the_model_the_same_amount_on_screen_at_any_distance() {
    // Scaled by distance, or panning crawls when zoomed out and flies when
    // zoomed in. Measured by watching a fixed world point move across the
    // screen -- the target itself moves with the pan, so it cannot be the
    // thing being measured.
    let drag = Vec2::new(100.0, 0.0);
    let height = 800.0;
    let aspect = 1.0;

    let shift_on_screen = |distance: f32| {
        let mut cam = Camera { distance, ..Default::default() };
        let marker = cam.target;          // a point at pivot depth
        let before = project(&cam, aspect, marker);
        cam.pan(drag, height);
        let after = project(&cam, aspect, marker);
        (after - before).length()
    };

    let near = shift_on_screen(2.0);
    let far = shift_on_screen(200.0);
    assert!(near > 0.01, "the pan did nothing");
    let ratio = near / far;
    assert!((0.95..1.05).contains(&ratio), "near moved {near}, far moved {far}");
}

#[test]
fn zoom_is_proportional_and_never_passes_through_the_pivot() {
    let mut cam = Camera::default();
    let start = cam.distance;
    cam.zoom(1.0);
    let in_once = cam.distance;
    assert!(in_once < start, "scrolling up should move closer");

    // Each notch takes the same proportion, so the ratio holds at any scale.
    let mut far = Camera { distance: start * 100.0, ..Default::default() };
    far.zoom(1.0);
    let r1 = in_once / start;
    let r2 = far.distance / (start * 100.0);
    assert!((r1 - r2).abs() < 1e-4, "{r1} vs {r2}");

    // And it can never arrive at or pass the pivot.
    for _ in 0..2000 {
        cam.zoom(1.0);
    }
    assert!(cam.distance > 0.0, "zoom reached the pivot");
    assert!(cam.distance.is_finite());
}

#[test]
fn framing_puts_the_whole_model_on_screen() {
    for aspect in [0.5, 1.0, 16.0 / 9.0, 3.0] {
        let m = sphere(2.5, 24, 16);
        let bounds = m.bounds().unwrap();
        let mut cam = Camera::default();
        cam.frame(bounds, aspect);

        for p in &m.positions {
            assert!(
                on_screen(&cam, aspect, *p),
                "a vertex is off screen at aspect {aspect}: {p:?} -> {:?}",
                project(&cam, aspect, *p)
            );
        }
    }
}

#[test]
fn framing_a_wide_model_on_a_wide_window_does_not_cut_the_sides_off() {
    // Fitting only the vertical field of view is the usual bug, and it shows
    // up exactly when you press Z on something long.
    let mut m = cube(1.0);
    for p in &mut m.positions {
        p.x *= 20.0;
    }
    let aspect = 16.0 / 9.0;
    let mut cam = Camera::default();
    cam.frame(m.bounds().unwrap(), aspect);
    for p in &m.positions {
        assert!(on_screen(&cam, aspect, *p), "cut off at {p:?}");
    }
}

#[test]
fn framing_a_single_point_does_not_divide_by_zero() {
    let mut cam = Camera::default();
    cam.frame((Vec3::ONE, Vec3::ONE), 1.6);
    assert!(cam.distance.is_finite() && cam.distance > 0.0);
    assert!(cam.view_projection(1.6).is_finite());
}

#[test]
fn the_basis_stays_orthonormal_all_the_way_round() {
    let mut cam = Camera::default();
    for i in 0..200 {
        cam.orbit(Vec2::new(i as f32, (i % 7) as f32 * 3.0), 0.02);
        let (r, u, f) = (cam.right(), cam.up(), cam.forward());
        assert!((r.length() - 1.0).abs() < 1e-3, "right not unit at step {i}");
        assert!((u.length() - 1.0).abs() < 1e-3, "up not unit at step {i}");
        assert!(r.dot(f).abs() < 1e-3, "right and forward not perpendicular");
        assert!(u.dot(f).abs() < 1e-3, "up and forward not perpendicular");
    }
}
