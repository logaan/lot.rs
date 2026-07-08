- The Textual UI no longer crashes to a raw Python stack trace when something
  goes wrong. A failed initial load (a missing/older `lot`, a broken vault, or
  malformed output) now brings the app up empty with a clear toast instead of
  crashing on launch; background workers (the live-update watcher, detail-pane
  loads, vault switching, post-action reloads) surface CLI and parse failures
  as error toasts and keep running; and any remaining unexpected exception is
  caught by a global backstop that shows a short, reassuring message and writes
  the full traceback to a crash-log file rather than dumping it over the
  terminal.
