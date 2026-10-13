//! Geometry for a lightweight 3D editor: the mesh, and what you do to it.
//!
//! Nothing here opens a window or touches the GPU. That is deliberate -- it is
//! what lets the parts that are easy to get subtly wrong be tested properly,
//! and what keeps the viewport a view of the data rather than the owner of it.

pub mod camera;
pub mod gizmo;
pub mod history;
pub mod mesh;
pub mod modifier;
pub mod pick;
pub mod normals;
pub mod primitives;
pub mod selection;
pub mod transform;

pub use camera::Camera;
pub use history::History;
pub use selection::{Level, Selection};
pub use transform::{Axes, Pivot, Tool};
pub use mesh::{Mesh, MeshError};
pub use normals::{orient, recompute_vertex_normals, signed_volume, OrientReport};
