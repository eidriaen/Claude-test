//! STL. The format with no vertices, only loose triangles.

use mesh_core::normals::orient;
use mesh_core::primitives::cube;
use mesh_io::stl;

#[test]
fn a_cube_written_as_binary_comes_back_welded() {
    // 12 triangles means 36 corners in the file. If they are not welded, every
    // later operation sees 12 disconnected faces: smoothing does nothing and
    // orient finds 12 shells instead of one.
    let original = cube(2.0);
    let mut buf = Vec::new();
    stl::write(&original, &mut buf).unwrap();

    let (back, report) = stl::parse(&buf).unwrap();
    assert_eq!(report.triangles, 12);
    assert_eq!(back.vertex_count(), 8, "corners must weld back into vertices");

    let mut m = back.clone();
    let r = orient(&mut m);
    assert_eq!(r.shells, 1, "a welded cube is one shell, not twelve");
}

#[test]
fn the_binary_header_is_the_right_length_and_the_count_matches() {
    let m = cube(1.0);
    let mut buf = Vec::new();
    stl::write(&m, &mut buf).unwrap();
    assert_eq!(buf.len(), 84 + 12 * 50, "exactly 84 + 50 per triangle");
    let count = u32::from_le_bytes(buf[80..84].try_into().unwrap());
    assert_eq!(count, 12);
}

#[test]
fn ascii_is_read() {
    let src = "\
solid thing
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
endsolid thing
";
    let (mesh, report) = stl::parse(src.as_bytes()).unwrap();
    assert_eq!(report.triangles, 1);
    assert_eq!(mesh.vertex_count(), 3);
    assert_eq!(mesh.name, "thing");
}

#[test]
fn a_binary_file_beginning_with_solid_is_not_mistaken_for_ascii() {
    // Plenty of exporters write "solid" into the binary header, so the word is
    // not a reliable test. The length is.
    let m = cube(1.0);
    let mut buf = Vec::new();
    stl::write(&m, &mut buf).unwrap();
    buf[..5].copy_from_slice(b"solid");

    let (back, report) = stl::parse(&buf).unwrap();
    assert_eq!(report.triangles, 12, "read as binary despite the header text");
    assert_eq!(back.vertex_count(), 8);
}

#[test]
fn a_truncated_binary_file_says_so() {
    let m = cube(1.0);
    let mut buf = Vec::new();
    stl::write(&m, &mut buf).unwrap();
    buf.truncate(buf.len() - 20);
    let err = stl::parse(&buf).unwrap_err().to_string();
    assert!(err.contains("12 triangles"), "{err}");
}

#[test]
fn an_ascii_file_missing_a_corner_is_rejected() {
    let src = "solid x\nvertex 0 0 0\nvertex 1 0 0\nendsolid x\n";
    let err = stl::parse(src.as_bytes()).unwrap_err().to_string();
    assert!(err.contains("whole number of triangles"), "{err}");
}

#[test]
fn stl_normals_are_recomputed_rather_than_trusted() {
    // The per-face normal in an STL is routinely zero or wrong; the winding is
    // the thing to believe.
    let m = cube(2.0);
    let mut buf = Vec::new();
    stl::write(&m, &mut buf).unwrap();
    for t in 0..12 {
        let base = 84 + t * 50;
        for b in 0..12 {
            buf[base + b] = 0;
        }
    }
    let (back, _) = stl::parse(&buf).unwrap();
    assert!(back.has_normals());
    assert!(back.normals.iter().all(|n| n.is_finite() && n.length() > 0.5));
}
