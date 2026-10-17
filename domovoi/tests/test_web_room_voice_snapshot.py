"""The room drawer's voice picker keeps the voice it just set (batch 15).

Seen in the batch-15 emulator smoke: picking Norman for a phone room
(POST /api/satellites/den/voice -> 200, kept_on "server") left the picker
showing "the server's voice" again. The drawer re-reads GET
/api/satellites right after the pick, and an online room's voice comes
from the cached core snapshot, which the web's poll loop refreshes only
every few seconds; the page doesn't read the list again until a reload.

The web route now writes a voice the server keeps into the cached
snapshot as soon as the core says so. A Pi's voice (kept_on "device") is
the one it reports back later, so it is left for the next poll. No DB.
"""

from __future__ import annotations

import pytest

from web.backend import domovoi_client
from web.backend.api import satellites as sat_api
from web.backend.schemas import RoomVoiceRequest


@pytest.fixture
def snapshot():
    before = domovoi_client.get_cached_snapshot()
    domovoi_client.set_cached_snapshot({"satellite_voice": {"den": None, "kitchen": "Ryan"}, "other": 1})
    yield
    domovoi_client.set_cached_snapshot(before)


def _core_answers(monkeypatch, status, payload):
    sent = []

    async def post_admin(path, body=None, timeout=30.0, headers=None):
        sent.append((path, body))
        return status, payload

    monkeypatch.setattr(sat_api, "post_admin", post_admin)
    monkeypatch.setattr(sat_api, "auth_forward_headers", lambda request: {})
    return sent


@pytest.mark.asyncio
async def test_a_voice_kept_on_the_server_is_in_the_snapshot_at_once(monkeypatch, snapshot):
    sent = _core_answers(monkeypatch, 200, {"room_id": "den", "voice": "Norman", "kept_on": "server", "said": True})
    await sat_api.set_room_voice("den", RoomVoiceRequest(voice="Norman"), request=None)
    assert sent == [("/v1/admin/satellite/den/voice", {"voice": "Norman"})]
    snap = domovoi_client.get_cached_snapshot()
    assert snap["satellite_voice"] == {"den": "Norman", "kitchen": "Ryan"}
    assert snap["other"] == 1


@pytest.mark.asyncio
async def test_going_back_to_the_servers_voice_clears_it(monkeypatch, snapshot):
    domovoi_client.set_cached_snapshot({"satellite_voice": {"den": "Norman"}})
    _core_answers(monkeypatch, 200, {"room_id": "den", "voice": None, "kept_on": "server", "said": False})
    await sat_api.set_room_voice("den", RoomVoiceRequest(voice=None), request=None)
    assert domovoi_client.get_cached_snapshot()["satellite_voice"] == {"den": None}


@pytest.mark.asyncio
async def test_a_pi_waits_for_its_own_report(monkeypatch, snapshot):
    _core_answers(monkeypatch, 200, {"room_id": "kitchen", "voice": "Norman", "kept_on": "device", "said": True})
    await sat_api.set_room_voice("kitchen", RoomVoiceRequest(voice="Norman"), request=None)
    assert domovoi_client.get_cached_snapshot()["satellite_voice"]["kitchen"] == "Ryan"


@pytest.mark.asyncio
async def test_a_refused_change_leaves_the_snapshot(monkeypatch, snapshot):
    _core_answers(monkeypatch, 409, {"detail": "the room is busy"})
    out = await sat_api.set_room_voice("den", RoomVoiceRequest(voice="Norman"), request=None)
    assert out.status_code == 409
    assert domovoi_client.get_cached_snapshot()["satellite_voice"]["den"] is None


@pytest.mark.asyncio
async def test_no_snapshot_yet_is_left_alone(monkeypatch):
    before = domovoi_client.get_cached_snapshot()
    domovoi_client.set_cached_snapshot(None)
    try:
        _core_answers(monkeypatch, 200, {"room_id": "den", "voice": "Norman", "kept_on": "server", "said": True})
        await sat_api.set_room_voice("den", RoomVoiceRequest(voice="Norman"), request=None)
        assert domovoi_client.get_cached_snapshot() is None
    finally:
        domovoi_client.set_cached_snapshot(before)
