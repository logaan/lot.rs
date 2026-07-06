"""Serve the LoT Textual UI to web browsers via ``textual-serve``.

This is the ``lot-textual-ui-web`` console script, launched by ``lot web`` (the
user-facing entry point, mirroring how ``lot pui`` launches ``lot-textual-ui``).
It runs a self-hosted `textual-serve <https://github.com/Textualize/textual-serve>`_
server that spawns one fresh ``lot-textual-ui`` process per browser session, so
every visitor gets their own app instance against the same vault.

Environment contract:

- ``LOT_VAULT_PATH`` is forwarded by ``lot web`` and inherited by every served
  app process (textual-serve copies ``os.environ`` into each session's
  subprocess), so all sessions hit the same vault.
- ``LOT_TEXTUAL_WEB=1`` marks web mode. ``lot web`` sets it, and :func:`main`
  also sets it defensively (covering a direct ``uv run lot-textual-ui-web``),
  so the served app can detect it is running in a browser rather than a
  terminal and adapt — see :func:`lot_textual_ui.webmode.is_web_mode`, the
  single helper the app consults (e.g. to disable the ``$EDITOR`` escape
  hatch, which would need to suspend to a local terminal).

Networking: the default bind is ``0.0.0.0`` so other machines on the local
network can reach the UI. textual-serve bakes its ``public_url`` into the served
page (the websocket and static-asset URLs), so when binding a wildcard address
this module points ``public_url`` at the machine's LAN address — a page whose
websocket URL is ``ws://0.0.0.0:...`` would load remotely but never connect.
There is no authentication or encryption: anyone who can reach the port gets
full read/write access to the vault. Bind ``--host 127.0.0.1`` for local-only
use.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
from pathlib import Path

from textual_serve.server import Server

from .webmode import WEB_MARKER_ENV

DEFAULT_HOST = "0.0.0.0"
"""Default bind address: all interfaces, so the LAN can reach the UI."""

DEFAULT_PORT = 8000
"""Default port (textual-serve's own default)."""

WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})
"""Bind addresses that mean "all interfaces" and need a routable public URL."""


def app_command(executable: str = sys.executable, which=shutil.which) -> str:
    """The command textual-serve runs for each browser session.

    Prefers the ``lot-textual-ui`` console script sitting in the same
    bin directory as the running interpreter (the project venv, so the served
    app is exactly the one this package was installed with), falling back to
    whatever ``lot-textual-ui`` is on ``PATH``, then to the bare name —
    mirroring how ``lot pui`` resolves the binary next to ``lot`` first.
    """
    sibling = Path(executable).parent / "lot-textual-ui"
    if sibling.exists():
        return str(sibling)
    return which("lot-textual-ui") or "lot-textual-ui"


def lan_ip() -> str | None:
    """Best-effort LAN address of this machine, or ``None`` when unknown.

    Uses the routing trick: "connecting" a UDP socket picks the local address
    the OS would route from, without sending any packet (the target is the
    TEST-NET-1 documentation range, never actually contacted).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
    except OSError:
        return None
    return address


def public_url(host: str, port: int, lan: str | None) -> str:
    """The URL the served page (and startup banner) should use.

    A wildcard bind is not a routable address, so it is replaced with the LAN
    address when one is known (else ``localhost``, which at least works on this
    machine). A concrete ``host`` is used as-is.
    """
    if host in WILDCARD_HOSTS:
        return f"http://{lan or 'localhost'}:{port}"
    return f"http://{host}:{port}"


def startup_urls(host: str, port: int, lan: str | None) -> list[str]:
    """Every URL worth announcing on startup.

    For a wildcard bind that is the localhost URL plus the LAN URL (when
    known); for a concrete bind, just that address.
    """
    if host in WILDCARD_HOSTS:
        urls = [f"http://localhost:{port}"]
        if lan:
            urls.append(f"http://{lan}:{port}")
        return urls
    return [f"http://{host}:{port}"]


def build_parser() -> argparse.ArgumentParser:
    """The ``lot-textual-ui-web`` argument parser (``--host``/``--port``)."""
    parser = argparse.ArgumentParser(
        prog="lot-textual-ui-web",
        description=(
            "Serve the LoT Textual UI to web browsers (one app process per "
            "browser session). Normally launched via `lot web`."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            "Address to bind (default: %(default)s, reachable from the local "
            "network; use 127.0.0.1 for local-only)"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to bind (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the web server (blocks until interrupted)."""
    args = build_parser().parse_args(argv)

    # Mark web mode for every served app process (children inherit our
    # environment). `lot web` sets this too; doing it here as well covers a
    # direct `uv run lot-textual-ui-web`.
    os.environ[WEB_MARKER_ENV] = "1"

    lan = lan_ip()
    for url in startup_urls(args.host, args.port, lan):
        print(f"Serving the LoT Textual UI at {url}")
    if args.host in WILDCARD_HOSTS:
        print(
            "Note: no authentication — anyone who can reach this port can "
            "read and change the vault. Use --host 127.0.0.1 for local-only."
        )

    server = Server(
        app_command(),
        host=args.host,
        port=args.port,
        title="LoT",
        public_url=public_url(args.host, args.port, lan),
    )
    server.serve()


if __name__ == "__main__":
    main()
