- The Textual UI can now run `thing move` and `thing archive` on the in-view
  Thing from the command palette and navigator instead of a "coming later"
  toast. `thing move` opens the destination picker (choose the vault's top level
  or any Thing) and `thing archive` asks for confirmation first (it removes the
  Thing and all its descendants from the vault, history kept in git); both
  refresh the view and report the outcome, surfacing CLI errors (a move cycle, a
  name collision, the auto-commit refusal) as a toast.
