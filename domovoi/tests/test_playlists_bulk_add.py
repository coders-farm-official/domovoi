"""``POST /api/playlists/{id}/tracks`` with ``track_ids`` (Up next P5).

"Save as playlist" in the browser's queue sent one request per song. The
route now also takes the whole list: in order, after the playlist's last
song, each song once, ids the library doesn't have left out, and answers
``{added, duplicates, missing}``. The one-song body keeps its 204 / 404 /
409 answers.

The body rules are checked DB-free (they must never skip); the insert runs
against the test database (``requires_db``).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend.main import app
from web.backend.schemas import PLAYLIST_BULK_ADD_MAX, PlaylistTrackAdd

H = {"X-Requested-With": "domovoi-tests"}


# ─── DB-free: the body ─────────────────────────────────────────────────────


def test_one_song_body_still_works() -> None:
    assert PlaylistTrackAdd(track_id=3).track_id == 3


def test_many_songs_body() -> None:
    assert PlaylistTrackAdd(track_ids=[3, 1, 2]).track_ids == [3, 1, 2]


@pytest.mark.parametrize("body", [
    {},                                        # neither
    {"track_id": 1, "track_ids": [2]},         # both
    {"track_ids": []},                         # nothing to add
    {"track_ids": [1, 0]},                     # not a track id
    {"track_ids": list(range(1, PLAYLIST_BULK_ADD_MAX + 2))},   # too many
])
def test_bad_bodies_are_refused(body) -> None:
    with pytest.raises(ValidationError):
        PlaylistTrackAdd(**body)


def test_the_browser_saves_the_queue_in_one_request() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web/static/player.jsx").read_text(encoding="utf-8")
    assert "{ track_ids: trackIds }" in src
    # No per-song loop left behind.
    assert "for (const tid of trackIds)" not in src


# ─── DB-backed ─────────────────────────────────────────────────────────────


async def _seed(n: int) -> tuple[int, list[int]]:
    from web.backend.db import session_scope

    async with session_scope() as s:
        pid = (
            await s.execute(text("INSERT INTO playlists (name) VALUES ('bulk-add-test') RETURNING id"))
        ).scalar_one()
        tids = []
        for i in range(n):
            tids.append((
                await s.execute(
                    text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t) RETURNING id"),
                    {"fp": f"/bulk-add-test/{i}.mp3", "t": f"B{i}"},
                )
            ).scalar_one())
        # The playlist already holds the first song.
        await s.execute(
            text("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, 0)"),
            {"p": pid, "t": tids[0]},
        )
    return pid, tids


async def _order(pid: int) -> list[int]:
    from web.backend.db import session_scope

    async with session_scope() as s:
        rows = (await s.execute(
            text("SELECT track_id FROM playlist_tracks WHERE playlist_id = :p ORDER BY position"),
            {"p": pid},
        )).all()
    return [int(r[0]) for r in rows]


async def _cleanup(pid: int, tids: list[int]) -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("DELETE FROM playlists WHERE id = :p"), {"p": pid})
        await s.execute(text("DELETE FROM library_tracks WHERE id = ANY(:t)"), {"t": tids})


@requires_db
def test_bulk_add_appends_in_order_once_each() -> None:
    pid, tids = asyncio.run(_seed(4))
    try:
        missing = max(tids) + 10_000
        body = {"track_ids": [tids[3], tids[0], tids[1], missing, tids[3], tids[2]]}
        with TestClient(app, headers=H) as c:
            r = c.post(f"/api/playlists/{pid}/tracks", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"added": 3, "duplicates": 2, "missing": 1}
        assert asyncio.run(_order(pid)) == [tids[0], tids[3], tids[1], tids[2]]
    finally:
        asyncio.run(_cleanup(pid, tids))


@requires_db
def test_one_song_body_keeps_its_answers() -> None:
    pid, tids = asyncio.run(_seed(2))
    try:
        with TestClient(app, headers=H) as c:
            assert c.post(f"/api/playlists/{pid}/tracks", json={"track_id": tids[1]}).status_code == 204
            assert c.post(f"/api/playlists/{pid}/tracks", json={"track_id": tids[1]}).status_code == 409
            assert c.post(f"/api/playlists/{pid}/tracks", json={"track_ids": [tids[1]]}).json() == {
                "added": 0, "duplicates": 1, "missing": 0}
            assert c.post("/api/playlists/0/tracks", json={"track_ids": [tids[1]]}).status_code == 400
            assert c.post(f"/api/playlists/{pid + 100000}/tracks",
                          json={"track_ids": [tids[1]]}).status_code == 404
        assert asyncio.run(_order(pid)) == [tids[0], tids[1]]
    finally:
        asyncio.run(_cleanup(pid, tids))
