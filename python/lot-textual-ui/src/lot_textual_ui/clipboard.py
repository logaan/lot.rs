"""The copy-to-clipboard actions (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from textual import work

from .detail import DetailPane
from .lot_cli import LotError
from .webmode import is_web_mode


class ClipboardMixin:
    """Copying the current selection's identifiers (and text) to the clipboard.

    Four actions copy the current selection's identifiers to the system
    clipboard via Textual's built-in OSC 52 ``copy_to_clipboard`` (works over
    SSH / most terminals, no extra dependency). A URI is already in memory so
    its copy is synchronous; a path comes from ``lot thing path`` /
    ``lot update path`` through the shared ``LotCli``, so those run in a
    worker. The "current update" is resolved by the detail pane (whichever
    UpdateItem is focused, else the Thing's latest update). A fifth action,
    ``copy_selection``, copies the free-form mouse text-selection.

    Web mode: textual-serve relays the OSC 52 sequence to xterm.js in the
    browser, whose clipboard addon hands it to ``navigator.clipboard`` — an
    API that only exists on secure pages (http://localhost or HTTPS). Served
    over plain HTTP on a LAN address the copy silently does nothing, and the
    app has no way to observe either outcome, so the web toast
    (:data:`~lot_textual_ui.app.WEB_COPY_NOTICE`) says "sent to the browser"
    rather than over-promising "copied".

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it uses the app's ``_require_current_thing`` guard, the shared
    ``self._lot_cli`` and the detail pane.
    """

    def _copy(self, text: str, label: str) -> None:
        """Put ``text`` on the clipboard and confirm with a toast.

        The web-mode toast is honest about the handoff: the browser may block
        the write (see the class docstring), and the app cannot tell.
        """
        # Imported at call time: the notice lives in app.py (part of the app's
        # public surface), which imports this module (avoids the cycle).
        from .app import WEB_COPY_NOTICE

        self.copy_to_clipboard(text)
        if is_web_mode():
            self.notify(WEB_COPY_NOTICE.format(text=text), title=label)
        else:
            self.notify(f"Copied {text} to clipboard", title=label)

    def action_copy_selection(self) -> None:
        """Copy the current mouse text-selection to the clipboard.

        Text selection itself is native to Textual (widgets default
        ``ALLOW_SELECT = True`` and the screen tracks a mouse-drag selection),
        and ``ctrl+c`` already copies it silently via the screen's own
        ``copy_text`` action. This action is the app's discoverable, *toasting*
        entry point for the same thing — bound to a key (see
        :mod:`lot_textual_ui.keys`) and offered in the palette. It reads the
        screen's selected text (spanning the detail pane's computed-state and
        update-body widgets); with nothing selected it notifies rather than
        clobbering the clipboard with an empty string.
        """
        selection = self.screen.get_selected_text()
        if not selection:
            self.notify(
                "Select some text first (drag with the mouse).",
                title="Nothing to copy",
                severity="warning",
            )
            return
        self._copy(selection, "Selection")

    def _current_update_id(self) -> str | None:
        """The update the copy-Update actions target (from the detail pane)."""
        return self.query_one(DetailPane).current_update_id

    def _require_current_update(self, message: str, title: str) -> str | None:
        """The current Update's id for an Update-scoped action, or ``None`` + a hint.

        Mirrors :meth:`_require_current_thing` for the two Update-scoped copy
        actions: returns :meth:`_current_update_id`'s pick when there is one,
        else toasts ``message`` and returns ``None`` for the caller to bail on.
        """
        update_id = self._current_update_id()
        if update_id is None:
            self.notify(message, title=title, severity="warning")
        return update_id

    def action_copy_thing_uri(self) -> None:
        """Copy the in-view Thing's ``lot:`` id to the clipboard."""
        thing_id = self._require_current_thing(
            "Select a Thing first.", title="Nothing to copy"
        )
        if thing_id is None:
            return
        self._copy(thing_id, "Thing URI")

    @work(exclusive=False, group="copy")
    async def action_copy_thing_path(self) -> None:
        """Copy the selected Thing's filesystem path to the clipboard.

        The path comes from ``lot thing path`` via :class:`LotCli`, so this runs
        in a worker; a failed lookup surfaces as an error toast.
        """
        thing_id = self._require_current_thing(
            "Select a Thing first.", title="Nothing to copy"
        )
        if thing_id is None:
            return
        try:
            path = await self._lot_cli.thing_path(thing_id)
        except LotError as error:
            self.notify(str(error), title="Copy failed", severity="error")
            return
        self._copy(path, "Thing path")

    def action_copy_update_uri(self) -> None:
        """Copy the focused/current Update's ``lot:`` id to the clipboard."""
        update_id = self._require_current_update(
            "No update to copy — select a Thing with updates.",
            title="Nothing to copy",
        )
        if update_id is None:
            return
        self._copy(update_id, "Update URI")

    @work(exclusive=False, group="copy")
    async def action_copy_update_path(self) -> None:
        """Copy the focused/current Update's filesystem path to the clipboard.

        The path comes from ``lot update path`` via :class:`LotCli`, so this runs
        in a worker; a failed lookup surfaces as an error toast.
        """
        update_id = self._require_current_update(
            "No update to copy — select a Thing with updates.",
            title="Nothing to copy",
        )
        if update_id is None:
            return
        try:
            path = await self._lot_cli.update_path(update_id)
        except LotError as error:
            self.notify(str(error), title="Copy failed", severity="error")
            return
        self._copy(path, "Update path")
