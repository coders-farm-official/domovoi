"""Add-by-URL checks WHERE the URL points, not only who asked (CORE-16).

Audit A2-04: `POST /v1/admin/music/add-by-url` ran `check_outbound_fetch`
(an admin session passes outright) and nothing else, so an admin request
queued `http://127.0.0.1:6394/...` or `http://192.168.0.1/` for a provider
plugin to fetch — outside `net_safety` and the internet switch entirely —
while SECURITY_PRIVACY.md said add-by-URL goes through the outbound-URL
check and that "No internet" closes it. Under **No** the route answered
"Queued" and the fetch would start the moment the answer changed.

DB-FREE: the auth primitives are faked, and the acquisition queue is
replaced so a request that gets through is recorded rather than written.
Every URL here is an IP literal, so nothing resolves a name.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import egress
from domovoi import main as core_main
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import bearer, install_fake_db

ADMIN = "admin-token"
ROUTE = "/v1/admin/music/add-by-url"


@pytest.fixture
def queue(monkeypatch):
    """Admin signed in; the queue and the intent log are in memory."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN})
    queued: list[str] = []

    async def enqueue(_s, *, kind, text, **_kw):
        queued.append(text)
        return SimpleNamespace(
            outcome="enqueued", user_message="Queued",
            acquisition=SimpleNamespace(id=1), duplicate_of_id=None,
        )

    class _Log:
        def __init__(self, _s):
            pass

        async def log(self, **_kw):
            return None

    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(core_main.ACQUISITIONS, "enqueue", enqueue)
    monkeypatch.setattr("domovoi.db.repositories.IntentLogRepository", _Log)
    monkeypatch.setattr(core_main, "session_scope", scope)
    return queued


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


async def _add(url: str):
    async with _client() as c:
        return await c.post(
            ROUTE, json={"room_id": "kitchen", "url": url}, headers=bearer(ADMIN)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6394/v1/health",   # this core
        "http://127.0.0.1:6390/",            # the dashboard
        "http://192.168.0.1/",               # the router
        "http://10.0.0.5:8080/admin",
        "http://[::1]:6283/v1/agents/",      # Letta, on the box
        "http://0x7f000001/",                # loopback, spelled oddly
        "http://localhost:6370/",
        "file:///etc/passwd",
    ],
)
async def test_an_admin_cannot_queue_a_url_inside_the_house(queue, url) -> None:
    r = await _add(url)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("refusing this URL")
    assert queue == []


@pytest.mark.asyncio
async def test_a_public_url_is_still_queued(queue) -> None:
    r = await _add("https://93.184.215.14/watch?v=1")
    assert r.status_code == 200, r.text
    assert r.json()["queued"] is True
    assert queue == ["https://93.184.215.14/watch?v=1"]


@pytest.mark.asyncio
async def test_no_internet_refuses_instead_of_queueing(queue) -> None:
    """The documented 409: nothing waits in the queue to go out the
    moment the answer changes."""
    with egress.override_policy("never"):
        r = await _add("https://93.184.215.14/watch?v=1")
    assert r.status_code == 409, r.text
    assert r.headers.get(egress.REFUSAL_HEADER) == egress.REFUSAL_VALUE
    assert queue == []


@pytest.mark.asyncio
async def test_who_may_ask_is_still_decided_first(monkeypatch, queue) -> None:
    """No admin session and no fulfiller claiming the URL: still the
    outbound-fetch tier's 403, whatever the URL."""
    async with _client() as c:
        r = await c.post(ROUTE, json={"room_id": "kitchen", "url": "http://127.0.0.1/"})
    assert r.status_code == 403, r.text
    assert queue == []
