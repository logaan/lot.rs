- Archiving now seeks confirmation before deleting unfinished work. Both `lot
  vault archive` and `lot thing archive` refuse — deleting and committing
  nothing — when a Thing being archived has a not-done (non-terminal)
  descendant that would be swept away with it, listing those Things so nothing
  disappears by surprise. Pass `--force` (`-f`) to archive the whole subtree
  anyway. In the Textual UI the archive confirmation dialogs (batch `d` and
  vault-wide) name the not-done Things that would also go, and confirming
  passes `--force`.
