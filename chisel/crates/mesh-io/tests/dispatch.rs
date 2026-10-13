//! Choosing a format, and not losing the file when a save goes wrong.

use mesh_core::primitives::cube;
use mesh_io::{load, save, Format};
use std::path::Path;

#[test]
fn format_comes_from_the_extension_whatever_its_case() {
    assert_eq!(Format::from_path(Path::new("a.obj")), Some(Format::Obj));
    assert_eq!(Format::from_path(Path::new("a.OBJ")), Some(Format::Obj));
    assert_eq!(Format::from_path(Path::new("a.Stl")), Some(Format::Stl));
    assert_eq!(Format::from_path(Path::new("a.fbx")), None);
    assert_eq!(Format::from_path(Path::new("noextension")), None);
}

#[test]
fn an_unknown_extension_names_itself_in_the_error() {
    let dir = tempdir();
    let path = dir.join("thing.blend");
    std::fs::write(&path, b"x").unwrap();
    let err = load(&path).unwrap_err().to_string();
    assert!(err.contains("blend"), "{err}");
}

#[test]
fn obj_and_stl_both_round_trip_through_the_dispatcher() {
    let dir = tempdir();
    for ext in ["obj", "stl"] {
        let path = dir.join(format!("cube.{ext}"));
        let original = cube(2.0);
        save(&path, &original).unwrap();
        let (back, _) = load(&path).unwrap();
        assert_eq!(back.tri_count(), original.tri_count(), "{ext}");
        assert_eq!(back.vertex_count(), original.vertex_count(), "{ext}");
    }
}

#[test]
fn saving_leaves_no_temporary_file_behind() {
    let dir = tempdir();
    let path = dir.join("cube.obj");
    save(&path, &cube(1.0)).unwrap();
    let leftovers: Vec<_> = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().to_string())
        .filter(|n| n.contains("tmp"))
        .collect();
    assert!(leftovers.is_empty(), "{leftovers:?}");
}

#[test]
fn an_overwrite_that_succeeds_replaces_the_old_file_entirely() {
    let dir = tempdir();
    let path = dir.join("cube.obj");
    save(&path, &mesh_core::primitives::sphere(1.0, 16, 12)).unwrap();
    let big = std::fs::metadata(&path).unwrap().len();
    save(&path, &cube(1.0)).unwrap();
    let small = std::fs::metadata(&path).unwrap().len();
    assert!(small < big, "the file should be the cube now, not a mix of both");
    let (back, _) = load(&path).unwrap();
    assert_eq!(back.tri_count(), 12);
}

fn tempdir() -> std::path::PathBuf {
    let base = std::env::temp_dir().join(format!(
        "chisel-test-{}-{:?}",
        std::process::id(),
        std::thread::current().id()
    ));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    base
}
