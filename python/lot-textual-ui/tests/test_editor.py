"""Tests for the ``$EDITOR`` escape hatch (:mod:`lot_textual_ui.editor`).

Two layers:

* **Unit** — :func:`resolve_editor` resolution order and :func:`edit_text`
  round-trip / cancel handling, driving the injectable ``run_editor`` seam with
  a fake so no real editor is ever launched.
* **Form** — both body forms booted headless with ``App.run_test()`` against a
  fake :class:`LotCli`; pressing ``ctrl+o`` opens the (faked) editor on the body
  TextArea and writes the result back. The new-Thing form is a modal screen, so
  the ``run_editor`` seam is injected on ``app.screen``; the new-Update form is
  the inline :class:`InlineUpdateForm` widget, so the seam goes on the *widget*.
  Injecting it in the wrong place would launch a **real** ``$EDITOR`` and hang
  the suite, so both tests reach for the form object they actually rendered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import TextArea

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.detail import InlineUpdateForm
from lot_textual_ui.editor import edit_text, resolve_editor
from lot_textual_ui.forms import (
    BODY_TEXTAREA_ID,
    UPDATE_BODY_TEXTAREA_ID,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)

# --- resolve_editor --------------------------------------------------------


def test_resolve_prefers_visual_then_editor_then_nvim(monkeypatch) -> None:
    monkeypatch.setenv("VISUAL", "vim")
    monkeypatch.setenv("EDITOR", "emacs")
    assert resolve_editor() == ["vim"]

    monkeypatch.delenv("VISUAL", raising=False)
    assert resolve_editor() == ["emacs"]

    monkeypatch.delenv("EDITOR", raising=False)
    assert resolve_editor() == ["nvim"]


def test_resolve_ignores_blank_values(monkeypatch) -> None:
    # A whitespace-only $VISUAL must not shadow $EDITOR / the fallback.
    monkeypatch.setenv("VISUAL", "   ")
    monkeypatch.setenv("EDITOR", "hx")
    assert resolve_editor() == ["hx"]

    monkeypatch.setenv("EDITOR", "")
    monkeypatch.delenv("VISUAL", raising=False)
    assert resolve_editor() == ["nvim"]


def test_resolve_splits_editor_arguments(monkeypatch) -> None:
    # An editor carrying its own args (e.g. `code --wait`) is shell-split.
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait")
    assert resolve_editor() == ["code", "--wait"]


# --- edit_text -------------------------------------------------------------


def test_edit_text_writes_initial_and_reads_back(monkeypatch) -> None:
    monkeypatch.setenv("EDITOR", "myeditor --flag")
    seen: dict[str, object] = {}

    def fake_editor(argv):
        # The temp path is the final argv element; the resolved editor + its
        # args precede it.
        path = argv[-1]
        seen["argv"] = list(argv)
        seen["initial"] = Path(path).read_text(encoding="utf-8")
        seen["suffix"] = Path(path).suffix
        Path(path).write_text("edited in $EDITOR", encoding="utf-8")
        return 0

    result = edit_text("original text", run_editor=fake_editor)

    assert result == "edited in $EDITOR"
    assert seen["initial"] == "original text"
    assert seen["suffix"] == ".md"
    assert seen["argv"][:2] == ["myeditor", "--flag"]


def test_edit_text_unchanged_returns_same_text() -> None:
    def noop_editor(argv):
        # User opened and quit without touching the file.
        return 0

    assert edit_text("keep me", run_editor=noop_editor) == "keep me"


def test_edit_text_nonzero_exit_keeps_original() -> None:
    def aborting_editor(argv):
        # Simulate an aborted edit (e.g. vim `:cq`) after mangling the file.
        Path(argv[-1]).write_text("junk", encoding="utf-8")
        return 1

    assert edit_text("safe", run_editor=aborting_editor) == "safe"


def test_edit_text_removes_temp_file() -> None:
    captured: dict[str, str] = {}

    def fake_editor(argv):
        captured["path"] = argv[-1]
        return 0

    edit_text("body", run_editor=fake_editor)
    assert not Path(captured["path"]).exists()


# --- form-level round-trip -------------------------------------------------


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


def _fake_editor(seen: dict[str, object]):
    def run(argv):
        path = argv[-1]
        seen["initial"] = Path(path).read_text(encoding="utf-8")
        Path(path).write_text("edited via $EDITOR", encoding="utf-8")
        return 0

    return run


def test_ctrl_o_edits_new_thing_body() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            body = app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea)
            body.text = "draft body"
            seen: dict[str, object] = {}
            app.screen._run_editor = _fake_editor(seen)
            body.focus()

            # ctrl+o is priority=True so it fires even while the TextArea (which
            # binds its own ctrl+e for cursor-to-line-end) has focus.
            await pilot.press("ctrl+o")
            await pilot.pause()

            assert seen["initial"] == "draft body"
            assert body.text == "edited via $EDITOR"

    asyncio.run(scenario())


def test_ctrl_o_edits_new_update_body() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # The update form is inline, so the editor seam belongs on the
            # widget, not the screen it happens to be mounted on.
            form = app.query_one(InlineUpdateForm)
            body = form.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            body.text = "wip notes"
            seen: dict[str, object] = {}
            form._run_editor = _fake_editor(seen)
            body.focus()

            await pilot.press("ctrl+o")
            await pilot.pause()

            assert seen["initial"] == "wip notes"
            assert body.text == "edited via $EDITOR"

    asyncio.run(scenario())


def test_both_forms_show_the_editor_binding() -> None:
    from lot_textual_ui.forms import NewThingScreen

    for form in (NewThingScreen, InlineUpdateForm):
        binding = next(b for b in form.BINDINGS if b.key == "ctrl+o")
        assert binding.action == "edit_body"
        assert binding.priority is True
        assert binding.show is True
        assert "$EDITOR" in binding.description


if __name__ == "__main__":  # pragma: no cover
    pass
