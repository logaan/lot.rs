"""Tests for the crash boundary: the ``errors`` helpers, a resilient mount, and
a resilient ``_reload_vault``.

Booted headless with ``App.run_test()`` against a *fake* :class:`LotCli` whose
``thing_list`` is rigged to fail, matching ``test_app.py``'s style (scenarios
driven via ``asyncio.run``, no pytest-asyncio). The point of these tests is that
a failing data load no longer takes the app down: it comes up usable with an
empty tree and a toast instead of a traceback.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Tree

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.errors import crash_message, write_crash_log
from lot_textual_ui.lot_cli import LotError
from test_app import FakeLotCli, node_datas, sample_listing


class FailingListFakeLotCli(FakeLotCli):
    """A :class:`FakeLotCli` whose ``thing_list`` always raises.

    Everything else is inherited, so the app boots normally up to the data load;
    ``thing_list`` then raises ``error`` to exercise the mount/reload crash
    boundary. Defaults to a :class:`LotError`, but any exception can be injected
    (e.g. a generic one, standing in for malformed CLI output).
    """

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(sample_listing())
        self._error = error or LotError(("thing", "list"), 1, "no such vault")

    async def thing_list(self):
        raise self._error


# --- errors.py helpers -------------------------------------------------------


def test_write_crash_log_writes_the_traceback_and_returns_its_path() -> None:
    try:
        raise ValueError("boom in the vault")
    except ValueError as error:
        path = write_crash_log(error)
    assert path is not None
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # The full traceback render is preserved, not just the message.
    assert "ValueError" in text
    assert "boom in the vault" in text
    assert "Traceback" in text


def test_write_crash_log_never_raises_on_an_unwritable_temp_dir(monkeypatch) -> None:
    # The helper runs on the crash path, so a failure to even write the log must
    # be swallowed (returning None) rather than re-entering the teardown.
    import lot_textual_ui.errors as errors

    def boom() -> str:
        raise OSError("no temp dir")

    monkeypatch.setattr(errors.tempfile, "gettempdir", boom)
    assert write_crash_log(ValueError("x")) is None


def test_crash_message_names_the_error_and_the_log_path(tmp_path) -> None:
    log_path = tmp_path / "crash.log"
    message = crash_message(RuntimeError("kaboom"), log_path)
    assert "RuntimeError" in message
    assert "kaboom" in message
    assert str(log_path) in message
    # Reassures the user it is a bug, not their fault.
    assert "bug" in message.lower()


def test_crash_message_without_a_log_path_omits_the_location() -> None:
    message = crash_message(RuntimeError("kaboom"), None)
    assert "RuntimeError" in message
    assert "kaboom" in message
    # No dangling "written to None" when the log could not be written.
    assert "None" not in message


# --- resilient mount ---------------------------------------------------------


def test_mount_does_not_crash_when_thing_list_raises_lot_error() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FailingListFakeLotCli())
        async with app.run_test() as pilot:
            await pilot.pause()
            # The app came up usable: the shell is present with an empty tree,
            # the vault-root selection still resolved, and no crash exception
            # was recorded by Textual's backstop.
            assert app._exception is None
            left = app.query_one("#left-tree", Tree)
            assert node_datas(left) == []
            assert app.selected_id == VAULT_ROOT
            # The failure was surfaced as a toast rather than a traceback.
            assert any("Could not load vault" == n.title for n in app._notifications)

    asyncio.run(scenario())


def test_mount_does_not_crash_when_thing_list_raises_generic_exception() -> None:
    async def scenario() -> None:
        cli = FailingListFakeLotCli(error=RuntimeError("malformed CLI output"))
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._exception is None
            centre = app.query_one("#centre-tree", Tree)
            assert node_datas(centre) == []
            assert any("Could not load vault" == n.title for n in app._notifications)

    asyncio.run(scenario())


# --- resilient reload --------------------------------------------------------


def test_reload_vault_swallows_a_failing_thing_list() -> None:
    async def scenario() -> None:
        # Boot cleanly first, then make thing_list start failing so the reload
        # (not the mount) hits the error boundary.
        cli = FakeLotCli(sample_listing())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "c1", "r2"}

            async def failing_thing_list():
                raise LotError(("thing", "list"), 1, "gone away")

            cli.thing_list = failing_thing_list
            # Must not raise even though the reload's listing call fails.
            await app._reload_vault()
            await pilot.pause()

            # Previous state is left intact...
            assert set(node_datas(left)) == {"r1", "c1", "r2"}
            assert app.selected_id == VAULT_ROOT
            # ...and the failure surfaced as a toast.
            assert any("Could not reload vault" == n.title for n in app._notifications)

    asyncio.run(scenario())


# --- backstop override -------------------------------------------------------


def test_handle_exception_exits_cleanly_without_a_traceback() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli(sample_listing()))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_exception(RuntimeError("kaboom"))
            await pilot.pause()
            # The app is exiting with an error code, and the friendly message —
            # not a Rich traceback — is queued for display.
            assert app._exit is True
            assert app.return_code == 1
            rendered = " ".join(str(r) for r in app._exit_renderables)
            assert "RuntimeError" in rendered
            assert "kaboom" in rendered
            # The backstop records the exception (mirroring Textual, so real
            # test frameworks can re-raise); clear it here so run_test's own
            # exit-time re-raise doesn't fail this deliberately-injected crash.
            app._exception = None

    asyncio.run(scenario())


if __name__ == "__main__":  # pragma: no cover
    pass
