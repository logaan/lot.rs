- Interface: Thing-scoped actions (add update, retire, add child, archive,
  move, copy, send to Claude) now act on the Thing under the *focused* column's
  cursor, which is also the Thing the detail pane shows. Previously they always
  acted on the centre column's highlighted item, so after drilling into a child
  and stepping back to the left column, `ctrl+u` `d` retired the child rather
  than the Thing under the cursor.
- Interface: cursoring onto the centre column's whole-vault root row now leaves
  nothing to act on, instead of silently keeping the last highlighted Thing as
  the target of the next action.
- Interface: the marking keys (`x`, `X`) and every other Thing-scoped action now
  agree on which Thing they target; they used to resolve it two different ways.
- Interface: creating a Thing, and following a `lot:` link in an update body,
  now put the cursor on the Thing you land on and focus the column holding it.
