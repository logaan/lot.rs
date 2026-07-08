- `lot watch` now accepts a `--thing <id>` flag (falling back to
  `LOT_THING_ID`) that scopes the event stream to one Thing and its
  descendants, so a coordinator can watch just its own subtree instead of the
  whole vault. Omitting it watches the whole vault, as before.
