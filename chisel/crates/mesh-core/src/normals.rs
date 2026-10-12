//! Normals: recompute them, and fix them when they are wrong.
//!
//! "Flipped normals" is two separate problems wearing one name, and a tool
//! that only solves one of them looks broken half the time:
//!
//!   1. Faces disagree with their neighbours -- the mesh is not consistently
//!      wound. Fixed by walking the adjacency graph and flipping whatever
//!      disagrees with the face it was reached from.
//!   2. Every face agrees, and they all point inwards. Winding is already
//!      consistent, so step 1 finds nothing to do. Caught by the sign of the
//!      enclosed volume instead.
//!
//! Both are fixed here, in that order, because the volume test is meaningless
//! until the winding agrees.

use crate::mesh::Mesh;
use glam::Vec3;
use std::collections::HashMap;

/// What `orient` actually did, so the UI can say so rather than claiming
/// success on a mesh it could not help.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct OrientReport {
    /// Faces flipped to agree with their neighbours.
    pub rewound: usize,
    /// Connected pieces found. More than one is normal for a scene, but it is
    /// also the usual reason a mesh cannot be oriented by volume.
    pub shells: usize,
    /// Shells flipped because they enclosed a negative volume.
    pub shells_flipped: usize,
    /// Edges shared by more than two faces. Non-manifold: there is no single
    /// right answer for which way these should face.
    pub non_manifold_edges: usize,
}

impl OrientReport {
    pub fn changed(&self) -> bool {
        self.rewound > 0 || self.shells_flipped > 0
    }

    pub fn summary(&self) -> String {
        if !self.changed() {
            return if self.non_manifold_edges > 0 {
                format!(
                    "normals already consistent ({} non-manifold edge(s) left alone)",
                    self.non_manifold_edges
                )
            } else {
                "normals already consistent".to_string()
            };
        }
        let mut parts = Vec::new();
        if self.rewound > 0 {
            parts.push(format!("rewound {} face(s)", self.rewound));
        }
        if self.shells_flipped > 0 {
            parts.push(format!(
                "turned {} of {} shell(s) outward",
                self.shells_flipped, self.shells
            ));
        }
        if self.non_manifold_edges > 0 {
            parts.push(format!("{} non-manifold edge(s) left alone", self.non_manifold_edges));
        }
        parts.join(", ")
    }
}

/// Area-weighted vertex normals.
///
/// Weighted by area because the cross product's length already is twice the
/// area: normalising each face first would let a sliver triangle pull a vertex
/// normal as hard as the large face it sits beside.
pub fn recompute_vertex_normals(mesh: &mut Mesh) {
    let mut normals = vec![Vec3::ZERO; mesh.positions.len()];
    for t in 0..mesh.tri_count() {
        let raw = mesh.face_normal_raw(t);
        for i in mesh.tri(t) {
            normals[i as usize] += raw;
        }
    }
    for n in &mut normals {
        // A vertex used only by degenerate faces has no direction to point in.
        // Up is a lie, but it is a stable one, and normalize() would give NaN.
        *n = n.normalize_or_zero();
    }
    mesh.normals = normals;
}

/// Signed volume of a closed mesh. Positive when faces wind outwards.
///
/// Meaningless for an open mesh -- which is why `orient` reports shells rather
/// than silently trusting this on a plane or a bowl.
pub fn signed_volume(mesh: &Mesh) -> f32 {
    (0..mesh.tri_count())
        .map(|t| {
            let [a, b, c] = mesh.tri_positions(t);
            a.dot(b.cross(c)) / 6.0
        })
        .sum()
}

/// Make the winding consistent, then turn each closed shell outwards.
pub fn orient(mesh: &mut Mesh) -> OrientReport {
    let mut report = OrientReport::default();
    let tris = mesh.tri_count();
    if tris == 0 {
        return report;
    }

    // edge (low, high) -> the faces using it
    let mut edge_faces: HashMap<(u32, u32), Vec<usize>> = HashMap::new();
    for t in 0..tris {
        let [a, b, c] = mesh.tri(t);
        for (x, y) in [(a, b), (b, c), (c, a)] {
            edge_faces.entry(key(x, y)).or_default().push(t);
        }
    }
    report.non_manifold_edges = edge_faces.values().filter(|f| f.len() > 2).count();

    let mut visited = vec![false; tris];
    let mut flipped = vec![false; tris];

    for seed in 0..tris {
        if visited[seed] {
            continue;
        }
        report.shells += 1;
        let mut shell = Vec::new();
        let mut stack = vec![seed];
        visited[seed] = true;

        while let Some(t) = stack.pop() {
            shell.push(t);
            let [a, b, c] = oriented_tri(mesh, t, flipped[t]);
            for (x, y) in [(a, b), (b, c), (c, a)] {
                let Some(faces) = edge_faces.get(&key(x, y)) else { continue };
                // Only a cleanly shared edge tells us anything: with three or
                // more faces on it there is no "the other side".
                if faces.len() != 2 {
                    continue;
                }
                let other = if faces[0] == t { faces[1] } else { faces[0] };
                if visited[other] {
                    continue;
                }
                // Neighbours agree when they traverse the shared edge in
                // opposite directions. Same direction means one is inside out.
                let [p, q, r] = oriented_tri(mesh, other, flipped[other]);
                let same_direction = [(p, q), (q, r), (r, p)].contains(&(x, y));
                if same_direction {
                    flipped[other] = true;
                    report.rewound += 1;
                }
                visited[other] = true;
                stack.push(other);
            }
        }

        // Now that this shell agrees with itself, ask which way it faces.
        if let Some(sub) = shell_submesh(mesh, &shell, &flipped) {
            if signed_volume(&sub) < 0.0 {
                report.shells_flipped += 1;
                for &t in &shell {
                    flipped[t] = !flipped[t];
                }
            }
        }
    }

    for (t, &flip) in flipped.iter().enumerate() {
        if flip {
            mesh.indices.swap(t * 3 + 1, t * 3 + 2);
        }
    }
    if report.changed() {
        recompute_vertex_normals(mesh);
    }
    report
}

fn key(a: u32, b: u32) -> (u32, u32) {
    if a < b { (a, b) } else { (b, a) }
}

fn oriented_tri(mesh: &Mesh, t: usize, flipped: bool) -> [u32; 3] {
    let [a, b, c] = mesh.tri(t);
    if flipped { [a, c, b] } else { [a, b, c] }
}

/// The faces of one shell as a mesh of their own, so the volume test sees only
/// that shell. Returns None when the shell is not closed, because the sign of
/// an open surface's "volume" depends on where the origin happens to be.
fn shell_submesh(mesh: &Mesh, shell: &[usize], flipped: &[bool]) -> Option<Mesh> {
    let mut edge_count: HashMap<(u32, u32), usize> = HashMap::new();
    let mut indices = Vec::with_capacity(shell.len() * 3);
    for &t in shell {
        let [a, b, c] = oriented_tri(mesh, t, flipped[t]);
        indices.extend_from_slice(&[a, b, c]);
        for (x, y) in [(a, b), (b, c), (c, a)] {
            *edge_count.entry(key(x, y)).or_default() += 1;
        }
    }
    if edge_count.values().any(|&n| n != 2) {
        return None;
    }
    Some(Mesh {
        positions: mesh.positions.clone(),
        indices,
        ..Default::default()
    })
}
