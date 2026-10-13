//! Edit poly: the operations that change topology rather than just position.
//!
//! All of them work on a set of triangles or vertices, which is what the
//! selection hands over. None of them know about the viewport.
//!
//! A note on triangles. Max works in polygons and these meshes are triangles,
//! so a "face" here is one triangle. Extruding a quad you see on screen means
//! selecting its two triangles -- which is what selecting a face at the edge
//! level already gives you, since both share the diagonal. Quad support is a
//! later change to the mesh, not to these operations.

use crate::mesh::Mesh;
use glam::Vec3;
use std::collections::{HashMap, HashSet};

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct EditReport {
    pub faces_added: usize,
    pub vertices_added: usize,
    pub vertices_removed: usize,
    pub faces_removed: usize,
    pub note: String,
}

impl EditReport {
    pub fn summary(&self) -> String {
        if !self.note.is_empty() {
            return self.note.clone();
        }
        let mut parts = Vec::new();
        if self.faces_added > 0 {
            parts.push(format!("+{} face(s)", self.faces_added));
        }
        if self.vertices_added > 0 {
            parts.push(format!("+{} vertex(es)", self.vertices_added));
        }
        if self.faces_removed > 0 {
            parts.push(format!("-{} face(s)", self.faces_removed));
        }
        if self.vertices_removed > 0 {
            parts.push(format!("-{} vertex(es)", self.vertices_removed));
        }
        if parts.is_empty() {
            "nothing to do".into()
        } else {
            parts.join(", ")
        }
    }
}

/// The edges of a face region that no second selected face shares.
///
/// These are where the walls go. An edge with a selected face on both sides is
/// inside the region and must not grow a wall, or an extruded patch comes out
/// with a sheet of geometry buried through the middle of it.
fn boundary_edges(mesh: &Mesh, faces: &HashSet<u32>) -> Vec<(u32, u32)> {
    let mut count: HashMap<(u32, u32), usize> = HashMap::new();
    for &f in faces {
        let [a, b, c] = mesh.tri(f as usize);
        for (x, y) in [(a, b), (b, c), (c, a)] {
            *count.entry(if x < y { (x, y) } else { (y, x) }).or_default() += 1;
        }
    }
    // Returned in the direction the face uses them, so the walls can be wound
    // to match.
    let mut out = Vec::new();
    for &f in faces {
        let [a, b, c] = mesh.tri(f as usize);
        for (x, y) in [(a, b), (b, c), (c, a)] {
            let key = if x < y { (x, y) } else { (y, x) };
            if count.get(&key) == Some(&1) {
                out.push((x, y));
            }
        }
    }
    out
}

/// Per-vertex direction to extrude along: the average normal of the *selected*
/// faces touching that vertex.
///
/// Max calls this Local Normal, and it is the right default. One averaged
/// normal for the whole region is the alternative (Max's Group), and it fails
/// completely on anything curved: on a closed mesh the face normals cancel
/// exactly, so the region direction is zero and nothing moves at all.
///
/// Using only selected faces matters too. Averaging in the neighbours would
/// drag the edge of an extrusion sideways, away from the face you asked for.
fn extrude_directions(mesh: &Mesh, faces: &HashSet<u32>) -> HashMap<u32, Vec3> {
    let mut sums: HashMap<u32, Vec3> = HashMap::new();
    for &f in faces {
        let raw = mesh.face_normal_raw(f as usize);
        for v in mesh.tri(f as usize) {
            *sums.entry(v).or_insert(Vec3::ZERO) += raw;
        }
    }
    sums.into_iter()
        .map(|(v, n)| (v, n.normalize_or_zero()))
        .collect()
}

/// Extrude a face region, with an optional inset. This is Max's Bevel: height
/// along the normal, inset in the plane. Either can be zero.
///
/// Returns the new face indices, so the caller can leave them selected -- which
/// is what makes extrude-extrude-extrude work the way it does in Max.
pub fn extrude(mesh: &mut Mesh, faces: &[u32], height: f32, inset: f32) -> (EditReport, Vec<u32>) {
    let mut report = EditReport::default();
    let set: HashSet<u32> = faces.iter().copied().filter(|&f| (f as usize) < mesh.tri_count()).collect();
    if set.is_empty() {
        report.note = "no faces selected".into();
        return (report, Vec::new());
    }

    let directions = extrude_directions(mesh, &set);
    if directions.values().all(|n| n.length_squared() < 1e-12) {
        // Every selected face is degenerate, so there is no direction to move
        // along. Moving by a zero vector would report success and do nothing.
        report.note = "the selected faces are degenerate — nothing to extrude along".into();
        return (report, Vec::new());
    }

    let used: HashSet<u32> = set.iter().flat_map(|&f| mesh.tri(f as usize)).collect();
    let centre = used
        .iter()
        .map(|&v| mesh.positions[v as usize])
        .fold(Vec3::ZERO, |a, b| a + b)
        / used.len() as f32;

    // A duplicate of every vertex the region uses. Interior ones are orphaned
    // afterwards and swept up by compact(); handling them separately buys
    // nothing but a longer function.
    let mut duplicate: HashMap<u32, u32> = HashMap::new();
    for &v in &used {
        let p = mesh.positions[v as usize];
        let normal = directions.get(&v).copied().unwrap_or(Vec3::ZERO);
        let mut moved = p + normal * height;
        if inset != 0.0 {
            // Inward along the in-plane direction from the region's centre.
            // Flat regions are the common case and this is exactly right for
            // them; on a curved patch it is a reasonable approximation.
            let radial = (p - centre) - normal * (p - centre).dot(normal);
            if let Some(dir) = radial.try_normalize() {
                let distance = radial.length();
                // Never past the centre: an inset larger than the face turns
                // it inside out, which looks like a crumpled mess.
                moved -= dir * inset.min(distance * 0.99);
            }
        }
        let new = mesh.positions.len() as u32;
        mesh.positions.push(moved);
        duplicate.insert(v, new);
    }
    report.vertices_added = duplicate.len();

    let walls = boundary_edges(mesh, &set);
    for (a, b) in walls {
        let (a2, b2) = (duplicate[&a], duplicate[&b]);
        // (a, b, b') then (a, b', a'): wound so the wall faces away from the
        // region. The other order gives walls you can see straight through.
        mesh.indices.extend_from_slice(&[a, b, b2, a, b2, a2]);
        report.faces_added += 2;
    }

    // The selected faces move to the new vertices; the old ones stay behind as
    // the base of the extrusion.
    let mut new_faces = Vec::with_capacity(set.len());
    for &f in &set {
        let t = f as usize;
        for i in 0..3 {
            let v = mesh.indices[t * 3 + i];
            mesh.indices[t * 3 + i] = duplicate[&v];
        }
        new_faces.push(f);
    }

    let removed = mesh.compact();
    report.vertices_removed = removed;
    if removed > 0 {
        // compact() renumbers, so face indices stay valid but vertex ones do
        // not. Faces are what we hand back, so this is safe.
    }
    crate::normals::recompute_vertex_normals(mesh);
    (report, new_faces)
}

/// Inset without height.
pub fn inset(mesh: &mut Mesh, faces: &[u32], amount: f32) -> (EditReport, Vec<u32>) {
    extrude(mesh, faces, 0.0, amount)
}

/// Merge the given vertices into one, at their average position.
pub fn weld_selected(mesh: &mut Mesh, verts: &[u32]) -> EditReport {
    let mut report = EditReport::default();
    let set: HashSet<u32> = verts.iter().copied().filter(|&v| (v as usize) < mesh.vertex_count()).collect();
    if set.len() < 2 {
        report.note = "select at least two vertices to weld".into();
        return report;
    }
    let centre = set
        .iter()
        .map(|&v| mesh.positions[v as usize])
        .fold(Vec3::ZERO, |a, b| a + b)
        / set.len() as f32;

    let target = *set.iter().min().expect("checked non-empty");
    mesh.positions[target as usize] = centre;
    for i in &mut mesh.indices {
        if set.contains(i) {
            *i = target;
        }
    }
    // Faces that collapsed to a line or a point when their corners merged.
    let before = mesh.tri_count();
    mesh.indices = mesh
        .indices
        .chunks_exact(3)
        .filter(|t| t[0] != t[1] && t[1] != t[2] && t[2] != t[0])
        .flatten()
        .copied()
        .collect();
    report.faces_removed = before - mesh.tri_count();
    report.vertices_removed = mesh.compact();
    crate::normals::recompute_vertex_normals(mesh);
    report
}

/// Weld every pair of vertices closer together than `tolerance`.
pub fn weld_by_distance(mesh: &mut Mesh, tolerance: f32) -> EditReport {
    let before_v = mesh.vertex_count();
    let before_f = mesh.tri_count();
    mesh.weld(tolerance);
    crate::normals::recompute_vertex_normals(mesh);
    EditReport {
        vertices_removed: before_v - mesh.vertex_count(),
        faces_removed: before_f.saturating_sub(mesh.tri_count()),
        ..Default::default()
    }
}

/// Delete faces, leaving their vertices alone unless nothing else uses them.
pub fn delete_faces(mesh: &mut Mesh, faces: &[u32]) -> EditReport {
    let set: HashSet<u32> = faces.iter().copied().collect();
    if set.is_empty() {
        return EditReport { note: "no faces selected".into(), ..Default::default() };
    }
    let before = mesh.tri_count();
    let kept: Vec<u32> = (0..mesh.tri_count() as u32)
        .filter(|t| !set.contains(t))
        .flat_map(|t| mesh.tri(t as usize))
        .collect();
    mesh.indices = kept;
    let mut report = EditReport {
        faces_removed: before - mesh.tri_count(),
        ..Default::default()
    };
    report.vertices_removed = mesh.compact();
    crate::normals::recompute_vertex_normals(mesh);
    report
}

/// Delete vertices, and every face that used them.
pub fn delete_vertices(mesh: &mut Mesh, verts: &[u32]) -> EditReport {
    let set: HashSet<u32> = verts.iter().copied().collect();
    if set.is_empty() {
        return EditReport { note: "no vertices selected".into(), ..Default::default() };
    }
    let before_f = mesh.tri_count();
    mesh.indices = mesh
        .indices
        .chunks_exact(3)
        .filter(|t| !t.iter().any(|v| set.contains(v)))
        .flatten()
        .copied()
        .collect();
    let mut report = EditReport {
        faces_removed: before_f - mesh.tri_count(),
        ..Default::default()
    };
    report.vertices_removed = mesh.compact();
    crate::normals::recompute_vertex_normals(mesh);
    report
}

/// Split every selected face into three by adding a vertex at its centre.
/// Max calls this Tessellate; it is the quickest way to get geometry exactly
/// where you want it without touching the rest of the model.
pub fn tessellate(mesh: &mut Mesh, faces: &[u32]) -> EditReport {
    let set: HashSet<u32> = faces.iter().copied().filter(|&f| (f as usize) < mesh.tri_count()).collect();
    if set.is_empty() {
        return EditReport { note: "no faces selected".into(), ..Default::default() };
    }
    let mut report = EditReport::default();
    let mut extra = Vec::new();
    // Highest index first, so removing one does not shift the next.
    let mut ordered: Vec<u32> = set.into_iter().collect();
    ordered.sort_unstable_by(|a, b| b.cmp(a));

    for f in ordered {
        let t = f as usize;
        let [a, b, c] = mesh.tri(t);
        let centre = mesh.face_centroid(t);
        let m = mesh.positions.len() as u32;
        mesh.positions.push(centre);
        report.vertices_added += 1;
        extra.extend_from_slice(&[a, b, m, b, c, m, c, a, m]);
        report.faces_added += 2; // three replace one
        mesh.indices.drain(t * 3..t * 3 + 3);
    }
    mesh.indices.extend(extra);
    crate::normals::recompute_vertex_normals(mesh);
    report
}
