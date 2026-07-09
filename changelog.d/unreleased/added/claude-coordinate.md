- `lot claude coordinate <model> <skill> [thing]` launches a background Claude
  **coordinator** session on one of the bundled coordinator skills, chosen by
  alias: `decide`, `plan`, or `act`. Like `lot claude send`, it commits the
  working tree first, names the session after the Thing, and records the launch
  on the Thing as a `work` update. `lot claude install` now installs all four
  bundled skills (`lot-task` plus the three `lot-coordinate-*` skills).
