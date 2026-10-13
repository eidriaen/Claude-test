//! Move, rotate and scale — on a selection, or on everything.
//!
//! Separated from the gizmo that drives it so the arithmetic can be tested on
//! its own: an axis-constrained drag that quietly moves in two axes, or a
//! rotation about the wrong pivot, is the kind of thing you only notice three
//! operations later.

use crate::mesh::Mesh;
use crate::selection::Selection;
use glam::{Mat4, Quat, Vec3};

/// What a transform happens around. Max's "use pivot" / "use selection
/// centre" / "use transform coordinate centre", trimmed to what is useful.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub enum Pivot {
    /// The middle of whatever is selected.
    #[default]
    SelectionCentre,
    /// The world origin.
    Origin,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub enum Tool {
    #[default]
    Select,
    Move,
    Rotate,
    Scale,
}

impl Tool {
    pub fn label(self) -> &'static str {
        match self {
            Self::Select => "Select",
            Self::Move => "Move",
            Self::Rotate => "Rotate",
            Self::Scale => "Scale",
        }
    }
}

/// The axes a drag is constrained to. None set means "all", as in a
/// screen-space drag.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Axes {
    pub x: bool,
    pub y: bool,
    pub z: bool,
}

impl Axes {
    pub const ALL: Self = Self { x: true, y: true, z: true };
    pub const X: Self = Self { x: true, y: false, z: false };
    pub const Y: Self = Self { x: false, y: true, z: false };
    pub const Z: Self = Self { x: false, y: false, z: true };

    pub fn mask(self) -> Vec3 {
        Vec3::new(self.x as u8 as f32, self.y as u8 as f32, self.z as u8 as f32)
    }

    pub fn axis(self) -> Option<Vec3> {
        match (self.x, self.y, self.z) {
            (true, false, false) => Some(Vec3::X),
            (false, true, false) => Some(Vec3::Y),
            (false, false, true) => Some(Vec3::Z),
            _ => None,
        }
    }
}

impl Default for Axes {
    fn default() -> Self {
        Self::ALL
    }
}

/// The vertices a transform applies to.
///
/// An empty sub-object selection means nothing, not everything: "move with
/// nothing selected" quietly moving the whole model is a nasty surprise.
/// Object level means the whole mesh, which is the one case where everything
/// is right.
pub fn affected(mesh: &Mesh, selection: &Selection) -> Vec<u32> {
    use crate::selection::Level;
    if selection.level == Level::Object {
        return (0..mesh.vertex_count() as u32).collect();
    }
    let mut v: Vec<u32> = selection.to_vertices(mesh).into_iter().collect();
    v.sort_unstable();
    v
}

pub fn centre_of(mesh: &Mesh, verts: &[u32]) -> Vec3 {
    if verts.is_empty() {
        return Vec3::ZERO;
    }
    verts
        .iter()
        .map(|&i| mesh.positions[i as usize])
        .fold(Vec3::ZERO, |a, b| a + b)
        / verts.len() as f32
}

pub fn pivot_point(mesh: &Mesh, verts: &[u32], pivot: Pivot) -> Vec3 {
    match pivot {
        Pivot::SelectionCentre => centre_of(mesh, verts),
        Pivot::Origin => Vec3::ZERO,
    }
}

/// Apply an arbitrary matrix to the given vertices, about a pivot.
pub fn apply(mesh: &mut Mesh, verts: &[u32], pivot: Vec3, m: Mat4) {
    let to_pivot = Mat4::from_translation(pivot) * m * Mat4::from_translation(-pivot);
    for &i in verts {
        let p = mesh.positions[i as usize];
        mesh.positions[i as usize] = to_pivot.transform_point3(p);
    }
}

pub fn translate(mesh: &mut Mesh, verts: &[u32], delta: Vec3) {
    for &i in verts {
        mesh.positions[i as usize] += delta;
    }
}

pub fn rotate(mesh: &mut Mesh, verts: &[u32], pivot: Vec3, axis: Vec3, radians: f32) {
    let Some(axis) = axis.try_normalize() else { return };
    apply(mesh, verts, pivot, Mat4::from_quat(Quat::from_axis_angle(axis, radians)));
}

/// Scale about a pivot. A zero factor is clamped away from zero: flattening a
/// selection to nothing is not undoable by scaling back up, because every
/// vertex has already landed on the same plane.
pub fn scale(mesh: &mut Mesh, verts: &[u32], pivot: Vec3, factor: Vec3) {
    const MIN: f32 = 1e-4;
    let safe = Vec3::new(
        if factor.x.abs() < MIN { MIN.copysign(factor.x) } else { factor.x },
        if factor.y.abs() < MIN { MIN.copysign(factor.y) } else { factor.y },
        if factor.z.abs() < MIN { MIN.copysign(factor.z) } else { factor.z },
    );
    apply(mesh, verts, pivot, Mat4::from_scale(safe));
}

/// Normals are not positions: a rotation turns them, a non-uniform scale bends
/// them, and a translation leaves them alone. Recomputing from the faces is
/// both simpler and more correct than carrying an inverse-transpose around,
/// and the meshes here are small enough that it costs nothing noticeable.
pub fn finish(mesh: &mut Mesh) {
    crate::normals::recompute_vertex_normals(mesh);
}
