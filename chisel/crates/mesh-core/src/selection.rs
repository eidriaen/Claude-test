//! What is selected, at which sub-object level.
//!
//! One set of indices plus the level they mean. Keeping a separate set per
//! level sounds tidier and is worse: the sets drift apart, and "what is
//! selected" stops having one answer the moment you change level.
//!
//! Changing level **converts** the selection, as 3ds Max does -- picking four
//! verts and pressing 4 should give you the faces they bound, not nothing.

use crate::mesh::Mesh;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, Hash)]
pub enum Level {
    #[default]
    Object,
    Vertex,
    Edge,
    Face,
    Element,
}

impl Level {
    pub fn label(self) -> &'static str {
        match self {
            Self::Object => "Object",
            Self::Vertex => "Vertex",
            Self::Edge => "Edge",
            Self::Face => "Face",
            Self::Element => "Element",
        }
    }
}

/// An edge, identified by its two vertices in a fixed order so the same edge
/// gets the same identity whichever face mentions it.
pub type EdgeId = (u32, u32);

pub fn edge_id(a: u32, b: u32) -> EdgeId {
    if a < b { (a, b) } else { (b, a) }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Selection {
    pub level: Level,
    /// Vertex or face indices, depending on `level`. Edges live in `edges`.
    pub items: HashSet<u32>,
    pub edges: HashSet<EdgeId>,
}

impl Selection {
    pub fn new(level: Level) -> Self {
        Self { level, ..Default::default() }
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty() && self.edges.is_empty()
    }

    pub fn len(&self) -> usize {
        if self.level == Level::Edge { self.edges.len() } else { self.items.len() }
    }

    pub fn clear(&mut self) {
        self.items.clear();
        self.edges.clear();
    }

    pub fn summary(&self) -> String {
        match self.level {
            Level::Object => "Object".into(),
            l => format!("{} {} selected", self.len(), l.label().to_lowercase()),
        }
    }

    // -- editing --------------------------------------------------------
    pub fn add(&mut self, index: u32) {
        self.items.insert(index);
    }

    pub fn toggle(&mut self, index: u32) {
        if !self.items.remove(&index) {
            self.items.insert(index);
        }
    }

    pub fn add_edge(&mut self, e: EdgeId) {
        self.edges.insert(e);
    }

    pub fn toggle_edge(&mut self, e: EdgeId) {
        if !self.edges.remove(&e) {
            self.edges.insert(e);
        }
    }

    pub fn select_all(&mut self, mesh: &Mesh) {
        self.clear();
        match self.level {
            Level::Vertex | Level::Element => {
                self.items.extend(0..mesh.vertex_count() as u32);
            }
            Level::Face => self.items.extend(0..mesh.tri_count() as u32),
            Level::Edge => self.edges.extend(all_edges(mesh)),
            Level::Object => {}
        }
    }

    pub fn invert(&mut self, mesh: &Mesh) {
        match self.level {
            Level::Vertex | Level::Element => {
                self.items = (0..mesh.vertex_count() as u32)
                    .filter(|i| !self.items.contains(i))
                    .collect();
            }
            Level::Face => {
                self.items = (0..mesh.tri_count() as u32)
                    .filter(|i| !self.items.contains(i))
                    .collect();
            }
            Level::Edge => {
                self.edges = all_edges(mesh).filter(|e| !self.edges.contains(e)).collect();
            }
            Level::Object => {}
        }
    }

    /// Everything touching what is already selected.
    pub fn grow(&mut self, mesh: &Mesh) {
        match self.level {
            Level::Vertex => {
                let mut next = self.items.clone();
                for t in 0..mesh.tri_count() {
                    let tri = mesh.tri(t);
                    if tri.iter().any(|i| self.items.contains(i)) {
                        next.extend(tri);
                    }
                }
                self.items = next;
            }
            Level::Face => {
                let verts: HashSet<u32> = self
                    .items
                    .iter()
                    .flat_map(|&t| mesh.tri(t as usize))
                    .collect();
                for t in 0..mesh.tri_count() {
                    if mesh.tri(t).iter().any(|i| verts.contains(i)) {
                        self.items.insert(t as u32);
                    }
                }
            }
            Level::Edge => {
                let verts: HashSet<u32> =
                    self.edges.iter().flat_map(|&(a, b)| [a, b]).collect();
                self.edges
                    .extend(all_edges(mesh).filter(|(a, b)| verts.contains(a) || verts.contains(b)));
            }
            _ => {}
        }
    }

    /// Drop whatever sits on the boundary of the selection.
    pub fn shrink(&mut self, mesh: &Mesh) {
        match self.level {
            Level::Vertex => {
                // A vertex survives only if every vertex it shares a face with
                // is also selected.
                let mut neighbours: HashMap<u32, HashSet<u32>> = HashMap::new();
                for t in 0..mesh.tri_count() {
                    let tri = mesh.tri(t);
                    for &a in &tri {
                        for &b in &tri {
                            if a != b {
                                neighbours.entry(a).or_default().insert(b);
                            }
                        }
                    }
                }
                // Decided against a copy of the set, because retain holds a
                // borrow of the very set the predicate needs to read.
                let selected = self.items.clone();
                self.items.retain(|v| {
                    neighbours
                        .get(v)
                        .map(|ns| ns.iter().all(|n| selected.contains(n)))
                        .unwrap_or(false)
                });
            }
            Level::Face => {
                let unselected: HashSet<u32> = (0..mesh.tri_count() as u32)
                    .filter(|t| !self.items.contains(t))
                    .flat_map(|t| mesh.tri(t as usize))
                    .collect();
                self.items
                    .retain(|&t| !mesh.tri(t as usize).iter().any(|v| unselected.contains(v)));
            }
            _ => {}
        }
    }

    /// Change level, carrying the selection across.
    ///
    /// Going "up" (vertex to face) takes faces whose corners are *all*
    /// selected -- the other rule, any corner, turns a single picked vertex
    /// into a fistful of faces, which is not what anyone means.
    /// Going "down" takes every vertex of what was selected.
    pub fn convert(&mut self, mesh: &Mesh, to: Level) {
        if to == self.level {
            return;
        }
        let verts = self.to_vertices(mesh);
        let from = self.level;
        self.level = to;
        self.clear();
        match to {
            Level::Object => {}
            Level::Vertex => self.items = verts,
            Level::Face => {
                for t in 0..mesh.tri_count() {
                    if mesh.tri(t).iter().all(|v| verts.contains(v)) {
                        self.items.insert(t as u32);
                    }
                }
            }
            Level::Edge => {
                self.edges = all_edges(mesh)
                    .filter(|(a, b)| verts.contains(a) && verts.contains(b))
                    .collect();
            }
            Level::Element => {
                // Whole connected pieces: anything touched selects all of it.
                let shells = shells(mesh);
                let wanted: HashSet<usize> =
                    verts.iter().filter_map(|v| shells.get(v).copied()).collect();
                self.items = shells
                    .iter()
                    .filter(|(_, s)| wanted.contains(s))
                    .map(|(v, _)| *v)
                    .collect();
            }
        }
        // Converting out of Object with nothing selected should not silently
        // produce an empty selection the user cannot explain.
        let _ = from;
    }

    /// The vertices this selection covers, whatever level it is at.
    pub fn to_vertices(&self, mesh: &Mesh) -> HashSet<u32> {
        match self.level {
            Level::Vertex | Level::Element => self.items.clone(),
            Level::Face => self
                .items
                .iter()
                .filter(|&&t| (t as usize) < mesh.tri_count())
                .flat_map(|&t| mesh.tri(t as usize))
                .collect(),
            Level::Edge => self.edges.iter().flat_map(|&(a, b)| [a, b]).collect(),
            Level::Object => (0..mesh.vertex_count() as u32).collect(),
        }
    }

    /// Drop anything the mesh no longer has.
    ///
    /// Called after an operation changes the topology. A selection left
    /// pointing at vertices that have gone is not a cosmetic problem: the next
    /// operation indexes straight past the end of the array.
    pub fn prune(&mut self, mesh: &Mesh) {
        let verts = mesh.vertex_count() as u32;
        let tris = mesh.tri_count() as u32;
        match self.level {
            Level::Vertex | Level::Element => self.items.retain(|&i| i < verts),
            Level::Face => self.items.retain(|&i| i < tris),
            Level::Edge => self.edges.retain(|&(a, b)| a < verts && b < verts),
            Level::Object => {}
        }
    }
}

pub fn all_edges(mesh: &Mesh) -> impl Iterator<Item = EdgeId> + '_ {
    let mut seen = HashSet::new();
    (0..mesh.tri_count())
        .flat_map(move |t| {
            let [a, b, c] = mesh.tri(t);
            [edge_id(a, b), edge_id(b, c), edge_id(c, a)]
        })
        .filter(move |e| seen.insert(*e))
}

/// vertex -> which connected piece it belongs to.
fn shells(mesh: &Mesh) -> HashMap<u32, usize> {
    let mut adjacency: HashMap<u32, Vec<u32>> = HashMap::new();
    for t in 0..mesh.tri_count() {
        let [a, b, c] = mesh.tri(t);
        for (x, y) in [(a, b), (b, c), (c, a)] {
            adjacency.entry(x).or_default().push(y);
            adjacency.entry(y).or_default().push(x);
        }
    }
    let mut shell_of = HashMap::new();
    let mut next = 0;
    for v in 0..mesh.vertex_count() as u32 {
        if shell_of.contains_key(&v) {
            continue;
        }
        let mut stack = vec![v];
        shell_of.insert(v, next);
        while let Some(u) = stack.pop() {
            for &n in adjacency.get(&u).map(|v| v.as_slice()).unwrap_or(&[]) {
                if shell_of.insert(n, next).is_none() {
                    stack.push(n);
                }
            }
        }
        next += 1;
    }
    shell_of
}
