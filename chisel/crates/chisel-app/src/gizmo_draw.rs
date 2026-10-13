//! Building the gizmo's lines.
//!
//! Lines rather than solid cones and rings: far less geometry, no shading to
//! get wrong, and at the sizes a gizmo is drawn the difference is a few
//! pixels. It can be made prettier later without touching the maths that
//! decides what a drag means.

use glam::Vec3;
use mesh_core::gizmo::{basis_for, Handle};
use mesh_core::transform::Tool;

pub type Line = ([f32; 3], [f32; 3]);

/// Brighten the handle under the cursor, so you can see what you are about to
/// grab before you commit to a drag.
fn colour(handle: Handle, hot: Option<Handle>) -> [f32; 3] {
    let c = handle.colour();
    if hot == Some(handle) {
        [
            (c[0] + 0.45).min(1.0),
            (c[1] + 0.45).min(1.0),
            (c[2] + 0.45).min(1.0),
        ]
    } else {
        c
    }
}

fn push(out: &mut Vec<Line>, a: Vec3, b: Vec3, c: [f32; 3]) {
    out.push((a.to_array(), c));
    out.push((b.to_array(), c));
}

pub fn build(tool: Tool, origin: Vec3, scale: f32, hot: Option<Handle>) -> Vec<Line> {
    let mut out = Vec::new();
    if tool == Tool::Select {
        return out;
    }
    match tool {
        Tool::Move | Tool::Scale => {
            for (handle, dir) in
                [(Handle::X, Vec3::X), (Handle::Y, Vec3::Y), (Handle::Z, Vec3::Z)]
            {
                let c = colour(handle, hot);
                let tip = origin + dir * scale;
                push(&mut out, origin, tip, c);

                let (u, v) = basis_for(dir);
                if tool == Tool::Move {
                    // A little arrowhead: four lines back from the tip.
                    let back = tip - dir * scale * 0.18;
                    let r = scale * 0.06;
                    for s in [u, -u, v, -v] {
                        push(&mut out, tip, back + s * r, c);
                    }
                } else {
                    // A box at the tip, as Max's scale gizmo has.
                    let r = scale * 0.05;
                    let corners = [u + v, u - v, -u - v, -u + v].map(|d| tip + d * r);
                    for i in 0..4 {
                        push(&mut out, corners[i], corners[(i + 1) % 4], c);
                    }
                }
            }
            // Plane handles: a small square in each pair of axes.
            for (handle, a, b) in [
                (Handle::XY, Vec3::X, Vec3::Y),
                (Handle::YZ, Vec3::Y, Vec3::Z),
                (Handle::ZX, Vec3::Z, Vec3::X),
            ] {
                let c = colour(handle, hot);
                let (near, far) = (scale * 0.22, scale * 0.48);
                let p = [
                    origin + a * near + b * near,
                    origin + a * far + b * near,
                    origin + a * far + b * far,
                    origin + a * near + b * far,
                ];
                for i in 0..4 {
                    push(&mut out, p[i], p[(i + 1) % 4], c);
                }
            }
        }
        Tool::Rotate => {
            for (handle, axis) in
                [(Handle::X, Vec3::X), (Handle::Y, Vec3::Y), (Handle::Z, Vec3::Z)]
            {
                let c = colour(handle, hot);
                let (u, v) = basis_for(axis);
                const STEPS: usize = 64;
                let mut previous = origin + u * scale;
                for i in 1..=STEPS {
                    let a = i as f32 / STEPS as f32 * std::f32::consts::TAU;
                    let p = origin + (u * a.cos() + v * a.sin()) * scale;
                    push(&mut out, previous, p, c);
                    previous = p;
                }
            }
        }
        Tool::Select => {}
    }

    // The centre box, in both tools that have one.
    if tool != Tool::Rotate {
        let c = colour(Handle::Uniform, hot);
        let r = scale * 0.06;
        for (a, b) in [(Vec3::X, Vec3::Y), (Vec3::Y, Vec3::Z), (Vec3::Z, Vec3::X)] {
            let p = [
                origin + a * r + b * r,
                origin + a * r - b * r,
                origin - a * r - b * r,
                origin - a * r + b * r,
            ];
            for i in 0..4 {
                push(&mut out, p[i], p[(i + 1) % 4], c);
            }
        }
    }
    out
}
