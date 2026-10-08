"""Podcast directory search is device tier (WEB-18).

``GET /api/podcasts/discover?q=`` makes the server send the caller's term
to Apple and mints artwork keys into the bounded discovered-feed map. It
was open: any LAN host drove the box's internet egress with words of its
choosing and could churn the map the next subscribe-by-search relies on.
Every other "the server goes and fetches" route on the podcasts surface
(subscribe, poll) was already device tier. The artwork route a search
result points at stays open, because an ``<img>`` cannot send a header.

DB-free: auth primitives faked; the outbound client is a marker that
raises, so a refusal also proves nothing left the house.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import web.backend.api.podcasts as podcasts_api
from domovoi import admin_auth
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_app
from domovoi.tests.route_walk import iter_route_contexts

ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"


class Fetched(BaseException):
    """The server tried to reach the directory."""


@pytest.fixture
def no_egress(monkeypatch):
    attempts: list[str] = []

    def _client(*a, **kw):
        attempts.append("client")
        raise Fetched("the server went to fetch Apple's search")

    monkeypatch.setattr(podcasts_api.egress, "async_client", _client)
    monkeypatch.setattr(podcasts_api.egress, "internet_turned_off", lambda: False)
    return attempts


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


def _client(headers=None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test", headers=headers or {}
    )


@pytest.mark.asyncio
async def test_an_unauthenticated_search_reaches_nothing(claimed, no_egress) -> None:
    """The phase-2 repro: ``GET /api/podcasts/discover?q=audit`` with no
    headers used to come back 200 with Apple's results."""
    async with _client() as c:
        r = await c.get("/api/podcasts/discover", params={"q": "audit"})
    assert r.status_code == 401, r.text
    assert no_egress == []


@pytest.mark.asyncio
async def test_the_query_token_is_not_a_credential_here(claimed, no_egress) -> None:
    """``?device_token=`` is the read tier's allowance for things a browser
    fetches by URL; a search the dashboard runs with ``apiGet`` sends the
    header, and the device tier does not read the query."""
    async with _client() as c:
        r = await c.get(
            "/api/podcasts/discover", params={"q": "audit", "device_token": DEVICE_TOKEN}
        )
    assert r.status_code == 401, r.text
    assert no_egress == []


@pytest.mark.parametrize("headers", [{HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)])
@pytest.mark.asyncio
async def test_a_paired_device_or_an_admin_searches(claimed, no_egress, headers) -> None:
    async with _client(headers) as c:
        with pytest.raises(Fetched):
            await c.get("/api/podcasts/discover", params={"q": "audit"})
    assert no_egress == ["client"]


def test_the_artwork_route_stays_open() -> None:
    gates = {
        admin_auth.require_device, admin_auth.require_device_read,
        admin_auth.require_admin_read, admin_auth.require_admin_mutation,
    }

    def calls(path):
        for rc in iter_route_contexts(web_app.routes):
            if getattr(rc, "path", None) == path and "GET" in (getattr(rc, "methods", None) or ()):
                out, stack = set(), [rc.dependant]
                while stack:
                    for dep in stack.pop().dependencies:
                        if dep.call is not None:
                            out.add(dep.call)
                        stack.append(dep)
                return out
        raise AssertionError(path)

    assert admin_auth.require_device in calls("/api/podcasts/discover")
    assert not gates & calls("/api/podcasts/discover/artwork/{key}")
