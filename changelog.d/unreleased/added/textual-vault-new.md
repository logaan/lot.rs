- The Textual UI can now run `vault new` from the command palette and navigator:
  it collects a path and creates a new LoT vault there, toasting success or the
  CLI error (the path already exists, an unwritable location). Creating a vault
  only creates it on disk — it does not register the vault or switch the running
  session to it; use the existing switch-vault flow for that. With this, every
  `lot` command surfaced in the UI now has a real handler — the "coming in a
  later phase" placeholder is gone.
