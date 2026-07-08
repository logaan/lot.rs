- Modal dialog buttons now use a consistent, edit-safe `ctrl+<letter>` shortcut
  scheme. **Cancel** is always `ctrl+l`, and each dialog's primary/confirm
  action (Create `ctrl+r`, Add `ctrl+d`, Archive/Delete `ctrl+r`/`ctrl+d`) is
  assigned so that no dialog's submit/confirm chord can ever equal another
  dialog's Cancel chord — you can no longer fire a destructive Archive with a
  chord that means Cancel on a different screen. Button shortcuts also no longer
  shadow a text field's editing or cursor-navigation keys: the destructive
  edits (`ctrl+a/u/k/w/x/v/y`) *and* the emacs/readline cursor motions
  (`ctrl+a/e/b/f/n/p`). This closes a data-loss trap where **Cancel** used to
  sit on `ctrl+n` — pressing it to move the cursor down a line while editing a
  form discarded the whole form.
