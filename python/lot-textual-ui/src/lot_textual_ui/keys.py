"""The central keybinding table — the single seam for Phase 5 overrides.

Every key binding the LoT app itself defines lives here in
:data:`ACTION_BINDINGS`, a flat list of :class:`textual.binding.Binding`
objects. :class:`~lot_textual_ui.app.LotTextualApp` sets
``BINDINGS = ACTION_BINDINGS`` verbatim, so this list is the *only* place app
keys are declared.

.. _keybinding-seam:

Why one table
-------------

Phase 5 will let users remap keys. Because there is exactly one structure to
read and rewrite, an override layer can be applied by transforming this list
(swapping ``key`` for a matching ``action``) without hunting for
``Binding``\\s scattered across widgets. Keep it that way: any new app-level
key belongs in this list, not in a widget's ``BINDINGS``.

What is *not* here (on purpose)
-------------------------------

Keys that belong to Textual's own widgets — a :class:`~textual.widgets.Tree`'s
arrow-key cursor and ``enter``-to-select, a
:class:`~textual.containers.VerticalScroll`'s ``pageup``/``pagedown`` — stay
with those widgets. The app layers vim-style motions (``j``/``k``/``g``/``G``)
and cross-column focus (``h``/``l``) on top; each action method dispatches to
whichever pane currently holds focus, so one binding drives every pane.

Actions (all implemented as ``action_*`` methods on the app, except
``command_palette`` which is Textual's own built-in):

* ``quit`` — leave the app.
* ``command_palette`` — open the ``ctrl+p`` fuzzy command palette (see
  :mod:`lot_textual_ui.palette`).
* ``cursor_down`` / ``cursor_up`` — move the focused tree's cursor, or scroll
  the detail pane, by one row.
* ``cursor_top`` / ``cursor_bottom`` — jump the focused pane to its first/last
  row (a single ``g`` stands in for vim's ``gg``).
* ``focus_left`` / ``focus_right`` — move focus one column out/in across
  left tree -> centre tree -> detail pane (drilling out/in).
* ``new_thing`` — open the new-Thing form to create a top-level Thing.
* ``new_child_thing`` — open the new-Thing form seeded with the current
  selection as the parent, creating a child of it.
"""

from __future__ import annotations

from textual.binding import Binding

# The one and only app-level binding table. See the module docstring for the
# override seam this exists to provide.
ACTION_BINDINGS: list[Binding] = [
    Binding("q", "quit", "Quit"),
    # The command palette (Textual's built-in ``command_palette`` action). Textual
    # auto-binds ``ctrl+p`` when ``ENABLE_COMMAND_PALETTE`` is on; it is declared
    # here too so this table stays the single source of truth for app keys and
    # Phase 5's override layer can remap it like any other.
    Binding("ctrl+p", "command_palette", "Palette"),
    # Vertical motion within the focused pane.
    Binding("j", "cursor_down", "Down"),
    Binding("k", "cursor_up", "Up"),
    Binding("g", "cursor_top", "Top"),
    Binding("G", "cursor_bottom", "Bottom"),
    # Horizontal focus movement / drill in & out across the three columns.
    Binding("l", "focus_right", "In"),
    Binding("h", "focus_left", "Out"),
    # Creating Things. ``n`` starts a new top-level Thing; ``a`` adds a child
    # under the current selection (mnemonic: "add child").
    Binding("n", "new_thing", "New"),
    Binding("a", "new_child_thing", "Add child"),
    # Aliases that read naturally as "drill in / back out"; hidden from the
    # footer to keep the hints uncluttered. On a focused tree, Textual's own
    # ``enter`` binding (select) fires first, so these only take effect
    # elsewhere — selection keeps working unchanged.
    Binding("enter", "focus_right", "In", show=False),
    Binding("backspace", "focus_left", "Out", show=False),
]
