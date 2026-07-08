- Clicking a `lot:` link in a detail-pane update body now navigates to that
  Thing (or the Thing owning that update) *inside* the UI instead of opening
  the raw `lot:` URI in a web browser. Textual's `Markdown` widget opens every
  link itself by default, and its handler fired before the pane's, so each
  in-vault link both launched a browser and navigated; the body Markdown
  widgets now run with `open_links=False` and the detail pane routes every
  link — `lot:` in-app, other schemes (`https:`, …) to the browser as before.
