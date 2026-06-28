//! Serialise clap's command tree to YAML for `lot help --format=yaml`.
//!
//! The TUI reads this once at startup to discover which commands exist, so the
//! palette reflects whatever `lot` is installed rather than a hard-coded list.
//! Everything visible in a command's `--help` is represented here: its
//! description, its arguments, and its sub-commands nested beneath it.

use clap::{Arg, ArgAction, Command};
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
}
