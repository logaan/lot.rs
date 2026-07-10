- The Textual UI's new-Thing form is now **inline** too: instead of a centred
  modal popup, it opens in the detail pane (name, body and preamble fields),
  where the new Thing lands once it is created and the selection jumps to it. It
  keeps the same body editor, preamble box, `$EDITOR` escape hatch (`ctrl+o`),
  empty-name validation and discard confirmation; `ctrl+s` creates, `ctrl+t`
  creates **and** hands the Thing to Claude, and `escape` cancels. (The old
  modal `NewThingScreen` is removed.)
