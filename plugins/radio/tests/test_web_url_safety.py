"""Which stream URLs the radio web tier will store and fetch.

The web process reaches out for a station in exactly one place (the
browser stream proxy), and it stores a URL it will later reach out for in
two (``POST /stations`` favorites one, ``POST /play`` persists a
directory hit so the proxy has an id to resolve). All three go through
the shared outbound-URL check, so a station URL can never point the
server at the house's own services.

DB-free: the router is mounted against a context whose session scope
records that it was opened and refuses — so "no row was written" is an
assertion. ``resolve_host`` is patched, so nothing here touches DNS.
"""

from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Pins USE_STUBS + the *_test DATABASE_URL guard before any domovoi import.
from domovoi.tests.conftest import requires_db  # noqa: F401
from domovoi.webkit import net_safety

PUBLIC_V4 = "93.184.216.34"

# Everything a station row must never be allowed to point at.
REFUSED_STREAM_URLS = [
    "file:///etc/passwd",
    "concat:a.mp3|b.mp3",
    "ftp://kexp.example/stream.mp3",
    "http://localhost:6369/api/config/editable",
    "http://127.0.0.1:6370/v1/admin/snapshot",
    "http://127.1:6391/fm.mp3",
    "http://0x7f000001:6391/fm.mp3",
    "http://[::1]:6370/v1/admin/snapshot",
    "http://10.0.0.5/stream",
    "http://172.16.9.9/stream",
    "http://192.168.1.50/stream",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/stream",
    "http://[fc00::1]/stream",
]


class _DBTouched(Exception):
    """The handler opened a session — i.e. it was about to write."""


class _NoDBContext:
    """WebPluginContext shape whose DB is a tripwire."""

    def __init__(self) -> None:
        import logging

        self.slug = "radio"
        self.log = logging.getLogger("webplugin.radio")
        self.core = None
        self.routers: list[Any] = []
        self.opened = False

    @asynccontextmanager
    async def db_session_scope(self):
        self.opened = True
        raise _DBTouched("handler opened a database session")
        yield  # pragma: no cover

    def add_router(self, router: Any) -> None:
        self.routers.append(router)


@pytest.fixture
def no_db_client(monkeypatch):
    from domovoi_plugin_radio import web as radio_web

    monkeypatch.setattr(
        net_safety,
        "resolve_host",
        lambda host: (
            [ipaddress.ip_address(PUBLIC_V4)]
            if host.lower().endswith("example")
            else []
        ),
    )
    ctx = _NoDBContext()
    radio_web.register_web(ctx)
    app = FastAPI()
    for router in ctx.routers:
        app.include_router(router, prefix="/api/plugins/radio")
    with TestClient(app) as client:
        client.ctx = ctx
        yield client


# ─── POST /stations — favoriting a station (WEB-7) ───────────────────────


@pytest.mark.parametrize("url", REFUSED_STREAM_URLS)
def test_favoriting_a_house_local_stream_is_refused(no_db_client, url) -> None:
    resp = no_db_client.post(
        "/api/plugins/radio/stations",
        json={"name": "Nope", "source": "online", "stream_url": url},
    )
    assert resp.status_code == 400, resp.text
    assert "stream URL" in resp.json()["detail"]
    assert no_db_client.ctx.opened is False


def test_favoriting_a_public_stream_reaches_the_database(no_db_client) -> None:
    """The check is about the URL, not about refusing stations: a real
    stream URL goes straight through to the insert."""
    with pytest.raises(_DBTouched):
        no_db_client.post(
            "/api/plugins/radio/stations",
            json={
                "name": "KEXP",
                "source": "online",
                "stream_url": "http://kexp.example/stream.mp3",
            },
        )
    assert no_db_client.ctx.opened is True


def test_an_fm_station_without_a_stream_url_still_saves(no_db_client) -> None:
    """FM rows carry a frequency and no URL — there is nothing to check,
    and they must keep working."""
    with pytest.raises(_DBTouched):
        no_db_client.post(
            "/api/plugins/radio/stations",
            json={"name": "WKAR", "source": "fm", "frequency_mhz": 90.5},
        )
    assert no_db_client.ctx.opened is True


# ─── POST /play — persisting a directory hit (ADD-4) ─────────────────────


@pytest.mark.parametrize("url", REFUSED_STREAM_URLS)
def test_playing_a_house_local_stream_is_refused(no_db_client, url) -> None:
    """``/play`` writes a row the stream proxy fetches by id, so it is a
    way into the same fetch — and refuses the same URLs."""
    resp = no_db_client.post(
        "/api/plugins/radio/play",
        json={"name": "Nope", "source": "online", "stream_url": url},
    )
    assert resp.status_code == 400, resp.text
    assert "stream URL" in resp.json()["detail"]
    assert no_db_client.ctx.opened is False


def test_playing_a_public_stream_reaches_the_database(no_db_client) -> None:
    with pytest.raises(_DBTouched):
        no_db_client.post(
            "/api/plugins/radio/play",
            json={
                "name": "KEXP",
                "source": "online",
                "stream_url": "http://kexp.example/stream.mp3",
                "external_id": "uuid-kexp",
            },
        )
    assert no_db_client.ctx.opened is True


def test_playing_a_station_by_id_carries_no_url_to_check(no_db_client) -> None:
    """Replaying a row that already exists sends no ``stream_url`` — the
    check has nothing to say, and the request goes through to the row."""
    with pytest.raises(_DBTouched):
        no_db_client.post("/api/plugins/radio/play", json={"station_id": 3})
    assert no_db_client.ctx.opened is True


# ─── PATCH /stations/{id} — editing one afterwards ───────────────────────


def test_editing_a_station_cannot_swap_in_a_house_local_stream(no_db_client) -> None:
    resp = no_db_client.patch(
        "/api/plugins/radio/stations/1",
        json={"stream_url": "http://127.0.0.1:11434/api/tags"},
    )
    assert resp.status_code == 400, resp.text
    assert no_db_client.ctx.opened is False


# ─── GET /stations/{id}/stream — the browser proxy ───────────────────────


class _OneRowSession:
    """Answers the proxy's single SELECT with one station row."""

    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    async def execute(self, statement, params=None):
        outer = self

        class _Result:
            def first(self):
                return outer.row

        return _Result()


@pytest.fixture
def proxy_client(monkeypatch):
    """Router mounted over a session that hands back whatever station row
    the test sets, and an httpx that refuses to be used."""
    from domovoi_plugin_radio import web as radio_web

    monkeypatch.setattr(
        net_safety,
        "resolve_host",
        lambda host: (
            [ipaddress.ip_address(PUBLIC_V4)]
            if host.lower().endswith("example")
            else []
        ),
    )

    holder: dict[str, Any] = {"row": None}

    class _Ctx(_NoDBContext):
        @asynccontextmanager
        async def db_session_scope(self):
            self.opened = True
            yield _OneRowSession(holder["row"])

    import httpx

    def never(*args, **kwargs):  # pragma: no cover — opening IS the failure
        raise AssertionError("the proxy opened an upstream connection")

    monkeypatch.setattr(httpx, "AsyncClient", never)

    ctx = _Ctx()
    radio_web.register_web(ctx)
    app = FastAPI()
    for router in ctx.routers:
        app.include_router(router, prefix="/api/plugins/radio")
    with TestClient(app) as client:
        client.station = lambda **kw: holder.__setitem__(
            "row", (kw.get("name", "Station"), kw.get("source", "online"), kw.get("stream_url"))
        )
        yield client


@pytest.mark.parametrize("url", REFUSED_STREAM_URLS)
def test_the_proxy_refuses_to_fetch_a_house_local_station(proxy_client, url) -> None:
    """Rows written before the check existed (or whose name now points
    somewhere else) are caught here, where the fetch would happen."""
    proxy_client.station(name="Seeded", stream_url=url)
    resp = proxy_client.get("/api/plugins/radio/stations/1/stream")
    assert resp.status_code == 409, resp.text
    assert "Seeded" in resp.json()["detail"]


def test_the_proxy_still_explains_an_fm_row_without_a_url(proxy_client) -> None:
    proxy_client.station(name="WKAR", source="fm", stream_url=None)
    resp = proxy_client.get("/api/plugins/radio/stations/1/stream")
    assert resp.status_code == 409
    assert "satellite room" in resp.json()["detail"]


def test_the_proxy_404s_for_a_station_that_is_not_there(proxy_client) -> None:
    resp = proxy_client.get("/api/plugins/radio/stations/9999/stream")
    assert resp.status_code == 404
