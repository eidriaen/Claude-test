//! STL, binary and ASCII.
//!
//! STL has no vertices, only loose triangles: every corner is spelled out, so
//! a cube arrives as 36 positions rather than 8. Importing it means welding
//! them back together, or every later operation sees a mesh made of
//! disconnected faces -- smoothing does nothing, orientation finds 12 shells
//! instead of 1, and sculpting tears it apart.

use crate::error::{IoError, Result};
use crate::obj::ImportReport;
use glam::Vec3;
use mesh_core::Mesh;
use std::collections::HashMap;
use std::io::Write;

/// How close two corners must be to count as the same vertex.
///
/// Quantised rather than compared, because floats that should be equal often
/// are not, and a tolerance you can sort by is far cheaper than one you have
/// to search for. 1e-5 of a unit is finer than any modelling tolerance and
/// coarser than the error an exporter introduces.
const WELD: f32 = 1e5;

pub fn parse(bytes: &[u8]) -> Result<(Mesh, ImportReport)> {
    if looks_ascii(bytes) {
        parse_ascii(std::str::from_utf8(bytes).map_err(|_| {
            IoError::bad("this looks like an ASCII STL but is not valid UTF-8")
        })?)
    } else {
        parse_binary(bytes)
    }
}

/// Binary STL has an 80-byte header that is *conventionally* not "solid", but
/// plenty of exporters write "solid" into it anyway. The reliable test is the
/// length: a binary file's size is exactly determined by its triangle count.
fn looks_ascii(bytes: &[u8]) -> bool {
    if bytes.len() >= 84 {
        let count = u32::from_le_bytes([bytes[80], bytes[81], bytes[82], bytes[83]]) as usize;
        if bytes.len() == 84 + count * 50 {
            return false;
        }
    }
    bytes.starts_with(b"solid")
}

fn parse_binary(bytes: &[u8]) -> Result<(Mesh, ImportReport)> {
    if bytes.len() < 84 {
        return Err(IoError::bad("too short to be a binary STL"));
    }
    let count = u32::from_le_bytes([bytes[80], bytes[81], bytes[82], bytes[83]]) as usize;
    let want = 84 + count * 50;
    if bytes.len() < want {
        return Err(IoError::bad(format!(
            "header says {count} triangles, which needs {want} bytes, but the file is {}",
            bytes.len()
        )));
    }
    let mut corners = Vec::with_capacity(count * 3);
    for t in 0..count {
        let base = 84 + t * 50;
        // Bytes 0..12 are the face normal, which is ignored: it is routinely
        // wrong or zero, and it is recomputed from the winding anyway.
        for c in 0..3 {
            let o = base + 12 + c * 12;
            corners.push(Vec3::new(
                f32::from_le_bytes(bytes[o..o + 4].try_into().unwrap()),
                f32::from_le_bytes(bytes[o + 4..o + 8].try_into().unwrap()),
                f32::from_le_bytes(bytes[o + 8..o + 12].try_into().unwrap()),
            ));
        }
    }
    Ok(weld(corners, "STL"))
}

fn parse_ascii(text: &str) -> Result<(Mesh, ImportReport)> {
    let mut corners = Vec::new();
    let mut name = String::new();
    for (no, line) in text.lines().enumerate() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("vertex ") {
            let mut it = rest.split_whitespace();
            let mut next = || -> Result<f32> {
                it.next()
                    .ok_or_else(|| IoError::parse(no + 1, "vertex is missing a number"))?
                    .parse()
                    .map_err(|_| IoError::parse(no + 1, "vertex has a value that is not a number"))
            };
            corners.push(Vec3::new(next()?, next()?, next()?));
        } else if let Some(rest) = line.strip_prefix("solid ") {
            if name.is_empty() {
                name = rest.trim().to_string();
            }
        }
    }
    if corners.len() % 3 != 0 {
        return Err(IoError::bad(format!(
            "{} vertices is not a whole number of triangles",
            corners.len()
        )));
    }
    let (mut mesh, report) = weld(corners, if name.is_empty() { "STL" } else { &name });
    mesh.name = if name.is_empty() { "STL".into() } else { name };
    Ok((mesh, report))
}

/// Merge coincident corners into shared vertices.
fn weld(corners: Vec<Vec3>, name: &str) -> (Mesh, ImportReport) {
    let mut map: HashMap<[i64; 3], u32> = HashMap::new();
    let mut mesh = Mesh { name: name.to_string(), ..Default::default() };
    mesh.indices.reserve(corners.len());
    for c in corners {
        let key = [
            (c.x * WELD).round() as i64,
            (c.y * WELD).round() as i64,
            (c.z * WELD).round() as i64,
        ];
        let index = *map.entry(key).or_insert_with(|| {
            mesh.positions.push(c);
            (mesh.positions.len() - 1) as u32
        });
        mesh.indices.push(index);
    }
    mesh_core::normals::recompute_vertex_normals(&mut mesh);
    let report = ImportReport {
        triangles: mesh.tri_count(),
        ..Default::default()
    };
    (mesh, report)
}

/// Binary STL: smaller, and the format every tool reads without complaint.
pub fn write(mesh: &Mesh, out: &mut impl Write) -> Result<()> {
    let mut header = [0u8; 80];
    let tag = b"Chisel";
    header[..tag.len()].copy_from_slice(tag);
    out.write_all(&header)?;
    out.write_all(&(mesh.tri_count() as u32).to_le_bytes())?;
    for t in 0..mesh.tri_count() {
        let n = mesh.face_normal(t);
        for v in [n.x, n.y, n.z] {
            out.write_all(&v.to_le_bytes())?;
        }
        for p in mesh.tri_positions(t) {
            for v in [p.x, p.y, p.z] {
                out.write_all(&v.to_le_bytes())?;
            }
        }
        out.write_all(&0u16.to_le_bytes())?; // attribute byte count
    }
    Ok(())
}
