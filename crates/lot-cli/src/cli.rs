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
///
/// `disable_help_subcommand` turns off clap's auto-generated `help` subcommand
/// (here and on every group below) so the only `help` is our explicit one, which
/// adds `--format=yaml`.
#[derive(Debug, Parser)]
#[command(
    name = "lot",
    version,
    about,
    arg_required_else_help = true,
    disable_help_subcommand = true
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Work with vaults (the git-backed directories that store Things).
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Vault(VaultCommand),

    /// Work with Things (the items in your lists).
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Thing(ThingCommand),

    /// Add typed Updates to a Thing.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Update(UpdateCommand),

    /// Interact with Claude.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Claude(ClaudeCommand),

    /// Launch the terminal interface (runs the separate `lot-tui` binary).
    Interface,

    /// Launch the Python Textual interface (runs the separate `lot-pui` binary).
    Pui,

    /// Print help. With `--format=yaml`, emit the whole command tree as YAML.
    Help(HelpArgs),
}

/// Arguments for the `help` command.
#[derive(Debug, Args)]
pub struct HelpArgs {
    /// Output format. `yaml` emits the full command tree (every command and
    /// sub-command, with its description and arguments) as a YAML document.
    /// Omit it for the usual human-readable help text.
    #[arg(long, value_enum)]
    pub format: Option<HelpFormat>,
}

/// The machine-readable formats `lot help` can emit.
#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum HelpFormat {
    /// The full command tree as YAML.
    Yaml,
}

#[derive(Debug, Subcommand)]
pub enum VaultCommand {
    /// Create a brand-new vault at <path>.
    ///
    /// Creates the folder, seeds its readme, runs `git init`, and makes the
    /// initial commit, then prints the vault path. Errors if <path> already
    /// exists. A leading `~` is expanded against your home directory. This does
    /// not touch any config file or write a `.lot.toml`.
    New {
        /// The path for the new vault (e.g. ~/my-vault).
        path: String,
    },
}

#[derive(Debug, Subcommand)]
pub enum ThingCommand {
    /// Create a new Thing. Pass the name as arguments; pipe contents on stdin.
    ///
    /// Example: echo "the contents" | lot thing new This is the name
    ///
    /// With no name (and an interactive terminal) it opens your editor on a
    /// temporary file seeded with a markdown h1: type the name after the `# `,
    /// then write the body below. Leaving the name empty cancels.
    New {
        /// Compose the contents in your editor ($VISUAL, $EDITOR, else nvim)
        /// instead of reading them from stdin. If you save an empty file the
        /// creation is cancelled.
        #[arg(long)]
        editor: bool,

        /// Create the Thing as a child of this parent Thing (its `task-id`,
        /// e.g. lot:6Ic9...). The child's folder lives inside the parent's.
        #[arg(long)]
        parent: Option<String>,

        /// The Thing's name. `allow_hyphen_values` lets the name start with or
        /// contain `-`/`--` tokens (e.g. "-30C marinade") without clap treating
        /// them as flags, so no leading `--` separator is required.
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
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

    /// Print a Thing's update thread as a YAML list (oldest first).
    ///
    /// One entry per update, each carrying its `update-id`, `type`
    /// (note/work/info/done), `at` timestamp, any other frontmatter, and the raw
    /// markdown `body`. Unlike `get` (which merges the updates into the computed
    /// state), this keeps every update separate.
    Updates(ThingRef),

    /// Print a list of all Things.
    List {
        /// Output format: `yaml` (default) or `markdown`.
        #[arg(long, value_enum, default_value_t = Format::default())]
        format: Format,
    },
}

/// A reference to a Thing by the `id` of its created update.
///
/// The id is optional on the command line: when omitted it falls back to the
/// `LOT_THING_ID` environment variable (resolved in `main`).
#[derive(Debug, Args)]
pub struct ThingRef {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). Defaults to
    /// `LOT_THING_ID` when not given.
    pub thing: Option<String>,
}

/// A reference to a Thing via `--thing`, used by Update sub-commands that take
/// no trailing content. Falls back to `LOT_THING_ID` when omitted.
#[derive(Debug, Args)]
pub struct ThingFlag {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). Defaults to
    /// `LOT_THING_ID` when not given.
    #[arg(long)]
    pub thing: Option<String>,
}

#[derive(Debug, Subcommand)]
pub enum UpdateCommand {
    /// Create a `work` update describing a task, its next steps, or progress.
    Work(UpdateArgs),
    /// Create an `info` update recording a conclusion or result.
    Info(UpdateArgs),
    /// Create a `done` update retiring the Thing (no contents).
    Done(ThingFlag),
}

/// Shared arguments for content-bearing updates.
#[derive(Debug, Args)]
pub struct UpdateArgs {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). Defaults to
    /// `LOT_THING_ID` when not given.
    #[arg(long)]
    pub thing: Option<String>,

    /// Update content, supplied after `--`. Mutually exclusive with stdin.
    #[arg(trailing_var_arg = true)]
    pub content: Vec<String>,
}

#[derive(Debug, Subcommand)]
pub enum ClaudeCommand {
    /// Install the LoT skills into ~/.claude/skills.
    Install,
    /// Start a background Claude session working on a Thing. Requires a model
    /// sub-command; run with no arguments to list them.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Send(SendModel),
}

/// Model selection for `lot claude send`. Each variant maps to a `--model`
/// value passed through to the `claude` CLI.
#[derive(Debug, Subcommand)]
pub enum SendModel {
    /// Launch the session with Claude Sonnet.
    Sonnet(ThingRef),
    /// Launch the session with Claude Opus.
    Opus(ThingRef),
    /// Launch the session with Claude Fable.
    Fable(ThingRef),
}

impl SendModel {
    /// The `--model` value passed to the `claude` CLI.
    pub fn flag(&self) -> &'static str {
        match self {
            SendModel::Sonnet(_) => "sonnet",
            SendModel::Opus(_) => "opus",
            SendModel::Fable(_) => "fable",
        }
    }

    /// The Thing reference this model sub-command was invoked with.
    pub fn thing(self) -> Option<String> {
        match self {
            SendModel::Sonnet(r) | SendModel::Opus(r) | SendModel::Fable(r) => r.thing,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Parse `lot thing new <args...>` and return the captured name tokens.
    fn parse_new_name(args: &[&str]) -> Result<Vec<String>, clap::Error> {
        let mut argv = vec!["lot", "thing", "new"];
        argv.extend_from_slice(args);
        let cli = Cli::try_parse_from(argv)?;
        match cli.command {
            Command::Thing(ThingCommand::New { name, .. }) => Ok(name),
            other => panic!("expected `thing new`, got {other:?}"),
        }
    }

    #[test]
    fn new_accepts_plain_multiword_name() {
        assert_eq!(
            parse_new_name(&["This", "is", "the", "name"]).unwrap(),
            vec!["This", "is", "the", "name"]
        );
    }

    #[test]
    fn new_accepts_name_after_double_dash() {
        assert_eq!(
            parse_new_name(&["--", "After", "dash"]).unwrap(),
            vec!["After", "dash"]
        );
    }

    #[test]
    fn new_accepts_hyphen_leading_name_without_separator() {
        // Regression: names that begin with `-` or look like flags must not be
        // rejected as unknown arguments (the `name is required` bug).
        assert_eq!(
            parse_new_name(&["-30C", "marinade"]).unwrap(),
            vec!["-30C", "marinade"]
        );
        assert_eq!(
            parse_new_name(&["--format", "is", "weird"]).unwrap(),
            vec!["--format", "is", "weird"]
        );
    }

    #[test]
    fn new_with_no_args_yields_empty_name() {
        // The empty-name error is enforced in `main`, after parsing succeeds.
        assert_eq!(parse_new_name(&[]).unwrap(), Vec::<String>::new());
    }
}
