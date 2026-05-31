use std::path::PathBuf;

/// Errors produced by the LoT core.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("io error at {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("io error: {0}")]
    PlainIo(#[from] std::io::Error),

    #[error("could not determine the config directory for this platform")]
    NoConfigDir,

    #[error("failed to parse config file {path}: {source}")]
    ConfigParse {
        path: PathBuf,
        #[source]
        source: toml::de::Error,
    },

    #[error("failed to parse YAML frontmatter: {0}")]
    Yaml(#[from] serde_yaml_ng::Error),

    #[error("a thing named {0:?} already exists in the vault")]
    ThingExists(String),

    #[error("cannot create a vault at {0}: something already exists there")]
    VaultExists(PathBuf),

    #[error("no thing found with id {0}")]
    ThingNotFound(String),

    #[error("the thing name {0:?} is not valid (it must not be empty or contain path separators)")]
    InvalidThingName(String),

    #[error("update content was supplied both on stdin and as an argument; pass only one")]
    AmbiguousContent,

    #[error("git command failed: {0}")]
    Git(String),
}

/// Convenience result type for the LoT core.
pub type Result<T> = std::result::Result<T, Error>;

/// Helper to attach a path to an [`std::io::Error`].
pub(crate) fn io_err(path: impl Into<PathBuf>) -> impl FnOnce(std::io::Error) -> Error {
    move |source| Error::Io {
        path: path.into(),
        source,
    }
}
