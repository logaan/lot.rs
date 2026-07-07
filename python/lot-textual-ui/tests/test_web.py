"""Tests for :mod:`lot_textual_ui.web` (the `lot-textual-ui-web` entry point).

These cover the pure decision logic — app-command resolution, URL construction
for wildcard vs concrete binds, and argument parsing — without binding any
sockets or spawning textual-serve.
"""

from __future__ import annotations

import pytest

from lot_textual_ui import web


class TestAppCommand:
    def test_prefers_sibling_console_script(self, tmp_path):
        """A `lot-textual-ui` next to the interpreter wins over PATH."""
        exe = tmp_path / "python"
        exe.touch()
        sibling = tmp_path / "lot-textual-ui"
        sibling.touch()
        found = web.app_command(
            executable=str(exe),
            which=lambda name: "/elsewhere/lot-textual-ui",
        )
        assert found == str(sibling)

    def test_falls_back_to_path_lookup(self, tmp_path):
        """No sibling script: whatever PATH resolves is used."""
        exe = tmp_path / "python"
        exe.touch()
        found = web.app_command(
            executable=str(exe),
            which=lambda name: "/elsewhere/lot-textual-ui",
        )
        assert found == "/elsewhere/lot-textual-ui"

    def test_falls_back_to_bare_name(self, tmp_path):
        """Neither sibling nor PATH: the bare name is the last resort."""
        exe = tmp_path / "python"
        exe.touch()
        found = web.app_command(executable=str(exe), which=lambda name: None)
        assert found == "lot-textual-ui"

    def test_real_venv_resolves_sibling(self):
        """In this test venv the console script exists next to the interpreter,
        so the default resolution lands on it — the exact app being tested."""
        command = web.app_command()
        assert command.endswith("lot-textual-ui")


class TestRequestPublicUrl:
    def test_uses_the_request_host_verbatim(self):
        """The browser reached us via this host, so the page's websocket and
        asset URLs must use it too — localhost stays localhost, LAN stays
        LAN (the fix for 'page loads but the app never appears')."""
        assert (
            web.request_public_url("localhost:8000", "http", "http://192.168.1.7:8000")
            == "http://localhost:8000"
        )
        assert (
            web.request_public_url("192.168.1.7:8000", "http", "http://localhost:8000")
            == "http://192.168.1.7:8000"
        )

    def test_host_without_port(self):
        assert (
            web.request_public_url("lot.example", "http", "http://localhost:8000")
            == "http://lot.example"
        )

    def test_missing_host_falls_back(self):
        assert (
            web.request_public_url("", "http", "http://192.168.1.7:8000")
            == "http://192.168.1.7:8000"
        )

    def test_scheme_is_respected(self):
        """Behind a TLS-terminating proxy the request scheme is https, and the
        page must get https/wss URLs."""
        assert (
            web.request_public_url("lot.example", "https", "http://localhost:8000")
            == "https://lot.example"
        )


class TestRequestHostServer:
    def test_handle_index_rebinds_public_url_per_request(self):
        """Each index request repoints public_url at its own Host header
        before the base class bakes URLs into the page."""
        import asyncio
        from types import SimpleNamespace
        from unittest import mock

        server = web.RequestHostServer(
            "lot-textual-ui", public_url="http://192.168.1.7:8000"
        )
        seen: list[str] = []

        async def base_handle_index(self, request):
            seen.append(self.public_url)
            return mock.sentinel.response

        request = SimpleNamespace(host="localhost:8000", scheme="http")
        with mock.patch.object(web.Server, "handle_index", base_handle_index):
            response = asyncio.run(server.handle_index(request))

        assert response is mock.sentinel.response
        assert seen == ["http://localhost:8000"]


class TestUrls:
    def test_wildcard_bind_uses_lan_address_for_public_url(self):
        """0.0.0.0 is not routable from a browser; the LAN address is the
        fallback when a request carries no Host header."""
        assert (
            web.public_url("0.0.0.0", 8000, "192.168.1.7") == "http://192.168.1.7:8000"
        )

    def test_wildcard_bind_without_lan_falls_back_to_localhost(self):
        assert web.public_url("0.0.0.0", 8000, None) == "http://localhost:8000"

    def test_concrete_bind_is_used_verbatim(self):
        """An explicit --host is what the user asked for; the LAN guess must
        not override it."""
        assert (
            web.public_url("127.0.0.1", 9001, "192.168.1.7") == "http://127.0.0.1:9001"
        )

    def test_ipv6_wildcard_counts_as_wildcard(self):
        assert web.public_url("::", 8000, "10.0.0.5") == "http://10.0.0.5:8000"

    def test_startup_urls_for_wildcard_lists_localhost_then_lan(self):
        assert web.startup_urls("0.0.0.0", 8000, "192.168.1.7") == [
            "http://localhost:8000",
            "http://192.168.1.7:8000",
        ]

    def test_startup_urls_for_wildcard_without_lan(self):
        assert web.startup_urls("0.0.0.0", 8000, None) == ["http://localhost:8000"]

    def test_startup_urls_for_concrete_bind(self):
        assert web.startup_urls("127.0.0.1", 9001, "192.168.1.7") == [
            "http://127.0.0.1:9001"
        ]


class TestParser:
    def test_defaults_bind_all_interfaces_on_8000(self):
        """The default suits LAN access (documented in the readme with its
        security caveat)."""
        args = web.build_parser().parse_args([])
        assert args.host == web.DEFAULT_HOST == "0.0.0.0"
        assert args.port == web.DEFAULT_PORT == 8000

    def test_host_and_port_flags(self):
        args = web.build_parser().parse_args(["--host", "127.0.0.1", "--port", "9001"])
        assert args.host == "127.0.0.1"
        assert args.port == 9001

    def test_non_numeric_port_is_rejected(self):
        with pytest.raises(SystemExit):
            web.build_parser().parse_args(["--port", "web"])


class TestLanIp:
    def test_lan_ip_is_none_or_a_dotted_address(self):
        """`lan_ip` never raises; it yields None or something address-shaped
        (no packets are sent, so this is safe offline)."""
        ip = web.lan_ip()
        assert ip is None or ip.count(".") == 3
