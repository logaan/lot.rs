//! `lot watch`: stream one YAML event per vault change on stdout.

use crate::cli::ThingFlag;
use crate::context::{open_vault, resolve_thing_optional};
use anyhow::{Context, Result};
use std::io::Write;

/// `lot watch`: watch the resolved vault and stream one YAML event per change on
/// stdout. Each event is framed with a leading `---` document marker and flushed
/// immediately, so a consumer can read one YAML document at a time even off a
/// live pipe. This blocks until the process is interrupted (Ctrl-C).
///
/// `thing` scopes the stream to one Thing and its descendants (falling back to
/// `LOT_THING_ID`, like other Thing references); when neither is set, the
/// whole vault is watched, unchanged from before scoping existed.
pub(crate) fn run(thing: ThingFlag) -> Result<()> {
    let vault = open_vault()?;
    let root = resolve_thing_optional(thing.thing);
    let mut stdout = std::io::stdout();
    lot_core::watch::watch(&vault, root.as_deref(), |event| {
        let yaml = event.to_yaml()?;
        // The `---` marker separates documents in the stream; the YAML body is
        // block-style with all content indented, so a bare `---` at column 0
        // only ever marks an event boundary. Flush so live consumers see each
        // event immediately rather than when the OS buffer fills. IO errors
        // convert into `lot_core::Error` via `?`, matching the closure's result
        // type.
        write!(stdout, "---\n{yaml}")?;
        stdout.flush()?;
        Ok(())
    })
    .context("watching the vault")?;
    Ok(())
}
