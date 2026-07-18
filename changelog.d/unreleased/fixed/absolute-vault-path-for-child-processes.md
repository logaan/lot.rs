- The resolved vault path is now always absolute, so the `LOT_VAULT_PATH`
  handed to child processes (`lot interface`, `lot web`, `lot claude send`)
  names the same vault from any working directory. Previously a relative
  `vault.path` — or a relative `LOT_VAULT_PATH` — was forwarded verbatim, and a
  child that ran elsewhere (a `claude` session in a git worktree, say) resolved
  it against its own directory, silently opening or creating a different vault.
