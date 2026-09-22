"""WEB-8 — every response carries the security headers, cross-origin
credentials are confined to this port, and a proxied plugin body has a
ceiling.

DB-free: the responses inspected here are static files, refusals and 404s,
none of which reach Postgres.
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import web_app
from web.backend import main as web_main
from web.backend import middleware as mw
from web.backend.api import plugins as plugins_api

BROWSER = {"X-Requested-With": "domovoi-tests"}
WEB_ORIGIN = f"http://192.168.1.50:{web_main._PORT}"
OTHER_PORT_ORIGIN = f"http://192.168.1.50:{web_main._PORT + 1}"


def _client(**kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test", **kw)


# ─── The headers ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "headers"),
    (
        ("GET", "/index.html", {}),                     # the dashboard shell
        ("GET", "/sw.js", {}),                          # the service worker
        ("GET", "/api/no-such-route", {}),              # a 404
        ("POST", "/api/podcasts/poll", {}),             # a 403 from the backstop
        ("POST", "/api/no-such-route", BROWSER),        # past the backstop
    ),
    ids=["shell", "service-worker", "404", "refused-write", "routed"],
)
async def test_every_response_carries_the_security_headers(method, path, headers) -> None:
    async with _client() as c:
        r = await c.request(method, path, headers=headers)
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_the_cors_preflight_carries_them_too() -> None:
    """The header middleware is outermost, so even the response CORS writes
    on its own is stamped."""
    async with _client() as c:
        r = await c.options(
            "/api/music/pause/kitchen",
            headers={
                "Origin": WEB_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert r.status_code == 200
    assert "content-security-policy" in r.headers


def test_the_policy_permits_what_the_dashboard_actually_does() -> None:
    """The bundle compiles JSX in the browser and runs plugin page code
    through `new Function`, and index.html holds inline <script> blocks —
    so the policy has to allow eval and inline script, or the dashboard is
    a blank page. It still forbids framing, objects and <base>."""
    directives = dict(
        (d.split(" ", 1) + [""])[:2] for d in (p.strip() for p in mw.CSP.split(";")) if d
    )
    assert "'unsafe-eval'" in directives["script-src"]
    assert "'unsafe-inline'" in directives["script-src"]
    assert "'unsafe-inline'" in directives["style-src"]
    # The service worker registers from /sw.js on this origin.
    assert "'self'" in directives["worker-src"]
    # The server switcher talks to another Domovoi, and plugin pages render
    # artwork from whatever they front.
    assert "*" in directives["connect-src"]
    assert "*" in directives["img-src"]
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"


def test_the_shell_s_own_script_tags_are_covered_by_the_policy() -> None:
    """index.html loads vendored scripts from this origin, declares inline
    text/babel blocks, and registers a service worker: 'self',
    'unsafe-inline' and worker-src 'self' between them cover it, and there
    is no third-party script host to allow."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "web" / "static"
    index = (root / "index.html").read_text(encoding="utf-8")
    srcs = re.findall(r"<script[^>]*\ssrc=\"([^\"]+)\"", index)
    assert srcs, "no script tags found — the assertion below would be vacuous"
    assert all(s.startswith("/") or not re.match(r"^\w+:", s) for s in srcs), (
        f"index.html loads a script from another origin: {srcs}"
    )
    assert 'type="text/babel"' in index
    assert "Babel.transform(" in index and "new Function(" in index
    player = (root / "player.jsx").read_text(encoding="utf-8")
    assert "navigator.serviceWorker.register('/sw.js')" in player


# ─── CORS, narrowed to this port ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lan_origin_on_another_port_gets_no_cors_grant() -> None:
    """Same host, different port: a different origin but the same site, so
    it used to be allowed. Now the preflight comes back without an
    Access-Control-Allow-Origin, and the browser refuses the call."""
    async with _client() as c:
        denied = await c.options(
            "/api/music/pause/kitchen",
            headers={
                "Origin": OTHER_PORT_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = await c.options(
            "/api/music/pause/kitchen",
            headers={
                "Origin": WEB_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in denied.headers
    assert allowed.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize(
    "origin",
    (
        "http://localhost:{port}",
        "http://127.0.0.1:{port}",
        "http://192.168.0.117:{port}",
        "http://10.0.0.4:{port}",
        "http://172.16.3.9:{port}",
        "http://domovoi.local:{port}",
    ),
)
def test_the_lan_origins_the_household_uses_are_allowed(origin) -> None:
    pattern = re.compile(mw.cors_origin_regex(6369))
    assert pattern.match(origin.format(port=6369))


@pytest.mark.parametrize(
    "origin",
    (
        "http://192.168.0.117:8096",      # the media server on the same box
        "http://192.168.0.117",           # port 80 on the same box
        "https://evil.example.com",
        "http://domovoi.local.evil.com:6369",
        "http://8.8.8.8:6369",
    ),
)
def test_origins_that_are_not_this_dashboard_are_refused(origin) -> None:
    pattern = re.compile(mw.cors_origin_regex(6369))
    assert not pattern.match(origin)


def test_the_app_pins_cors_to_its_own_port() -> None:
    cors = [m for m in web_app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert len(cors) == 1
    assert cors[0].kwargs["allow_origin_regex"] == mw.cors_origin_regex(web_main._PORT)


# ─── The plugin proxy's ceiling ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_plugin_upload_over_the_cap_is_refused_before_the_hop(monkeypatch) -> None:
    """The core decides who may install; until it has, this process does not
    buffer an arbitrary upload. The refusal is 413 and no request is made."""
    monkeypatch.setattr(plugins_api, "_MAX_PROXY_BODY_BYTES", 512)

    async def no_hop(*a, **kw):  # pragma: no cover — must never run
        raise AssertionError("the oversized body was forwarded to the core")

    monkeypatch.setattr(plugins_api.httpx, "AsyncClient", no_hop)
    async with _client(headers=BROWSER) as c:
        r = await c.post("/api/plugins/install", content=b"P" * 4096)
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]


def test_the_install_route_has_an_asgi_budget_too() -> None:
    """Belt and braces: a body that never declares its length is metered as
    it arrives, before the handler sees any of it."""
    assert mw.body_limit_for("/api/plugins/install") == 66 * mw.MB
    assert mw.body_limit_for("/api/plugins/install/abc123/confirm") == 66 * mw.MB
    assert mw.body_limit_for("/api/plugins") is None


def test_a_handler_s_own_policy_wins_over_the_site_one() -> None:
    """A stored HTML or SVG file is served with
    ``Content-Security-Policy: sandbox`` so it cannot run as a page (WEB-3).
    The site policy must not overwrite that — the middleware only fills in
    a header the response does not already carry."""
    from starlette.datastructures import Headers

    sent: list[dict] = []

    async def app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-security-policy", b"sandbox")],
        })
        await send({"type": "http.response.body", "body": b""})

    async def capture(message):
        sent.append(message)

    import asyncio

    asyncio.run(
        mw.SecurityHeadersMiddleware(app)(
            {"type": "http", "method": "GET", "path": "/api/documents/raw/x.html",
             "headers": []},
            None,
            capture,
        )
    )
    headers = Headers(raw=sent[0]["headers"])
    assert headers["content-security-policy"] == "sandbox"
    # The rest still lands.
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_the_web_app_runs_the_header_middleware_outermost() -> None:
    assert web_app.user_middleware[0].cls is mw.SecurityHeadersMiddleware
