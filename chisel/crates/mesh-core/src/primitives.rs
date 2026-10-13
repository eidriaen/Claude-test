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
            // Wound so the faces look outwards. Rings run north to south and
            // theta runs counter-clockwise seen from +Y, which makes this the
            // outward order -- the other one builds a sphere you are inside.
            if r != 0 {
                indices.extend_from_slice(&[a, a + 1, b]);
            }
            if r + 1 != rings {
                indices.extend_from_slice(&[a + 1, b + 1, b]);
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
    // And the wrap-around column is a duplicate of the first, so without this
    // the seam lights as a visible crease all the way down the sphere.
    mesh.weld(1e-5);
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// Box with segment counts, as Max's does. Segments matter: a box you intend
/// to subdivide or bend needs them, and adding them afterwards is a chore.
pub fn box_mesh(w: f32, h: f32, d: f32, sw: u32, sh: u32, sd: u32) -> Mesh {
    let (sw, sh, sd) = (sw.max(1), sh.max(1), sd.max(1));
    let mut mesh = Mesh { name: "Box".into(), ..Default::default() };
    let half = Vec3::new(w, h, d) / 2.0;

    // Six grids, one per face. Axis u, axis v, the fixed axis and its sign.
    let faces: [(Vec3, Vec3, Vec3, u32, u32); 6] = [
        (Vec3::X, Vec3::Y, Vec3::Z, sw, sh),
        (Vec3::Y, Vec3::X, -Vec3::Z, sh, sw),
        (Vec3::Z, Vec3::Y, -Vec3::X, sd, sh),
        (Vec3::Y, Vec3::Z, Vec3::X, sh, sd),
        (Vec3::X, Vec3::Z, -Vec3::Y, sw, sd),
        (Vec3::Z, Vec3::X, Vec3::Y, sd, sw),
    ];
    for (ua, va, na, us, vs) in faces {
        let base = mesh.positions.len() as u32;
        for vi in 0..=vs {
            for ui in 0..=us {
                let u = ui as f32 / us as f32 * 2.0 - 1.0;
                let v = vi as f32 / vs as f32 * 2.0 - 1.0;
                mesh.positions.push((ua * u + va * v + na) * half);
            }
        }
        let row = us + 1;
        for vi in 0..vs {
            for ui in 0..us {
                // a -> u+ -> v+ is counter-clockwise seen from along +n, which
                // is outwards: each basis below is right-handed with n last.
                // The other order winds every face inward, and the whole box
                // arrives needing Fix normals.
                let a = base + vi * row + ui;
                mesh.indices.extend_from_slice(&[a, a + 1, a + row, a + 1, a + row + 1, a + row]);
            }
        }
    }
    mesh.weld(1e-5);
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// Cylinder, optionally capped.
pub fn cylinder(radius: f32, height: f32, sides: u32, caps: bool) -> Mesh {
    let sides = sides.max(3);
    let h = height / 2.0;
    let mut mesh = Mesh { name: "Cylinder".into(), ..Default::default() };
    for s in 0..sides {
        let a = s as f32 / sides as f32 * std::f32::consts::TAU;
        let (sa, ca) = a.sin_cos();
        mesh.positions.push(Vec3::new(ca * radius, -h, sa * radius));
        mesh.positions.push(Vec3::new(ca * radius, h, sa * radius));
    }
    for s in 0..sides {
        // bottom_s -> top_s -> bottom_next. The other order (around the ring
        // first) gives (-cos, 0, -sin): a tube you are standing inside.
        let a = s * 2;
        let b = ((s + 1) % sides) * 2;
        mesh.indices.extend_from_slice(&[a, a + 1, b, a + 1, b + 1, b]);
    }
    if caps {
        let bottom = mesh.positions.len() as u32;
        mesh.positions.push(Vec3::new(0.0, -h, 0.0));
        let top = mesh.positions.len() as u32;
        mesh.positions.push(Vec3::new(0.0, h, 0.0));
        for s in 0..sides {
            let a = s * 2;
            let b = ((s + 1) % sides) * 2;
            mesh.indices.extend_from_slice(&[bottom, a, b]);
            mesh.indices.extend_from_slice(&[top, b + 1, a + 1]);
        }
    }
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// Cone, or a truncated one when the top radius is not zero.
pub fn cone(bottom_radius: f32, top_radius: f32, height: f32, sides: u32) -> Mesh {
    let sides = sides.max(3);
    let h = height / 2.0;
    let mut mesh = Mesh { name: "Cone".into(), ..Default::default() };
    for s in 0..sides {
        let a = s as f32 / sides as f32 * std::f32::consts::TAU;
        let (sa, ca) = a.sin_cos();
        mesh.positions.push(Vec3::new(ca * bottom_radius, -h, sa * bottom_radius));
        mesh.positions.push(Vec3::new(ca * top_radius, h, sa * top_radius));
    }
    for s in 0..sides {
        // bottom_s -> top_s -> bottom_next. The other order (around the ring
        // first) gives (-cos, 0, -sin): a tube you are standing inside.
        let a = s * 2;
        let b = ((s + 1) % sides) * 2;
        mesh.indices.extend_from_slice(&[a, a + 1, b, a + 1, b + 1, b]);
    }
    let bottom = mesh.positions.len() as u32;
    mesh.positions.push(Vec3::new(0.0, -h, 0.0));
    let top = mesh.positions.len() as u32;
    mesh.positions.push(Vec3::new(0.0, h, 0.0));
    for s in 0..sides {
        let a = s * 2;
        let b = ((s + 1) % sides) * 2;
        mesh.indices.extend_from_slice(&[bottom, a, b]);
        if top_radius > 0.0 {
            mesh.indices.extend_from_slice(&[top, b + 1, a + 1]);
        }
    }
    // A true cone's apex is one point reached by every side; welding turns the
    // ring of coincident tips into it, and drops the slivers that leaves.
    mesh.weld(1e-5);
    mesh.compact();
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

pub fn torus(radius: f32, tube: f32, sides: u32, segments: u32) -> Mesh {
    let sides = sides.max(3);
    let segments = segments.max(3);
    let mut mesh = Mesh { name: "Torus".into(), ..Default::default() };
    for i in 0..segments {
        let u = i as f32 / segments as f32 * std::f32::consts::TAU;
        let (su, cu) = u.sin_cos();
        for j in 0..sides {
            let v = j as f32 / sides as f32 * std::f32::consts::TAU;
            let (sv, cv) = v.sin_cos();
            let r = radius + tube * cv;
            mesh.positions.push(Vec3::new(cu * r, tube * sv, su * r));
        }
    }
    for i in 0..segments {
        for j in 0..sides {
            let a = i * sides + j;
            let b = i * sides + (j + 1) % sides;
            let c = ((i + 1) % segments) * sides + j;
            let d = ((i + 1) % segments) * sides + (j + 1) % sides;
            // Around the tube before around the ring: the cross product the
            // other way round points into the doughnut.
            mesh.indices.extend_from_slice(&[a, b, c, b, d, c]);
        }
    }
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}

/// A ground plane with segments, for anything that will be subdivided or
/// displaced later.
pub fn grid(width: f32, depth: f32, cols: u32, rows: u32) -> Mesh {
    let (cols, rows) = (cols.max(1), rows.max(1));
    let mut mesh = Mesh { name: "Plane".into(), ..Default::default() };
    for r in 0..=rows {
        for c in 0..=cols {
            mesh.positions.push(Vec3::new(
                (c as f32 / cols as f32 - 0.5) * width,
                0.0,
                (r as f32 / rows as f32 - 0.5) * depth,
            ));
        }
    }
    let row = cols + 1;
    for r in 0..rows {
        for c in 0..cols {
            let a = r * row + c;
            mesh.indices.extend_from_slice(&[a, a + 1, a + row, a + 1, a + row + 1, a + row]);
        }
    }
    crate::normals::recompute_vertex_normals(&mut mesh);
    mesh
}
