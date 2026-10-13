//! The transform gizmo: which handle you grabbed, and what dragging it means.
//!
//! All of it is maths against the camera, so all of it is testable without a
//! window. Worth the separation: an axis drag that creeps in a second axis, or
//! a rotation that reverses when you orbit behind it, is the sort of thing
//! that feels "slightly off" for weeks before anyone pins it down.

use crate::camera::Camera;
use crate::transform::Tool;
use glam::{Vec2, Vec3, Vec4Swizzles};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Handle {
    X,
    Y,
    Z,
    /// The plane handles: drag in the plane spanned by two axes.
    XY,
    YZ,
    ZX,
    /// The centre box: uniform scale, or screen-space move.
    Uniform,
}

impl Handle {
    /// The axis a handle constrains to, if it is a single axis.
    pub fn axis(self) -> Option<Vec3> {
        match self {
            Self::X => Some(Vec3::X),
            Self::Y => Some(Vec3::Y),
            Self::Z => Some(Vec3::Z),
            _ => None,
        }
    }

    /// The normal of the plane a handle drags in.
    pub fn plane_normal(self) -> Option<Vec3> {
        match self {
            Self::XY => Some(Vec3::Z),
            Self::YZ => Some(Vec3::X),
            Self::ZX => Some(Vec3::Y),
            _ => None,
        }
    }

    pub fn colour(self) -> [f32; 3] {
        match self {
            Self::X | Self::YZ => [0.85, 0.25, 0.25],
            Self::Y | Self::ZX => [0.35, 0.80, 0.35],
            Self::Z | Self::XY => [0.30, 0.50, 0.95],
            Self::Uniform => [0.85, 0.85, 0.40],
        }
    }
}

/// How big to draw the gizmo so it stays the same size on screen.
///
/// A gizmo in world units shrinks to nothing when you zoom out and swallows
/// the model when you zoom in, which makes it useless at exactly the moments
/// you reach for it.
pub fn screen_scale(camera: &Camera, origin: Vec3, fraction_of_height: f32) -> f32 {
    let distance = (origin - camera.eye()).length().max(1e-4);
    2.0 * distance * (camera.fov_y / 2.0).tan() * fraction_of_height
}

/// Which handle is under the cursor, if any. Checked nearest-first by screen
/// distance, with plane and centre handles taking priority over the axis bars
/// they overlap.
pub fn pick_handle(
    camera: &Camera,
    aspect: f32,
    origin: Vec3,
    scale: f32,
    ndc: Vec2,
    tool: Tool,
) -> Option<Handle> {
    if tool == Tool::Select {
        return None;
    }
    let vp = camera.view_projection(aspect);
    let project = |p: Vec3| -> Option<Vec2> {
        let clip = vp * p.extend(1.0);
        (clip.w > 0.0).then(|| clip.xy() / clip.w)
    };
    let centre = project(origin)?;

    // Grab radius in NDC. Generous, because a 2-pixel line is miserable to hit
    // and being slightly too grabby costs nothing: the click would otherwise
    // have gone to selection, which is one undo away.
    const GRAB: f32 = 0.035;

    if (ndc - centre).length() < GRAB * 1.2 {
        return Some(Handle::Uniform);
    }

    // Plane handles sit as small squares near the origin, between two axes,
    // and are tested first because they overlap the axis bars.
    if tool != Tool::Rotate {
        for (handle, a, b) in [
            (Handle::XY, Vec3::X, Vec3::Y),
            (Handle::YZ, Vec3::Y, Vec3::Z),
            (Handle::ZX, Vec3::Z, Vec3::X),
        ] {
            let corner = origin + (a + b) * scale * 0.35;
            if let Some(p) = project(corner) {
                if (ndc - p).length() < GRAB * 0.9 {
                    return Some(handle);
                }
            }
        }
    }

    let mut best: Option<(f32, Handle)> = None;
    for (handle, dir) in [(Handle::X, Vec3::X), (Handle::Y, Vec3::Y), (Handle::Z, Vec3::Z)] {
        let distance = if tool == Tool::Rotate {
            // The rotate handles are rings, so the test is distance to the
            // ring rather than to a bar.
            ring_distance(&project, origin, dir, scale, ndc)
        } else {
            let tip = origin + dir * scale;
            match project(tip) {
                Some(t) => segment_distance(ndc, centre, t),
                None => continue,
            }
        };
        if distance < GRAB && best.is_none_or(|(d, _)| distance < d) {
            best = Some((distance, handle));
        }
    }
    best.map(|(_, h)| h)
}

fn ring_distance(
    project: &impl Fn(Vec3) -> Option<Vec2>,
    origin: Vec3,
    axis: Vec3,
    radius: f32,
    ndc: Vec2,
) -> f32 {
    let (u, v) = basis_for(axis);
    let mut nearest = f32::MAX;
    const STEPS: usize = 48;
    let mut previous: Option<Vec2> = None;
    for i in 0..=STEPS {
        let a = i as f32 / STEPS as f32 * std::f32::consts::TAU;
        let p = origin + (u * a.cos() + v * a.sin()) * radius;
        let Some(s) = project(p) else { continue };
        if let Some(prev) = previous {
            nearest = nearest.min(segment_distance(ndc, prev, s));
        }
        previous = Some(s);
    }
    nearest
}

/// Two unit vectors spanning the plane perpendicular to `axis`.
pub fn basis_for(axis: Vec3) -> (Vec3, Vec3) {
    // Pick whichever world axis is least parallel, or the cross product
    // collapses to zero exactly when the axis lines up with the one chosen.
    let helper = if axis.x.abs() < 0.9 { Vec3::X } else { Vec3::Y };
    let u = axis.cross(helper).normalize_or_zero();
    (u, axis.cross(u).normalize_or_zero())
}

fn segment_distance(p: Vec2, a: Vec2, b: Vec2) -> f32 {
    let ab = b - a;
    let len2 = ab.length_squared();
    if len2 < 1e-12 {
        return (p - a).length();
    }
    let t = ((p - a).dot(ab) / len2).clamp(0.0, 1.0);
    (p - (a + ab * t)).length()
}

/// The point on an infinite line that a screen ray passes closest to.
///
/// This is what makes an axis drag follow the cursor rather than the camera:
/// the handle stays under the pointer even when the axis is steeply angled on
/// screen.
pub fn closest_point_on_axis(
    camera: &Camera,
    aspect: f32,
    origin: Vec3,
    axis: Vec3,
    ndc: Vec2,
) -> Option<Vec3> {
    let ray = camera.ray_through(ndc, aspect);
    let axis = axis.try_normalize()?;
    let w0 = origin - ray.origin;
    let a = axis.dot(ray.dir);
    let denominator = 1.0 - a * a;
    if denominator.abs() < 1e-6 {
        // Looking straight down the axis: every point projects to the same
        // place, so there is no answer and pretending otherwise sends the
        // selection to infinity.
        return None;
    }
    // sc = (b·e - d) / (1 - b²) for lines P0+s·u and Q0+t·v with w0 = P0-Q0.
    // Written the other way round it reads just as plausibly and drags the
    // selection the wrong way down the axis.
    let t = (a * w0.dot(ray.dir) - w0.dot(axis)) / denominator;
    Some(origin + axis * t)
}

/// Where a screen ray meets a plane through `origin`.
pub fn ray_plane(
    camera: &Camera,
    aspect: f32,
    origin: Vec3,
    normal: Vec3,
    ndc: Vec2,
) -> Option<Vec3> {
    let ray = camera.ray_through(ndc, aspect);
    let normal = normal.try_normalize()?;
    let denominator = normal.dot(ray.dir);
    if denominator.abs() < 1e-6 {
        return None; // edge-on
    }
    let t = (origin - ray.origin).dot(normal) / denominator;
    (t > 0.0).then(|| ray.at(t))
}

/// The angle, about `axis`, between two screen positions.
pub fn drag_angle(
    camera: &Camera,
    aspect: f32,
    origin: Vec3,
    axis: Vec3,
    from: Vec2,
    to: Vec2,
) -> Option<f32> {
    let a = ray_plane(camera, aspect, origin, axis, from)? - origin;
    let b = ray_plane(camera, aspect, origin, axis, to)? - origin;
    let axis = axis.try_normalize()?;
    let (u, v) = basis_for(axis);
    let angle_a = a.dot(v).atan2(a.dot(u));
    let angle_b = b.dot(v).atan2(b.dot(u));
    let mut delta = angle_b - angle_a;
    // Shortest way round, or crossing the seam jumps a full turn.
    while delta > std::f32::consts::PI {
        delta -= std::f32::consts::TAU;
    }
    while delta < -std::f32::consts::PI {
        delta += std::f32::consts::TAU;
    }
    Some(delta)
}

/// Scale factor from how much further the cursor moved from the gizmo centre.
pub fn drag_scale_factor(
    camera: &Camera,
    aspect: f32,
    origin: Vec3,
    from: Vec2,
    to: Vec2,
) -> Option<f32> {
    let vp = camera.view_projection(aspect);
    let clip = vp * origin.extend(1.0);
    if clip.w <= 0.0 {
        return None;
    }
    let centre = clip.xy() / clip.w;
    let before = (from - centre).length();
    let after = (to - centre).length();
    // Starting a scale drag exactly on the centre would divide by nothing.
    (before > 1e-4).then(|| (after / before).clamp(0.01, 100.0))
}
