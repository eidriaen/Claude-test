//! The mesh everything else operates on.
//!
//! Triangles only, in a flat index buffer. A half-edge structure would make
//! some edit-poly operations cheaper, but it makes every import, export and
//! GPU upload more expensive, and those happen far more often. Adjacency is
//! built on demand instead, by the operations that need it.

use glam::{Vec2, Vec3};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Mesh {
    pub positions: Vec<Vec3>,
    pub normals: Vec<Vec3>,
    pub uvs: Vec<Vec2>,
    /// Three indices per triangle.
    pub indices: Vec<u32>,
    pub name: String,
}

impl Mesh {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn vertex_count(&self) -> usize {
        self.positions.len()
    }

    pub fn tri_count(&self) -> usize {
        self.indices.len() / 3
    }

    pub fn tri(&self, t: usize) -> [u32; 3] {
        [
            self.indices[t * 3],
            self.indices[t * 3 + 1],
            self.indices[t * 3 + 2],
        ]
    }

    pub fn tri_positions(&self, t: usize) -> [Vec3; 3] {
        let [a, b, c] = self.tri(t);
        [
            self.positions[a as usize],
            self.positions[b as usize],
            self.positions[c as usize],
        ]
    }

    /// Unnormalised face normal; its length is twice the triangle's area, which
    /// is what area-weighted averaging wants.
    pub fn face_normal_raw(&self, t: usize) -> Vec3 {
        let [a, b, c] = self.tri_positions(t);
        (b - a).cross(c - a)
    }

    pub fn face_normal(&self, t: usize) -> Vec3 {
        self.face_normal_raw(t).normalize_or_zero()
    }

    pub fn face_centroid(&self, t: usize) -> Vec3 {
        let [a, b, c] = self.tri_positions(t);
        (a + b + c) / 3.0
    }

    /// Axis-aligned bounds, or None for an empty mesh.
    pub fn bounds(&self) -> Option<(Vec3, Vec3)> {
        let first = *self.positions.first()?;
        Some(self.positions.iter().fold((first, first), |(lo, hi), p| {
            (lo.min(*p), hi.max(*p))
        }))
    }

    pub fn has_uvs(&self) -> bool {
        self.uvs.len() == self.positions.len() && !self.uvs.is_empty()
    }

    pub fn has_normals(&self) -> bool {
        self.normals.len() == self.positions.len() && !self.normals.is_empty()
    }

    /// Structural problems that would make later operations lie rather than
    /// fail. Checked on import, because a mesh with an out-of-range index
    /// crashes somewhere far from the file that caused it.
    pub fn validate(&self) -> Result<(), MeshError> {
        if !self.indices.len().is_multiple_of(3) {
            return Err(MeshError::IndexCountNotTriangles(self.indices.len()));
        }
        let n = self.positions.len() as u32;
        if let Some(&bad) = self.indices.iter().find(|&&i| i >= n) {
            return Err(MeshError::IndexOutOfRange { index: bad, vertices: n });
        }
        if !self.normals.is_empty() && self.normals.len() != self.positions.len() {
            return Err(MeshError::AttributeLength {
                what: "normals",
                got: self.normals.len(),
                want: self.positions.len(),
            });
        }
        if !self.uvs.is_empty() && self.uvs.len() != self.positions.len() {
            return Err(MeshError::AttributeLength {
                what: "uvs",
                got: self.uvs.len(),
                want: self.positions.len(),
            });
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MeshError {
    IndexCountNotTriangles(usize),
    IndexOutOfRange { index: u32, vertices: u32 },
    AttributeLength { what: &'static str, got: usize, want: usize },
}

impl std::fmt::Display for MeshError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::IndexCountNotTriangles(n) => {
                write!(f, "{n} indices is not a whole number of triangles")
            }
            Self::IndexOutOfRange { index, vertices } => {
                write!(f, "index {index} refers to a vertex this mesh does not have ({vertices} vertices)")
            }
            Self::AttributeLength { what, got, want } => {
                write!(f, "{got} {what} for {want} vertices")
            }
        }
    }
}

impl std::error::Error for MeshError {}

impl Mesh {
    /// Drop vertices no triangle refers to, remapping the indices.
    ///
    /// Generators and importers both leave these behind -- a pole ring on a UV
    /// sphere, a vertex an exporter wrote but never used. They are not
    /// harmless: they have no face to take a normal from, so they sit at zero
    /// length, drag the bounding box around, and inflate every vertex count
    /// the UI shows.
    ///
    /// Returns how many were removed.
    pub fn compact(&mut self) -> usize {
        let n = self.positions.len();
        let mut used = vec![false; n];
        for &i in &self.indices {
            used[i as usize] = true;
        }
        let removed = used.iter().filter(|u| !**u).count();
        if removed == 0 {
            return 0;
        }

        let mut remap = vec![u32::MAX; n];
        let mut next = 0u32;
        for (old, keep) in used.iter().enumerate() {
            if *keep {
                remap[old] = next;
                next += 1;
            }
        }
        // A generic fn, not a closure: closures take one concrete argument
        // type, and this runs over Vec<Vec3> and Vec<Vec2> alike.
        fn keep_attr<T>(v: &mut Vec<T>, used: &[bool]) {
            if v.len() == used.len() {
                let mut it = used.iter();
                v.retain(|_| *it.next().unwrap());
            }
        }
        keep_attr(&mut self.positions, &used);
        keep_attr(&mut self.normals, &used);
        keep_attr(&mut self.uvs, &used);
        for i in &mut self.indices {
            *i = remap[*i as usize];
        }
        removed
    }
}
