- The Textual UI's new-Thing, update, and batch-update forms now carry a
  **Preamble (YAML)** editor below the body. It opens seeded with a commented
  preview of the frontmatter `lot` will write (`status`, the ids, the
  `<type>-at` timestamp), so it is clear what the preamble will be; adding a
  field of your own — `claude-model: opus` — passes the box to
  `lot ... --preamble`. Left untouched it carries nothing but comments, so no
  flag is sent and cancelling prompts no discard. On the batch form the one
  preamble is stamped onto every marked Thing. A reserved key or non-mapping
  YAML comes back from `lot` as an error toast with the form left open.
