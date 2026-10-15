"""`last_seen_at` on /api/satellites (incident 2026-10-14).

With the core frozen, every card read "offline · 19d ago": the card showed
mpd_rooms.last_connected_at, which the core sets when a room's MPD row is
allocated and, since warm_known_rooms() fills the port cache at every boot,
never again. last_seen_at is when the room was really last heard from:
the newest of its health samples (V022), its last paired hello
(satellite_pairings.last_seen_at) and that MPD time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from web.backend.api import satellites as sat_api

ROOMS = ("lastseen_health", "lastseen_pairing", "lastseen_mpd_only")


async def _clean() -> None:
    async with session_scope() as s:
        for table in ("satellite_health", "satellite_pairings", "mpd_rooms"):
            await s.execute(text(f"DELETE FROM {table} WHERE room_id = ANY(:r)"), {"r": list(ROOMS)})


@requires_db
async def test_last_seen_is_when_the_room_was_last_heard(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    allocated = now - timedelta(days=19)
    paired_hello = now - timedelta(hours=2)
    sample = now - timedelta(minutes=12)

    async def no_music(_room):
        return None

    monkeypatch.setattr(sat_api, "_now_playing_for", no_music)
    monkeypatch.setattr(sat_api, "get_cached_snapshot", lambda: None)   # the core is down
    await _clean()
    try:
        async with session_scope() as s:
            for i, room in enumerate(ROOMS):
                await s.execute(text(
                    "INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name, "
                    "last_connected_at) VALUES (:r, :c, :h, :n, :t)"),
                    {"r": room, "c": 16650 + i, "h": 18050 + i, "n": f"x-{room}", "t": allocated})
            for room in ROOMS[:2]:
                await s.execute(text(
                    "INSERT INTO satellite_pairings (room_id, token_hash, last_seen_at) "
                    "VALUES (:r, :h, :t)"), {"r": room, "h": "0" * 64, "t": paired_hello})
            await s.execute(text(
                "INSERT INTO satellite_health (room_id, sample, received_at) "
                "VALUES (:r, CAST('{}' AS jsonb), :t)"), {"r": ROOMS[0], "t": sample})
        rows = {r.room_id: r for r in await sat_api.list_satellites() if r.room_id in ROOMS}
        assert set(rows) == set(ROOMS)
        assert all(r.status == "offline" for r in rows.values())
        assert rows["lastseen_health"].last_seen_at == sample
        assert rows["lastseen_pairing"].last_seen_at == paired_hello
        assert rows["lastseen_mpd_only"].last_seen_at == allocated
        # The old field is unchanged, for older clients.
        assert all(r.last_connected_at == allocated for r in rows.values())
        one = await sat_api.get_satellite("lastseen_health")
        assert one.last_seen_at == sample
    finally:
        await _clean()


def test_latest_ignores_what_is_unknown() -> None:
    a = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
    b = a + timedelta(minutes=5)
    assert sat_api._latest(None, a, b, None) == b
    assert sat_api._latest(None, None) is None
