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
* commands that need input raise a placeholder toast — **this is your hook.**

To add the forms, replace the ``needs_input`` branch of
``LotTextualApp.run_lot_command``: dispatch on ``command.path`` (e.g.
``("thing", "new")`` or ``("update", "work")``), push the matching form screen,
collect the fields/stdin from :attr:`LeafCommand.args` (each an
:class:`ArgSpec` carrying name/help/required/default/possible-values), then run
the command via ``LotCli`` and refresh. The read-only branch and the generic
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


def flatten_help_tree(tree: dict[str, Any]) -> list[LeafCommand]:
    """Flatten a ``lot help --format=yaml`` tree into runnable leaf commands.

    Walks the nested ``subcommands`` mapping depth-first, emitting one
    :class:`LeafCommand` per node that has *no* children (a runnable leaf).
    Group nodes (``vault``, ``thing``, ``update``, ``claude``, ``claude
    send``, …) carry no runnable action of their own and are skipped; only
    their leaves are emitted. The top-level ``lot`` name is not part of any
    path. Order follows the help document, so related commands stay grouped.
    """
    leaves: list[LeafCommand] = []

    def walk(node: dict[str, Any], prefix: tuple[str, ...]) -> None:
        subcommands = node.get("subcommands") or []
        if not subcommands:
            # A leaf: runnable. (The root, which always has subcommands, and any
            # group node are therefore never emitted as commands themselves.)
            if prefix:
                args = tuple(ArgSpec.from_dict(a) for a in node.get("args") or [])
                leaves.append(
                    LeafCommand(
                        path=prefix,
                        about=str(node.get("about", "")),
                        long_about=node.get("long_about"),
                        args=args,
                    )
                )
            return
        for child in subcommands:
            name = child.get("name")
            if not name:
                continue
            walk(child, (*prefix, str(name)))

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
