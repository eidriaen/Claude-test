//! OBJ, on the files that break naive parsers.

use mesh_core::primitives::{cube, sphere};
use mesh_core::Mesh;
use mesh_io::obj;

/// Triangles as their corner attributes, quantised and canonically ordered.
///
/// Import renumbers vertices -- they come out in the order faces reach them,
/// not the order the file lists them, which is what every OBJ importer does.
/// So a round trip is checked by what the mesh *is*, not by which index
/// happens to hold which corner.
fn fingerprint(m: &Mesh) -> Vec<[(i64, i64, i64, i64, i64, i64, i64, i64); 3]> {
    let q = |v: f32| (v * 10_000.0).round() as i64;
    let mut tris: Vec<_> = (0..m.tri_count())
        .map(|t| {
            let mut corners = m.tri(t).map(|i| {
                let i = i as usize;
                let p = m.positions[i];
                let n = if m.has_normals() { m.normals[i] } else { glam::Vec3::ZERO };
                let uv = if m.has_uvs() { m.uvs[i] } else { glam::Vec2::ZERO };
                (q(p.x), q(p.y), q(p.z), q(n.x), q(n.y), q(n.z), q(uv.x), q(uv.y))
            });
            corners.sort();
            corners
        })
        .collect();
    tris.sort();
    tris
}

#[test]
fn a_triangle_round_trips() {
    let src = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
    let (mesh, report) = obj::parse(src).unwrap();
    assert_eq!(mesh.vertex_count(), 3);
    assert_eq!(mesh.tri_count(), 1);
    assert_eq!(report.triangles, 1);
    assert!(!report.had_normals, "the file had none");
    assert!(mesh.has_normals(), "so they should have been computed");
}

#[test]
fn a_cube_survives_a_write_and_a_read() {
    let original = cube(2.0);
    let mut buf = Vec::new();
    obj::write(&original, &mut buf).unwrap();
    let text = String::from_utf8(buf).unwrap();
    let (back, _) = obj::parse(&text).unwrap();

    assert_eq!(back.vertex_count(), original.vertex_count());
    assert_eq!(back.tri_count(), original.tri_count());
    assert_eq!(fingerprint(&back), fingerprint(&original), "the geometry changed");
}

#[test]
fn the_same_position_with_two_normals_becomes_two_vertices() {
    // The classic OBJ bug. An OBJ vertex is a v/vt/vn triple; keying only on
    // position merges them and every hard edge comes in smoothed.
    let src = "\
v 0 0 0
v 1 0 0
v 0 1 0
vn 0 0 1
vn 0 1 0
f 1//1 2//1 3//1
f 1//2 2//2 3//2
";
    let (mesh, report) = obj::parse(src).unwrap();
    assert!(report.had_normals);
    assert_eq!(mesh.tri_count(), 2);
    assert_eq!(
        mesh.vertex_count(),
        6,
        "three positions x two normals: splitting is what keeps the edge hard"
    );
}

#[test]
fn the_same_triple_twice_is_reused_not_duplicated() {
    let src = "\
v 0 0 0
v 1 0 0
v 0 1 0
v 1 1 0
f 1 2 3
f 2 4 3
";
    let (mesh, _) = obj::parse(src).unwrap();
    assert_eq!(mesh.vertex_count(), 4, "the shared edge must share its vertices");
    assert_eq!(mesh.tri_count(), 2);
}

#[test]
fn a_quad_is_triangulated_and_counted() {
    let src = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n";
    let (mesh, report) = obj::parse(src).unwrap();
    assert_eq!(mesh.tri_count(), 2);
    assert_eq!(report.ngons, 1, "n-gons are reported, not silently reshaped");
    assert!(report.summary().contains("n-gon"));
}

#[test]
fn a_hexagon_fans_into_four_triangles() {
    let mut src = String::new();
    for i in 0..6 {
        let a = i as f32 * std::f32::consts::TAU / 6.0;
        src += &format!("v {} {} 0\n", a.cos(), a.sin());
    }
    src += "f 1 2 3 4 5 6\n";
    let (mesh, report) = obj::parse(&src).unwrap();
    assert_eq!(mesh.tri_count(), 4);
    assert_eq!(report.ngons, 1);
}

#[test]
fn negative_indices_count_back_from_here() {
    // Legal OBJ, and written by several exporters.
    let src = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\n";
    let (mesh, _) = obj::parse(src).unwrap();
    assert_eq!(mesh.tri_count(), 1);
    assert_eq!(mesh.positions[0], glam::Vec3::ZERO);
}

#[test]
fn uvs_and_normals_come_through_together() {
    let src = "\
v 0 0 0
v 1 0 0
v 0 1 0
vt 0 0
vt 1 0
vt 0 1
vn 0 0 1
f 1/1/1 2/2/1 3/3/1
";
    let (mesh, report) = obj::parse(src).unwrap();
    assert!(report.had_uvs && report.had_normals);
    assert!(mesh.has_uvs() && mesh.has_normals());
    assert_eq!(mesh.uvs.len(), mesh.positions.len());
    assert_eq!(mesh.normals.len(), mesh.positions.len());
}

#[test]
fn a_one_dimensional_texture_coordinate_is_allowed() {
    let src = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0.5\nf 1/1 2/1 3/1\n";
    let (mesh, _) = obj::parse(src).unwrap();
    assert!(mesh.has_uvs());
    assert_eq!(mesh.uvs[0].y, 0.0, "v defaults to zero");
}

#[test]
fn comments_blank_lines_and_unknown_directives_are_skipped() {
    let src = "\
# a comment
mtllib thing.mtl
usemtl shiny
s off

v 0 0 0   # trailing comment
v 1 0 0
v 0 1 0
f 1 2 3
";
    let (mesh, report) = obj::parse(src).unwrap();
    assert_eq!(mesh.tri_count(), 1);
    assert!(report.skipped_directives >= 3, "{report:?}");
}

#[test]
fn an_index_past_the_end_says_which_line() {
    let src = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n";
    let err = obj::parse(src).unwrap_err().to_string();
    assert!(err.contains("line 4"), "{err}");
    assert!(err.contains('9'), "{err}");
}

#[test]
fn index_zero_is_rejected_with_an_explanation() {
    let src = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n";
    let err = obj::parse(src).unwrap_err().to_string();
    assert!(err.contains("counts from 1"), "{err}");
}

#[test]
fn a_face_with_two_vertices_is_an_error_not_a_silent_skip() {
    let src = "v 0 0 0\nv 1 0 0\nf 1 2\n";
    let err = obj::parse(src).unwrap_err().to_string();
    assert!(err.contains("at least 3"), "{err}");
}

#[test]
fn a_word_where_a_number_belongs_is_reported() {
    let src = "v 0 zero 0\n";
    let err = obj::parse(src).unwrap_err().to_string();
    assert!(err.contains("line 1") && err.contains("zero"), "{err}");
}

#[test]
fn an_empty_file_is_an_empty_mesh_not_a_crash() {
    let (mesh, report) = obj::parse("").unwrap();
    assert_eq!(mesh.vertex_count(), 0);
    assert_eq!(report.triangles, 0);
}

#[test]
fn the_object_name_is_kept() {
    let src = "o Bolt_M6\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
    let (mesh, _) = obj::parse(src).unwrap();
    assert_eq!(mesh.name, "Bolt_M6");
}

#[test]
fn a_sphere_round_trips_with_its_uvs_and_normals_intact() {
    let mut original = sphere(1.0, 12, 8);
    original.uvs = original
        .positions
        .iter()
        .map(|p| glam::Vec2::new(p.x * 0.5 + 0.5, p.y * 0.5 + 0.5))
        .collect();

    let mut buf = Vec::new();
    obj::write(&original, &mut buf).unwrap();
    let (back, report) = obj::parse(&String::from_utf8(buf).unwrap()).unwrap();

    assert!(report.had_uvs && report.had_normals);
    assert_eq!(back.tri_count(), original.tri_count());
    assert_eq!(
        fingerprint(&back),
        fingerprint(&original),
        "a UV or a normal drifted through the round trip"
    );
}
