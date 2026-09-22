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


# ─── CORE-7: request-body limits ──────────────────────────────────────────


def _limited():
    from domovoi.transport_guard import BodyLimitMiddleware

    return BodyLimitMiddleware(ok_app)


async def test_a_declared_body_over_the_limit_is_refused_413():
    limit = default_limit()
    status, body = await call(
        _limited(),
        headers={"host": "localhost", "content-length": str(limit + 1)},
    )
    assert status == 413
    assert b"limit" in body


async def test_the_oversized_body_is_never_read():
    """The whole point: FastAPI buffers the body before validation, so a
    refusal that reads it first has not refused anything."""
    reads = []

    async def counting_app(scope, receive, send):
        reads.append(await receive())
        await ok_app(scope, receive, send)

    from domovoi.transport_guard import BodyLimitMiddleware

    status, _ = await call(
        BodyLimitMiddleware(counting_app),
        headers={"content-length": str(default_limit() + 1)},
    )
    assert status == 413
    assert reads == []


async def test_a_body_at_the_limit_passes():
    limit = default_limit()
    status, body = await call(
        _limited(), headers={"content-length": str(limit)}, body=b"x" * 16
    )
    assert status == 200
    assert body == b"through"


async def test_an_undeclared_body_is_cut_off_once_it_crosses_the_limit(monkeypatch):
    """A client can simply not send Content-Length. Counting as it
    streams is the only answer that works for chunked bodies."""
    # 1 KiB is the floor default_body_limit() clamps to, so this is the
    # smallest limit the middleware will actually honour.
    monkeypatch.setattr(settings, "max_request_bytes", 1024, raising=False)

    async def drain_app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if not message.get("more_body"):
                break
        await ok_app(scope, receive, send)

    from domovoi.transport_guard import BodyLimitMiddleware

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/intent",
        "headers": [(b"host", b"localhost")],
        "client": ("192.168.1.50", 5555),
    }
    chunks = [
        {"type": "http.request", "body": b"x" * 700, "more_body": True},
        {"type": "http.request", "body": b"x" * 700, "more_body": False},
    ]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await BodyLimitMiddleware(drain_app)(scope, receive, send)
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, "exactly one response, not the handler's as well"
    assert starts[0]["status"] == 413


def default_limit() -> int:
    from domovoi.transport_guard import default_body_limit

    return default_body_limit()


def test_the_default_limit_is_about_a_mebibyte():
    assert default_limit() == 1024 * 1024


def test_the_limit_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "max_request_bytes", 4096, raising=False)
    assert default_limit() == 4096


@pytest.mark.parametrize(
    ("path", "at_least"),
    [
        ("/api/music/library/upload", 1024 * 1024 * 1024),
        ("/api/files/upload", 1024 * 1024 * 1024),
        ("/api/files/import", 1024 * 1024 * 1024),
        ("/api/voices/piper", 256 * 1024 * 1024),
        ("/api/satellites/media/prepare", 512 * 1024 * 1024),
        ("/api/documents/upload", 64 * 1024 * 1024),
        ("/api/chat/uploads", 16 * 1024 * 1024),
        ("/v1/plugins/install", 64 * 1024 * 1024),
        ("/api/plugins/install", 64 * 1024 * 1024),
    ],
)
def test_upload_routes_keep_room_to_upload(path, at_least):
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for(path) >= at_least


@pytest.mark.parametrize(
    "path",
    [
        "/v1/intent",
        "/v1/admin/announce",
        "/api/satellites/kitchen/config",
        "/api/music/play",
        # Adjacent to an upload prefix, but not under it.
        "/api/documents/uploaded-by-someone-else",
        "/api/music/library/reindex",
    ],
)
def test_ordinary_routes_get_the_default_limit(path):
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for(path) == default_limit()


def test_a_nested_upload_path_still_gets_the_upload_limit():
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for("/api/documents/text/notes/today.md") > default_limit()


async def test_websocket_scopes_are_not_body_limited():
    seen = []

    async def spy(scope, receive, send):
        seen.append(scope["type"])

    from domovoi.transport_guard import BodyLimitMiddleware

    await call(
        BodyLimitMiddleware(spy),
        headers={"content-length": str(default_limit() * 100)},
        scope_type="websocket",
    )
    assert seen == ["websocket"]


# ─── CORE-7: the limits as the real apps wear them ────────────────────────


async def test_intent_with_an_oversized_content_length_is_413(monkeypatch):
    """Through the REAL core app: the refusal happens in front of the
    route, so it does not matter that /v1/intent is gated or that the
    body would not have parsed."""
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=False)
    transport = ASGITransport(app=core_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/intent",
            content=b"x" * (default_limit() + 1),
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 413


async def test_a_transcript_over_the_model_limit_is_422(monkeypatch):
    """Under the transport cap but still not a sentence anyone said: the
    model refuses it before the router, the LLM prompt and intents_log
    ever see it."""
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.models import MAX_TRANSCRIPT_CHARS
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=False)  # pre-setup LAN grace
    transport = ASGITransport(app=core_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/intent", json={"transcript": "a" * (MAX_TRANSCRIPT_CHARS + 1)}
        )
    assert r.status_code == 422


async def test_a_room_id_over_the_model_limit_is_422(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.models import MAX_ROOM_ID_CHARS
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=False)
    transport = ASGITransport(app=core_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/intent",
            json={"transcript": "hello", "room_id": "r" * (MAX_ROOM_ID_CHARS + 1)},
        )
    assert r.status_code == 422


def test_a_config_push_with_too_many_keys_is_refused():
    import pydantic

    from domovoi.main import _AdminConfigUpdateBody, _AdminSatelliteConfigBody
    from domovoi.models import MAX_CONFIG_CHANGES

    too_many = {f"k{i}": 1 for i in range(MAX_CONFIG_CHANGES + 1)}
    for model in (_AdminConfigUpdateBody, _AdminSatelliteConfigBody):
        with pytest.raises(pydantic.ValidationError):
            model(changes=too_many)
        # A normal edit is nowhere near the bound.
        model(changes={"bot_name": "Domovoi"})


@pytest.mark.parametrize("which", ["core", "web"])
def test_the_uvicorn_launcher_caps_concurrency(monkeypatch, which):
    """Unlimited concurrent connections is a promise this box cannot
    keep: every satellite WebSocket holds buffers for as long as it is
    open."""
    import uvicorn

    captured: dict = {}

    def fake_run(_app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["domovoi"])

    if which == "core":
        from domovoi.main import main
    else:
        from web.backend.main import main

    main()
    assert captured["limit_concurrency"] == settings.max_concurrent_connections
    assert captured["limit_concurrency"] > 0
