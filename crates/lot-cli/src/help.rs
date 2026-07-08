//! Serialise clap's command tree to YAML for `lot help --format=yaml`.
//!
//! The Textual UI reads this once at startup to discover which commands exist,
//! so the palette reflects whatever `lot` is installed rather than a hard-coded
//! list.
//! Everything visible in a command's `--help` is represented here: its
//! description, its arguments, and its sub-commands nested beneath it.

use clap::{Arg, ArgAction, Command};
use lot_core::UpdateType;
use serde::Serialize;

/// One command (or sub-command) and everything below it.
#[derive(Debug, Serialize)]
pub struct HelpNode {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub about: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub long_about: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<HelpArg>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub subcommands: Vec<HelpNode>,
}

/// A single argument of a command (positional or option/flag).
#[derive(Debug, Serialize)]
pub struct HelpArg {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub help: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub long: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub short: Option<String>,
    pub required: bool,
    pub takes_value: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub possible_values: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default: Option<String>,
}

/// Render `cmd` and its whole sub-command tree as a YAML document.
pub fn command_tree_yaml(cmd: &Command) -> serde_yaml_ng::Result<String> {
    serde_yaml_ng::to_string(&node(cmd))
}

/// Graft the effective update types onto the static clap tree as sub-commands
/// of `update`, so `lot help` — and any command palette built from it —
/// reflects the vault's configured types. The static tree carries none of its
/// own: update types are entirely config-defined.
///
/// Each grafted sub-command carries the arguments the external-subcommand
/// fallback will parse it with: `--thing` always, plus the trailing `content`
/// for body-bearing types but not for bare markers.
pub fn with_update_types(cmd: Command, types: &[UpdateType]) -> Command {
    if types.is_empty() {
        return cmd;
    }
    cmd.mut_subcommand("update", |update| {
        types
            .iter()
            .fold(update, |u, t| u.subcommand(update_type_subcommand(t)))
    })
}

/// Build the clap sub-command for one configured update type. Kept in sync
/// with `UpdateArgs`/`ThingFlag` in `cli.rs`, which are what actually parse the
/// invocation (via the external-subcommand fallback).
fn update_type_subcommand(t: &UpdateType) -> Command {
    let mut about = format!("Create a `{}` update (a type from config", t.name);
    if t.terminal {
        about.push_str("; a terminal state");
    }
    if !t.takes_body {
        about.push_str("; no contents");
    }
    about.push_str(").");

    let mut sub = Command::new(t.name.clone()).about(about).arg(
        Arg::new("thing")
            .long("thing")
            .help(
                "The Thing's id (e.g. lot:6Ic9Cg6kx0Xk2hQhVz3aBd). \
                 Defaults to `LOT_THING_ID` when not given.",
            )
            .action(ArgAction::Set),
    );
    if t.takes_body {
        sub = sub.arg(
            Arg::new("content")
                .help("Update content, supplied after `--`. Mutually exclusive with stdin.")
                .num_args(0..)
                .trailing_var_arg(true),
        );
    }
    sub
}

/// Build a [`HelpNode`] from a clap [`Command`], recursing into sub-commands.
fn node(cmd: &Command) -> HelpNode {
    HelpNode {
        name: cmd.get_name().to_string(),
        about: cmd.get_about().map(|s| s.to_string()),
        long_about: cmd.get_long_about().map(|s| s.to_string()),
        args: cmd
            .get_arguments()
            .filter(|a| !is_builtin(a) && !a.is_hide_set())
            .map(arg)
            .collect(),
        subcommands: cmd
            .get_subcommands()
            .filter(|c| !c.is_hide_set())
            .map(node)
            .collect(),
    }
}

/// Convert a clap [`Arg`] to a [`HelpArg`].
fn arg(a: &Arg) -> HelpArg {
    let default = a
        .get_default_values()
        .iter()
        .map(|v| v.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(", ");
    HelpArg {
        name: a.get_id().as_str().to_string(),
        help: a.get_help().map(|s| s.to_string()),
        long: a.get_long().map(|s| s.to_string()),
        short: a.get_short().map(|c| c.to_string()),
        required: a.is_required_set(),
        takes_value: takes_value(a),
        possible_values: a
            .get_possible_values()
            .iter()
            .map(|p| p.get_name().to_string())
            .collect(),
        default: (!default.is_empty()).then_some(default),
    }
}

/// Whether an argument consumes a value (an option/positional) rather than
/// being a bare flag/counter.
fn takes_value(a: &Arg) -> bool {
    !matches!(
        a.get_action(),
        ArgAction::SetTrue
            | ArgAction::SetFalse
            | ArgAction::Count
            | ArgAction::Help
            | ArgAction::Version
    )
}

/// clap auto-adds `--help`/`--version`; those are noise in the tree.
fn is_builtin(a: &Arg) -> bool {
    matches!(a.get_id().as_str(), "help" | "version")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cli::Cli;
    use clap::CommandFactory;

    #[test]
    fn yaml_nests_subcommands_and_omits_builtin_args() {
        let yaml = command_tree_yaml(&Cli::command()).unwrap();

        // The root and a couple of nested commands are present.
        assert!(yaml.contains("name: lot"));
        assert!(yaml.contains("name: thing"));
        assert!(yaml.contains("name: new"));
        assert!(yaml.contains("name: interface"));
        // Our explicit help command shows up; clap's auto `--help`/`--version`
        // args and auto `help` subcommand do not.
        assert!(yaml.contains("name: help"));
        assert!(!yaml.contains("name: version"));

        // It parses back as a tree whose root is `lot` with subcommands.
        let parsed: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(parsed["name"].as_str(), Some("lot"));
        let subs = parsed["subcommands"].as_sequence().unwrap();
        let names: Vec<&str> = subs.iter().filter_map(|s| s["name"].as_str()).collect();
        assert!(names.contains(&"thing"));
        assert!(names.contains(&"vault"));
    }

    #[test]
    fn thing_get_records_its_format_arg() {
        let yaml = command_tree_yaml(&Cli::command()).unwrap();
        let tree: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();

        let thing = find(&tree, "thing").expect("thing command");
        let get = find(thing, "get").expect("thing get command");
        let args = get["args"].as_sequence().expect("get has args");
        let format = args
            .iter()
            .find(|a| a["name"].as_str() == Some("format"))
            .expect("format arg");
        assert_eq!(format["long"].as_str(), Some("format"));
        assert_eq!(format["takes_value"].as_bool(), Some(true));
        // Its possible values are surfaced for callers/UIs.
        let values: Vec<&str> = format["possible_values"]
            .as_sequence()
            .unwrap()
            .iter()
            .filter_map(|v| v.as_str())
            .collect();
        assert!(values.contains(&"yaml"));
        assert!(values.contains(&"markdown"));
    }

    /// Find a direct sub-command of `node` by name.
    fn find<'a>(node: &'a serde_yaml_ng::Value, name: &str) -> Option<&'a serde_yaml_ng::Value> {
        node["subcommands"]
            .as_sequence()?
            .iter()
            .find(|s| s["name"].as_str() == Some(name))
    }

    #[test]
    fn update_types_graft_onto_the_update_subcommand() {
        let types = [
            UpdateType {
                name: "blocked".into(),
                takes_body: true,
                terminal: false,
            },
            UpdateType {
                name: "wont-do".into(),
                takes_body: false,
                terminal: true,
            },
        ];
        let cmd = with_update_types(Cli::command(), &types);
        let yaml = command_tree_yaml(&cmd).unwrap();
        let tree: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();

        let update = find(&tree, "update").expect("update command");
        // The static tree carries no types of its own — only `path` plus the
        // grafted, config-defined set.
        assert!(find(update, "path").is_some());
        assert!(find(update, "work").is_none());

        // Each type appears with its mirrored arguments: a body-bearing type
        // gets `--thing` plus the trailing `content`...
        let blocked = find(update, "blocked").expect("blocked subcommand");
        let arg_names = |node: &serde_yaml_ng::Value| -> Vec<String> {
            node["args"]
                .as_sequence()
                .map(|args| {
                    args.iter()
                        .filter_map(|a| a["name"].as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default()
        };
        assert_eq!(arg_names(blocked), ["thing", "content"]);

        // ...while a bare marker gets `--thing` only, like `done`.
        let wont_do = find(update, "wont-do").expect("wont-do subcommand");
        assert_eq!(arg_names(wont_do), ["thing"]);
        assert!(wont_do["about"]
            .as_str()
            .unwrap()
            .contains("a terminal state"));
    }

    #[test]
    fn no_types_leaves_the_tree_unchanged() {
        let plain = command_tree_yaml(&Cli::command()).unwrap();
        let grafted = command_tree_yaml(&with_update_types(Cli::command(), &[])).unwrap();
        assert_eq!(plain, grafted);
    }

    #[test]
    fn stock_types_graft_like_any_other() {
        // The default set (note/work/info/done) reaches help the same way a
        // custom set does: by grafting the effective types.
        let cmd = with_update_types(Cli::command(), &lot_core::default_update_types());
        let yaml = command_tree_yaml(&cmd).unwrap();
        let tree: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();

        let update = find(&tree, "update").expect("update command");
        for name in ["note", "work", "info", "done"] {
            assert!(find(update, name).is_some(), "{name} grafted");
        }
    }
}
