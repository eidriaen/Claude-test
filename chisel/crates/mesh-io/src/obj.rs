//! Wavefront OBJ.
//!
//! Written out rather than pulled in, because the awkward parts are the parts
//! that matter and a crate would decide them for us:
//!
//! - **An OBJ vertex is a triple**, `v/vt/vn`. The same position with two
//!   different normals is two vertices in an indexed mesh. Keying only on
//!   position is the classic bug: every hard edge comes in smoothed.
//! - **Indices are 1-based, and may be negative**, meaning "counting back from
//!   what has been read so far".
//! - **Faces are n-gons.** Fan triangulation is right for the convex faces
//!   almost every OBJ contains, and visibly wrong for concave ones -- so it is
//!   counted and reported rather than done silently.

use crate::error::{IoError, Result};
use glam::{Vec2, Vec3};
use mesh_core::Mesh;
use std::collections::HashMap;
use std::io::Write;

/// What had to be decided on the way in, so the UI can say so.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ImportReport {
    pub triangles: usize,
    /// Faces with more than three sides, fan-triangulated.
    pub ngons: usize,
    /// Lines we did not understand and skipped (materials, curves, smoothing
    /// groups). Not an error; worth saying out loud.
    pub skipped_directives: usize,
    pub had_normals: bool,
    pub had_uvs: bool,
}

impl ImportReport {
    pub fn summary(&self) -> String {
        let mut s = format!("{} triangle(s)", self.triangles);
        if self.ngons > 0 {
            s += &format!(", {} n-gon(s) triangulated", self.ngons);
        }
        match (self.had_uvs, self.had_normals) {
            (true, true) => s += ", with UVs and normals",
            (true, false) => s += ", with UVs; normals computed",
            (false, true) => s += ", with normals; no UVs",
            (false, false) => s += "; normals computed, no UVs",
        }
        s
    }
}

pub fn parse(text: &str) -> Result<(Mesh, ImportReport)> {
    let mut positions: Vec<Vec3> = Vec::new();
    let mut uvs: Vec<Vec2> = Vec::new();
    let mut normals: Vec<Vec3> = Vec::new();

    let mut mesh = Mesh::new();
    let mut report = ImportReport::default();
    // (v, vt, vn) as read -> our vertex index
    let mut combined: HashMap<(i64, i64, i64), u32> = HashMap::new();

    for (no, raw) in text.lines().enumerate() {
        let line_no = no + 1;
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.split_whitespace();
        let Some(tag) = parts.next() else { continue };
        match tag {
            "v" => positions.push(xyz(&mut parts, line_no, "v")?),
            "vn" => normals.push(xyz(&mut parts, line_no, "vn")?),
            "vt" => {
                let u = next_f32(&mut parts, line_no, "vt")?;
                // A 1-D texture coordinate is legal; v defaults to 0.
                let v = parts.next().map(|t| t.parse::<f32>().unwrap_or(0.0)).unwrap_or(0.0);
                uvs.push(Vec2::new(u, v));
            }
            "f" => {
                let mut face: Vec<u32> = Vec::with_capacity(4);
                for token in parts {
                    let (v, vt, vn) = face_token(token, line_no)?;
                    let key = (v, vt, vn);
                    let index = match combined.get(&key) {
                        Some(&i) => i,
                        None => {
                            let pos = resolve(v, positions.len(), line_no, "vertex")?;
                            let i = mesh.positions.len() as u32;
                            mesh.positions.push(positions[pos]);
                            if vt != 0 {
                                let t = resolve(vt, uvs.len(), line_no, "texture coordinate")?;
                                grow(&mut mesh.uvs, i, Vec2::ZERO);
                                mesh.uvs.push(uvs[t]);
                            }
                            if vn != 0 {
                                let n = resolve(vn, normals.len(), line_no, "normal")?;
                                grow(&mut mesh.normals, i, Vec3::Y);
                                mesh.normals.push(normals[n]);
                            }
                            combined.insert(key, i);
                            i
                        }
                    };
                    face.push(index);
                }
                if face.len() < 3 {
                    return Err(IoError::parse(
                        line_no,
                        format!("a face needs at least 3 vertices, found {}", face.len()),
                    ));
                }
                if face.len() > 3 {
                    report.ngons += 1;
                }
                for i in 1..face.len() - 1 {
                    mesh.indices.extend_from_slice(&[face[0], face[i], face[i + 1]]);
                }
            }
            "o" | "g" => {
                // Groups are flattened into one mesh for now. The name of the
                // first is kept so the window has something to show.
                if mesh.name.is_empty() {
                    mesh.name = parts.collect::<Vec<_>>().join(" ");
                }
            }
            _ => report.skipped_directives += 1,
        }
    }

    // Attribute arrays have to match the vertex count exactly or nothing
    // downstream can index them.
    if !mesh.uvs.is_empty() {
        mesh.uvs.resize(mesh.positions.len(), Vec2::ZERO);
        report.had_uvs = true;
    }
    if !mesh.normals.is_empty() {
        mesh.normals.resize(mesh.positions.len(), Vec3::Y);
        report.had_normals = true;
    } else {
        mesh_core::normals::recompute_vertex_normals(&mut mesh);
    }

    report.triangles = mesh.tri_count();
    mesh.validate().map_err(IoError::Invalid)?;
    Ok((mesh, report))
}

pub fn write(mesh: &Mesh, out: &mut impl Write) -> Result<()> {
    writeln!(out, "# written by Chisel")?;
    if !mesh.name.is_empty() {
        writeln!(out, "o {}", mesh.name)?;
    }
    for p in &mesh.positions {
        writeln!(out, "v {} {} {}", f(p.x), f(p.y), f(p.z))?;
    }
    for t in &mesh.uvs {
        writeln!(out, "vt {} {}", f(t.x), f(t.y))?;
    }
    for n in &mesh.normals {
        writeln!(out, "vn {} {} {}", f(n.x), f(n.y), f(n.z))?;
    }
    let (uv, nm) = (mesh.has_uvs(), mesh.has_normals());
    for t in 0..mesh.tri_count() {
        write!(out, "f")?;
        for i in mesh.tri(t) {
            let i = i + 1; // OBJ counts from one
            match (uv, nm) {
                (true, true) => write!(out, " {i}/{i}/{i}")?,
                (true, false) => write!(out, " {i}/{i}")?,
                (false, true) => write!(out, " {i}//{i}")?,
                (false, false) => write!(out, " {i}")?,
            }
        }
        writeln!(out)?;
    }
    Ok(())
}

/// Trim trailing zeros: "1" rather than "1.000000", which halves the file for
/// the box-modelled meshes this will mostly see.
fn f(v: f32) -> String {
    let s = format!("{v:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s.is_empty() || s == "-" { "0".into() } else { s.into() }
}

fn xyz<'a>(parts: &mut impl Iterator<Item = &'a str>, line: usize, tag: &str) -> Result<Vec3> {
    Ok(Vec3::new(
        next_f32(parts, line, tag)?,
        next_f32(parts, line, tag)?,
        next_f32(parts, line, tag)?,
    ))
}

fn next_f32<'a>(parts: &mut impl Iterator<Item = &'a str>, line: usize, tag: &str) -> Result<f32> {
    let token = parts
        .next()
        .ok_or_else(|| IoError::parse(line, format!("'{tag}' is missing a number")))?;
    token
        .parse()
        .map_err(|_| IoError::parse(line, format!("'{token}' is not a number")))
}

/// `v`, `v/vt`, `v//vn`, `v/vt/vn`. Zero means absent.
fn face_token(token: &str, line: usize) -> Result<(i64, i64, i64)> {
    let mut it = token.split('/');
    let v = it
        .next()
        .and_then(|s| s.parse::<i64>().ok())
        .ok_or_else(|| IoError::parse(line, format!("'{token}' is not a face vertex")))?;
    let parse_opt = |s: Option<&str>| -> i64 {
        s.filter(|s| !s.is_empty()).and_then(|s| s.parse().ok()).unwrap_or(0)
    };
    Ok((v, parse_opt(it.next()), parse_opt(it.next())))
}

/// OBJ indices count from 1, and a negative index counts back from however
/// many have been read so far.
fn resolve(index: i64, count: usize, line: usize, what: &str) -> Result<usize> {
    let i = if index > 0 {
        index - 1
    } else if index < 0 {
        count as i64 + index
    } else {
        return Err(IoError::parse(line, format!("{what} index 0 (OBJ counts from 1)")));
    };
    if i < 0 || i as usize >= count {
        return Err(IoError::parse(
            line,
            format!("{what} index {index} but the file has {count}"),
        ));
    }
    Ok(i as usize)
}

/// Fill a lagging attribute array up to `upto` before pushing.
///
/// A file where only some vertices carry UVs would otherwise end up with the
/// attribute offset against the positions, which looks like scrambled texture
/// coordinates rather than a missing one.
fn grow<T: Copy>(v: &mut Vec<T>, upto: u32, fill: T) {
    v.resize(upto as usize, fill);
}
