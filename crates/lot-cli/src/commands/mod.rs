//! One module per `lot` command family. `run()` in `main.rs` is pure
//! dispatch; each family's handler (and the helpers only it uses) lives here.

pub(crate) mod claude;
pub(crate) mod help;
pub(crate) mod settings;
pub(crate) mod thing;
pub(crate) mod ui;
pub(crate) mod update;
pub(crate) mod vault;
pub(crate) mod watch;
