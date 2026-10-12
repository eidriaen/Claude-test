//! Shapes to test against and to drop into a scene.

use crate::mesh::Mesh;
use glam::Vec3;

/// Unit cube centred on the origin, wound outwards.
pub fn cube(size: f32) -> Mesh {
    let h = size / 2.0;
    let p = |x: f32, y: f32, z: f32| Vec3::new(x * h, y * h, z * h);
    let corners = [
        p(-1.0, -1.0, -1.0), p(1.0, -1.0, -1.0), p(1.0, 1.0, -1.0), p(-1.0, 1.0, -1.0),
        p(-1.0, -1.0, 1.0), p(1.0, -1.0, 1.0), p(1.0, 1.0, 1.0), p(-1.0, 1.0, 1.0),
    ];
    let faces: [[u32; 4]; 6] = [
        [0, 3, 2, 1], // -Z
        [4, 5, 6, 7], // +Z
        [0, 1, 5, 4], // -Y
        [3, 7, 6, 2], // +Y
        [0, 4, 7, 3], // -X
        [1, 2, 6, 5], // +X
    ];
    let mut indices = Vec::with_capacity(36);
    for [a, b, c, d] in faces {
        indices.extend_from_slice(&[a, b, c, a, c, d]);
    }
    let mut mesh = Mesh {
        positions: corners.to_vec(),
        indices,
        name: "Cube".into(),
        ..Default::default()
    };
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// A flat quad on the XZ plane -- an open surface, for testing the cases where
/// "which way is out" has no answer.
pub fn plane(size: f32) -> Mesh {
    let h = size / 2.0;
    let mut mesh = Mesh {
        positions: vec![
            Vec3::new(-h, 0.0, -h),
            Vec3::new(h, 0.0, -h),
            Vec3::new(h, 0.0, h),
            Vec3::new(-h, 0.0, h),
        ],
        indices: vec![0, 2, 1, 0, 3, 2],
        name: "Plane".into(),
        ..Default::default()
    };
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// UV sphere. Rings of quads, with triangle fans at the poles.
pub fn sphere(radius: f32, segments: u32, rings: u32) -> Mesh {
    let segments = segments.max(3);
    let rings = rings.max(2);
    let mut positions = Vec::new();
    for r in 0..=rings {
        let v = r as f32 / rings as f32;
        let phi = v * std::f32::consts::PI;
        for s in 0..=segments {
            let u = s as f32 / segments as f32;
            let theta = u * std::f32::consts::TAU;
            positions.push(Vec3::new(
                radius * phi.sin() * theta.cos(),
                radius * phi.cos(),
                radius * phi.sin() * theta.sin(),
            ));
        }
    }
    let row = segments + 1;
    let mut indices = Vec::new();
    for r in 0..rings {
        for s in 0..segments {
            let a = r * row + s;
            let b = a + row;
            // Degenerate triangles at the poles are skipped rather than
            // emitted: a zero-area face has no normal, and every averaging
            // step afterwards has to keep stepping around it.
            if r != 0 {
                indices.extend_from_slice(&[a, b, a + 1]);
            }
            if r + 1 != rings {
                indices.extend_from_slice(&[a + 1, b, b + 1]);
            }
        }
    }
    let mut mesh = Mesh {
        positions,
        indices,
        name: "Sphere".into(),
        ..Default::default()
    };
    // The pole rings are generated whole and then mostly unused; without this
    // the sphere carries vertices no face touches.
    mesh.compact();
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}
