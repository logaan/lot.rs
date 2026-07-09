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

    /// Add typed Updates to a Thing. The types come from the vault's config
    /// (`[[update-types]]`); run `lot settings get` to list them.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Update(UpdateCommand),

    /// Read the effective LoT settings.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Settings(SettingsCommand),

    /// Interact with Claude.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Claude(ClaudeCommand),

    /// Launch the terminal interface (runs the separate `lot-textual-ui` binary).
    Interface,

    /// Serve the Textual interface to web browsers on the local network.
    ///
    /// Runs the separate `lot-textual-ui-web` binary, a self-hosted
    /// textual-serve server that starts one fresh Textual UI process per
    /// browser session against the resolved vault. It binds all interfaces
    /// (0.0.0.0) by default so other machines on the LAN can reach it, and
    /// prints the URL(s) to open on startup. There is no authentication:
    /// anyone who can reach the port can read and change the vault — pass
    /// `--host 127.0.0.1` for local-only serving. Stop it with Ctrl-C.
    Web(WebArgs),

    /// Watch the vault and stream one YAML event per change on stdout.
    ///
    /// Blocks, emitting a YAML document per change — each preceded by a `---`
    /// marker line and flushed immediately — so a front-end can patch its view
    /// live without re-reading the vault. Each event's `kind` is created,
    /// modified, deleted, or reload. Created/modified events carry the
    /// affected Thing's `id`, `name`, `status`, `parent` (absent for a
    /// top-level Thing), recomputed `state` (as `thing get`), and `updates`
    /// thread (as `thing updates`); a deleted event carries only its `id`; a
    /// reload event carries only `kind` and tells the consumer to reload from
    /// scratch. There is no whole-vault snapshot in any event. Git internals
    /// are ignored and bursts are coalesced. Stop it with Ctrl-C.
    ///
    /// `--thing <id>` (falling back to `LOT_THING_ID`) scopes the stream to
    /// one Thing and its descendants — a coordinator watching only its own
    /// subtree. Omit it (and leave `LOT_THING_ID` unset) to watch the whole
    /// vault, as before this flag existed.
    Watch(ThingFlag),

    /// Print help. With `--format=yaml`, emit the whole command tree as YAML.
    Help(HelpArgs),
}

/// Arguments for `lot web`. Defaults suit LAN access: bind every interface on
/// textual-serve's usual port. Both values are passed straight through to the
/// `lot-textual-ui-web` entry point, which owns the actual server.
#[derive(Debug, Args)]
pub struct WebArgs {
    /// Address to bind. The default (0.0.0.0) makes the UI reachable from
    /// other machines on the local network; use 127.0.0.1 for local-only.
    #[arg(long, default_value = "0.0.0.0")]
    pub host: String,

    /// Port to bind.
    #[arg(long, default_value_t = 8000)]
    pub port: u16,
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
pub enum SettingsCommand {
    /// Print the effective (merged) configuration front-ends read.
    ///
    /// Merges the user-level `[tui]` table (`~/.config/lot/config.toml`) with
    /// the vault-level `[tui]` table (`<vault>/.lot/config.toml`), with the
    /// vault winning field-by-field, and prints the result. The `yaml` output
    /// (the default) is the stable, documented shape:
    ///
    /// ```yaml
    /// theme: <string|null>            # effective theme, null when unset
    /// keybindings: {action: key, ...} # merged overrides ({} when none)
    /// vaults:                         # known vaults ([] when none)
    /// - {name?: <string>, path: <string>}
    /// vault-path: <string>            # the active vault's resolved path
    /// update-types:                   # the effective update types
    /// - {name: <string>, takes-body: <bool>, terminal: <bool>}
    /// default-update-type: <string>   # first update `thing new` writes
    /// ```
    Get {
        /// Output format: `yaml` (default) or `markdown`.
        #[arg(long, value_enum, default_value_t = Format::default())]
        format: Format,
    },

    /// Persist a user-level front-end setting to the user config file.
    ///
    /// Unlike `get` (which reads the merged, effective config), `set` writes to
    /// the user-level config file `lot` resolves (`~/.config/lot/config.toml`,
    /// or a project-local `.lot.toml`), creating it from the example on first
    /// run and leaving the rest of the file — its other keys and comments —
    /// untouched. Run with no arguments to list the settings it can write.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Set(SettingsSet),
}

/// The individual user-level settings `lot settings set` can persist.
#[derive(Debug, Subcommand)]
pub enum SettingsSet {
    /// Set the colour scheme / theme (`[tui].theme`) in the user config.
    ///
    /// Front-ends read the value back through `lot settings get`. The name is
    /// front-end-specific (each front-end knows its own theme set), so it is
    /// written verbatim and not validated here.
    Theme {
        /// The theme name to persist (e.g. `ansi-dark`).
        name: String,
    },
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

    /// Archive every done Thing in the vault: commit them, then delete their
    /// folders and commit all the deletions in one commit.
    ///
    /// A Thing counts as done when its status is a terminal state — the
    /// built-in `done`, or a custom update type declared with `terminal =
    /// true`. Each archived Thing takes all its descendant Things with it,
    /// exactly as `lot thing archive` would; a done Thing nested inside
    /// another done Thing is covered by its ancestor. Any uncommitted changes
    /// under an archived Thing's folder are committed first, so nothing is
    /// lost from history, and no file is deleted until every commit has
    /// succeeded. Like `lot thing archive` it refuses to run when
    /// `vault.auto-commit` is false. It prints the archived Things' ids, one
    /// per line (nothing when the vault has no done Things).
    ///
    /// If any done Thing has a not-done (non-terminal) descendant it refuses,
    /// listing those Things, so unfinished work is never swept away silently;
    /// pass `--force` to archive them along with their done ancestors.
    Archive {
        /// Archive even when a done Thing has not-done descendants that would
        /// be deleted with it.
        #[arg(long, short)]
        force: bool,
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

        /// Extra preamble frontmatter for the Thing's first update, as a small
        /// YAML mapping (e.g. `--preamble 'claude-model: opus'`). It is folded
        /// into the Thing's computed state like any other field. The keys `lot`
        /// manages — `status`, `task-id`, `update-id`, and `<type>-at` — are
        /// rejected.
        #[arg(long)]
        preamble: Option<String>,

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

    /// Archive a Thing: commit it and all its descendants, then delete their
    /// folders and commit the deletion, preserving the history in git.
    ///
    /// Any uncommitted changes under the Thing's folder are committed first, so
    /// nothing is lost from history. If any commit fails the archive aborts and
    /// nothing is deleted. Because archiving works by committing, it refuses to
    /// run when `vault.auto-commit` is false. It prints the archived Thing's id.
    ///
    /// If the Thing has a not-done (non-terminal) descendant it refuses,
    /// listing those Things, so unfinished work is never deleted by surprise;
    /// pass `--force` to archive the whole subtree anyway.
    Archive {
        #[command(flatten)]
        thing: ThingRef,

        /// Archive even when the Thing has not-done descendants that would be
        /// deleted with it.
        #[arg(long, short)]
        force: bool,
    },

    /// Move a Thing — and its whole subtree of descendant Things — under a
    /// new parent Thing, or to the top level with `--root`.
    ///
    /// The Thing's folder is renamed into the destination and the move is
    /// committed so `git log --follow` tracks history across it (when
    /// `vault.auto-commit` is false the folder is only renamed on disk,
    /// leaving the change for an enclosing repo to version). It refuses to
    /// move a Thing under itself or one of its own descendants, to where it
    /// already is, or into a destination that already contains a folder with
    /// its name. It prints the moved Thing's id.
    Move(MoveArgs),

    /// Print a list of all Things.
    List {
        /// Output format: `yaml` (default) or `markdown`.
        #[arg(long, value_enum, default_value_t = Format::default())]
        format: Format,
    },
}

/// Arguments for `lot thing move`. The `destination` group makes `--parent`
/// and `--root` mutually exclusive and requires exactly one of them: a move
/// must always name its destination explicitly.
#[derive(Debug, Args)]
#[command(group(
    clap::ArgGroup::new("destination")
        .required(true)
        .args(["parent", "root"])
))]
pub struct MoveArgs {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). Defaults to
    /// `LOT_THING_ID` when not given.
    pub thing: Option<String>,

    /// The destination parent Thing's id (its `task-id`). The moved Thing's
    /// folder ends up inside this Thing's folder.
    #[arg(long)]
    pub parent: Option<String>,

    /// Move the Thing to the top level of the vault.
    #[arg(long)]
    pub root: bool,
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
    /// Print the filesystem path of an Update, given its `update-id`.
    ///
    /// Mirrors `lot thing path`, but resolves an individual update file rather
    /// than a Thing's folder. The id is searched across every Thing in the
    /// vault (and their descendants); it errors if no update carries it.
    Path(UpdateRef),

    /// An update type defined in config (`[[update-types]]`).
    ///
    /// Update types are entirely config-defined (the stock set is
    /// note/work/info/done), and clap is static, so they can't be listed
    /// here; instead every sub-command other than `path` is captured verbatim
    /// (the first element is the sub-command name, the rest its raw
    /// arguments) and resolved against the effective update types in `main`.
    /// An unknown name errors there with the list of known types.
    #[command(external_subcommand)]
    Type(Vec<String>),
}

/// A reference to a single Update by its `update-id`.
///
/// Unlike a Thing reference there is no environment-variable fallback: an
/// update id is always given explicitly (front-ends pass the id they already
/// hold from a Thing's update thread).
#[derive(Debug, Args)]
pub struct UpdateRef {
    /// The Update's id (e.g. lot:033QI8ChY3vGg0spUGXJlp).
    pub update: String,
}

/// Shared arguments for content-bearing updates.
#[derive(Debug, Args)]
pub struct UpdateArgs {
    /// The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). Defaults to
    /// `LOT_THING_ID` when not given.
    #[arg(long)]
    pub thing: Option<String>,

    /// Extra preamble frontmatter for this update, as a small YAML mapping
    /// (e.g. `--preamble 'claude-model: opus'`). It is folded into the Thing's
    /// computed state like any other field. The keys `lot` manages — `status`,
    /// `task-id`, `update-id`, and `<type>-at` — are rejected.
    #[arg(long)]
    pub preamble: Option<String>,

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
    /// Start a background Claude *coordinator* session on a Thing: it drives the
    /// Thing's subtree of child Things across worker sessions. Requires a model
    /// sub-command, then a workflow sub-command (decide | plan | act) taking an
    /// optional Thing id. Run either with no arguments to list its choices.
    #[command(
        subcommand,
        arg_required_else_help = true,
        disable_help_subcommand = true
    )]
    Coordinate(CoordinateModel),
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

/// Model selection for `lot claude coordinate`, mirroring [`SendModel`] but
/// nesting a [`CoordinateWorkflow`] sub-command instead of taking a Thing id
/// directly: the workflow is chosen after the model, and carries the id.
#[derive(Debug, Subcommand)]
pub enum CoordinateModel {
    /// Coordinate with Claude Sonnet. Requires a workflow sub-command.
    #[command(subcommand, arg_required_else_help = true)]
    Sonnet(CoordinateWorkflow),
    /// Coordinate with Claude Opus. Requires a workflow sub-command.
    #[command(subcommand, arg_required_else_help = true)]
    Opus(CoordinateWorkflow),
    /// Coordinate with Claude Fable. Requires a workflow sub-command.
    #[command(subcommand, arg_required_else_help = true)]
    Fable(CoordinateWorkflow),
}

/// Which coordinator skill a `lot claude coordinate <model>` session runs.
///
/// One variant per entry in `lot_core::skills::COORDINATE_SKILLS`, keyed by the
/// same alias: [`CoordinateWorkflow::alias`] resolves back to the bundled
/// `lot-coordinate-<alias>` skill. They are sub-commands rather than a free-text
/// positional so `lot claude coordinate <model>` — and the Textual UI's command
/// navigator, which walks the sub-command tree — offer the three workflows as
/// named choices.
#[derive(Debug, Subcommand)]
pub enum CoordinateWorkflow {
    /// Decide, Plan, Initiate: decompose the Thing into a plan, then hand back
    /// to the human for sign-off before any work is dispatched.
    Decide(ThingRef),
    /// Plan, Act: decompose the Thing and execute it to completion, with no
    /// human checkpoints.
    Plan(ThingRef),
    /// Act with an existing plan: execute the Thing's existing child Things
    /// without re-decomposing them.
    Act(ThingRef),
}

impl CoordinateWorkflow {
    /// The coordinator skill alias this workflow selects, as registered in
    /// `lot_core::skills::COORDINATE_SKILLS`.
    pub fn alias(&self) -> &'static str {
        match self {
            CoordinateWorkflow::Decide(_) => "decide",
            CoordinateWorkflow::Plan(_) => "plan",
            CoordinateWorkflow::Act(_) => "act",
        }
    }

    /// The Thing reference this workflow sub-command was invoked with.
    pub fn thing(self) -> Option<String> {
        match self {
            CoordinateWorkflow::Decide(r)
            | CoordinateWorkflow::Plan(r)
            | CoordinateWorkflow::Act(r) => r.thing,
        }
    }
}

impl CoordinateModel {
    /// The `--model` value passed to the `claude` CLI.
    pub fn flag(&self) -> &'static str {
        match self {
            CoordinateModel::Sonnet(_) => "sonnet",
            CoordinateModel::Opus(_) => "opus",
            CoordinateModel::Fable(_) => "fable",
        }
    }

    /// The `(skill alias, thing)` this model sub-command was invoked with.
    pub fn into_parts(self) -> (&'static str, Option<String>) {
        match self {
            CoordinateModel::Sonnet(w) | CoordinateModel::Opus(w) | CoordinateModel::Fable(w) => {
                (w.alias(), w.thing())
            }
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
    fn settings_get_parses_with_default_and_explicit_format() {
        // Bare `settings get` defaults to YAML.
        let cli = Cli::try_parse_from(["lot", "settings", "get"]).unwrap();
        match cli.command {
            Command::Settings(SettingsCommand::Get { format }) => {
                assert!(matches!(format, Format::Yaml));
            }
            other => panic!("expected `settings get`, got {other:?}"),
        }
        // `--format markdown` is accepted.
        let cli = Cli::try_parse_from(["lot", "settings", "get", "--format", "markdown"]).unwrap();
        match cli.command {
            Command::Settings(SettingsCommand::Get { format }) => {
                assert!(matches!(format, Format::Markdown));
            }
            other => panic!("expected `settings get`, got {other:?}"),
        }
    }

    #[test]
    fn settings_set_theme_parses_the_name() {
        let cli = Cli::try_parse_from(["lot", "settings", "set", "theme", "ansi-dark"]).unwrap();
        match cli.command {
            Command::Settings(SettingsCommand::Set(SettingsSet::Theme { name })) => {
                assert_eq!(name, "ansi-dark");
            }
            other => panic!("expected `settings set theme`, got {other:?}"),
        }
        // `set` with no setting is rejected (arg_required_else_help).
        assert!(Cli::try_parse_from(["lot", "settings", "set"]).is_err());
        // `set theme` with no name is rejected (the positional is required).
        assert!(Cli::try_parse_from(["lot", "settings", "set", "theme"]).is_err());
    }

    /// Parse `lot thing move <args...>` and return the parsed [`MoveArgs`].
    fn parse_move(args: &[&str]) -> Result<MoveArgs, clap::Error> {
        let mut argv = vec!["lot", "thing", "move"];
        argv.extend_from_slice(args);
        let cli = Cli::try_parse_from(argv)?;
        match cli.command {
            Command::Thing(ThingCommand::Move(args)) => Ok(args),
            other => panic!("expected `thing move`, got {other:?}"),
        }
    }

    #[test]
    fn move_parses_parent_and_root_destinations() {
        let args = parse_move(&["lot:abc", "--parent", "lot:def"]).unwrap();
        assert_eq!(args.thing.as_deref(), Some("lot:abc"));
        assert_eq!(args.parent.as_deref(), Some("lot:def"));
        assert!(!args.root);

        let args = parse_move(&["lot:abc", "--root"]).unwrap();
        assert_eq!(args.parent, None);
        assert!(args.root);

        // The thing id may be omitted (LOT_THING_ID fallback in main).
        let args = parse_move(&["--root"]).unwrap();
        assert_eq!(args.thing, None);
    }

    #[test]
    fn move_requires_exactly_one_destination() {
        // Neither `--parent` nor `--root`: rejected.
        assert!(parse_move(&["lot:abc"]).is_err());
        // Both at once: rejected.
        assert!(parse_move(&["lot:abc", "--parent", "lot:def", "--root"]).is_err());
    }

    #[test]
    fn new_with_no_args_yields_empty_name() {
        // The empty-name error is enforced in `main`, after parsing succeeds.
        assert_eq!(parse_new_name(&[]).unwrap(), Vec::<String>::new());
    }

    #[test]
    fn web_defaults_to_lan_bind_and_accepts_overrides() {
        // Bare `lot web` binds every interface on textual-serve's usual port.
        let cli = Cli::try_parse_from(["lot", "web"]).unwrap();
        match cli.command {
            Command::Web(args) => {
                assert_eq!(args.host, "0.0.0.0");
                assert_eq!(args.port, 8000);
            }
            other => panic!("expected `web`, got {other:?}"),
        }
        // Both flags are overridable.
        let cli =
            Cli::try_parse_from(["lot", "web", "--host", "127.0.0.1", "--port", "9001"]).unwrap();
        match cli.command {
            Command::Web(args) => {
                assert_eq!(args.host, "127.0.0.1");
                assert_eq!(args.port, 9001);
            }
            other => panic!("expected `web`, got {other:?}"),
        }
        // A port that doesn't fit u16 is rejected at parse time.
        assert!(Cli::try_parse_from(["lot", "web", "--port", "70000"]).is_err());
    }

    #[test]
    fn update_routes_every_type_through_the_dynamic_fallback() {
        // Every `lot update` sub-command other than `path` is captured
        // verbatim — its name first, then its raw arguments (including a `--`
        // separator) — so `main` can resolve it against the config-defined
        // update types. The stock names are no different from any other type.
        for name in ["work", "note", "blocked"] {
            let cli = Cli::try_parse_from([
                "lot", "update", name, "--thing", "lot:abc", "--", "body", "here",
            ])
            .unwrap();
            match cli.command {
                Command::Update(UpdateCommand::Type(argv)) => {
                    assert_eq!(argv, [name, "--thing", "lot:abc", "--", "body", "here"]);
                }
                other => panic!("expected `update` type fallback, got {other:?}"),
            }
        }
    }

    #[test]
    fn coordinate_parses_model_skill_and_optional_thing() {
        // `coordinate <model> <skill> [id]`: the model is a sub-command, then
        // the workflow is a nested sub-command, then an optional Thing id.
        let cli = Cli::try_parse_from(["lot", "claude", "coordinate", "sonnet", "plan", "lot:abc"])
            .unwrap();
        match cli.command {
            Command::Claude(ClaudeCommand::Coordinate(model)) => {
                assert_eq!(model.flag(), "sonnet");
                assert_eq!(model.into_parts(), ("plan", Some("lot:abc".to_string())));
            }
            other => panic!("expected `claude coordinate`, got {other:?}"),
        }

        // The Thing id may be omitted (LOT_THING_ID fallback in the command).
        let cli = Cli::try_parse_from(["lot", "claude", "coordinate", "opus", "act"]).unwrap();
        match cli.command {
            Command::Claude(ClaudeCommand::Coordinate(model)) => {
                assert_eq!(model.flag(), "opus");
                assert_eq!(model.into_parts(), ("act", None));
            }
            other => panic!("expected `claude coordinate`, got {other:?}"),
        }
    }

    #[test]
    fn coordinate_requires_a_model_and_a_skill() {
        // No model sub-command: rejected (arg_required_else_help).
        assert!(Cli::try_parse_from(["lot", "claude", "coordinate"]).is_err());
        // A model but no workflow: the nested sub-command is required.
        assert!(Cli::try_parse_from(["lot", "claude", "coordinate", "fable"]).is_err());
        // A workflow outside the bundled set is rejected by clap itself, so it
        // can never reach the skill registry.
        assert!(Cli::try_parse_from(["lot", "claude", "coordinate", "fable", "bogus"]).is_err());
    }

    #[test]
    fn every_bundled_coordinator_skill_has_a_workflow_sub_command() {
        // The workflow sub-commands and the bundled-skill registry are two
        // hand-written lists keyed by the same aliases. If a skill is added to
        // `COORDINATE_SKILLS` without a matching variant here, it becomes
        // unreachable from the CLI — so pin them to each other.
        let aliases: Vec<&str> = lot_core::skills::COORDINATE_SKILLS
            .iter()
            .map(|s| s.alias)
            .collect();
        for alias in &aliases {
            let cli = Cli::try_parse_from(["lot", "claude", "coordinate", "opus", alias])
                .unwrap_or_else(|e| panic!("no `coordinate` sub-command for skill {alias:?}: {e}"));
            match cli.command {
                Command::Claude(ClaudeCommand::Coordinate(model)) => {
                    assert_eq!(model.into_parts().0, *alias);
                }
                other => panic!("expected `claude coordinate`, got {other:?}"),
            }
        }
        // ...and no variant exists without a skill behind it.
        assert_eq!(aliases, ["decide", "plan", "act"]);
    }

    #[test]
    fn update_path_stays_a_static_subcommand() {
        // `path` is the one static `lot update` sub-command; it must keep
        // matching ahead of the dynamic type fallback.
        let cli = Cli::try_parse_from(["lot", "update", "path", "lot:abc"]).unwrap();
        assert!(matches!(
            cli.command,
            Command::Update(UpdateCommand::Path(_))
        ));
    }

    #[test]
    fn watch_parses_bare_and_scoped_by_thing() {
        // Bare `lot watch` still parses with no thing id (unscoped; falls back
        // to `LOT_THING_ID` in `main`, and `None` there means whole-vault).
        let cli = Cli::try_parse_from(["lot", "watch"]).unwrap();
        match cli.command {
            Command::Watch(ThingFlag { thing }) => assert_eq!(thing, None),
            other => panic!("expected `watch`, got {other:?}"),
        }

        // `--thing <id>` scopes the stream to that Thing's subtree.
        let cli = Cli::try_parse_from(["lot", "watch", "--thing", "lot:abc"]).unwrap();
        match cli.command {
            Command::Watch(ThingFlag { thing }) => assert_eq!(thing.as_deref(), Some("lot:abc")),
            other => panic!("expected `watch`, got {other:?}"),
        }
    }
}
