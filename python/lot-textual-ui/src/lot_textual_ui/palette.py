"""The command palette: ``lot`` leaf commands plus TUI-internal commands.

This module feeds Textual's *native* command palette (opened with ``ctrl+p``)
rather than re-implementing the Rust TUI's Space-leader tree walk. Two
:class:`~textual.command.Provider`\\s are registered on the app (see
:data:`~lot_textual_ui.app.LotTextualApp.COMMANDS`):

* :class:`LotCommandProvider` — the ``lot`` command tree, discovered at runtime
  from ``lot help --format=yaml`` (via the app's shared
  :class:`~lot_textual_ui.lot_cli.LotCli`) and flattened into runnable leaf
  commands (``thing new``, ``update work``, ``vault new``, …).
* :class:`InternalCommandProvider` — commands the TUI implements itself (Quit,
  Refresh vault, …), taken from the :data:`INTERNAL_COMMANDS` registry.

.. _palette-forms-seam:

The forms seam (READ THIS if you are the new-Thing / new-Update forms agent)
---------------------------------------------------------------------------

Picking a ``lot`` leaf command in the palette calls
:meth:`~lot_textual_ui.app.LotTextualApp.run_lot_command` with the selected
:class:`LeafCommand`. Today that method has two branches:

* commands that need **no** user input (every argument optional and defaulted —
  see :attr:`LeafCommand.needs_input`) are run as-is through
  :meth:`LotCli.run_command` and the vault view is refreshed; and
* commands that need input are dispatched on ``command.path`` to a handler:
  ``thing new`` opens its form screen; each ``update <type>`` leaf (built-in
  or custom) is **type-specific** — a body-taking type opens a form fixed to
  it (body only, no type selector) and a bodyless type (``done``-likes) runs
  immediately on the in-view Thing with no form; ``claude send <model>``
  launches on the in-view Thing; any input-needing command without its own
  handler raises a placeholder toast — **this is your hook** for the
  remaining ones.

To add a form for one of the remaining commands, extend the ``needs_input``
branch of ``LotTextualApp.run_lot_command``: dispatch on ``command.path``,
push the matching form screen, collect the fields/stdin from
:attr:`LeafCommand.args` (each an :class:`ArgSpec` carrying
name/help/required/default/possible-values), then run the command via
``LotCli`` and refresh. The read-only branch and the generic
``LotCli.run_command`` seam can stay exactly as they are.

To add more **internal** palette commands (switch theme, switch vault, copy
URI, new Thing/Update entry points, …) just append an :class:`InternalCommand`
to :data:`INTERNAL_COMMANDS`; no provider changes are needed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from .app import LotTextualApp

# --- flattening the help tree (pure, fixture-testable) ---------------------

# Command paths discovered from ``lot help`` that must never be offered as
# runnable palette / command-navigator entries:
#
# * ``lot watch`` blocks forever streaming events — run through the generic
#   ``run_command`` seam it would never return (and would leak the spawned
#   process; the app already consumes the stream via :meth:`LotCli.watch`).
# * ``lot web`` starts a server and blocks forever, hanging the worker.
# * ``lot interface`` launches *this very UI*, recursively, from inside a
#   running session.
#
# Both consumers of the help tree honour this set: :func:`flatten_help_tree`
# (the fuzzy palette) skips these paths, and the command navigator prunes them
# from its tree via :func:`prune_hidden_commands`.
HIDDEN_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {("watch",), ("web",), ("interface",)}
)


def prune_hidden_commands(tree: dict[str, Any]) -> dict[str, Any]:
    """A copy of a ``lot help --format=yaml`` tree without the hidden commands.

    Drops every subtree whose command path is in :data:`HIDDEN_COMMANDS`, so a
    consumer walking the returned tree (the command navigator) can never reach
    a blocking/self-referential command. Nodes are shallow-copied only along
    pruned paths; leaves are shared with the input tree.
    """

    def prune(node: dict[str, Any], prefix: tuple[str, ...]) -> dict[str, Any]:
        subcommands = node.get("subcommands") or []
        if not subcommands:
            return node
        kept = []
        for child in subcommands:
            name = child.get("name")
            path = (*prefix, str(name)) if name else prefix
            if path in HIDDEN_COMMANDS:
                continue
            kept.append(prune(child, path))
        pruned = dict(node)
        pruned["subcommands"] = kept
        return pruned

    return prune(tree, ())


@dataclass(frozen=True)
class ArgSpec:
    """One argument of a ``lot`` leaf command, as described by ``lot help``.

    Carries everything a future input form needs to render a field for this
    argument. ``long`` is the ``--flag`` name for options (``None`` for bare
    positionals); ``default`` and ``possible_values`` mirror clap's metadata.
    """

    name: str
    help: str
    required: bool
    takes_value: bool
    long: str | None = None
    default: str | None = None
    possible_values: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ArgSpec:
        values = raw.get("possible_values") or []
        return cls(
            name=str(raw.get("name", "")),
            help=str(raw.get("help", "")),
            required=bool(raw.get("required", False)),
            takes_value=bool(raw.get("takes_value", False)),
            long=raw.get("long"),
            default=raw.get("default"),
            possible_values=tuple(str(v) for v in values),
        )

    @property
    def needs_value_from_user(self) -> bool:
        """True when the user must supply this argument for a valid invocation.

        A required argument always qualifies. A value-taking option with no
        default also does: leaving it out relies on ambient state (e.g.
        ``LOT_THING_ID``) that the palette should not assume, so such commands
        route to the forms hook rather than being fired blind.
        """
        return self.required or (self.takes_value and self.default is None)


@dataclass(frozen=True)
class LeafCommand:
    """A runnable ``lot`` leaf command (a subcommand with no subcommands).

    ``path`` is the argument vector to hand to ``lot`` (e.g.
    ``("thing", "new")``); :attr:`label` joins it for display. ``args`` is the
    ordered list of :class:`ArgSpec`\\s a form would collect.
    """

    path: tuple[str, ...]
    about: str
    long_about: str | None = None
    args: tuple[ArgSpec, ...] = ()

    @property
    def label(self) -> str:
        """The palette label: the command path, space-joined."""
        return " ".join(self.path)

    @property
    def help_text(self) -> str:
        """The palette hint: the command's short description."""
        return self.about

    @property
    def needs_input(self) -> bool:
        """True when any argument requires a value the user must supply.

        Discriminates the two :ref:`palette-forms-seam` branches: no-input
        commands run directly; input-needing ones route to the forms hook.
        """
        return any(arg.needs_value_from_user for arg in self.args)


def leaf_from_node(node: dict[str, Any], path: tuple[str, ...]) -> LeafCommand:
    """Build the runnable :class:`LeafCommand` for a childless help-tree node.

    Shared by :func:`flatten_help_tree` (the fuzzy palette's flat list) and the
    command navigator (:mod:`lot_textual_ui.command_nav`), so a command picked
    by either route carries identical metadata into
    :meth:`~lot_textual_ui.app.LotTextualApp.run_lot_command`.
    """
    args = tuple(ArgSpec.from_dict(a) for a in node.get("args") or [])
    return LeafCommand(
        path=path,
        about=str(node.get("about", "")),
        long_about=node.get("long_about"),
        args=args,
    )


# Leaf commands the *fuzzy palette* suppresses because an
# :data:`INTERNAL_COMMANDS` entry already offers the exact same action — listing
# both just shows two entries that do the same thing. Unlike
# :data:`HIDDEN_COMMANDS` these are perfectly good CLI commands, so only the
# fuzzy palette drops them; the command navigator (which faithfully mirrors the
# whole ``lot`` tree) still surfaces them.
#
# * ``settings set theme`` opens the very theme picker the *Switch theme*
#   internal command opens (see
#   :meth:`~lot_textual_ui.commands.CommandsMixin.run_lot_command` /
#   ``action_switch_theme``), applying and persisting the pick either way. We
#   keep *Switch theme* (purpose-built, clearer help) and drop the raw leaf.
PALETTE_DUPLICATE_LEAVES: frozenset[tuple[str, ...]] = frozenset(
    {("settings", "set", "theme")}
)


def flatten_help_tree(tree: dict[str, Any]) -> list[LeafCommand]:
    """Flatten a ``lot help --format=yaml`` tree into runnable leaf commands.

    Walks the nested ``subcommands`` mapping depth-first, emitting one
    :class:`LeafCommand` per node that has *no* children (a runnable leaf).
    Group nodes (``vault``, ``thing``, ``update``, ``claude``, ``claude
    send``, …) carry no runnable action of their own and are skipped; only
    their leaves are emitted. Blocking/self-referential commands
    (:data:`HIDDEN_COMMANDS` — ``watch``, ``web``, ``interface``) are never
    emitted, and leaves already offered by an internal command
    (:data:`PALETTE_DUPLICATE_LEAVES` — ``settings set theme``) are dropped from
    this fuzzy list too. The top-level ``lot`` name is not part of any path.
    Order follows the help document, so related commands stay grouped.
    """
    leaves: list[LeafCommand] = []

    def walk(node: dict[str, Any], prefix: tuple[str, ...]) -> None:
        subcommands = node.get("subcommands") or []
        if not subcommands:
            # A leaf: runnable. (The root, which always has subcommands, and any
            # group node are therefore never emitted as commands themselves.)
            # Leaves duplicated by an internal command are dropped here (they
            # stay reachable via the command navigator, which does not consult
            # this set).
            if prefix and prefix not in PALETTE_DUPLICATE_LEAVES:
                leaves.append(leaf_from_node(node, prefix))
            return
        for child in subcommands:
            name = child.get("name")
            if not name:
                continue
            path = (*prefix, str(name))
            if path in HIDDEN_COMMANDS:
                continue
            walk(child, path)

    walk(tree, ())
    return leaves


# --- TUI-internal commands (an extensible registry) ------------------------


@dataclass(frozen=True)
class InternalCommand:
    """A command the TUI implements itself, surfaced in the palette.

    ``callback`` receives the running :class:`~lot_textual_ui.app.LotTextualApp`
    and does the work (it may be sync or return an awaitable; Textual awaits
    either). Later phases extend the palette by appending to
    :data:`INTERNAL_COMMANDS` — no provider changes required.
    """

    title: str
    help: str
    callback: Callable[[LotTextualApp], Any]


# The internal-command registry. Append here to add palette commands (switch
# theme, switch vault, copy URI, new-Thing / new-Update entry points, …); the
# provider below picks them up automatically. Kept deliberately tiny for the
# MVP: everything else is a `lot` leaf command discovered from `lot help`.
INTERNAL_COMMANDS: list[InternalCommand] = [
    InternalCommand(
        title="New child Thing",
        help="Create a new Thing under the currently selected Thing",
        callback=lambda app: app.action_new_child_thing(),
    ),
    InternalCommand(
        title="Copy Thing URI",
        help="Copy the selected Thing's lot: id to the clipboard",
        callback=lambda app: app.action_copy_thing_uri(),
    ),
    InternalCommand(
        title="Copy Thing path",
        help="Copy the selected Thing's filesystem path to the clipboard",
        callback=lambda app: app.action_copy_thing_path(),
    ),
    InternalCommand(
        title="Copy Update URI",
        help="Copy the focused (or latest) Update's lot: id to the clipboard",
        callback=lambda app: app.action_copy_update_uri(),
    ),
    InternalCommand(
        title="Copy Update path",
        help="Copy the focused (or latest) Update's filesystem path to the clipboard",
        callback=lambda app: app.action_copy_update_path(),
    ),
    InternalCommand(
        title="Copy selection",
        help="Copy the current mouse text-selection to the clipboard",
        callback=lambda app: app.action_copy_selection(),
    ),
    InternalCommand(
        title="Collapse all updates",
        help="Collapse every update in the detail thread to its header",
        callback=lambda app: app.action_collapse_all_updates(),
    ),
    InternalCommand(
        title="Expand all updates",
        help="Expand every update in the detail thread to show its body",
        callback=lambda app: app.action_expand_all_updates(),
    ),
    InternalCommand(
        title="Toggle mark",
        help="Mark/unmark the highlighted Thing for batch operations",
        callback=lambda app: app.action_toggle_mark(),
    ),
    InternalCommand(
        title="Clear marks",
        help="Unmark every marked Thing",
        callback=lambda app: app.action_clear_marks(),
    ),
    InternalCommand(
        title="Move marked Things",
        help="Move every marked Thing under a picked Thing (or to the top level)",
        callback=lambda app: app.action_batch_move(),
    ),
    InternalCommand(
        title="Archive marked Things",
        help="Archive every marked Thing (asks for confirmation first)",
        callback=lambda app: app.action_batch_archive(),
    ),
    InternalCommand(
        title="Archive done Things",
        help="Archive every Thing in a terminal status (asks for confirmation first)",
        callback=lambda app: app.action_vault_archive(),
    ),
    InternalCommand(
        title="Update marked Things",
        help="Append one new Update to every marked Thing",
        callback=lambda app: app.action_batch_update(),
    ),
    InternalCommand(
        title="Switch theme",
        help="Open the theme picker to change the colour scheme (saved for next time)",
        callback=lambda app: app.action_switch_theme(),
    ),
    InternalCommand(
        title="Switch vault",
        help="Switch the TUI to another configured vault",
        callback=lambda app: app.action_switch_vault_picker(),
    ),
    InternalCommand(
        title="Refresh vault",
        help="Reload the whole vault from disk and repaint",
        callback=lambda app: app.action_refresh_vault(),
    ),
    InternalCommand(
        title="Quit",
        help="Exit the LoT browser",
        callback=lambda app: app.action_quit(),
    ),
]


# --- providers -------------------------------------------------------------


class LotCommandProvider(Provider):
    """Palette provider for the dynamically-discovered ``lot`` command tree.

    On palette open it fetches and flattens ``lot help --format=yaml`` once
    (through the app's shared :class:`LotCli` — this provider never spawns
    ``lot`` itself), then fuzzy-matches leaf-command labels against the query.
    Selecting a hit calls :meth:`LotTextualApp.run_lot_command` (the
    :ref:`palette-forms-seam`).
    """

    def __init__(self, screen: Any, match_style: Any = None) -> None:
        super().__init__(screen, match_style)
        self._commands: list[LeafCommand] = []

    async def startup(self) -> None:
        """Discover the command tree once when the palette opens."""
        app = self._lot_app()
        tree = await app.lot_cli.help_yaml()
        self._commands = flatten_help_tree(tree)

    async def discover(self) -> Hits:
        """Offer every leaf command when the palette opens with no query."""
        app = self._lot_app()
        for command in self._commands:
            yield DiscoveryHit(
                command.label,
                partial(app.run_lot_command, command),
                text=command.label,
                help=command.help_text,
            )

    async def search(self, query: str) -> Hits:
        """Fuzzy-match leaf-command labels against ``query``."""
        matcher = self.matcher(query)
        app = self._lot_app()
        for command in self._commands:
            score = matcher.match(command.label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(command.label),
                    partial(app.run_lot_command, command),
                    text=command.label,
                    help=command.help_text,
                )

    def _lot_app(self) -> LotTextualApp:
        # Typed accessor so the provider's calls to app-specific members
        # (`lot_cli`, `run_lot_command`) read clearly.
        from .app import LotTextualApp

        assert isinstance(self.app, LotTextualApp)
        return self.app


class InternalCommandProvider(Provider):
    """Palette provider for the TUI's own commands (:data:`INTERNAL_COMMANDS`)."""

    async def discover(self) -> Hits:
        """Offer every internal command when the palette opens with no query."""
        for command in INTERNAL_COMMANDS:
            yield DiscoveryHit(
                command.title,
                partial(command.callback, self.app),
                text=command.title,
                help=command.help,
            )

    async def search(self, query: str) -> Hits:
        """Fuzzy-match internal-command titles against ``query``."""
        matcher = self.matcher(query)
        for command in INTERNAL_COMMANDS:
            score = matcher.match(command.title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(command.title),
                    partial(command.callback, self.app),
                    text=command.title,
                    help=command.help,
                )


# The set the app registers via ``COMMANDS``. Grouped here so app.py stays a
# thin wiring layer and this module owns the palette's composition.
PALETTE_PROVIDERS: Iterable[type[Provider]] = (
    LotCommandProvider,
    InternalCommandProvider,
)
