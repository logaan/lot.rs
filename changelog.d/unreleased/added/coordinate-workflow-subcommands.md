- `lot claude coordinate <model>` now offers its three workflows as
  sub-commands — `decide`, `plan`, `act` — so running it without one lists them
  with a description of each, instead of failing with a missing-argument error.
  The command line is unchanged: `lot claude coordinate opus plan lot:abc`.
- The Textual interface can start a coordinator: the command navigator's
  `claude` → `coordinate` step now walks model → workflow, then launches on the
  Thing you are looking at, just as `claude send` does.
