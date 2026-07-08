- `lot thing new` and `lot update` accept `--preamble <yaml>`: a small YAML
  mapping merged into the update's frontmatter, so it folds into the Thing's
  computed-state preamble like any managed field. This is how a coordinator
  flags a child task's model — `--preamble 'claude-model: opus'` — and reads it
  back from `lot thing get`. Keys `lot` manages (`status`, `task-id`,
  `update-id`, and `<type>-at`) are rejected, and the newest update's value
  wins.
