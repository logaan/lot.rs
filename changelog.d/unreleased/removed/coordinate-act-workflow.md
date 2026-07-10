- `lot claude coordinate <model> act` and the bundled `lot-coordinate-act`
  skill are gone: executing an existing plan now happens by `lot claude
  send`-ing its "Update plan and begin coordination" child, which loads the
  `lot-coordinate-begin` skill (act plus decision reconciliation).
