//! Turning a click into a thing.
//!
//! All of it is maths against the same matrices the viewport draws with, so
//! all of it can be checked without a window: build a ray through a known
//! point, assert it hits what is there.

use crate::camera::Camera;
use crate::mesh::Mesh;
use crate::selection::EdgeId;
use glam::{Vec2, Vec3, Vec4Swizzles};

#[derive(Clone, Copy, Debug)]
pub struct Ray {
    pub origin: Vec3,
    pub dir: Vec3,
}

impl Ray {
    pub fn at(&self, t: f32) -> Vec3 {
        self.origin + self.dir * t
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Hit {
    pub tri: usize,
    /// Distance along the ray.
    pub t: f32,
    pub point: Vec3,
}

impl Camera {
    /// A ray through a point given in normalised device coordinates
    /// (-1..1, with +Y up).
    pub fn ray_through(&self, ndc: Vec2, aspect: f32) -> Ray {
        let inv = self.view_projection(aspect).inverse();
        // Near and far plane points, unprojected. Taking the eye position as
        // the origin instead looks equivalent and is not: it is only true for
        // a perspective camera, and this has to keep working if an orthographic
        // view is added.
        let near = inv * ndc.extend(0.0).extend(1.0);
        let far = inv * ndc.extend(1.0).extend(1.0);
        let near = near.xyz() / near.w;
        let far = far.xyz() / far.w;
        Ray { origin: near, dir: (far - near).normalize_or_zero() }
    }
}

/// Möller–Trumbore, without back-face culling.
///
/// Not culled deliberately: this app exists partly to find faces pointing the
/// wrong way, and a face you cannot click because it is inside out is a face
/// you cannot fix.
pub fn ray_triangle(ray: &Ray, a: Vec3, b: Vec3, c: Vec3) -> Option<f32> {
    const EPS: f32 = 1e-7;
    let e1 = b - a;
    let e2 = c - a;
    let h = ray.dir.cross(e2);
    let det = e1.dot(h);
    if det.abs() < EPS {
        return None; // parallel, or a degenerate triangle
    }
    let inv_det = 1.0 / det;
    let s = ray.origin - a;
    let u = inv_det * s.dot(h);
    if !(-EPS..=1.0 + EPS).contains(&u) {
        return None;
    }
    let q = s.cross(e1);
    let v = inv_det * ray.dir.dot(q);
    if v < -EPS || u + v > 1.0 + EPS {
        return None;
    }
    let t = inv_det * e2.dot(q);
    (t > EPS).then_some(t)
}

/// The nearest triangle the ray hits.
pub fn raycast(mesh: &Mesh, ray: &Ray) -> Option<Hit> {
    let mut best: Option<Hit> = None;
    for tri in 0..mesh.tri_count() {
        let [a, b, c] = mesh.tri_positions(tri);
        if let Some(t) = ray_triangle(ray, a, b, c) {
            if best.is_none_or(|h| t < h.t) {
                best = Some(Hit { tri, t, point: ray.at(t) });
            }
        }
    }
    best
}

/// The vertex nearest a click, in screen space.
///
/// Screen space, not world space: the vertex that *looks* closest to the
/// cursor is the one you meant, and a world-space search picks whatever
/// happens to be nearest the ray in 3D, which on a dense mesh is rarely the
/// one under the pointer.
pub fn pick_vertex(
    mesh: &Mesh,
    camera: &Camera,
    aspect: f32,
    ndc: Vec2,
    radius_ndc: f32,
) -> Option<u32> {
    let vp = camera.view_projection(aspect);
    let mut best: Option<(f32, f32, u32)> = None; // (screen dist, depth, index)
    for (i, p) in mesh.positions.iter().enumerate() {
        let clip = vp * p.extend(1.0);
        if clip.w <= 0.0 {
            continue; // behind the camera
        }
        let s = clip.xy() / clip.w;
        let d = (s - ndc).length();
        if d > radius_ndc {
            continue;
        }
        let depth = clip.z / clip.w;
        // Nearest to the cursor wins; ties go to whichever is in front, so
        // clicking a silhouette does not reach through to the far side.
        let better = best.is_none_or(|(bd, bz, _)| {
            d < bd - 1e-6 || ((d - bd).abs() <= 1e-6 && depth < bz)
        });
        if better {
            best = Some((d, depth, i as u32));
        }
    }
    best.map(|(_, _, i)| i)
}

/// The edge nearest a click, in screen space.
pub fn pick_edge(
    mesh: &Mesh,
    camera: &Camera,
    aspect: f32,
    ndc: Vec2,
    radius_ndc: f32,
) -> Option<EdgeId> {
    let vp = camera.view_projection(aspect);
    let project = |v: u32| -> Option<Vec2> {
        let clip = vp * mesh.positions[v as usize].extend(1.0);
        (clip.w > 0.0).then(|| clip.xy() / clip.w)
    };
    let mut best: Option<(f32, EdgeId)> = None;
    for e in crate::selection::all_edges(mesh) {
        let (Some(a), Some(b)) = (project(e.0), project(e.1)) else { continue };
        let d = point_segment_distance(ndc, a, b);
        if d <= radius_ndc && best.is_none_or(|(bd, _)| d < bd) {
            best = Some((d, e));
        }
    }
    best.map(|(_, e)| e)
}

fn point_segment_distance(p: Vec2, a: Vec2, b: Vec2) -> f32 {
    let ab = b - a;
    let len2 = ab.length_squared();
    if len2 < 1e-12 {
        return (p - a).length();
    }
    let t = ((p - a).dot(ab) / len2).clamp(0.0, 1.0);
    (p - (a + ab * t)).length()
}
