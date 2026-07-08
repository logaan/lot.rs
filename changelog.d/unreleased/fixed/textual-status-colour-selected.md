- The Textual UI now keeps a Thing's colour-coded status visible on the
  selected row. Previously the focused tree's cursor styling was painted over
  the whole leading `mark`/`status` column, so the status word on the selected
  Thing lost its type colour (and the default `note` even rendered blue-on-blue
  against the block cursor) — most noticeable right after creating a Thing,
  which jumps the selection onto it. The cursor/hover highlight now applies to
  the name only; the status chip keeps its own colour in every state.
