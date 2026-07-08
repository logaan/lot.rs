//! `lot vault`: create a vault and archive its done Things.

use crate::cli::VaultCommand;
use crate::context::{open_vault, warn_if_no_update_types};
use anyhow::{Context, Result};
use lot_core::Vault;

pub(crate) fn run(cmd: VaultCommand) -> Result<()> {
    match cmd {
        VaultCommand::New { path } => {
            let vault = Vault::create(&path).context("creating vault")?;
            // Print the vault path so it can be referenced by scripts.
            println!("{}", vault.path().display());
        }
        VaultCommand::Archive => {
            let vault = open_vault()?;
            // Which statuses count as terminal comes entirely from the
            // config-defined update types. With none configured nothing is
            // terminal and nothing archives — warn rather than pass silently.
            let types = lot_core::load_update_types().context("resolving update types")?;
            warn_if_no_update_types(types.all());
            let archived = vault.archive_done_things(&types)?;
            // Print the archived Things' ids so scripts can confirm what went.
            for id in archived {
                println!("{id}");
            }
        }
    }
    Ok(())
}
