"""The central keybinding table — the single seam for Phase 5 overrides.

Every key binding the LoT app itself defines lives here in
:data:`ACTION_BINDINGS`, a flat list of :class:`textual.binding.Binding`
objects. :class:`~lot_textual_ui.app.LotTextualApp` sets
``BINDINGS = ACTION_BINDINGS`` verbatim, so this list is the *only* place app
keys are declared.

.. _keybinding-seam:

Why one table
-------------

Users remap keys through config. Because there is exactly one structure to read
and rewrite, the override layer is a single pure transform of this list —
:func:`apply_overrides` swaps ``key`` for a matching ``action`` — with no need
to hunt for ``Binding``\\s scattered across widgets.
:class:`~lot_textual_ui.app.LotTextualApp` calls it on mount (once ``lot config
get``'s ``keybindings`` are loaded) and rebuilds its merged bindings from the
result, so the footer and dispatch both reflect the overrides. Keep it that
way: any new app-level key belongs in this list, not in a widget's
``BINDINGS``, so it stays remappable through the one seam.

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
* ``command_nav`` — open the ``space`` hierarchical command navigator (see
  :mod:`lot_textual_ui.command_nav`). Its ``ctrl+<letter>`` top-level
  shortcuts are *not* bindings — they are derived at runtime from the
  discovered command tree and handled in the app's ``on_key``.
* ``cursor_down`` / ``cursor_up`` — move the focused tree's cursor, or scroll
  the detail pane, by one row.
* ``cursor_top`` / ``cursor_bottom`` — jump the focused pane to its first/last
  row (a single ``g`` stands in for vim's ``gg``).
* ``focus_left`` / ``focus_right`` — move focus one column out/in across
  left tree -> centre tree -> detail pane (drilling out/in).
* ``new_thing`` — open the new-Thing form to create a top-level Thing.
* ``new_child_thing`` — open the new-Thing form seeded with the current
  selection as the parent, creating a child of it.
* ``copy_thing_uri`` / ``copy_thing_path`` — copy the selected Thing's ``lot:``
  id / filesystem path to the system clipboard. The two less-common
  Update-scoped copies (URI and path) are palette-only (see
  :data:`lot_textual_ui.palette.INTERNAL_COMMANDS`).
* ``copy_selection`` — copy the current mouse text-selection to the clipboard
  (Textual's native ``ctrl+c`` does the same silently; this key toasts).
* ``toggle_update`` — collapse/expand the focused update in the detail thread.
  Collapse-all / expand-all are palette-only (see
  :data:`lot_textual_ui.palette.INTERNAL_COMMANDS`).
"""

from __future__ import annotations

from collections.abc import Mapping

from textual.binding import Binding

# The one and only app-level binding table. See the module docstring for the
# override seam this exists to provide.
ACTION_BINDINGS: list[Binding] = [
    Binding("q", "quit", "Quit"),
    # The hierarchical command navigator (see :mod:`lot_textual_ui.command_nav`):
    # Space opens it at the command tree's top level. ``priority=True`` so it
    # beats a focused Tree's own space-to-toggle; the app's ``check_action``
    # disables it whenever a modal is up, so a space typed into a form's input
    # stays a space.
    Binding("space", "command_nav", "Commands", priority=True),
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
    # Yank the selected Thing's URI / path to the clipboard. The Update-scoped
    # copies live in the palette (Copy Update URI/path) rather than taking two
    # more top-level keys.
    Binding("y", "copy_thing_uri", "Copy URI"),
    Binding("Y", "copy_thing_path", "Copy path"),
    # Copy the current mouse text-selection (drag to select in the detail pane).
    # Textual's own ``ctrl+c`` copies a selection too, but silently; this key
    # (and the "Copy selection" palette command) is the discoverable, toasting
    # entry point. ``c`` for "copy".
    Binding("c", "copy_selection", "Copy text"),
    # Collapse/expand the focused update in the detail thread (mnemonic: vim's
    # ``z`` fold prefix). Collapse-all / expand-all live in the palette rather
    # than taking more top-level keys.
    Binding("z", "toggle_update", "Fold update"),
    # Aliases that read naturally as "drill in / back out"; hidden from the
    # footer to keep the hints uncluttered. On a focused tree, Textual's own
    # ``enter`` binding (select) fires first, so these only take effect
    # elsewhere — selection keeps working unchanged.
    Binding("enter", "focus_right", "In", show=False),
    Binding("backspace", "focus_left", "Out", show=False),
]

# The canonical action names a user may remap in config (``[keybindings]`` in
# ``config.toml`` / ``vault.toml``, surfaced by ``lot settings get`` under the
# ``keybindings`` map). This is exactly the set of ``action`` strings declared
# in :data:`ACTION_BINDINGS` — the only keys the app itself binds — so it is
# derived from that table rather than repeated. Documented for users in the
# Python README's "Remappable actions" section; keep the two in sync.
REMAPPABLE_ACTIONS: tuple[str, ...] = tuple(
    dict.fromkeys(binding.action for binding in ACTION_BINDINGS)
)


def apply_overrides(
    bindings: list[Binding], overrides: Mapping[str, str]
) -> list[Binding]:
    """Return a copy of ``bindings`` with configured key overrides applied.

    This is the transform the module docstring's :ref:`override seam
    <keybinding-seam>` promises: rather than hunting for ``Binding``\\ s across
    widgets, the whole override layer is one pure rewrite of the single central
    table.

    ``overrides`` maps an **action name** to the **key** (or keys) that should
    trigger it — the shape of :attr:`EffectiveConfig.keybindings
    <lot_textual_ui.models.EffectiveConfig.keybindings>` (``lot settings get``'s
    already-merged user+vault ``keybindings`` map). For every binding whose
    :attr:`~textual.binding.Binding.action` is present in ``overrides``, a new
    binding is emitted with the overridden ``key`` and *every other* property
    preserved (``description``/``show``/``priority``/``tooltip`` …), via
    :meth:`Binding.with_key`; bindings whose action is not overridden pass
    through unchanged. A brand-new list is returned — the module-level
    :data:`ACTION_BINDINGS` constant is never mutated.

    Semantics (kept deliberately simple and predictable):

    * **Match is by action, so an override rewrites *all* of that action's
      bindings.** ``focus_right`` has two entries (``l`` and, hidden, ``enter``);
      overriding ``focus_right`` moves both onto the new key. This reads as
      "this action's key is now X".
    * **Multiple keys per action** are supported for free: a comma-separated
      value (``"s,down"``) is carried onto the binding, and Textual's
      :class:`~textual.binding.BindingsMap` splits it into one binding per key.
    * **Unknown action** — an ``overrides`` entry naming an action that no
      binding uses (a typo, or a palette-only action) matches nothing and is
      silently ignored; it never raises.
    * **Collisions** — because ``overrides`` is keyed by action, one action maps
      to one key value, so an override cannot contradict itself. If two *different*
      actions are overridden onto the same key (or an override lands on a key
      another, non-overridden binding already uses), both bindings end up under
      that key in the resulting :class:`~textual.binding.BindingsMap`; Textual
      resolves the clash at dispatch time (first match wins) and reports it via
      :meth:`App.handle_bindings_clash`. This transform does not police it — the
      config is taken at face value.
    """
    if not overrides:
        return list(bindings)
    return [
        binding.with_key(key=override, key_display=None)
        if (override := overrides.get(binding.action)) is not None
        else binding
        for binding in bindings
    ]
