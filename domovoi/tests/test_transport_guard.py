"""Which names this server answers to, and how big a body it will read
(CORE-8, CORE-7).

Pure ASGI, no DB, no app: every middleware here is exercised against a
tiny downstream app so the assertions are about the middleware and
nothing else. The matrices are written as "this passes / this does not"
because that is the only form in which a host or origin rule can be
reviewed — a regex nobody can read is how these go wrong.
"""

from __future__ import annotations

import pytest

from domovoi.config import settings
from domovoi.transport_guard import (
    LanHostMiddleware,
    host_allowed,
    lan_origin_regex,
    origin_allowed,
)


# ─── A downstream app + a caller ──────────────────────────────────────────


async def ok_app(scope, receive, send):
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({"type": "http.response.body", "body": b"through"})


async def call(app, *, headers=None, scope_type="http", body=b""):
    """Drive one request through ``app``; return (status, body)."""
    scope = {
        "type": scope_type,
        "method": "POST",
        "path": "/v1/intent",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("192.168.1.50", 5555),
    }
    chunks = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, payload


# ─── CORE-8: the Host matrix ──────────────────────────────────────────────

LAN_HOSTS = [
    "localhost",
    "localhost:6370",
    "127.0.0.1:6370",
    "192.168.0.117:6370",
    "10.1.2.3",
    "172.16.4.5",
    "172.31.255.254",
    "169.254.1.1",
    "[::1]:6370",
    "domovoi.local",
    "beelink.lan",
    "domovoi.home.arpa",
    "box.internal",
    # Single-label names: a container name, a NetBIOS name, the httpx
    # test client's default. None of these can be a public FQDN.
    "beelink",
    "testserver",
    "test",
]

PUBLIC_HOSTS = [
    "attacker.example",
    "attacker.example:6370",
    "domovoi.attacker.example",
    "8.8.8.8",
    # A routable address with a port — what a rebound name resolves to
    # before it flips. (Python calls the RFC 5737 documentation ranges
    # "private", so those are not a useful public example.)
    "93.184.216.34:6370",
    "rebind.evil.com",
    # .local is fine; .local.evil.com is not the same thing.
    "domovoi.local.evil.com",
]


@pytest.mark.parametrize("host", LAN_HOSTS)
def test_lan_hosts_pass(host):
    assert host_allowed(host) is True


@pytest.mark.parametrize("host", PUBLIC_HOSTS)
def test_public_hosts_are_refused(host):
    assert host_allowed(host) is False


def test_a_missing_host_passes():
    """Rebinding needs a NAME. A request carrying none cannot be a
    rebound one, and HTTP/1.0 health checks send none."""
    assert host_allowed(None) is True
    assert host_allowed("") is True


def test_a_configured_host_passes(monkeypatch):
    monkeypatch.setattr(
        settings, "trusted_hosts", "domovoi.example.net, *.tailnet.ts.net",
        raising=False,
    )
    assert host_allowed("domovoi.example.net") is True
    assert host_allowed("DOMOVOI.EXAMPLE.NET:6369") is True
    assert host_allowed("box.tailnet.ts.net") is True
    assert host_allowed("someone.else.example") is False


# ─── CORE-8: the middleware ───────────────────────────────────────────────


async def test_middleware_passes_a_lan_host():
    status, body = await call(LanHostMiddleware(ok_app), headers={"host": "192.168.0.117:6370"})
    assert status == 200
    assert body == b"through"


async def test_middleware_returns_400_for_a_public_host():
    status, body = await call(LanHostMiddleware(ok_app), headers={"host": "attacker.example"})
    assert status == 400
    assert b"Host" in body


async def test_middleware_answers_before_the_route_runs():
    """The refusal must not be a 400 the ROUTE chose — nothing
    downstream may see the request at all."""
    seen = []

    async def spy(scope, receive, send):
        seen.append(scope["path"])
        await ok_app(scope, receive, send)

    await call(LanHostMiddleware(spy), headers={"host": "attacker.example"})
    assert seen == []


async def test_middleware_lets_websocket_scopes_through():
    """A socket is judged on Origin, in the route: an HTTP 400 is not
    something a WebSocket client can report usefully."""
    seen = []

    async def spy(scope, receive, send):
        seen.append(scope["type"])

    await call(LanHostMiddleware(spy), headers={"host": "attacker.example"},
               scope_type="websocket")
    assert seen == ["websocket"]


# ─── CORE-8: the Origin matrix ────────────────────────────────────────────

LAN_ORIGINS = [
    "http://localhost:6369",
    "http://127.0.0.1:6369",
    "http://192.168.0.117:6369",
    "http://10.0.0.4",
    "http://172.20.1.1:8080",
    "https://domovoi.local",
    "http://beelink.lan:6369",
]

FOREIGN_ORIGINS = [
    "https://attacker.example",
    "http://attacker.example:6369",
    "https://domovoi.local.evil.com",
    "http://8.8.8.8",
    "null",
]


@pytest.mark.parametrize("origin", LAN_ORIGINS)
def test_lan_origins_pass(origin):
    assert origin_allowed(origin) is True


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
def test_foreign_origins_are_refused(origin):
    assert origin_allowed(origin) is False


def test_an_absent_origin_passes():
    """Satellites, the Android app and every command-line client send
    none — and only a browser is subject to the rule this enforces."""
    assert origin_allowed(None) is True
    assert origin_allowed("") is True
    assert origin_allowed("   ") is True


def test_a_configured_host_is_a_valid_origin(monkeypatch):
    monkeypatch.setattr(settings, "trusted_hosts", "domovoi.example.net", raising=False)
    assert origin_allowed("https://domovoi.example.net") is True
    assert origin_allowed("https://domovoi.example.net:6369") is True
    assert origin_allowed("https://other.example.net") is False


def test_the_cors_regex_and_the_socket_check_agree(monkeypatch):
    """The web process feeds ``lan_origin_regex()`` to CORSMiddleware and
    the core feeds the same module to its WebSocket routes. Anything the
    REST API accepts cross-origin, a socket must accept too."""
    import re

    monkeypatch.setattr(settings, "trusted_hosts", "", raising=False)
    pattern = lan_origin_regex()
    for origin in LAN_ORIGINS:
        assert re.match(pattern, origin), origin
        assert origin_allowed(origin)
    for origin in FOREIGN_ORIGINS:
        assert not re.match(pattern, origin), origin
