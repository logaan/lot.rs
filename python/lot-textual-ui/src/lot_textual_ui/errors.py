"""Crash-boundary helpers: a durable crash log and a friendly exit message.

The app's last-resort backstop (:meth:`LotTextualApp._handle_exception`) turns
an otherwise-fatal, unhandled exception into a clean exit rather than a raw Rich
traceback dumped over the user's terminal. To do that without losing the
debugging trail, it writes the *full* traceback to a temp-file crash log
(:func:`write_crash_log`) and shows the user a short, reassuring sentence that
points at that log (:func:`crash_message`).

Both helpers are deliberately total — they never raise — because they run on the
crash path itself: a failure here would re-enter the very teardown they exist to
tame.
"""

from __future__ import annotations

import tempfile
import traceback
from datetime import datetime
from pathlib import Path


def write_crash_log(error: BaseException) -> Path | None:
    """Write ``error``'s full traceback to a temp-file crash log; never raise.

    The friendly exit message (see :func:`crash_message`) deliberately hides the
    traceback, so this preserves it out-of-band: a
    ``lot-textual-ui-crash-<timestamp>.log`` file under the system temp dir
    holding the complete :func:`traceback.format_exception` render. Returns the
    path written, or ``None`` if anything went wrong — this runs on the crash
    path, so it swallows every error (including a failure to write the log)
    rather than re-entering the teardown it supports.
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path(tempfile.gettempdir()) / f"lot-textual-ui-crash-{timestamp}.log"
        text = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        path.write_text(text, encoding="utf-8")
        return path
    except Exception:
        return None


def crash_message(error: BaseException, log_path: Path | None) -> str:
    """A short, reassuring exit message naming the crash and where it was logged.

    Shown in place of the raw traceback when the app hits an unhandled exception
    (see :meth:`~lot_textual_ui.app.LotTextualApp._handle_exception`). Names what
    went wrong (``type(error).__name__: error``), reassures the user it is a bug
    rather than something they did, and — when :func:`write_crash_log` managed to
    write one — tells them where the full details live. Returns plain text; like
    its companion it never raises.
    """
    detail = f"{type(error).__name__}: {error}"
    lines = [
        "LoT hit an unexpected error and had to close.",
        "",
        detail,
        "",
        "This is a bug in LoT, not something you did wrong.",
    ]
    if log_path is not None:
        lines.append(f"The full details were written to {log_path}.")
    return "\n".join(lines)
