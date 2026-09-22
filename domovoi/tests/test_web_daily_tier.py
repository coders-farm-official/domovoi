"""REV-1 (and the web half of CORE-4) — the dashboard's ordinary
mutations answer to a HOUSEHOLD credential, and the code-adjacent ones to
the admin tier.

Two halves, both DB-free:

* a **route-table** assertion, one test per route, naming the tier every
  mutation this batch moved is expected to wear. It reads the live
  dependency trees, so a gate that is deleted or downgraded fails here
  even if nobody ever calls the endpoint;
* a **gate matrix** per route family, driven through the real web app
  with the fake auth primitives from :mod:`domovoi.tests.auth_testkit`.
  For each family: no credential is 401, the household token gets
  through, an admin Bearer gets through, the dashboard cookie alone is
  403, and a fresh install keeps its pre-setup LAN grace. On the
  admin-tier families the household token is 401 as well — that is the
  whole point of having two tiers.

"Gets through" is proved by REACHING the handler, not by writing
anything: each module's database session (or its hop to the core) is
replaced with a marker that raises. So an allowed call raises the marker
and a refused one never touches it, which is also the proof that the
refusal happened before any side effect.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_app
from domovoi.tests.test_route_auth_matrix import ROUTES, _dependency_calls

BROWSER = {"X-Requested-With": "XMLHttpRequest"}
ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"

# ─── What tier each route this batch moved is expected to wear ────────────

DEVICE_TIER = [
    # music: playing, queueing, tagging, uploading, cancelling an acquisition
    ("PATCH", "/api/music/library/{track_id}"),
    ("POST", "/api/music/library/upload"),
    ("DELETE", "/api/music/acquisitions/{acq_id}"),
    ("POST", "/api/music/now-playing/{room_id}/favorite"),
    ("POST", "/api/music/play"),
    ("POST", "/api/music/play-playlist"),
    ("POST", "/api/music/add-by-query"),
    ("POST", "/api/music/play-track"),
    ("POST", "/api/music/play-tracks"),
    ("POST", "/api/music/queue/{room_id}/add"),
    ("POST", "/api/music/queue/{room_id}/remove"),
    ("POST", "/api/music/queue/{room_id}/move"),
    ("POST", "/api/music/queue/{room_id}/clear"),
    # devices: a client introducing itself, and renaming itself
    ("POST", "/api/devices/register"),
    ("PATCH", "/api/devices/{device_id}"),
    # people: a person's own memories, favorites and preferences
    ("POST", "/api/people/{person_id}/memories"),
    ("PATCH", "/api/people/{person_id}/memories/{memory_id}"),
    ("DELETE", "/api/people/{person_id}/memories/{memory_id}"),
    ("POST", "/api/people/{person_id}/favorites"),
    ("DELETE", "/api/people/{person_id}/favorites/{favorite_id}"),
    ("PATCH", "/api/people/{person_id}/preferences"),
    # satellites: the household verbs (the core names the same tier)
    ("PATCH", "/api/satellites/{room_id}"),
    ("DELETE", "/api/satellites/{room_id}/timers/{timer_id}"),
    ("POST", "/api/satellites/{room_id}/announce"),
    ("POST", "/api/satellites/announce-all"),
    ("POST", "/api/satellites/{room_id}/volume"),
    # calendar
    ("POST", "/api/calendar/events"),
    ("PATCH", "/api/calendar/events/{event_id}"),
    ("DELETE", "/api/calendar/events/{event_id}"),
    # version: a check only fetches and reports
    ("POST", "/api/config/version/check"),
    # playlists
    ("POST", "/api/playlists"),
    ("PATCH", "/api/playlists/{playlist_id}"),
    ("PATCH", "/api/playlists/{playlist_id}/order"),
    ("DELETE", "/api/playlists/{playlist_id}"),
    ("POST", "/api/playlists/{playlist_id}/tracks"),
    ("DELETE", "/api/playlists/{playlist_id}/tracks/{track_id}"),
    # podcasts + audiobooks
    ("DELETE", "/api/podcasts/subscriptions/{sub_id}"),
    ("POST", "/api/podcasts/positions/{episode_id}"),
    ("POST", "/api/audiobooks/reindex"),
    ("POST", "/api/audiobooks/{book_id}/position"),
    # chat
    ("POST", "/api/chat/threads"),
    ("PATCH", "/api/chat/threads/{thread_id}"),
    ("DELETE", "/api/chat/threads/{thread_id}"),
    ("POST", "/api/chat/threads/{thread_id}/messages"),
    ("POST", "/api/chat/uploads"),
    # news
    ("POST", "/api/news/people/{person_id}/topics"),
    ("DELETE", "/api/news/topics/{topic_id}"),
    ("DELETE", "/api/news/topics/{topic_id}/feeds/{feed_id}"),
    ("POST", "/api/news/items/{item_id}/favorite"),
    ("POST", "/api/news/poll"),
]

ADMIN_TIER = [
    # library-wide jobs and the physical/code-adjacent satellite verbs —
    # the core routes behind each of these are admin-gated too.
    ("POST", "/api/music/library/reindex"),
    ("POST", "/api/music/library/enrich"),
    ("POST", "/api/satellites/{room_id}/restart"),
    ("POST", "/api/satellites/{room_id}/display"),
    ("PATCH", "/api/satellites/{room_id}/config"),
    ("POST", "/api/config/version/pull"),
]

# FE-3: the video satellite's kiosk renders unattended, so its transport
# row stays open. Pinned here as well as in
# test_kiosk_surface_is_documented.py, because this is the module a reader
# comes to asking "why is that one not gated?".
KIOSK_OPEN = [
    ("POST", "/api/music/pause/{room_id}"),
    ("POST", "/api/music/resume/{room_id}"),
    ("POST", "/api/music/stop/{room_id}"),
    ("POST", "/api/music/skip/{room_id}"),
]

_WEB_ROUTES = {(m, p): d for a, m, p, d in ROUTES if a == "web"}


def _gates(method: str, path: str) -> list[Any]:
    assert (method, path) in _WEB_ROUTES, f"{method} {path} is missing from the web app"
    return list(_dependency_calls(_WEB_ROUTES[(method, path)]))


@pytest.mark.parametrize(
    ("method", "path"), DEVICE_TIER, ids=[f"{m} {p}" for m, p in DEVICE_TIER]
)
def test_the_daily_mutations_are_on_the_device_tier(method, path) -> None:
    calls = _gates(method, path)
    assert admin_auth.require_device in calls, (
        f"{method} {path} is an ordinary household action — it must depend on "
        f"require_device (the household token OR an admin Bearer)"
    )


@pytest.mark.parametrize(
    ("method", "path"), ADMIN_TIER, ids=[f"{m} {p}" for m, p in ADMIN_TIER]
)
def test_the_code_adjacent_mutations_are_on_the_admin_tier(method, path) -> None:
    calls = _gates(method, path)
    assert admin_auth.require_admin_mutation in calls, (
        f"{method} {path} matches an admin-gated core route — it must depend "
        f"on require_admin_mutation so both hops name the same tier"
    )
    assert admin_auth.require_device not in calls, (
        f"{method} {path} must not also accept the household token"
    )


@pytest.mark.parametrize(
    ("method", "path"), KIOSK_OPEN, ids=[f"{m} {p}" for m, p in KIOSK_OPEN]
)
def test_the_kiosk_transport_row_is_still_open(method, path) -> None:
    """FE-3 is a product decision, not an oversight. If one of these grows
    a gate, the kiosk loses a button — change the decision first."""
    calls = _gates(method, path)
    assert admin_auth.require_device not in calls
    assert admin_auth.require_admin_mutation not in calls


# ─── The gate matrix, family by family ────────────────────────────────────


class Reached(BaseException):
    """Raised by the markers below when a request gets past the gate.

    Deliberately a ``BaseException``: several handlers wrap their work in
    ``except Exception`` and would otherwise swallow the proof.
    """


# family -> (method, path, json body, module, the attribute to mark).
# The marked attribute is whatever that handler touches FIRST once the
# gate lets it through: its database session, or its hop to the core.
DEVICE_FAMILIES = {
    "music": ("POST", "/api/music/play", {"room_id": "kitchen", "query": "x"},
              "music", "post_admin"),
    "music-queue": ("POST", "/api/music/queue/kitchen/add",
                    {"track_ids": [1], "device_id": "browser-abc"},
                    "music_queue", "session_scope"),
    "devices": ("POST", "/api/devices/register", {"device_id": "browser-abc"},
                "devices", "session_scope"),
    "people": ("POST", "/api/people/1/memories", {"body": "milk"},
               "people", "session_scope"),
    "satellites": ("POST", "/api/satellites/kitchen/volume", {"level": 30},
                   "satellites", "post_admin"),
    "calendar": ("POST", "/api/calendar/events",
                 {"title": "dinner", "starts_at": "2026-09-22T18:00:00Z"},
                 "calendar", "session_scope"),
    "version-check": ("POST", "/api/config/version/check", None,
                      "config", "post_admin"),
    "playlists": ("POST", "/api/playlists", {"name": "road trip"},
                  "playlists", "session_scope"),
    "podcasts": ("POST", "/api/podcasts/positions/1",
                 {"device_id": "browser-abc", "position_sec": 12},
                 "podcasts", "session_scope"),
    "audiobooks": ("POST", "/api/audiobooks/1/position",
                   {"device_id": "browser-abc", "position_sec": 12},
                   "audiobooks", "session_scope"),
    "chat": ("POST", "/api/chat/threads", {"title": "hello"},
             "chat", "session_scope"),
    "news": ("POST", "/api/news/poll", None, "news", "session_scope"),
}

ADMIN_FAMILIES = {
    "music-reindex": ("POST", "/api/music/library/reindex", None,
                      "music", "post_admin"),
    "music-enrich": ("POST", "/api/music/library/enrich", None,
                     "music", "post_admin"),
    "satellite-restart": ("POST", "/api/satellites/kitchen/restart", None,
                          "satellites", "post_admin"),
    "satellite-display": ("POST", "/api/satellites/kitchen/display",
                          {"action": "off"}, "satellites", "post_admin"),
    "satellite-config": ("PATCH", "/api/satellites/kitchen/config",
                         {"changes": {"volume": 3}}, "satellites", "post_admin"),
    "version-pull": ("POST", "/api/config/version/pull", None,
                     "config", "post_admin"),
}

_DEVICE_IDS = sorted(DEVICE_FAMILIES)
_ADMIN_IDS = sorted(ADMIN_FAMILIES)


@pytest.fixture
def mark_reached(monkeypatch):
    """Replace every module's first side effect with a marker, and return
    the recorder. A call that gets past a gate raises :class:`Reached`; a
    call that does not never touches the marker, which is the proof that
    the refusal landed before anything was written or proxied."""
    from web.backend import api as web_api

    seen: list[str] = []

    @asynccontextmanager
    async def marked_session():
        seen.append("session")
        raise Reached("reached the database")
        yield  # pragma: no cover

    async def marked_hop(*a, **kw):
        seen.append("hop")
        raise Reached("reached the core hop")

    families = list(DEVICE_FAMILIES.values()) + list(ADMIN_FAMILIES.values())
    for _m, _p, _b, module_name, attr in families:
        module = getattr(web_api, module_name)
        replacement = marked_session if attr == "session_scope" else marked_hop
        monkeypatch.setattr(module, attr, replacement)
    return seen


def _client(extra_headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    """The web app, spoken to the way a browser speaks to it: every write
    carries ``X-Requested-With`` (the middleware refuses one without it),
    plus whatever credential this case is testing."""
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={**BROWSER, **(extra_headers or {})}, **kw,
    )


async def _send(client: AsyncClient, method: str, path: str, body):
    return await client.request(method, path, json=body)


def _spec(family: str) -> tuple[str, str, Any]:
    spec = DEVICE_FAMILIES.get(family) or ADMIN_FAMILIES[family]
    return spec[0], spec[1], spec[2]


@pytest.mark.parametrize("family", _DEVICE_IDS + _ADMIN_IDS)
@pytest.mark.asyncio
async def test_no_credential_is_refused(family, monkeypatch, mark_reached) -> None:
    """Signed out, unpaired, on the LAN: 401, and nothing was touched."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client() as c:
        r = await _send(c, method, path, body)
    assert r.status_code == 401, r.text
    assert mark_reached == []


@pytest.mark.parametrize("family", _DEVICE_IDS + _ADMIN_IDS)
@pytest.mark.asyncio
async def test_the_dashboard_cookie_alone_is_refused(
    family, monkeypatch, mark_reached
) -> None:
    """A cookie renders GET state and never authorizes a change: 403 on
    both tiers."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client(cookies={COOKIE: ADMIN_TOKEN}) as c:
        r = await _send(c, method, path, body)
    assert r.status_code == 403, r.text
    assert mark_reached == []


@pytest.mark.parametrize("family", _DEVICE_IDS)
@pytest.mark.asyncio
async def test_the_household_token_gets_through(
    family, monkeypatch, mark_reached
) -> None:
    """A paired phone or browser does the daily thing without the admin
    password."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        with pytest.raises(Reached):
            await _send(c, method, path, body)


@pytest.mark.parametrize("family", _DEVICE_IDS)
@pytest.mark.asyncio
async def test_a_stale_household_token_is_refused(
    family, monkeypatch, mark_reached
) -> None:
    """A token read before the admin claim rotated it is not a
    credential."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client({HEADER: "stale-token"}) as c:
        r = await _send(c, method, path, body)
    assert r.status_code == 401, r.text
    assert mark_reached == []


@pytest.mark.parametrize("family", _DEVICE_IDS + _ADMIN_IDS)
@pytest.mark.asyncio
async def test_an_admin_bearer_gets_through(
    family, monkeypatch, mark_reached
) -> None:
    """The admin tier is a superset of the device tier, so one Bearer
    works everywhere."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client(bearer(ADMIN_TOKEN)) as c:
        with pytest.raises(Reached):
            await _send(c, method, path, body)


@pytest.mark.parametrize("family", _ADMIN_IDS)
@pytest.mark.asyncio
async def test_the_household_token_is_not_enough_for_the_admin_tier(
    family, monkeypatch, mark_reached
) -> None:
    """Restarting a satellite, rewriting its config, driving its screen,
    re-walking the library or pulling code is not a daily action — the
    household token does not buy it."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    method, path, body = _spec(family)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await _send(c, method, path, body)
    assert r.status_code == 401, r.text
    assert mark_reached == []


@pytest.mark.parametrize("family", _DEVICE_IDS + _ADMIN_IDS)
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(
    family, monkeypatch, mark_reached
) -> None:
    """Before anyone has claimed the admin password the whole surface is
    open on the LAN — that is what makes a first boot, and a throwaway
    test instance, usable before there is a credential to hold."""
    install_fake_db(monkeypatch, admin=False)
    method, path, body = _spec(family)
    async with _client() as c:
        with pytest.raises(Reached):
            await _send(c, method, path, body)


@pytest.mark.asyncio
async def test_a_write_without_the_preflight_header_never_reaches_a_gate(
    monkeypatch, mark_reached
) -> None:
    """The CSRF backstop still runs first: no ``X-Requested-With`` is 403
    from the middleware, whatever credential the caller holds."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={HEADER: DEVICE_TOKEN},
    ) as c:
        r = await c.post("/api/playlists", json={"name": "road trip"})
    assert r.status_code == 403
    assert mark_reached == []
