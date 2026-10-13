//! Undo and redo.
//!
//! **Snapshots, not inverse commands.** The command pattern stores less, and
//! requires every operation ever written to implement its own exact inverse.
//! One that gets it subtly wrong corrupts the model in a way that only shows
//! up several steps later, and those bugs are miserable to find. Snapshots
//! cost memory and cannot be wrong.
//!
//! The memory is bounded rather than ignored: the stack drops its oldest
//! entries once it passes a budget, so a long session on a dense mesh loses
//! deep history instead of the machine.
//!
//! The selection is part of the snapshot. Undoing a delete that left the
//! selection pointing at vertices which no longer exist is not a cosmetic
//! problem -- the next operation indexes past the end of the array.

use crate::mesh::Mesh;
use crate::selection::Selection;

#[derive(Clone, Debug)]
struct Snapshot {
    label: String,
    mesh: Mesh,
    selection: Selection,
    /// Set when several small changes should undo as one -- a drag, say.
    coalesce: Option<String>,
}

impl Snapshot {
    fn bytes(&self) -> usize {
        let m = &self.mesh;
        m.positions.len() * 12
            + m.normals.len() * 12
            + m.uvs.len() * 8
            + m.indices.len() * 4
            + self.selection.items.len() * 4
            + self.selection.edges.len() * 8
            + self.label.len()
    }
}

pub struct History {
    past: Vec<Snapshot>,
    future: Vec<Snapshot>,
    budget: usize,
}

impl Default for History {
    fn default() -> Self {
        // 256 MB of history. A 100k-vertex mesh snapshots at roughly 4 MB, so
        // that is tens of steps on something heavy and thousands on the boxes
        // this will mostly edit.
        Self::with_budget(256 * 1024 * 1024)
    }
}

impl History {
    pub fn with_budget(bytes: usize) -> Self {
        Self { past: Vec::new(), future: Vec::new(), budget: bytes }
    }

    pub fn can_undo(&self) -> bool {
        !self.past.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.future.is_empty()
    }

    pub fn undo_label(&self) -> Option<&str> {
        self.past.last().map(|s| s.label.as_str())
    }

    pub fn redo_label(&self) -> Option<&str> {
        self.future.last().map(|s| s.label.as_str())
    }

    pub fn depth(&self) -> usize {
        self.past.len()
    }

    pub fn bytes(&self) -> usize {
        self.past.iter().chain(&self.future).map(Snapshot::bytes).sum()
    }

    /// Record the state *before* an edit. Call this first, then edit.
    pub fn push(&mut self, label: impl Into<String>, mesh: &Mesh, selection: &Selection) {
        self.record(label.into(), mesh, selection, None);
    }

    /// Record the state before an edit that belongs with the ones around it.
    ///
    /// Consecutive pushes sharing a token collapse into one step, so dragging
    /// a vertex across the screen is a single undo rather than two hundred.
    pub fn push_coalescing(
        &mut self,
        label: impl Into<String>,
        token: impl Into<String>,
        mesh: &Mesh,
        selection: &Selection,
    ) {
        let token = token.into();
        if self.past.last().and_then(|s| s.coalesce.as_deref()) == Some(token.as_str()) {
            // The earlier snapshot already holds the state before the drag
            // began, which is exactly what undo should return to.
            self.future.clear();
            return;
        }
        self.record(label.into(), mesh, selection, Some(token));
    }

    fn record(
        &mut self,
        label: String,
        mesh: &Mesh,
        selection: &Selection,
        coalesce: Option<String>,
    ) {
        // Anything redoable is now a branch nobody can reach.
        self.future.clear();
        self.past.push(Snapshot {
            label,
            mesh: mesh.clone(),
            selection: selection.clone(),
            coalesce,
        });
        self.trim();
    }

    /// Step back. Returns the label of what was undone.
    pub fn undo(&mut self, mesh: &mut Mesh, selection: &mut Selection) -> Option<String> {
        let snapshot = self.past.pop()?;
        self.future.push(Snapshot {
            label: snapshot.label.clone(),
            mesh: std::mem::replace(mesh, snapshot.mesh),
            selection: std::mem::replace(selection, snapshot.selection),
            coalesce: None,
        });
        Some(snapshot.label)
    }

    pub fn redo(&mut self, mesh: &mut Mesh, selection: &mut Selection) -> Option<String> {
        let snapshot = self.future.pop()?;
        self.past.push(Snapshot {
            label: snapshot.label.clone(),
            mesh: std::mem::replace(mesh, snapshot.mesh),
            selection: std::mem::replace(selection, snapshot.selection),
            coalesce: None,
        });
        Some(snapshot.label)
    }

    pub fn clear(&mut self) {
        self.past.clear();
        self.future.clear();
    }

    /// Drop the oldest steps until the budget is met.
    ///
    /// Oldest first: the step you are most likely to want back is the one you
    /// just took. One snapshot is always kept, however large -- a budget
    /// smaller than the mesh must not mean no undo at all.
    fn trim(&mut self) {
        while self.bytes() > self.budget && self.past.len() > 1 {
            self.past.remove(0);
        }
    }
}
