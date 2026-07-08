//! `lot help`: print the usual help, or the whole command tree as YAML.

use crate::cli::{Cli, HelpArgs, HelpFormat};
use crate::help;
use anyhow::{Context, Result};
use clap::CommandFactory;

/// `lot help`: print the usual help, or — with `--format=yaml` — the whole
/// command tree as YAML for machine consumers (notably the TUI).
///
/// Update types are entirely config-defined, so both forms graft the
/// effective types onto the `update` sub-command: the YAML tree so a
/// front-end's command palette offers them (their flags —
/// takes-body/terminal — live in `lot settings get`, the canonical discovery
/// surface), and the human help so `lot help` lists what `lot update` can
/// actually create. When config can't be read the human help degrades to the
/// static tree rather than failing; the YAML form errors, as a machine
/// consumer must not act on an incomplete tree.
pub(crate) fn run(args: HelpArgs) -> Result<()> {
    match args.format {
        Some(HelpFormat::Yaml) => {
            let types = lot_core::load_update_types().context("resolving update types")?;
            let cmd = help::with_update_types(Cli::command(), types.all());
            let yaml = help::command_tree_yaml(&cmd).context("rendering help YAML")?;
            print!("{yaml}");
        }
        None => {
            let mut cmd = match lot_core::load_update_types() {
                Ok(types) => help::with_update_types(Cli::command(), types.all()),
                Err(_) => Cli::command(),
            };
            cmd.print_help().context("printing help")?;
            println!();
        }
    }
    Ok(())
}
