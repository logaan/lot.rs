use clap::{Args, Parser, Subcommand, ValueEnum};

/// Output format for commands that can render either structured YAML or human
/// readable markdown.
#[derive(Debug, Clone, Copy, Default, ValueEnum)]
pub enum Format {
    /// Structured YAML (the default).
    #[default]
    Yaml,
    /// Human readable markdown.
    Markdown,
}

/// Lists of Things (LoT): manage git-backed lists of anything.
#[derive(Debug, Parser)]
#[command(name = "lot", version, about, arg_required_else_help = true)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Work with Things (the items in your lists).
    #[command(subcommand, arg_required_else_help = true)]
    Thing(ThingCommand),

    /// Add typed Updates to a Thing.
    #[command(subcommand, arg_required_else_help = true)]
    Update(UpdateCommand),

    /// Interact with Claude.
    #[command(subcommand, arg_required_else_help = true)]
    Claude(ClaudeCommand),
}

#[derive(Debug, Subcommand)]
pub enum ThingCommand {
    /// Create a new Thing. Pass the name as arguments; pipe contents on stdin.
    ///
    /// Example: echo "the contents" | lot thing new This is the name
    New {
        /// The Thing's name.
        #[arg(trailing_var_arg = true)]
        name: Vec<String>,
    },

    /// Print the filesystem path of a Thing.
    Path(ThingRef),

    /// Print the computed current state of a Thing.
    Get {
        #[command(flatten)]
        thing: ThingRef,

        /// Output format: `yaml` (default) or `markdown`.
        #[arg(long, value_enum, default_value_t = Format::default())]
        format: Format,
    },

    /// Print a list of all Things.
    List {
        /// Output format: `yaml` (default) or `markdown`.
        #[arg(long, value_enum, default_value_t = Format::default())]
        format: Format,
    },
}

/// A reference to a Thing by the `id` of its created update.
#[derive(Debug, Args)]
pub struct ThingRef {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd).
    pub thing: String,
}

/// A reference to a Thing via `--thing`, used by Update sub-commands that take
/// no trailing content.
#[derive(Debug, Args)]
pub struct ThingFlag {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd).
    #[arg(long)]
    pub thing: String,
}

#[derive(Debug, Subcommand)]
pub enum UpdateCommand {
    /// Create a `task` update describing a task or its next steps.
    Task(UpdateArgs),
    /// Create a `doing` update recording progress.
    Doing(UpdateArgs),
    /// Create a `done` update recording the conclusion.
    Done(UpdateArgs),
    /// Create an `archive` update retiring the Thing (no contents).
    Archive(ThingFlag),
}

/// Shared arguments for content-bearing updates.
#[derive(Debug, Args)]
pub struct UpdateArgs {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd).
    #[arg(long)]
    pub thing: String,

    /// Update content, supplied after `--`. Mutually exclusive with stdin.
    #[arg(trailing_var_arg = true)]
    pub content: Vec<String>,
}

#[derive(Debug, Subcommand)]
pub enum ClaudeCommand {
    /// Install the LoT skills into ~/.claude/skills.
    Install,
    /// Start a background Claude session working on a Thing.
    Send(ThingRef),
}
