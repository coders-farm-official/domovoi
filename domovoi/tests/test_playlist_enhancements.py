"""Playlist backlog — resume-position guards, the voice
'add after this' regex + insert-after, and the no-playlist fallback."""

from __future__ import annotations

import types

import pytest
from sqlalchemy import text

from domovoi.handlers.playlist import PlaylistHandler, _ADD_AFTER_THIS_RE
from domovoi.handlers.shared.playlist_pick import (
    persist_resume_position,
    read_resume_position,
)
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── regex ───────────────────────────────────────────────────────────────


def test_add_after_this_regex() -> None:
    m = _ADD_AFTER_THIS_RE.match("add creep after this")
    assert m and m.group(1) == "creep"
    # Must NOT poach "add this to my X playlist".
    assert _ADD_AFTER_THIS_RE.match("add this to my workout playlist") is None


# ─── resume position guards ──────────────────────────────────────────────


async def _make_playlist_with_tracks(db_session, n: int = 3) -> int:
    pid = (
        await db_session.execute(
            text("INSERT INTO playlists (name) VALUES ('rs') RETURNING id")
        )
    ).scalar_one()
    for i in range(n):
        tid = (
            await db_session.execute(
                text(
                    "INSERT INTO library_tracks (file_path, title) "
                    "VALUES (:fp, :t) RETURNING id"
                ),
                {"fp": f"/m/{i}.mp3", "t": f"T{i}"},
            )
        ).scalar_one()
        await db_session.execute(
            text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                "VALUES (:p, :t, :pos)"
            ),
            {"p": pid, "t": tid, "pos": i},
        )
    return pid


async def _resume(db_session, pid: int) -> int | None:
    return (
        await db_session.execute(
            text("SELECT resume_position FROM playlists WHERE id = :id"), {"id": pid}
        )
    ).scalar_one()


@requires_db
@pytest.mark.asyncio
async def test_resume_persists_for_ordered(db_session) -> None:
    pid = await _make_playlist_with_tracks(db_session)
    await persist_resume_position(db_session, pid, "ordered", 2)
    assert await _resume(db_session, pid) == 2
    assert await read_resume_position(db_session, pid, "ordered") == 2


@requires_db
@pytest.mark.asyncio
async def test_resume_skips_shuffle_and_favorites(db_session) -> None:
    pid = await _make_playlist_with_tracks(db_session)
    await persist_resume_position(db_session, pid, "shuffle", 2)
    assert await _resume(db_session, pid) is None          # shuffle never persists
    # Favorites (id 0) has no row; helpers must no-op, not error.
    await persist_resume_position(db_session, 0, "ordered", 5)
    assert await read_resume_position(db_session, 0, "ordered") is None
    assert await read_resume_position(db_session, pid, "shuffle") is None


# ─── voice 'add after this' ──────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_add_after_this_no_active_playlist(db_session) -> None:
    h = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)   # no app → no current_playlist
    resp = await h._add_after_this("creep", ctx, db_session)
    assert "nothing's playing from a playlist" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_add_after_this_inserts_after_current(db_session) -> None:
    pid = await _make_playlist_with_tracks(db_session, n=2)   # positions 0,1
    # A library track to add, matching by title.
    add_tid = (
        await db_session.execute(
            text(
                "INSERT INTO library_tracks (file_path, title) "
                "VALUES ('/m/new.mp3', 'New Song') RETURNING id"
            )
        )
    ).scalar_one()
    # Fake app.state.current_playlist: ordered, currently at position 0.
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            current_playlist={
                "kitchen": {
                    "playlist_id": pid, "name": "rs", "mode": "ordered",
                    "last_position": 0, "last_track_id": None,
                }
            }
        )
    )
    ctx = Context(room_id="kitchen", online=True, app=app)
    resp = await h_add(db_session, ctx, "New Song")
    assert "right after this one" in resp.text.lower()
    # The new track sits at position 1; the old position-1 track shifted to 2.
    rows = (
        await db_session.execute(
            text(
                "SELECT track_id, position FROM playlist_tracks "
                "WHERE playlist_id = :p ORDER BY position"
            ),
            {"p": pid},
        )
    ).all()
    by_pos = {int(r[1]): int(r[0]) for r in rows}
    assert by_pos[1] == add_tid


async def h_add(session, ctx, query):
    return await PlaylistHandler()._add_after_this(query, ctx, session)
