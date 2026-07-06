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

    #[error("failed to edit config file {path}: {source}")]
    ConfigEdit {
        path: PathBuf,
        // Boxed: a bare `toml_edit::TomlError` is large enough to bloat the
        // whole `Error` enum past clippy's `result_large_err` threshold.
        #[source]
        source: Box<toml_edit::TomlError>,
    },

    #[error("failed to parse YAML frontmatter: {0}")]
    Yaml(#[from] serde_yaml_ng::Error),

    #[error("a thing named {0:?} already exists in the vault")]
    ThingExists(String),

    #[error("cannot create a vault at {0}: something already exists there")]
    VaultExists(PathBuf),

    #[error("no thing found with id {0}")]
    ThingNotFound(String),

    #[error("no update found with id {0}")]
    UpdateNotFound(String),

    #[error("{0} is an update id, not a thing id; this command takes a thing id")]
    NotAThingId(String),

    #[error("cannot archive: vault.auto-commit is false and archiving works by committing to git")]
    ArchiveNeedsAutoCommit,

    #[error("cannot move {0} under itself or one of its own descendants")]
    MoveIntoSelf(String),

    #[error("{0} is already in that location; nothing to move")]
    MoveSameParent(String),

    #[error("cannot move: the destination already contains a thing named {0:?}")]
    MoveDestinationExists(String),

    #[error("the thing name {0:?} is not valid (it must not be empty or contain path separators)")]
    InvalidThingName(String),

    #[error("update content was supplied both on stdin and as an argument; pass only one")]
    AmbiguousContent,

    #[error("unknown update type {0:?}; known types: {1}")]
    UnknownUpdateType(String, String),

    #[error(
        "config redefines the built-in update type {0:?}; \
         note/work/info/done cannot be redefined"
    )]
    BuiltinUpdateTypeRedefined(String),

    #[error("config defines the update type {0:?}, which is reserved: it collides with the `lot update {0}` command")]
    ReservedUpdateTypeName(String),

    #[error(
        "invalid update type name {0:?}: names must start with a lowercase letter \
         and contain only lowercase letters, digits, and hyphens"
    )]
    InvalidUpdateTypeName(String),

    #[error("git command failed: {0}")]
    Git(String),

    #[error("filesystem watch error: {0}")]
    Watch(String),
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
