//! One error type for every format.

use std::fmt;

#[derive(Debug)]
pub enum IoError {
    Io(std::io::Error),
    /// Something wrong with the file's contents. Carries the line number when
    /// the format has lines, because "malformed file" on a 200,000-line OBJ
    /// is not something anyone can act on.
    Parse { line: Option<usize>, what: String },
    UnsupportedFormat(String),
    /// The structure parsed, but the result is not a mesh we can work with.
    Invalid(mesh_core::MeshError),
}

impl IoError {
    pub fn parse(line: usize, what: impl Into<String>) -> Self {
        Self::Parse { line: Some(line), what: what.into() }
    }

    pub fn bad(what: impl Into<String>) -> Self {
        Self::Parse { line: None, what: what.into() }
    }
}

impl fmt::Display for IoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(e) => write!(f, "{e}"),
            Self::Parse { line: Some(n), what } => write!(f, "line {n}: {what}"),
            Self::Parse { line: None, what } => write!(f, "{what}"),
            Self::UnsupportedFormat(ext) => {
                write!(f, "no importer for '{ext}' files")
            }
            Self::Invalid(e) => write!(f, "the file parsed but the mesh is not usable: {e}"),
        }
    }
}

impl std::error::Error for IoError {}

impl From<std::io::Error> for IoError {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, IoError>;
