"""The open reads that still told a LAN host when a room was in use, and
by whose device (REV-11, 2026-10-08).

The first pass (WEB-15) gated the room list, a room's row and sessions,
the people roster and the calendar. Its siblings kept answering anyone:

* the core's ``GET /v1/admin/satellite/{room}/config`` (and the dashboard
  proxy, driven in ``test_web_presence_reads``): a 200 or 404 says whether
  the room's satellite is connected, and the body is its reported mic,
  Wi-Fi and audio hardware. Now ``require_device_read``.
* ``GET /api/music/now-playing``: open, polled every 1.5 s, and its
  ``added_by`` named the registered device that queued each room's song
  ("Kamron's Pixel") — whose device is driving which room right now. The
  card stays open (the kiosk display reads it unpaired); ``added_by`` is
  named only to a household credential, the ``/ws/state`` tiers.

DB-free: the auth primitives are faked (``auth_testkit.install_fake_db``),
and the now-playing read's MPD and database halves are replaced.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_app
from web.backend.api import music as music_api
from web.backend.schemas import NowPlaying, NowPlayingSong

DEVICE_TOKEN = "room-activity-household-token"
ADMIN_TOKEN = "room-activity-admin-session"
DEVICE_NAME = "Kamron's Pixel"


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


# ─── the core's satellite config read ───────────────────────────────────


@pytest.fixture
def no_rooms_connected(monkeypatch):
    monkeypatch.setattr(core_app.state, "active_sessions", {}, raising=False)
    monkeypatch.setattr(core_app.state, "satellite_config", {}, raising=False)


async def _core_get(path: str, headers: dict[str, str] | None = None):
    async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test") as c:
        return await c.get(path, headers=headers or {})


CONFIG_PATH = "/v1/admin/satellite/kitchen/config"


@pytest.mark.asyncio
async def test_the_core_config_read_refuses_a_lan_host(claimed, no_rooms_connected) -> None:
    r = await _core_get(CONFIG_PATH)
    assert r.status_code == 401, r.text
    r = await _core_get(CONFIG_PATH, {HEADER: "stale-token"})
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)])
async def test_a_paired_device_reads_the_core_config(claimed, no_rooms_connected, headers) -> None:
    r = await _core_get(CONFIG_PATH, headers)
    # Past the gate: the handler's own answer for a room nobody connected.
    assert r.status_code == 404, r.text
    assert "not connected" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace_on_the_core_config(
    monkeypatch, no_rooms_connected
) -> None:
    install_fake_db(monkeypatch, admin=False)
    r = await _core_get(CONFIG_PATH)
    assert r.status_code == 404, r.text


# ─── now-playing: the card stays open, the device name does not ─────────


@pytest.fixture
def kitchen_playing(monkeypatch):
    """One provisioned room playing a song a phone queued. Returns the
    calls the classifier saw."""

    async def rooms():
        return [("kitchen", 6650, 8050), ("den", 6651, 8051)]

    async def card(room):
        if room[0] == "den":
            return NowPlaying(room_id="den", state="stop")
        return NowPlaying(
            room_id="kitchen", state="play", elapsed_sec=12.0, song_id=7,
            song=NowPlayingSong(file="a/b.flac", title="Song", artist="Artist"),
        )

    async def provenance(cards):
        for c in cards:
            if c.song_id == 7:
                c.added_by = DEVICE_NAME

    monkeypatch.setattr(music_api, "_list_provisioned_rooms", rooms)
    monkeypatch.setattr(music_api, "_now_playing_for", card)
    monkeypatch.setattr(music_api, "_attach_queue_provenance", provenance)


async def _now_playing(headers: dict[str, str] | None = None, cookies=None) -> list[dict]:
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test", cookies=cookies or {}
    ) as c:
        r = await c.get("/api/music/now-playing", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def _kitchen(body: list[dict]) -> dict:
    (row,) = [r for r in body if r["room_id"] == "kitchen"]
    return row


@pytest.mark.asyncio
async def test_a_lan_host_sees_what_plays_but_not_whose_device_queued_it(
    claimed, kitchen_playing
) -> None:
    """The phase-1 shape: ``curl /api/music/now-playing`` every 1.5 s."""
    body = await _now_playing()
    kitchen = _kitchen(body)
    assert kitchen["state"] == "play"
    assert kitchen["song"]["title"] == "Song"           # the kiosk still works
    assert kitchen["song_id"] == 7
    assert kitchen["added_by"] is None
    assert DEVICE_NAME not in str(body)


@pytest.mark.asyncio
async def test_a_stale_token_does_not_name_the_device(claimed, kitchen_playing) -> None:
    body = await _now_playing({HEADER: "stale-token"})
    assert _kitchen(body)["added_by"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["household-token", "admin-bearer", "dashboard-cookie"])
async def test_a_paired_device_sees_who_queued_it(claimed, kitchen_playing, credential) -> None:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if credential == "household-token":
        headers = {HEADER: DEVICE_TOKEN}
    elif credential == "admin-bearer":
        headers = bearer(ADMIN_TOKEN)
    else:
        cookies = {COOKIE: ADMIN_TOKEN}
    body = await _now_playing(headers, cookies)
    assert _kitchen(body)["added_by"] == DEVICE_NAME


@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(monkeypatch, kitchen_playing) -> None:
    install_fake_db(monkeypatch, admin=False)
    assert _kitchen(await _now_playing())["added_by"] == DEVICE_NAME


@pytest.mark.asyncio
async def test_a_poll_that_names_nobody_is_not_classified(
    monkeypatch, claimed, kitchen_playing
) -> None:
    """The 1.5 s poll of rooms nobody queued into costs no token check (and
    so no backoff charge for a kiosk holding a stale token)."""

    async def nobody(cards):
        return None

    async def boom(*a, **kw):  # pragma: no cover — reaching it IS the failure
        raise AssertionError("classified a caller for an answer that names nobody")

    monkeypatch.setattr(music_api, "_attach_queue_provenance", nobody)
    monkeypatch.setattr(music_api, "check_device_request", boom)
    body = await _now_playing({HEADER: "stale-token"})
    assert _kitchen(body)["added_by"] is None
