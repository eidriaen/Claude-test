//! The modifier stack.
//!
//! Max's model, and the reason it is worth copying: the mesh you edit is never
//! the mesh you see. The base mesh stays as you built it, modifiers sit above
//! it in an order you can change, and the result is recomputed. Turn one off,
//! reorder it, change a parameter — the base is untouched, so nothing is a
//! one-way door until you collapse the stack.
//!
//! Evaluation caches per entry, so changing the top modifier does not re-run
//! the four below it. A stack that re-evaluates everything on every parameter
//! tweak is unusable the moment a Subdivide lands in it.

use crate::mesh::Mesh;

/// One entry in the stack.
pub trait Modifier: std::fmt::Debug {
    /// Shown in the stack list.
    fn name(&self) -> String;

    fn apply(&self, mesh: &mut Mesh);

    /// Anything that changes the output must change this, or the cache will
    /// hand back a stale mesh. Returning a fresh value every call is the safe,
    /// slow answer; a hash of the parameters is the right one.
    fn signature(&self) -> u64;
}

#[derive(Debug)]
pub struct Entry {
    pub modifier: Box<dyn Modifier>,
    pub enabled: bool,
    /// The output of this entry and everything below it.
    cache: Option<(u64, Mesh)>,
}

impl Entry {
    pub fn new(modifier: impl Modifier + 'static) -> Self {
        Self { modifier: Box::new(modifier), enabled: true, cache: None }
    }
}

#[derive(Debug, Default)]
pub struct Stack {
    /// Bottom first, as the mesh flows upward. The UI shows it the other way
    /// round, as Max does.
    pub entries: Vec<Entry>,
}

impl Stack {
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn push(&mut self, modifier: impl Modifier + 'static) {
        self.entries.push(Entry::new(modifier));
    }

    pub fn remove(&mut self, index: usize) {
        if index < self.entries.len() {
            self.entries.remove(index);
            self.invalidate_from(index);
        }
    }

    /// Move an entry up or down the stack. Order changes the result -- a
    /// Subdivide below a Smooth is not the same as above it.
    pub fn move_entry(&mut self, from: usize, to: usize) {
        if from >= self.entries.len() || to >= self.entries.len() || from == to {
            return;
        }
        let e = self.entries.remove(from);
        self.entries.insert(to, e);
        self.invalidate_from(from.min(to));
    }

    pub fn set_enabled(&mut self, index: usize, on: bool) {
        if let Some(e) = self.entries.get_mut(index) {
            if e.enabled != on {
                e.enabled = on;
                self.invalidate_from(index);
            }
        }
    }

    /// Everything from `index` up is no longer valid; below it is untouched.
    pub fn invalidate_from(&mut self, index: usize) {
        for e in self.entries.iter_mut().skip(index) {
            e.cache = None;
        }
    }

    pub fn invalidate_all(&mut self) {
        self.invalidate_from(0);
    }

    /// Run the stack over a base mesh.
    ///
    /// `base_signature` must change whenever the base mesh does, or editing
    /// the base would leave every cache above it holding the old shape.
    pub fn evaluate(&mut self, base: &Mesh, base_signature: u64) -> Mesh {
        let mut current = base.clone();
        let mut signature = base_signature;

        for i in 0..self.entries.len() {
            let entry = &self.entries[i];
            if !entry.enabled {
                // A disabled modifier still has to carry the signature along,
                // or turning one off and on again would hit a stale cache.
                signature = mix(signature, 0x0DD0_0FFF);
                continue;
            }
            signature = mix(signature, entry.modifier.signature());
            match &entry.cache {
                Some((cached, mesh)) if *cached == signature => {
                    current = mesh.clone();
                }
                _ => {
                    entry.modifier.apply(&mut current);
                    self.entries[i].cache = Some((signature, current.clone()));
                }
            }
        }
        current
    }

    /// Bake the result and empty the stack -- Max's "collapse to".
    pub fn collapse(&mut self, base: &mut Mesh, base_signature: u64) {
        *base = self.evaluate(base, base_signature);
        self.entries.clear();
    }
}

fn mix(a: u64, b: u64) -> u64 {
    // Not a cryptographic hash. It only has to make "same inputs" and
    // "different inputs" reliably distinguishable.
    let mut h = a ^ b.wrapping_mul(0x9E37_79B9_7F4A_7C15);
    h ^= h >> 31;
    h.wrapping_mul(0xBF58_476D_1CE4_E5B9)
}

// --- the first few modifiers -----------------------------------------------

/// Split every triangle into four. Linear, so the shape does not change --
/// this is the one you put under a Smooth.
#[derive(Debug, Clone, Copy)]
pub struct Subdivide {
    pub iterations: u32,
}

impl Modifier for Subdivide {
    fn name(&self) -> String {
        format!("Subdivide ×{}", self.iterations)
    }

    fn apply(&self, mesh: &mut Mesh) {
        for _ in 0..self.iterations.min(5) {
            subdivide_once(mesh);
        }
        crate::normals::recompute_vertex_normals(mesh);
    }

    fn signature(&self) -> u64 {
        mix(0x5BD1_0001, self.iterations as u64)
    }
}

fn subdivide_once(mesh: &mut Mesh) {
    use std::collections::HashMap;
    let mut midpoints: HashMap<(u32, u32), u32> = HashMap::new();
    let mut indices = Vec::with_capacity(mesh.indices.len() * 4);

    for t in 0..mesh.tri_count() {
        let [a, b, c] = mesh.tri(t);
        let mut mid = |x: u32, y: u32, mesh: &mut Mesh| -> u32 {
            let key = if x < y { (x, y) } else { (y, x) };
            // One new vertex per edge, not per face: a midpoint created twice
            // splits the mesh along every edge it touches.
            *midpoints.entry(key).or_insert_with(|| {
                let p = (mesh.positions[x as usize] + mesh.positions[y as usize]) / 2.0;
                mesh.positions.push(p);
                (mesh.positions.len() - 1) as u32
            })
        };
        let ab = mid(a, b, mesh);
        let bc = mid(b, c, mesh);
        let ca = mid(c, a, mesh);
        indices.extend_from_slice(&[
            a, ab, ca,
            ab, b, bc,
            ca, bc, c,
            ab, bc, ca,
        ]);
    }
    mesh.indices = indices;
    mesh.uvs.clear();
    mesh.normals.clear();
}

/// Laplacian smoothing: each vertex drifts toward the average of its
/// neighbours. Shrinks the model as it goes, which is the honest behaviour of
/// this algorithm and why the strength is a parameter.
#[derive(Debug, Clone, Copy)]
pub struct Smooth {
    pub iterations: u32,
    pub strength: f32,
}

impl Modifier for Smooth {
    fn name(&self) -> String {
        format!("Smooth ×{} @ {:.2}", self.iterations, self.strength)
    }

    fn apply(&self, mesh: &mut Mesh) {
        use std::collections::HashMap;
        let mut neighbours: HashMap<u32, Vec<u32>> = HashMap::new();
        for t in 0..mesh.tri_count() {
            let [a, b, c] = mesh.tri(t);
            for (x, y) in [(a, b), (b, c), (c, a)] {
                neighbours.entry(x).or_default().push(y);
                neighbours.entry(y).or_default().push(x);
            }
        }
        for _ in 0..self.iterations.min(50) {
            let source = mesh.positions.clone();
            for (i, p) in mesh.positions.iter_mut().enumerate() {
                let Some(ns) = neighbours.get(&(i as u32)) else { continue };
                if ns.is_empty() {
                    continue;
                }
                let avg = ns.iter().map(|&n| source[n as usize]).fold(glam::Vec3::ZERO, |a, b| a + b)
                    / ns.len() as f32;
                *p = source[i].lerp(avg, self.strength.clamp(0.0, 1.0));
            }
        }
        crate::normals::recompute_vertex_normals(mesh);
    }

    fn signature(&self) -> u64 {
        mix(0x5_0007, mix(self.iterations as u64, self.strength.to_bits() as u64))
    }
}

/// Fix winding and turn shells outward — the normals tool, as a stack entry.
#[derive(Debug, Clone, Copy, Default)]
pub struct FixNormals;

impl Modifier for FixNormals {
    fn name(&self) -> String {
        "Fix normals".into()
    }

    fn apply(&self, mesh: &mut Mesh) {
        crate::normals::orient(mesh);
    }

    fn signature(&self) -> u64 {
        0x00F1_1A0A
    }
}
