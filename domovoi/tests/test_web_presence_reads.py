"""Household presence and the calendar are for PAIRED DEVICES only
(owner decision 2026-10-08, WEB-15).

``/ws/state`` needs a household credential on its handshake because it
pushes presence (``people.last_seen``), calendar titles and satellite
details — but the same state answered any LAN host over plain HTTP: the
people roster with every ``last_seen_at``, a person's and a room's session
lists, the calendar, every room's Wi-Fi SSID / hardware / code SHA /
``in_call_with``, and the MAC of a satellite being adopted. A host that
polled ``GET /api/people`` had the presence feed the socket gate withheld.

These reads now take ``require_device_read``, like the speech reads beside
them (``test_web_speech_reads``, whose marker technique this reuses): no
credential or a stale token is 401 and the handler never runs; the
household token, an admin Bearer, the dashboard cookie or
``?device_token=`` gets through; a fresh install keeps its grace.

DB-free: the auth primitives are faked and each route's first side effect
is a marker that raises.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    bearer,
    install_fake_db,
    web_app,
)

ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"


class Reached(BaseException):
    """Raised when a request gets past the gate (a BaseException, so no
    handler's ``except Exception`` can swallow the proof)."""


# name -> (concrete GET path, module, attribute the handler touches first)
READS: dict[str, tuple[str, str, str]] = {
    "people": ("/api/people", "web.backend.api.people", "session_scope"),
    "person": ("/api/people/1", "web.backend.api.people", "session_scope"),
    "person-sessions": ("/api/people/1/sessions", "web.backend.api.people", "session_scope"),
    "rooms": ("/api/satellites", "web.backend.api.satellites", "_list_rooms"),
    "room": ("/api/satellites/kitchen", "web.backend.api.satellites", "_list_rooms"),
    "room-sessions": ("/api/satellites/kitchen/sessions", "web.backend.api.satellites",
                      "session_scope"),
    "pending": ("/api/satellites/pending", "web.backend.satellite_adoption", "snapshot_pending"),
    "calendar": ("/api/calendar/events?start=2026-10-01T00:00:00Z", "web.backend.api.calendar",
                 "session_scope"),
    "event": ("/api/calendar/events/1", "web.backend.api.calendar", "session_scope"),
}
_IDS = sorted(READS)


@pytest.fixture
def mark_reached(monkeypatch):
    import importlib

    seen: list[str] = []

    @asynccontextmanager
    async def marked_session():
        seen.append("session")
        raise Reached("reached the database")
        yield  # pragma: no cover

    async def marked_call(*a, **kw):
        seen.append("call")
        raise Reached("reached the handler")

    for _path, module_name, attr in READS.values():
        module = importlib.import_module(module_name)
        monkeypatch.setattr(
            module, attr, marked_session if attr == "session_scope" else marked_call
        )
    return seen


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers=headers or {}, **kw,
    )


def _with_query(path: str, token: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}device_token={token}"


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_a_lan_host_with_no_credential_reads_nothing(name, claimed, mark_reached) -> None:
    """The phase-1 repro: ``curl /api/people`` from anywhere on the LAN."""
    async with _client() as c:
        r = await c.get(READS[name][0])
    assert r.status_code == 401, r.text
    assert HEADER in r.json()["detail"]
    assert mark_reached == []


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_a_stale_token_reads_nothing(name, claimed, mark_reached) -> None:
    path = READS[name][0]
    async with _client({HEADER: "stale-token"}) as c:
        assert (await c.get(path)).status_code == 401
    async with _client() as c:
        assert (await c.get(_with_query(path, "stale-token"))).status_code == 401
    assert mark_reached == []


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.parametrize(
    "credential",
    ["household-token", "admin-bearer", "dashboard-cookie", "query-token"],
)
@pytest.mark.asyncio
async def test_a_paired_device_reads_it(name, credential, claimed, mark_reached) -> None:
    path = READS[name][0]
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if credential == "household-token":
        headers = {HEADER: DEVICE_TOKEN}
    elif credential == "admin-bearer":
        headers = bearer(ADMIN_TOKEN)
    elif credential == "dashboard-cookie":
        cookies = {COOKIE: ADMIN_TOKEN}
    else:
        path = _with_query(path, DEVICE_TOKEN)
    async with _client(headers, cookies=cookies) as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(name, monkeypatch, mark_reached) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        with pytest.raises(Reached):
            await c.get(READS[name][0])
