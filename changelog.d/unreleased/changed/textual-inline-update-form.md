- The Textual UI's new-Update form is now **inline**: instead of a centred modal
  popup, it appears at the foot of the detail pane's update thread — right where
  the new Update will land. It keeps the same body editor, preamble box,
  `$EDITOR` escape hatch (`ctrl+o`), empty-body validation, and discard
  confirmation; `ctrl+s` adds the Update and `escape` cancels. (The batch
  "Update marked Things" form stays a modal, since it targets many Things at
  once with no single landing place.)
