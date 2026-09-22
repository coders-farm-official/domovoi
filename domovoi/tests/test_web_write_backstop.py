"""WEB-6 — every write under ``/api/`` must carry ``X-Requested-With``.

A form post, a multipart upload and a body-less POST are all requests a
browser will send cross-origin without asking the server first, so the side
effect lands before the server has any say. A header outside that set turns
the call into a preflighted one, and the preflight is something this server
can refuse.

The walk below is parametrized over the real route table, so a new write
added later is covered the day it is added. DB-free by construction: the
backstop answers before routing, so no handler and no database is reached.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import web_app
from web.backend import middleware as mw

HEADER = {"X-Requested-With": "domovoi-tests"}
REPO_ROOT = Path(__file__).resolve().parents[2]

MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def _concrete(path: str) -> str:
    """A path with every ``{param}`` (including ``{name:path}``) filled in,
    so the walk sends something the router could actually match."""
    return re.sub(r"\{[^}]+\}", "1", path)


def _web_writes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rc in iter_route_contexts(web_app.routes):
        path = getattr(rc, "path", None)
        if not path or not path.startswith("/api/"):
            continue
        for method in getattr(rc, "methods", None) or set():
            if method in MUTATING:
                out.append((method, path))
    return sorted(set(out))


WRITES = _web_writes()
WRITE_IDS = [f"{m} {p}" for m, p in WRITES]


def _client(**kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test", **kw)


def test_the_walk_found_the_write_surface() -> None:
    """Guard against an empty walk quietly passing everything."""
    assert len(WRITES) > 80
    assert ("POST", "/api/music/library/upload") in WRITES
    assert ("POST", "/api/config/version/pull") in WRITES


@pytest.mark.parametrize(("method", "path"), WRITES, ids=WRITE_IDS)
@pytest.mark.asyncio
async def test_a_write_without_the_header_is_refused(method, path) -> None:
    async with _client() as c:
        r = await c.request(method, _concrete(path))
    assert r.status_code == 403, f"{method} {path} answered {r.status_code}"
    assert "X-Requested-With" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_body_less_post_is_refused_too() -> None:
    """The playback verbs, the reindexes and the polls send no body at all —
    the shape a cross-origin form can produce without a preflight."""
    async with _client() as c:
        for path in (
            "/api/music/pause/kitchen",
            "/api/music/stop/kitchen",
            "/api/music/skip/kitchen",
            "/api/music/library/reindex",
            "/api/audiobooks/reindex",
            "/api/podcasts/poll",
            "/api/news/poll",
            "/api/config/version/pull",
        ):
            r = await c.post(path)
            assert r.status_code == 403, path


@pytest.mark.asyncio
async def test_a_multipart_upload_is_refused_before_its_body_is_read() -> None:
    """A multipart POST is the other simple-request shape. The refusal is
    the middleware's, before routing — so nothing parses the upload."""
    async with _client() as c:
        r = await c.post(
            "/api/music/library/upload",
            files={"files": ("song.mp3", b"ID3" + b"\x00" * 2048, "audio/mpeg")},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_refusal_happens_before_routing() -> None:
    """A path with no route at all still answers 403, which is only true if
    nothing downstream ran. With the header the same call gets past the
    backstop and is answered by what lies behind it."""
    async with _client() as c:
        assert (await c.post("/api/no-such-route")).status_code == 403
        r = await c.post("/api/no-such-route", headers=HEADER)
    # 405 from the static mount that catches unrouted paths — the point is
    # that it is no longer the backstop answering.
    assert r.status_code == 405


@pytest.mark.asyncio
async def test_reads_and_the_cors_preflight_are_untouched() -> None:
    """GET changes nothing, and refusing the OPTIONS would break the very
    negotiation the backstop relies on."""
    async with _client() as c:
        assert (await c.get("/api/no-such-route")).status_code == 404
        r = await c.options(
            "/api/music/pause/kitchen",
            headers={
                "Origin": "http://127.0.0.1:6369",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_an_empty_header_value_does_not_count() -> None:
    async with _client() as c:
        r = await c.post("/api/no-such-route", headers={"X-Requested-With": "   "})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_writes_outside_the_api_are_not_the_backstop_s_business() -> None:
    """The WebSocket and the static bundle live outside ``/api/``; the
    middleware leaves them alone (they have their own gates)."""
    async with _client() as c:
        assert (await c.post("/not-api")).status_code != 403


# ─── The clients all send it ──────────────────────────────────────────────


def test_the_dashboard_sends_it_on_every_api_call() -> None:
    data_js = (REPO_ROOT / "web" / "static" / "data.js").read_text(encoding="utf-8")
    assert "const REQUESTED_WITH = { 'X-Requested-With': 'XMLHttpRequest' }" in data_js
    # Both choke points — the JSON helper and the multipart one — send the
    # whole header set, so every page inherits it.
    assert data_js.count("apiHeaders()") >= 2
    assert "...apiHeaders()," in data_js


def test_the_raw_fetches_send_it_too() -> None:
    """The handful of callers that build their own request (streamed
    download, SSE chat, the auth surface) send the same header."""
    static = REPO_ROOT / "web" / "static"
    auth_js = (static / "auth.js").read_text(encoding="utf-8")
    # Login / setup / password change, and logout.
    assert auth_js.count("'X-Requested-With': 'XMLHttpRequest'") == 2
    for name in ("chat.jsx", "files.jsx", "doc_editor.jsx", "sheet_editor.jsx"):
        source = (static / name).read_text(encoding="utf-8")
        assert "apiHeaders()" in source, name


def test_the_android_app_sends_it() -> None:
    app = REPO_ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app"
    api_client = (app / "net" / "ApiClient.kt").read_text(encoding="utf-8")
    assert '.header("X-Requested-With", "DomovoiApp")' in api_client
    # The two calls that stream and so build their own Request.
    for rel in (
        ("ui", "screens", "chat", "ChatScreen.kt"),
        ("ui", "screens", "documents", "DocumentsIo.kt"),
    ):
        source = app.joinpath(*rel).read_text(encoding="utf-8")
        assert '.header("X-Requested-With", "DomovoiApp")' in source, rel


def test_the_web_app_runs_the_backstop_in_front_of_the_router() -> None:
    """Starlette wraps user_middleware[0] outermost, so the backstop --
    registered last -- sits closest to the router: after CORS (whose
    preflight must still be answered) and after the body limiter (an
    oversized body is refused 413 whether or not it brought the header),
    and in front of every endpoint."""
    classes = [m.cls for m in web_app.user_middleware]
    assert mw.RequireRequestedWithMiddleware in classes
    assert classes.index(mw.RequireRequestedWithMiddleware) > classes.index(
        mw.BodyLimitMiddleware
    )
