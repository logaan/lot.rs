- The Textual UI can now toggle how its tree columns are sorted. `s` cycles
  through three orders: **status** (grouped by the vault's configured update
  types — the new default), **recent activity** (most recently updated subtree
  first, folding in every descendant's updates), and **name** (alphabetical).
  The order is view-only and in-memory — it never reorders anything on disk and
  resets to the status grouping each launch.
