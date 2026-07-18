- The CLI integration tests no longer inherit the developer's `LOT_*`
  environment, so `scripts/check` passes from a session started by `lot claude
  send`. Those sessions export `LOT_THING_ID` for the real vault; the tests
  set `LOT_VAULT_PATH` to a temp vault but left the rest inherited, so
  `lot watch` tried to resolve that Thing in the temp vault and exited 1 —
  a failure that read as a regression in whatever branch was under test.
