//! System clipboard access for mouse-copy from the detail pane.

use anyhow::{Context, Result};

/// A lazily-connected system clipboard, kept alive for the whole session: on
/// X11 clipboard contents live only as long as the owning connection, so a
/// long-lived instance keeps a copy pasteable after the copy itself. On macOS
/// and Windows the write is durable either way.
#[derive(Default)]
pub struct SystemClipboard {
    inner: Option<arboard::Clipboard>,
}

impl SystemClipboard {
    pub fn copy(&mut self, text: &str) -> Result<()> {
        if self.inner.is_none() {
            self.inner =
                Some(arboard::Clipboard::new().context("connecting to the system clipboard")?);
        }
        self.inner
            .as_mut()
            .expect("clipboard was just connected")
            .set_text(text)
            .context("writing to the system clipboard")
    }
}
