"""B11 and B4 on the web side: ``web/backend/api/podcasts.py``.

* The artwork routes: a subscription's stored image (200 with its content
  type and a day's cache, or 404) and a discovery thumbnail (only for a key
  a ``/discover`` answer in this process minted — an unknown key is a 404
  with no fetch).
* ``artwork`` in the subscriptions list and in discovery results is a
  server path or null, never the publisher's URL; a list row whose image
  isn't stored yet schedules one background fill.
* Internet access turned off: discovery, subscribe-by-name and "poll now"
  are the 409 internet-off refusal, with no request made; subscribing by
  feed URL is still stored (it resolves nothing).

The routes are driven through a small app that mounts the real router with
the device gate overridden (the gate itself is test_route_auth_matrix's
business); the iTunes and image "internet" is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import ipaddress
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, egress, net_safety, podcast_artwork as pa
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from web.backend.api import podcasts as pc

PUBLIC_V4 = "93.184.216.34"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
FEED = "https://feeds.example.com/show.xml"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcast_artwork_dir", str(tmp_path / "art"))
    monkeypatch.setattr(settings, "outbound_allow_hosts", "", raising=False)
    monkeypatch.setattr(pa, "_fill_tried", set())
    monkeypatch.setattr(pa, "_discover_urls", type(pa._discover_urls)())
    monkeypatch.setattr(pa, "_discovered_feeds", type(pa._discovered_feeds)())

    def fake_resolve(host: str):
        if host.lower() in ("art.example.com", "feeds.example.com", "itunes.apple.com"):
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)


@pytest.fixture
def internet(monkeypatch):
    """A mock internet: iTunes search + an image host. ``hits`` records
    every request URL as written. A fetch through ``net_safety`` opens
    its connection to the address the check judged, so the request's URL
    host is that literal and the name travels in the ``Host`` header —
    the mock dispatches on the name the way a server does."""

    class _Net:
        hits: list[str] = []

        def handler(self, request: httpx.Request) -> httpx.Response:
            name = request.headers.get("host", request.url.host)
            as_written = f"{request.url.scheme}://{name}{request.url.raw_path.decode('ascii')}"
            self.hits.append(as_written)
            if name == "itunes.apple.com":
                return httpx.Response(200, json={"results": [{
                    "collectionName": "The Show", "artistName": "Someone",
                    "feedUrl": FEED,
                    "artworkUrl100": "https://art.example.com/100.jpg",
                    "artworkUrl600": "https://art.example.com/600.jpg",
                }]})
            if name == "art.example.com":
                return httpx.Response(200, content=JPEG)
            return httpx.Response(404)

    net = _Net()
    net.hits = []
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(net.handler), **kw),
    )
    return net


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(pc.router)
    app.dependency_overrides[admin_auth.require_device] = lambda: None
    return app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


async def _drain() -> None:
    for task in list(pa._tasks):
        await task


# ─── The artwork routes (DB-free) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_subscription_artwork_route_serves_the_stored_image(internet) -> None:
    await pa.ensure_artwork(5, "https://art.example.com/600.jpg")
    async with _client() as c:
        ok = await c.get("/api/podcasts/subscriptions/5/artwork")
        missing = await c.get("/api/podcasts/subscriptions/6/artwork")
    assert ok.status_code == 200
    assert ok.content == JPEG
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["cache-control"] == "max-age=86400"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_discover_key_is_a_404_with_no_fetch(internet) -> None:
    async with _client() as c:
        r = await c.get(f"/api/podcasts/discover/artwork/{'a' * 32}")
        bad = await c.get("/api/podcasts/discover/artwork/not-a-key")
    assert r.status_code == 404 and bad.status_code == 404
    assert internet.hits == []


@pytest.mark.asyncio
async def test_discovery_results_carry_server_paths_and_thumbnails_load_from_the_server(internet) -> None:
    async with _client() as c:
        found = (await c.get("/api/podcasts/discover", params={"q": "show"})).json()
        assert len(found) == 1
        art = found[0]["artwork"]
        assert art.startswith("/api/podcasts/discover/artwork/")
        assert "art.example.com" not in json.dumps(found)
        thumb = await c.get(art)
    assert thumb.status_code == 200 and thumb.content == JPEG
    assert thumb.headers["content-type"] == "image/jpeg"
    # The thumbnail is the small image; the big one is remembered for a subscribe.
    assert "https://art.example.com/100.jpg" in internet.hits
    assert pa.discovered_artwork_for(FEED) == "https://art.example.com/600.jpg"


# ─── Internet access turned off (DB-free) ─────────────────────────────────


@pytest.mark.asyncio
async def test_discover_subscribe_by_name_and_poll_are_409_under_never(internet, monkeypatch) -> None:
    def no_db():  # pragma: no cover - reaching the DB IS the failure
        raise AssertionError("touched the database")

    monkeypatch.setattr(pc, "session_scope", no_db)
    with egress.override_policy("never"):
        async with _client() as c:
            disc = await c.get("/api/podcasts/discover", params={"q": "npr"})
            sub = await c.post("/api/podcasts/subscriptions", json={"query": "the daily"})
            poll = await c.post("/api/podcasts/poll", json={})
    for r in (disc, sub, poll):
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == egress.TURNED_OFF_REASON
        assert r.headers["x-domovoi-refusal"] == "internet-off"
    assert internet.hits == []


@pytest.mark.asyncio
async def test_a_thumbnail_not_yet_stored_is_409_under_never(internet) -> None:
    key = pa.discover_key("https://art.example.com/100.jpg")
    with egress.override_policy("never"):
        async with _client() as c:
            r = await c.get(f"/api/podcasts/discover/artwork/{key}")
    assert r.status_code == 409
    assert internet.hits == []


# ─── The list and subscribe (DB) ──────────────────────────────────────────


async def _sub(session, url: str, artwork: str | None) -> int:
    row = (await session.execute(
        text("INSERT INTO podcast_subscriptions (feed_url, title, artwork) VALUES (:u, 'Show', :a) RETURNING id"),
        {"u": url, "a": artwork},
    )).first()
    await session.commit()
    return int(row[0])


@requires_db
@pytest.mark.asyncio
async def test_list_rows_carry_the_server_path_never_the_publisher_url(db_session, internet) -> None:
    stored = await _sub(db_session, "https://feeds.example.com/a.xml", "https://art.example.com/a.jpg")
    pending = await _sub(db_session, "https://feeds.example.com/b.xml", "https://art.example.com/b.jpg")
    none = await _sub(db_session, "https://feeds.example.com/c.xml", None)
    await pa.ensure_artwork(stored, "https://art.example.com/a.jpg")
    internet.hits.clear()

    async with _client() as c:
        rows = {r["id"]: r for r in (await c.get("/api/podcasts/subscriptions")).json()}
    assert rows[stored]["artwork"] == pa.api_path(stored)
    assert rows[stored]["artwork"].startswith(f"/api/podcasts/subscriptions/{stored}/artwork?v=")
    assert rows[pending]["artwork"] is None             # not stored yet: placeholder...
    assert rows[none]["artwork"] is None
    assert "art.example.com" not in json.dumps(rows)

    await _drain()                                      # ...and one background fill
    assert internet.hits == ["https://art.example.com/b.jpg"]
    async with _client() as c:
        rows = {r["id"]: r for r in (await c.get("/api/podcasts/subscriptions")).json()}
    assert rows[pending]["artwork"] == pa.api_path(pending)
    assert internet.hits == ["https://art.example.com/b.jpg"]   # no fetch per page view


@requires_db
@pytest.mark.asyncio
async def test_list_under_never_schedules_no_fetch(db_session, internet) -> None:
    await _sub(db_session, "https://feeds.example.com/a.xml", "https://art.example.com/a.jpg")
    with egress.override_policy("never"):
        async with _client() as c:
            rows = (await c.get("/api/podcasts/subscriptions")).json()
        await _drain()
    assert rows[0]["artwork"] is None
    assert internet.hits == []


@requires_db
@pytest.mark.asyncio
async def test_subscribe_by_name_stores_the_directory_artwork_on_the_server(db_session, internet) -> None:
    async with _client() as c:
        r = await c.post("/api/podcasts/subscriptions", json={"query": "the show"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feed_url"] == FEED and body["title"] == "The Show"
    assert body["artwork"] is None or body["artwork"].startswith("/api/")
    await _drain()
    src = (await db_session.execute(
        text("SELECT artwork FROM podcast_subscriptions WHERE id = :i"), {"i": body["id"]}
    )).scalar_one()
    assert src == "https://art.example.com/600.jpg"     # the source stays server-side
    assert pa.cached_file(body["id"]) is not None


@requires_db
@pytest.mark.asyncio
async def test_subscribing_to_a_search_result_takes_its_remembered_artwork(db_session, internet) -> None:
    async with _client() as c:
        await c.get("/api/podcasts/discover", params={"q": "show"})
        r = await c.post("/api/podcasts/subscriptions", json={"feed_url": FEED})
    assert r.status_code == 200, r.text
    await _drain()
    assert pa.cached_file(r.json()["id"]) is not None
    assert "https://art.example.com/600.jpg" in internet.hits


@requires_db
@pytest.mark.asyncio
async def test_subscribe_by_feed_url_still_works_under_never(db_session, internet) -> None:
    with egress.override_policy("never"):
        async with _client() as c:
            r = await c.post("/api/podcasts/subscriptions", json={"feed_url": "https://feeds.example.com/x.xml"})
        await _drain()
    assert r.status_code == 200, r.text
    assert r.json()["artwork"] is None
    assert internet.hits == []


@requires_db
@pytest.mark.asyncio
async def test_unsubscribe_forgets_the_stored_artwork(db_session, internet) -> None:
    sid = await _sub(db_session, "https://feeds.example.com/a.xml", "https://art.example.com/a.jpg")
    await pa.ensure_artwork(sid, "https://art.example.com/a.jpg")
    async with _client() as c:
        r = await c.delete(f"/api/podcasts/subscriptions/{sid}")
        gone = await c.get(f"/api/podcasts/subscriptions/{sid}/artwork")
    assert r.status_code == 200
    assert pa.cached_file(sid) is None and gone.status_code == 404
