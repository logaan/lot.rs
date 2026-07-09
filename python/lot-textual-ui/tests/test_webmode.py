"""Tests for web mode (:mod:`lot_textual_ui.webmode` and its consumers).

Web mode is marked by the ``LOT_TEXTUAL_WEB`` environment variable (set by
``lot web`` / ``lot-textual-ui-web`` for every served session process). These
tests fake it **both ways** with ``monkeypatch`` and assert the adaptations:

* :func:`~lot_textual_ui.webmode.is_web_mode` reads the marker at call time.
* The ``$EDITOR`` escape hatch is disabled in web mode: the binding is hidden
  from the form footers, pressing ``ctrl+o`` shows a notice, and the editor
  seam is never invoked — while non-web behaviour is unchanged.
* :func:`~lot_textual_ui.editor.edit_in_editor` itself is a hard no-op in web
  mode (the backstop for callers that forget the gate).
* The copy toasts promise only a browser handoff in web mode, and keep the
  plain "Copied ..." wording locally.
"""

from __future__ import annotations

import asyncio

from textual.widgets import TextArea

from lot_textual_ui import editor as editor_module
from lot_textual_ui.app import WEB_COPY_NOTICE, LotTextualApp
from lot_textual_ui.detail import InlineUpdateForm
from lot_textual_ui.forms import (
    BODY_TEXTAREA_ID,
    UPDATE_BODY_TEXTAREA_ID,
    WEB_EDITOR_NOTICE,
    NewThingScreen,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.webmode import WEB_MARKER_ENV, is_web_mode

# --- is_web_mode -------------------------------------------------------------


def test_unset_marker_is_not_web_mode(monkeypatch) -> None:
    monkeypatch.delenv(WEB_MARKER_ENV, raising=False)
    assert is_web_mode() is False


def test_marker_set_to_one_is_web_mode(monkeypatch) -> None:
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    assert is_web_mode() is True


def test_empty_and_zero_markers_are_not_web_mode(monkeypatch) -> None:
    # An explicit opt-out (or an emptied variable) must not read as web mode.
    monkeypatch.setenv(WEB_MARKER_ENV, "")
    assert is_web_mode() is False
    monkeypatch.setenv(WEB_MARKER_ENV, "0")
    assert is_web_mode() is False


def test_marker_is_read_at_call_time(monkeypatch) -> None:
    """Flipping the environment mid-process flips the answer (testability)."""
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    assert is_web_mode() is True
    monkeypatch.delenv(WEB_MARKER_ENV)
    assert is_web_mode() is False


# --- edit_in_editor backstop -------------------------------------------------


class _NoSuspendApp:
    """A stand-in app that fails the test if suspension is even attempted."""

    @property
    def is_headless(self) -> bool:
        raise AssertionError("web mode must bail out before consulting the app")

    def suspend(self):
        raise AssertionError("App.suspend must never be entered in web mode")


def test_edit_in_editor_is_a_hard_noop_in_web_mode(monkeypatch) -> None:
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    launched: list[object] = []

    def fake_editor(argv):
        launched.append(argv)
        return 0

    result = editor_module.edit_in_editor(
        _NoSuspendApp(), "the body", run_editor=fake_editor
    )
    assert result == "the body"
    assert launched == []


# --- the $EDITOR hatch on the forms -------------------------------------------


class FakeLotCli:
    """A minimal stand-in :class:`LotCli` — just enough to boot the app."""

    def __init__(self) -> None:
        self._roots = [Thing(id="r1", name="Root", status="work")]

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return ThingList(path="/x", things=list(self._roots))

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        for event in ():
            yield event


def _notify_recorder(app: LotTextualApp) -> list[tuple[str, dict]]:
    """Replace ``app.notify`` with a recorder; returns the record list."""
    records: list[tuple[str, dict]] = []

    def record(message: str, **kwargs) -> None:
        records.append((message, kwargs))

    app.notify = record  # type: ignore[method-assign]
    return records


def _edit_binding_shows(form) -> list[bool]:
    """The ``show`` flags of every ``edit_body`` binding on ``form``."""
    return [
        binding.show
        for bindings in form._bindings.key_to_bindings.values()
        for binding in bindings
        if binding.action == "edit_body"
    ]


def _body_forms() -> tuple[object, object]:
    """One instance of each body-taking form: the modal one and the inline one."""
    return NewThingScreen(), InlineUpdateForm(kind="work", thing_id="t1")


def _open_form(app: LotTextualApp, form: str) -> None:
    if form == "thing":
        app.open_new_thing_form()
    else:
        app.open_new_update_form(kind="work")


def _form_widget(app: LotTextualApp, form: str):
    """The object owning ``form``'s body editor and ``$EDITOR`` binding.

    The new-Thing form is a modal screen; the new-Update form is the inline
    :class:`InlineUpdateForm` widget mounted in the detail pane. The
    ``run_editor`` seam lives on whichever of the two rendered the body, so a
    test has to hold *that* object rather than reaching for ``app.screen`` —
    seeding the fake on the wrong one leaves the seam unset and launches a
    **real** ``$EDITOR``. Call only after the open has been pumped.
    """
    return app.screen if form == "thing" else app.query_one(InlineUpdateForm)


_BODY_IDS = {"thing": BODY_TEXTAREA_ID, "update": UPDATE_BODY_TEXTAREA_ID}


def _press_ctrl_o_scenario(
    form: str,
) -> tuple[list[object], list[tuple[str, dict]], str]:
    """Boot the app, open ``form``, press ``ctrl+o`` on a filled body.

    Returns the recorded editor launches, notifications, and the body text
    afterwards — the web/non-web assertions differ, the driving does not.
    """

    async def scenario() -> tuple[list[object], list[tuple[str, dict]], str]:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            _open_form(app, form)
            await pilot.pause()

            widget = _form_widget(app, form)
            body = widget.query_one(f"#{_BODY_IDS[form]}", TextArea)
            body.text = "draft body"
            launched: list[object] = []

            def fake_editor(argv):
                launched.append(list(argv))
                return 0

            widget._run_editor = fake_editor
            notifications = _notify_recorder(app)
            body.focus()

            await pilot.press("ctrl+o")
            await pilot.pause()
            return launched, notifications, body.text

    return asyncio.run(scenario())


def test_web_mode_ctrl_o_notifies_and_never_launches_editor(monkeypatch) -> None:
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    for form in ("thing", "update"):
        launched, notifications, body_text = _press_ctrl_o_scenario(form)
        assert launched == []
        assert body_text == "draft body"
        assert [m for m, _ in notifications] == [WEB_EDITOR_NOTICE]


def test_non_web_ctrl_o_still_round_trips_the_editor(monkeypatch) -> None:
    monkeypatch.delenv(WEB_MARKER_ENV, raising=False)
    for form in ("thing", "update"):
        launched, notifications, _ = _press_ctrl_o_scenario(form)
        assert len(launched) == 1
        assert notifications == []


def test_web_mode_hides_the_editor_binding_from_the_footer(monkeypatch) -> None:
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    for form in _body_forms():
        shows = _edit_binding_shows(form)
        assert shows and all(show is False for show in shows)


def test_non_web_keeps_the_editor_binding_visible(monkeypatch) -> None:
    monkeypatch.delenv(WEB_MARKER_ENV, raising=False)
    for form in _body_forms():
        shows = _edit_binding_shows(form)
        assert shows and all(show is True for show in shows)


# --- copy toasts ---------------------------------------------------------------


def _copy_uri_scenario() -> tuple[list[str], list[tuple[str, dict]]]:
    """Boot the app and copy the selected Thing's URI; return copies + toasts."""

    async def scenario() -> tuple[list[str], list[tuple[str, dict]]]:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            copied: list[str] = []
            app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
            notifications = _notify_recorder(app)
            app.action_copy_thing_uri()
            await pilot.pause()
            return copied, notifications

    return asyncio.run(scenario())


def test_web_mode_copy_toast_promises_only_a_browser_handoff(monkeypatch) -> None:
    monkeypatch.setenv(WEB_MARKER_ENV, "1")
    copied, notifications = _copy_uri_scenario()
    assert copied == ["r1"]
    assert [m for m, _ in notifications] == [WEB_COPY_NOTICE.format(text="r1")]
    # The wording must be honest: a handoff, not a confirmed copy.
    message = notifications[0][0]
    assert "browser" in message and not message.startswith("Copied")


def test_non_web_copy_toast_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(WEB_MARKER_ENV, raising=False)
    copied, notifications = _copy_uri_scenario()
    assert copied == ["r1"]
    assert [m for m, _ in notifications] == ["Copied r1 to clipboard"]
