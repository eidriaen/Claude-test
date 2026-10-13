//! Picking: a click in the viewport becoming a thing in the model.

use glam::{Vec2, Vec3};
use mesh_core::camera::Camera;
use mesh_core::mesh::Mesh;
use mesh_core::pick::{pick_edge, pick_vertex, ray_triangle, raycast, Ray};
use mesh_core::primitives::{cube, plane};

fn looking_down_z() -> Camera {
    Camera { target: Vec3::ZERO, distance: 5.0, yaw: 0.0, pitch: 0.0, ..Default::default() }
}

#[test]
fn a_ray_straight_at_a_triangle_hits_it() {
    let ray = Ray { origin: Vec3::new(0.25, 0.25, 1.0), dir: -Vec3::Z };
    let t = ray_triangle(&ray, Vec3::ZERO, Vec3::X, Vec3::Y).expect("should hit");
    assert!((t - 1.0).abs() < 1e-5, "t was {t}");
}

#[test]
fn a_ray_beside_the_triangle_misses() {
    let ray = Ray { origin: Vec3::new(2.0, 2.0, 1.0), dir: -Vec3::Z };
    assert!(ray_triangle(&ray, Vec3::ZERO, Vec3::X, Vec3::Y).is_none());
}

#[test]
fn a_triangle_behind_the_ray_is_not_a_hit() {
    // Facing away, so the only intersection is at negative t.
    let ray = Ray { origin: Vec3::new(0.25, 0.25, 1.0), dir: Vec3::Z };
    assert!(ray_triangle(&ray, Vec3::ZERO, Vec3::X, Vec3::Y).is_none());
}

#[test]
fn a_back_facing_triangle_is_still_pickable() {
    // Not culled deliberately: this app exists partly to find faces pointing
    // the wrong way, and one you cannot click is one you cannot fix.
    let ray = Ray { origin: Vec3::new(0.25, 0.25, 1.0), dir: -Vec3::Z };
    let front = ray_triangle(&ray, Vec3::ZERO, Vec3::X, Vec3::Y);
    let back = ray_triangle(&ray, Vec3::ZERO, Vec3::Y, Vec3::X);
    assert!(front.is_some() && back.is_some(), "{front:?} {back:?}");
}

#[test]
fn a_degenerate_triangle_is_not_a_hit_and_not_a_crash() {
    let ray = Ray { origin: Vec3::new(0.0, 0.0, 1.0), dir: -Vec3::Z };
    assert!(ray_triangle(&ray, Vec3::ZERO, Vec3::X, Vec3::X).is_none());
}

#[test]
fn raycast_returns_the_nearest_face_not_just_any() {
    // Two planes; the ray must stop at the near one.
    let mut m = plane(4.0);
    let mut back = plane(4.0);
    for p in &mut back.positions {
        p.y -= 2.0;
    }
    let offset = m.positions.len() as u32;
    m.positions.extend(back.positions);
    m.indices.extend(back.indices.iter().map(|i| i + offset));

    let ray = Ray { origin: Vec3::new(0.1, 5.0, 0.1), dir: -Vec3::Y };
    let hit = raycast(&m, &ray).expect("should hit something");
    assert!((hit.point.y - 0.0).abs() < 1e-4, "hit the far plane at {:?}", hit.point);
    assert!(hit.tri < 2, "should be one of the near plane's triangles");
}

#[test]
fn a_ray_through_the_middle_of_the_screen_hits_what_the_camera_looks_at() {
    let cam = looking_down_z();
    let ray = cam.ray_through(Vec2::ZERO, 16.0 / 9.0);
    let m = cube(2.0);
    let hit = raycast(&m, &ray).expect("the cube is dead centre");
    assert!(hit.point.length() < 1.5, "hit at {:?}", hit.point);
}

#[test]
fn a_ray_through_a_corner_of_the_screen_misses_a_small_object() {
    let cam = looking_down_z();
    let ray = cam.ray_through(Vec2::new(-0.99, 0.99), 16.0 / 9.0);
    assert!(raycast(&cube(0.2), &ray).is_none());
}

#[test]
fn the_ray_starts_in_front_of_the_camera_and_points_away_from_it() {
    let cam = looking_down_z();
    for ndc in [Vec2::ZERO, Vec2::new(0.5, -0.5), Vec2::new(-1.0, 1.0)] {
        let ray = cam.ray_through(ndc, 1.5);
        assert!(ray.dir.is_finite() && (ray.dir.length() - 1.0).abs() < 1e-4);
        assert!(
            ray.dir.dot(cam.forward()) > 0.0,
            "the ray should go into the scene, not behind the camera"
        );
    }
}

#[test]
fn clicking_on_a_vertex_picks_it() {
    let cam = looking_down_z();
    let aspect = 1.0;
    let m = cube(2.0);
    // Project a known vertex, then click exactly there.
    let vp = cam.view_projection(aspect);
    for v in 0..m.vertex_count() as u32 {
        let clip = vp * m.positions[v as usize].extend(1.0);
        if clip.w <= 0.0 {
            continue;
        }
        let ndc = Vec2::new(clip.x / clip.w, clip.y / clip.w);
        let picked = pick_vertex(&m, &cam, aspect, ndc, 0.02);
        let p = picked.expect("clicking on a vertex should find one");
        let hit = m.positions[p as usize];
        let want = m.positions[v as usize];
        // Several cube corners project to the same screen point from some
        // angles; any of those is a correct answer, so compare screen position
        // rather than index.
        let hit_clip = vp * hit.extend(1.0);
        let hit_ndc = Vec2::new(hit_clip.x / hit_clip.w, hit_clip.y / hit_clip.w);
        assert!(
            (hit_ndc - ndc).length() < 0.02,
            "picked {hit:?} for a click on {want:?}"
        );
    }
}

#[test]
fn clicking_empty_space_picks_nothing() {
    let cam = looking_down_z();
    let m = cube(0.5);
    assert!(pick_vertex(&m, &cam, 1.0, Vec2::new(0.95, 0.95), 0.02).is_none());
    assert!(pick_edge(&m, &cam, 1.0, Vec2::new(0.95, 0.95), 0.02).is_none());
}

#[test]
fn the_nearer_of_two_vertices_at_the_same_spot_wins() {
    // A silhouette click must not reach through to the far side.
    let cam = looking_down_z();
    let m = Mesh {
        positions: vec![Vec3::new(0.0, 0.0, 1.0), Vec3::new(0.0, 0.0, -1.0)],
        indices: vec![],
        ..Default::default()
    };
    let picked = pick_vertex(&m, &cam, 1.0, Vec2::ZERO, 0.1).expect("one of them");
    assert_eq!(picked, 0, "the near vertex, not the one behind it");
}

#[test]
fn clicking_along_an_edge_picks_that_edge() {
    let cam = looking_down_z();
    let aspect = 1.0;
    let m = plane(2.0);
    let vp = cam.view_projection(aspect);
    let a = m.positions[0];
    let b = m.positions[1];
    let mid = (a + b) / 2.0;
    let clip = vp * mid.extend(1.0);
    let ndc = Vec2::new(clip.x / clip.w, clip.y / clip.w);

    let e = pick_edge(&m, &cam, aspect, ndc, 0.05).expect("should find an edge");
    assert!(
        e == (0, 1) || e == (0, 3) || e == (1, 2),
        "picked {e:?}, which is not an edge near that point"
    );
}

#[test]
fn picking_an_empty_mesh_is_not_a_crash() {
    let cam = looking_down_z();
    let m = Mesh::new();
    assert!(raycast(&m, &cam.ray_through(Vec2::ZERO, 1.0)).is_none());
    assert!(pick_vertex(&m, &cam, 1.0, Vec2::ZERO, 0.1).is_none());
    assert!(pick_edge(&m, &cam, 1.0, Vec2::ZERO, 0.1).is_none());
}
