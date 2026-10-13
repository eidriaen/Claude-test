//! Geometry for a lightweight 3D editor: the mesh, and what you do to it.
//!
//! Nothing here opens a window or touches the GPU. That is deliberate -- it is
//! what lets the parts that are easy to get subtly wrong be tested properly,
//! and what keeps the viewport a view of the data rather than the owner of it.

pub mod camera;
pub mod mesh;
pub mod normals;
pub mod primitives;

pub use camera::Camera;
pub use mesh::{Mesh, MeshError};
pub use normals::{orient, recompute_vertex_normals, signed_volume, OrientReport};
