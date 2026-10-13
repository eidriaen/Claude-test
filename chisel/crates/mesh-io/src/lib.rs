//! Reading and writing mesh files.
//!
//! Every importer returns the mesh *and* a report of what it had to decide:
//! n-gons triangulated, normals computed because the file carried none,
//! directives skipped. Those decisions change the model, so they are said out
//! loud rather than discovered later.

pub mod error;
pub mod obj;
pub mod stl;

pub use error::{IoError, Result};

use mesh_core::Mesh;
use std::path::Path;

/// What a file can be read as, chosen by extension.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Obj,
    Stl,
}

impl Format {
    pub fn from_path(path: &Path) -> Option<Self> {
        let ext = path.extension()?.to_str()?.to_ascii_lowercase();
        match ext.as_str() {
            "obj" => Some(Self::Obj),
            "stl" => Some(Self::Stl),
            _ => None,
        }
    }

    pub fn extensions() -> &'static [&'static str] {
        &["obj", "stl"]
    }
}

/// Load a mesh, deciding the format from the extension.
pub fn load(path: &Path) -> Result<(Mesh, obj::ImportReport)> {
    let format = Format::from_path(path).ok_or_else(|| {
        IoError::UnsupportedFormat(
            path.extension().and_then(|e| e.to_str()).unwrap_or("").to_string(),
        )
    })?;
    match format {
        Format::Obj => {
            let text = std::fs::read_to_string(path)?;
            obj::parse(&text)
        }
        Format::Stl => {
            let bytes = std::fs::read(path)?;
            stl::parse(&bytes)
        }
    }
}

/// Save a mesh, deciding the format from the extension.
pub fn save(path: &Path, mesh: &Mesh) -> Result<()> {
    let format = Format::from_path(path).ok_or_else(|| {
        IoError::UnsupportedFormat(
            path.extension().and_then(|e| e.to_str()).unwrap_or("").to_string(),
        )
    })?;
    // Write to a temporary beside the target and rename over it, so an
    // interrupted save cannot leave a half-written file where the original
    // was. The Craft apps do the same thing in craft-atomic, for the same
    // reason: the moment you overwrite in place is the moment you lose work.
    let tmp = path.with_extension(format!(
        "{}.tmp",
        path.extension().and_then(|e| e.to_str()).unwrap_or("out")
    ));
    {
        let file = std::fs::File::create(&tmp)?;
        let mut out = std::io::BufWriter::new(file);
        match format {
            Format::Obj => obj::write(mesh, &mut out)?,
            Format::Stl => stl::write(mesh, &mut out)?,
        }
        use std::io::Write;
        out.flush()?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}
