- Modal dialog buttons now use a consistent, edit-safe `ctrl+<letter>` shortcut
  scheme. **Cancel** is always `ctrl+n`, and each dialog's primary/confirm
  action (Create `ctrl+r`, Add `ctrl+d`, Archive/Delete `ctrl+r`/`ctrl+d`) is
  assigned so that no dialog's submit/confirm chord can ever equal another
  dialog's Cancel chord — you can no longer fire a destructive Archive with a
  chord that means Cancel on a different screen. Button shortcuts also no longer
  shadow the terminal's text-editing keys (`ctrl+a/u/k/w/x/v/y`).
