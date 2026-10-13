//! Undo and redo.

use mesh_core::history::History;
use mesh_core::primitives::{cube, sphere};
use mesh_core::selection::{Level, Selection};

#[test]
fn undo_puts_back_what_was_there() {
    let mut mesh = cube(2.0);
    let mut sel = Selection::new(Level::Vertex);
    let mut h = History::default();

    let before = mesh.clone();
    h.push("Flip", &mesh, &sel);
    for t in 0..mesh.tri_count() {
        mesh.indices.swap(t * 3 + 1, t * 3 + 2);
    }
    assert_ne!(mesh.indices, before.indices);

    assert_eq!(h.undo(&mut mesh, &mut sel).as_deref(), Some("Flip"));
    assert_eq!(mesh.indices, before.indices);
}

#[test]
fn redo_puts_it_back_again() {
    let mut mesh = cube(2.0);
    let mut sel = Selection::new(Level::Vertex);
    let mut h = History::default();

    h.push("Delete everything", &mesh, &sel);
    mesh.indices.clear();

    h.undo(&mut mesh, &mut sel);
    assert_eq!(mesh.tri_count(), 12);
    assert_eq!(h.redo(&mut mesh, &mut sel).as_deref(), Some("Delete everything"));
    assert_eq!(mesh.tri_count(), 0);
}

#[test]
fn the_selection_comes_back_with_the_mesh() {
    // The case this exists for: undoing a delete that left the selection
    // pointing at vertices which no longer exist. Not cosmetic -- the next
    // operation indexes past the end of the array.
    let mut mesh = sphere(1.0, 12, 8);
    let mut sel = Selection::new(Level::Vertex);
    sel.select_all(&mesh);
    let selected = sel.items.len();

    let mut h = History::default();
    h.push("Delete", &mesh, &sel);
    mesh.positions.truncate(3);
    mesh.indices.clear();
    sel.prune(&mesh);
    assert!(sel.items.len() < selected);

    h.undo(&mut mesh, &mut sel);
    assert_eq!(sel.items.len(), selected, "the selection must come back too");
    assert!(sel.items.iter().all(|&i| (i as usize) < mesh.vertex_count()));
}

#[test]
fn editing_after_an_undo_discards_the_redo_branch() {
    let mut mesh = cube(2.0);
    let mut sel = Selection::default();
    let mut h = History::default();

    h.push("A", &mesh, &sel);
    mesh.name = "a".into();
    h.undo(&mut mesh, &mut sel);
    assert!(h.can_redo());

    h.push("B", &mesh, &sel);
    assert!(!h.can_redo(), "the old branch is unreachable now");
    assert_eq!(h.undo_label(), Some("B"));
}

#[test]
fn undo_and_redo_report_what_they_would_do() {
    let mesh = cube(2.0);
    let sel = Selection::default();
    let mut h = History::default();
    assert!(!h.can_undo() && !h.can_redo());
    assert_eq!(h.undo_label(), None);

    h.push("Fix normals", &mesh, &sel);
    assert_eq!(h.undo_label(), Some("Fix normals"));
}

#[test]
fn undoing_an_empty_history_does_nothing_at_all() {
    let mut mesh = cube(2.0);
    let mut sel = Selection::default();
    let before = mesh.clone();
    let mut h = History::default();
    assert!(h.undo(&mut mesh, &mut sel).is_none());
    assert_eq!(mesh, before);
}

#[test]
fn a_drag_collapses_into_one_undo_step() {
    // Two hundred snapshots for one drag would bury every earlier step.
    let mut mesh = cube(2.0);
    let mut sel = Selection::default();
    let mut h = History::default();
    let start = mesh.positions[0];

    for i in 0..200 {
        h.push_coalescing("Move", "drag-1", &mesh, &sel);
        mesh.positions[0].x += i as f32 * 0.001;
    }
    assert_eq!(h.depth(), 1, "one drag, one step");

    h.undo(&mut mesh, &mut sel);
    assert_eq!(mesh.positions[0], start, "undo returns to before the drag began");
}

#[test]
fn a_second_drag_is_its_own_step() {
    let mut mesh = cube(2.0);
    let mut sel = Selection::default();
    let mut h = History::default();

    h.push_coalescing("Move", "drag-1", &mesh, &sel);
    mesh.positions[0].x += 1.0;
    h.push_coalescing("Move", "drag-2", &mesh, &sel);
    mesh.positions[0].x += 1.0;
    assert_eq!(h.depth(), 2);
}

#[test]
fn the_stack_stays_inside_its_memory_budget() {
    let mesh = sphere(1.0, 32, 24);
    let sel = Selection::default();
    let one = {
        let mut h = History::with_budget(usize::MAX);
        h.push("x", &mesh, &sel);
        h.bytes()
    };

    let mut h = History::with_budget(one * 5);
    for i in 0..50 {
        h.push(format!("step {i}"), &mesh, &sel);
    }
    assert!(h.bytes() <= one * 5, "{} over budget", h.bytes());
    assert!(h.depth() >= 1);
    assert_eq!(h.undo_label(), Some("step 49"), "the newest steps are the ones kept");
}

#[test]
fn a_budget_smaller_than_the_mesh_still_leaves_one_step() {
    // Otherwise a heavy mesh means no undo at all, which is worse than a
    // shallow history.
    let mesh = sphere(1.0, 32, 24);
    let sel = Selection::default();
    let mut h = History::with_budget(1);
    h.push("only", &mesh, &sel);
    h.push("newer", &mesh, &sel);
    assert!(h.can_undo(), "there must always be something to undo");
}

#[test]
fn clearing_forgets_both_directions() {
    let mesh = cube(2.0);
    let mut sel = Selection::default();
    let mut m = mesh.clone();
    let mut h = History::default();
    h.push("A", &mesh, &sel);
    h.undo(&mut m, &mut sel);
    h.clear();
    assert!(!h.can_undo() && !h.can_redo());
}
