"""Tests for the /v1/admin/* surface used by the web management UI.

Covers each admin endpoint's happy path and the error shapes the web
backend's `bridge_response` relies on (404 for unknown room, 503 for
no satellites at all). Library reindex/enrich are covered by their
own worker tests; here we just verify the queued ack returns 200.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.db.session import engine
from domovoi.main import app
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests():
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield


@requires_db
@pytest.mark.asyncio
async def test_admin_snapshot_returns_state_dicts() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        # Seed app state to verify each field surfaces.
        app.state.active_sessions = {"kitchen": object(), "garage": object()}
        app.state.resumable_music = {"kitchen": "http://my-domovoi:8001"}
        app.state.wifi_status = {
            "kitchen": {"rx_mbits": 39.0, "tx_mbits": 72.2, "ssid": "Kamber Wifi 2.0"}
        }
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/admin/snapshot")
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["active_rooms"]) == ["garage", "kitchen"]
        assert body["resumable_music"]["kitchen"] == "http://my-domovoi:8001"
        assert body["wifi_status"]["kitchen"]["ssid"] == "Kamber Wifi 2.0"


@requires_db
@pytest.mark.asyncio
async def test_admin_announce_returns_503_with_no_sessions() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.active_sessions = {}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/announce", json={"message": "dinner is ready"}
            )
        assert r.status_code == 503
        assert "no satellites" in r.json()["detail"].lower()


@requires_db
@pytest.mark.asyncio
async def test_admin_announce_404_for_unknown_room() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        # At least one session so we don't hit the 503 path; targeting
        # a different room should still 404.
        fake = type("FakeSession", (), {"room_id": "kitchen", "announce": AsyncMock()})()
        app.state.active_sessions = {"kitchen": fake}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/announce",
                json={"room_id": "basement", "message": "hello"},
            )
        assert r.status_code == 404
        assert "basement" in r.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_admin_announce_targeted_calls_announce_once() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        kitchen = type(
            "FakeSession", (), {"room_id": "kitchen", "announce": AsyncMock()}
        )()
        garage = type(
            "FakeSession", (), {"room_id": "garage", "announce": AsyncMock()}
        )()
        app.state.active_sessions = {"kitchen": kitchen, "garage": garage}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/announce",
                json={"room_id": "kitchen", "message": "trash day"},
            )
        assert r.status_code == 200
        assert r.json()["announced_to"] == ["kitchen"]
        kitchen.announce.assert_awaited_once_with("trash day")
        garage.announce.assert_not_awaited()


@requires_db
@pytest.mark.asyncio
async def test_admin_announce_broadcast_hits_all_sessions() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        kitchen = type(
            "FakeSession", (), {"room_id": "kitchen", "announce": AsyncMock()}
        )()
        garage = type(
            "FakeSession", (), {"room_id": "garage", "announce": AsyncMock()}
        )()
        app.state.active_sessions = {"kitchen": kitchen, "garage": garage}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/announce",
                json={"room_id": None, "message": "dinner"},
            )
        assert r.status_code == 200
        assert sorted(r.json()["announced_to"]) == ["garage", "kitchen"]
        kitchen.announce.assert_awaited_once_with("dinner")
        garage.announce.assert_awaited_once_with("dinner")


@requires_db
@pytest.mark.asyncio
async def test_admin_music_action_rejects_unknown_action() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/music/unknown/kitchen")
        assert r.status_code == 400
        assert "invalid action" in r.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_admin_music_pause_routes_through_router() -> None:
    """Pause re-enters the regular router → MusicHandler.

    Stub mode auto-creates an MPD stub for the room, so pause goes
    through ``MusicHandler._pause`` → ``MPDStubClient.pause`` and
    returns the canned spoken response. Verifies the round-trip
    structure rather than asserting on exact text — Music handler
    phrasing is its own test surface.
    """
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/music/pause/kitchen")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "matched_handler" in body
        assert "text" in body and isinstance(body["text"], str)


@requires_db
@pytest.mark.asyncio
async def test_admin_music_play_tracks_casts_ordered_queue() -> None:
    """play-tracks loads an arbitrary ordered id list into the room's MPD
    queue, preserving request order, and logs one intents_log row (no
    conversation_log). Uses the stub MPD so no docker/MPD is needed."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.clients.mpd import MPDStubClient

    mpd_module._clients = {"kitchen": MPDStubClient()}

    # Seed three library rows; cast them in a non-id order.
    async with engine.begin() as conn:
        ids = []
        for title in ("Alpha", "Bravo", "Charlie"):
            rid = (
                await conn.execute(
                    text(
                        "INSERT INTO library_tracks (file_path, title, added_via) "
                        "VALUES (:fp, :t, 'manual') RETURNING id"
                    ),
                    {"fp": f"/music/{title}.mp3", "t": title},
                )
            ).scalar_one()
            ids.append(int(rid))

    ordered = [ids[2], ids[0], ids[1]]  # Charlie, Alpha, Bravo
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/music/play-tracks",
                json={"room_id": "kitchen", "track_ids": ordered},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["played"] is True
    assert body["queued"] == 3
    assert body["requested"] == 3

    async with engine.begin() as conn:
        log_rows = (
            await conn.execute(
                text(
                    "SELECT transcript, matched_handler FROM intents_log "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).all()
        assert log_rows
        assert "cast" in log_rows[0][0].lower()
        assert log_rows[0][1] == "music"
        # UI-initiated cast must NOT write a conversation_log row.
        conv = (
            await conn.execute(text("SELECT COUNT(*) FROM conversation_log"))
        ).scalar_one()
        assert conv == 0


@requires_db
@pytest.mark.asyncio
async def test_admin_music_play_tracks_404_for_unknown_ids() -> None:
    from domovoi.clients import mpd as mpd_module
    from domovoi.clients.mpd import MPDStubClient

    mpd_module._clients = {"kitchen": MPDStubClient()}
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/music/play-tracks",
                json={"room_id": "kitchen", "track_ids": [999998, 999999]},
            )
    assert r.status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_admin_music_play_synthesizes_intent() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/music/play",
                json={"room_id": "kitchen", "query": "creep by radiohead"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matched_handler"] == "music"
        # Going through the router means it logs an intent.
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT room_id, transcript FROM intents_log ORDER BY id DESC LIMIT 1"
                )
            )
        ).all()
        assert rows
        assert rows[0][0] == "kitchen"
        assert "creep" in rows[0][1].lower()


@requires_db
@pytest.mark.asyncio
async def test_admin_library_reindex_returns_queued_ack() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/library/reindex")
        assert r.status_code == 200
        assert r.json() == {"queued": True, "worker": "library_indexer"}


@requires_db
@pytest.mark.asyncio
async def test_admin_library_enrich_returns_queued_ack() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/library/enrich")
        assert r.status_code == 200
        assert r.json() == {"queued": True, "worker": "library_enricher"}
