"""The ``$EDITOR`` escape hatch: round-trip a text buffer through the editor.

Every multi-line text input in the TUI can offer a shortcut that dumps its
current contents into a temporary file, opens that file in the user's editor,
and loads the (possibly edited) file back when the editor exits. This lets a
user compose in their own editor — folding, syntax, macros — instead of the
plain in-TUI :class:`~textual.widgets.TextArea`.

Editor resolution matches the Rust ``lot`` CLI (``crates/lot-cli/src/main.rs``
``pick_editor``): ``$VISUAL`` → ``$EDITOR`` → ``nvim``. The temp file gets a
``.md`` suffix so the editor selects markdown mode. The editor owns the terminal
while it runs, so the Textual app is **suspended** around the (blocking) launch.
That makes the whole hatch a local-terminal feature: when the app is served to
a browser (:func:`lot_textual_ui.webmode.is_web_mode`) :func:`edit_in_editor`
refuses to run and returns the text unchanged — see its docstring.

The launch itself is a single injectable seam (:data:`RunEditor`) so tests can
substitute a fake that "edits" the temp file without spawning a real program;
:func:`edit_text` and :func:`edit_in_editor` both accept it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from .webmode import is_web_mode

if TYPE_CHECKING:
    from textual.app import App

# The editor-launch seam: given the full argv (editor command, its args, and the
# temp-file path as the final element), run it synchronously and return the
# process exit code. Tests inject a fake; production uses :func:`_spawn_editor`.
RunEditor = Callable[[Sequence[str]], int]

# Final fallback editor when neither $VISUAL nor $EDITOR is set, matching the
# Rust CLI so both front-ends behave identically out of the box.
_FALLBACK_EDITOR = "nvim"


def resolve_editor() -> list[str]:
    """Return the editor command as an argv list.

    Prefers ``$VISUAL``, then ``$EDITOR``, then ``nvim`` — matching the Rust
    CLI. Blank / whitespace-only values are ignored so an empty ``EDITOR=`` does
    not shadow the fallback. The value is shell-split so an editor carrying its
    own arguments (e.g. ``code --wait``) is honoured.
    """
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var, "")
        if value.strip():
            return shlex.split(value)
    return [_FALLBACK_EDITOR]


def _spawn_editor(argv: Sequence[str]) -> int:
    """Default :data:`RunEditor`: run the editor synchronously and wait.

    Blocking is correct here — the terminal belongs to the editor while the app
    is suspended. Stdio is inherited, so the editor draws to the real terminal.
    """
    return subprocess.run(list(argv)).returncode


def edit_text(text: str, *, run_editor: RunEditor | None = None) -> str:
    """Round-trip ``text`` through the user's editor and return the result.

    Writes ``text`` to a temporary ``.md`` file (so editors pick markdown mode),
    launches the resolved editor on it synchronously, then reads the (possibly
    edited) file back. The temp file is always removed. If the editor exits
    non-zero — a crash or an aborted edit such as vim's ``:cq`` — the original
    ``text`` is returned unchanged so nothing is lost.

    ``run_editor`` is the injectable launch seam; it defaults to spawning a real
    subprocess. Tests pass a fake that edits the temp file in place.
    """
    launch = run_editor if run_editor is not None else _spawn_editor
    with NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        tmp_path = handle.name
    try:
        argv = [*resolve_editor(), tmp_path]
        code = launch(argv)
        if code != 0:
            return text
        return Path(tmp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            # Best-effort cleanup: a missing/locked temp file must not mask the
            # edit result (or a real error) to the caller.
            pass


def edit_in_editor(app: App, text: str, *, run_editor: RunEditor | None = None) -> str:
    """Suspend ``app`` and round-trip ``text`` through the editor.

    Wraps :func:`edit_text` in :meth:`~textual.app.App.suspend` so the editor
    owns the terminal while it runs. Suspension is skipped when the driver
    cannot hand over the terminal (the headless test driver), in which case the
    editor seam is run directly — this keeps the helper testable, and tests
    replace the seam anyway.

    **Web mode** (:func:`lot_textual_ui.webmode.is_web_mode`): a remote browser
    client cannot drive a local ``$EDITOR``, and :meth:`App.suspend` is
    unsupported over the web transport (Textual's web driver raises
    ``SuspendNotSupported``). So in web mode this is a hard no-op: ``text`` is
    returned unchanged and the editor is never launched, whatever the caller.
    The user-facing gate lives one level up —
    ``_BodyEditorMixin.action_edit_body`` (:mod:`lot_textual_ui.forms`) shows a
    notice instead of calling here — this guard is the backstop for any future
    caller that forgets to check.
    """
    if is_web_mode():
        return text
    if app.is_headless:
        return edit_text(text, run_editor=run_editor)
    with app.suspend():
        return edit_text(text, run_editor=run_editor)
