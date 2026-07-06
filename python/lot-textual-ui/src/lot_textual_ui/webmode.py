"""Web-mode detection: is this app instance being served to a browser?

``lot web`` (and the ``lot-textual-ui-web`` entry point behind it, see
:mod:`lot_textual_ui.web`) serve the app via textual-serve: one fresh
``lot-textual-ui`` process per browser session, each with
:data:`WEB_MARKER_ENV` (``LOT_TEXTUAL_WEB=1``) in its environment —
textual-serve copies the server's environment into every session subprocess.

:func:`is_web_mode` is the **single** check the rest of the app consults for
anything that assumes a local terminal: the ``$EDITOR`` escape hatch (which
would need :meth:`textual.app.App.suspend`, unsupported over the web
transport) skips itself, and clipboard toasts word themselves honestly (the
browser, not a terminal, decides whether an OSC 52 copy lands). Keep any new
web-mode adaptation on this helper rather than reading the environment
directly.

The environment is read at **call** time, not import time, so tests fake
either mode with plain ``monkeypatch.setenv``/``delenv`` — no app or server
required.
"""

from __future__ import annotations

import os

WEB_MARKER_ENV = "LOT_TEXTUAL_WEB"
"""Environment variable marking that the app is served to a browser."""


def is_web_mode() -> bool:
    """True when this app instance is being served to a web browser.

    ``lot web`` sets :data:`WEB_MARKER_ENV` to ``1``; an unset, empty, or
    ``0`` value means a normal local-terminal run.
    """
    return os.environ.get(WEB_MARKER_ENV, "") not in ("", "0")
