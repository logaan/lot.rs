"""The command navigator: walking the ``lot`` command tree by typing letters.

An **alternative** to the fuzzy ``ctrl+p`` palette (see
:mod:`lot_textual_ui.palette`) that mirrors the CLI's command / sub-command
hierarchy. Two ways in:

* ``space`` opens the selector at the top level of the command tree; and
* ``ctrl+<first letter of a top-level command>`` opens it already *inside*
  that command (``ctrl+t`` → ``lot thing``, so ``ctrl+t`` ``n`` runs
  ``lot thing new``).

With it open, typing a command's **first letter** walks down the tree; a
letter that lands on a leaf (no sub-commands) runs it straight away. When a
letter matches several commands a chooser list appears to pick between them.

:class:`CommandNav` is the pure navigation state machine (it never touches the
terminal), so the modal screen drives it and tests exercise it directly.
:class:`CommandNavScreen` is the thin modal renderer/key-router around it; it
dismisses with the chosen :class:`~lot_textual_ui.palette.LeafCommand` (or
``None`` on cancel) and the app runs it through
:meth:`~lot_textual_ui.app.LotTextualApp.run_lot_command` — exactly the same
seam as a palette pick, so forms for input-needing commands come for free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from .palette import LeafCommand, leaf_from_node

#: How long after the chooser appears its confirming ``enter`` is ignored
#: (seconds), so a stray Enter can't pick an option the user hasn't seen yet.
#: Mirrors the Rust TUI's ``CHOOSER_GUARD``.
CHOOSER_GUARD = 0.25

#: ``ctrl+<letter>`` combinations that keep their usual meaning (quit, palette,
#: copy/quit, suspend) and are therefore never command-navigator shortcuts.
RESERVED_CTRL_LETTERS = frozenset("cpqz")

#: Outcome sentinel: dismiss the selector (nothing chosen).
CLOSE = "close"


@dataclass
class Chooser:
    """The disambiguation list shown when a letter matches several commands."""

    #: Indices (into the current level's children) of the colliding commands.
    candidates: list[int]
    #: Which candidate is highlighted.
    selected: int
    #: ``time.monotonic()`` when the list appeared, for :data:`CHOOSER_GUARD`.
    opened_at: float


class CommandNav:
    """Pure navigation state over a ``lot help --format=yaml`` tree.

    A Python port of the Rust TUI's palette state machine: ``path`` holds the
    child indices from the root down to the current level, and ``chooser`` is
    the open disambiguation list, if any.

    Key methods return one of three outcomes for the caller to apply: ``None``
    (nothing to do — the state may have moved), :data:`CLOSE` (dismiss the
    selector), or a :class:`LeafCommand` to run. ``now`` parameters are
    injected so the chooser guard is testable; they default to the clock.
    """

    def __init__(self, tree: dict[str, Any]) -> None:
        self._root = tree
        self.path: list[int] = []
        self.chooser: Chooser | None = None

    # --- inspection ---------------------------------------------------------

    def current_node(self) -> dict[str, Any]:
        """The node at ``path`` (the root when the path is empty)."""
        node = self._root
        for index in self.path:
            node = (node.get("subcommands") or [])[index]
        return node

    def children(self) -> list[dict[str, Any]]:
        """The commands at the current level."""
        return list(self.current_node().get("subcommands") or [])

    def command_path(self) -> tuple[str, ...]:
        """The sub-command names along ``path`` — the args to hand to ``lot``."""
        names: list[str] = []
        node = self._root
        for index in self.path:
            node = (node.get("subcommands") or [])[index]
            names.append(str(node.get("name", "")))
        return tuple(names)

    def breadcrumb(self) -> str:
        """A human-readable position, e.g. ``lot thing``."""
        return " ".join((str(self._root.get("name", "lot")), *self.command_path()))

    # --- keys ----------------------------------------------------------------

    def on_letter(
        self, letter: str, now: float | None = None
    ) -> LeafCommand | str | None:
        """Type a letter: navigate to the unique match, open a chooser when
        several match, or do nothing when none do. A unique match that is a
        leaf is invoked straight away. With the chooser open, ``j``/``k`` move
        its highlight instead."""
        if self.chooser is not None:
            if letter == "j":
                self.move_chooser(1)
            elif letter == "k":
                self.move_chooser(-1)
            return None
        wanted = letter.lower()
        matches = [
            index
            for index, node in enumerate(self.children())
            if str(node.get("name", ""))[:1].lower() == wanted
        ]
        if not matches:
            return None
        if len(matches) == 1:
            self.path.append(matches[0])
            return self._invoke_if_leaf()
        self.chooser = Chooser(candidates=matches, selected=0, opened_at=self._now(now))
        return None

    def on_enter(self, now: float | None = None) -> LeafCommand | str | None:
        """Confirm the chooser's highlighted candidate (a leaf runs straight
        away). Without a chooser there is nothing to confirm — navigation only
        ever parks on group nodes, since leaves invoke immediately."""
        if self.chooser is not None and self.confirm_chooser(now):
            return self._invoke_if_leaf()
        return None

    def on_backspace(self) -> str | None:
        """Undo: dismiss the chooser, else pop one step, else close."""
        if self.chooser is not None:
            self.chooser = None
            return None
        if not self.path:
            return CLOSE
        self.path.pop()
        return None

    def on_escape(self) -> str | None:
        """Clear: dismiss the chooser, else clear all input, else close."""
        if self.chooser is not None:
            self.chooser = None
            return None
        if not self.path:
            return CLOSE
        self.path.clear()
        return None

    def move_chooser(self, delta: int) -> None:
        """Move the chooser highlight, clamped to its candidates."""
        if self.chooser is None:
            return
        last = len(self.chooser.candidates) - 1
        self.chooser.selected = max(0, min(last, self.chooser.selected + delta))

    def confirm_chooser(self, now: float | None = None) -> bool:
        """Confirm the highlighted candidate, but only once the guard has
        elapsed. Returns whether it confirmed."""
        if self.chooser is None:
            return False
        if self._now(now) - self.chooser.opened_at < CHOOSER_GUARD:
            return False
        self.path.append(self.chooser.candidates[self.chooser.selected])
        self.chooser = None
        return True

    def reset(self) -> None:
        """Back to the top level (a ``ctrl+letter`` jump while already open)."""
        self.path.clear()
        self.chooser = None

    def _invoke_if_leaf(self) -> LeafCommand | None:
        """The current node as a runnable command when it is a leaf, so
        navigating onto it fires it without a separate Enter."""
        node = self.current_node()
        if node.get("subcommands"):
            return None
        return leaf_from_node(node, self.command_path())

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now


class CommandNavScreen(ModalScreen[LeafCommand | None]):
    """The modal command selector: a breadcrumb, the current level's commands
    (or the chooser), and a key hint.

    A thin renderer/key-router over the :class:`CommandNav` it is given —
    possibly already navigated by a ``ctrl+letter`` shortcut. Dismisses with
    the chosen :class:`LeafCommand`, or ``None`` when cancelled; the app runs
    the command (the same seam as a palette pick).
    """

    DEFAULT_CSS = """
    CommandNavScreen {
        align: center middle;
    }

    CommandNavScreen > #command-nav-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    CommandNavScreen #command-nav-title {
        text-style: bold;
        margin-bottom: 1;
    }

    CommandNavScreen #command-nav-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, nav: CommandNav) -> None:
        super().__init__()
        self._nav = nav

    def compose(self) -> ComposeResult:
        with Vertical(id="command-nav-dialog"):
            yield Label(id="command-nav-title")
            yield Static(id="command-nav-list")
            yield Label(id="command-nav-hint")

    def on_mount(self) -> None:
        self._refresh()

    def on_key(self, event: events.Key) -> None:
        """Route every key to the navigator and apply its outcome."""
        event.stop()
        event.prevent_default()
        nav = self._nav
        key = event.key
        outcome: LeafCommand | str | None = None
        if key == "escape":
            outcome = nav.on_escape()
        elif key == "backspace":
            outcome = nav.on_backspace()
        elif key == "enter":
            outcome = nav.on_enter()
        elif key == "up":
            nav.move_chooser(-1)
        elif key == "down":
            nav.move_chooser(1)
        elif key.startswith("ctrl+") and len(key) == 6 and key[5].isalpha():
            # A top-level shortcut while already open: jump there from the root.
            nav.reset()
            outcome = nav.on_letter(key[5])
        elif (
            event.character is not None
            and event.character.isprintable()
            and not event.character.isspace()
        ):
            outcome = nav.on_letter(event.character)
        self._apply(outcome)

    def _apply(self, outcome: LeafCommand | str | None) -> None:
        if isinstance(outcome, LeafCommand):
            self.dismiss(outcome)
        elif outcome == CLOSE:
            self.dismiss(None)
        else:
            self._refresh()

    def _refresh(self) -> None:
        self.query_one("#command-nav-title", Label).update(self._nav.breadcrumb())
        self.query_one("#command-nav-list", Static).update(self._render_rows())
        hint = (
            "↑/↓ (or j/k) choose · Enter confirm · Esc back"
            if self._nav.chooser is not None
            else "letter navigate · Backspace up · Esc clear/close"
        )
        self.query_one("#command-nav-hint", Label).update(hint)

    def _render_rows(self) -> Text:
        """One ``letter name — about`` line per command at the current level,
        narrowed to the candidates (with a highlight) while the chooser is
        open."""
        children = self._nav.children()
        chooser = self._nav.chooser
        if chooser is not None:
            rows = [
                (children[index], position == chooser.selected)
                for position, index in enumerate(chooser.candidates)
            ]
        else:
            rows = [(child, False) for child in children]
        text = Text()
        for node, highlighted in rows:
            name = str(node.get("name", ""))
            text.append("> " if highlighted else "  ")
            text.append(name[:1], style="bold yellow")
            text.append(f"  {name}")
            about = node.get("about")
            if about:
                text.append(f" — {about}", style="dim")
            text.append("\n")
        return text
