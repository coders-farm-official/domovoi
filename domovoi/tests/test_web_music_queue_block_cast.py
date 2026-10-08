"""A device blocked from a room's queue cannot replace that queue either
(WEB-11).

``queue_device_blocks`` was enforced only on ``/api/music/queue/{room}/
add|remove|move|clear``. The four routes that REPLACE a room's queue —
``/play``, ``/play-track``, ``/play-tracks`` (the browser player's cast)
and ``/play-playlist`` — consulted nothing, so phase 2's blocked tablet
had its add refused 403 and then simply cast over the whole queue (200).

DB-free: the auth primitives are faked; the queue block's two database
reads are answered in-process (the real ``_assert_can_edit`` and
``_blocked_message`` run); the core hop is a recorder.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

import web.backend.api.music as music_api
import web.backend.api.music_queue as queue_api
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.api.devices import DEVICE_ID_COOKIE
from web.backend.main import app

DEVICE_TOKEN = "device-token"
ROOM = "kitchen"
BLOCKED_ID = "tablet-blocked-01"
OTHER_ID = "phone-allowed-01"
BLOCK_MESSAGE = "Kids tablet isn't allowed to edit the queue in kitchen (bedtime)"

REPLACERS = [
    ("/api/music/play", {"room_id": ROOM, "query": "something"}, "/v1/admin/music/play"),
    ("/api/music/play-track", {"room_id": ROOM, "track_id": 10}, "/v1/admin/music/play-track"),
    ("/api/music/play-tracks", {"room_id": ROOM, "track_ids": [12, 11]}, "/v1/admin/music/play-tracks"),
    (
        "/api/music/play-playlist",
        {"room_id": ROOM, "playlist_id": 3},
        "/v1/admin/music/play-playlist",
    ),
]


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture
def one_device_is_blocked_in_the_kitchen(monkeypatch):
    @asynccontextmanager
    async def _no_db():
        yield None

    async def _name(_s, device_id):
        return "Kids tablet" if device_id == BLOCKED_ID else None

    async def _block(_s, device_id, device_name, room_id):
        if device_id == BLOCKED_ID and room_id == ROOM:
            return {"id": 1, "device_id": BLOCKED_ID, "device_name": device_name,
                    "room_id": ROOM, "note": "bedtime"}
        return None

    monkeypatch.setattr(queue_api, "session_scope", _no_db)
    monkeypatch.setattr(queue_api, "_device_name_for", _name)
    monkeypatch.setattr(queue_api, "_block_for", _block)


@pytest.fixture
def core(monkeypatch):
    calls: list[str] = []

    async def _post(path, body=None, headers=None):
        calls.append(path)
        return 200, {"ok": True}

    monkeypatch.setattr(music_api, "post_admin", _post)
    return calls


def _client(cookies=None, headers=None) -> TestClient:
    c = TestClient(
        app,
        headers={"X-Requested-With": "domovoi-tests", HEADER: DEVICE_TOKEN, **(headers or {})},
    )
    for k, v in (cookies or {}).items():
        c.cookies.set(k, v)
    return c


@pytest.mark.parametrize(("path", "body", "_core"), REPLACERS)
@pytest.mark.parametrize("how", ["cookie", "header", "body"])
def test_a_blocked_device_cannot_replace_the_queue(
    claimed, one_device_is_blocked_in_the_kitchen, core, path, body, _core, how
):
    cookies = {DEVICE_ID_COOKIE: BLOCKED_ID} if how == "cookie" else None
    headers = {"X-Device-Id": BLOCKED_ID} if how == "header" else None
    sent = {**body, "device_id": BLOCKED_ID} if how == "body" else body
    r = _client(cookies=cookies, headers=headers).post(path, json=sent)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == BLOCK_MESSAGE
    assert core == [], "the room's queue was replaced anyway"


@pytest.mark.parametrize(("path", "body", "core_path"), REPLACERS)
def test_the_block_is_per_room_and_per_device(
    claimed, one_device_is_blocked_in_the_kitchen, core, path, body, core_path
):
    c = _client(cookies={DEVICE_ID_COOKIE: BLOCKED_ID})
    r = c.post(path, json={**body, "room_id": "den"})
    assert r.status_code == 200, r.text
    r = _client(cookies={DEVICE_ID_COOKIE: OTHER_ID}).post(path, json=body)
    assert r.status_code == 200, r.text
    assert core == [core_path, core_path]


def test_the_device_id_field_never_reaches_the_core(claimed, core, monkeypatch):
    seen: list[dict] = []

    async def _post(path, body=None, headers=None):
        seen.append(body or {})
        return 200, {"ok": True}

    monkeypatch.setattr(music_api, "post_admin", _post)

    async def _ok(device_id, room_id):
        return device_id, None

    monkeypatch.setattr(queue_api, "_assert_can_edit", _ok)
    r = _client().post(
        "/api/music/play-tracks",
        json={"room_id": ROOM, "track_ids": [1], "device_id": OTHER_ID},
    )
    assert r.status_code == 200, r.text
    assert seen == [{"room_id": ROOM, "track_ids": [1]}]


@pytest.mark.parametrize("verb", ["pause", "resume", "stop", "skip"])
def test_the_transport_verbs_are_not_queue_edits(
    claimed, one_device_is_blocked_in_the_kitchen, core, verb
):
    """Decided and documented (API_REFERENCE §3.5a): the block covers what
    a room will play, not the playhead. The kiosk's transport row is these
    four verbs; the core route behind them takes the household token."""
    r = _client(cookies={DEVICE_ID_COOKIE: BLOCKED_ID}).post(f"/api/music/{verb}/{ROOM}")
    assert r.status_code == 200, r.text
    assert core == [f"/v1/admin/music/{verb}/{ROOM}"]
