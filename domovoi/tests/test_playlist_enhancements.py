"""Playlist backlog — resume-position guards, the voice
'add after this' regex + insert into the room's queue (never the playlist)."""

from __future__ import annotations

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
# Since 2026-11-12 it edits the room's QUEUE (a copy of any playlist it came
# from), never a saved playlist.


def _in_music(name: str) -> str:
    from pathlib import Path

    from domovoi.config import settings

    return str(Path(settings.music_dir).expanduser() / name)


@requires_db
@pytest.mark.asyncio
async def test_add_after_this_nothing_playing(db_session, monkeypatch) -> None:
    from domovoi.clients import mpd as mpd_module
    from domovoi.clients.mpd import MPDStubClient

    monkeypatch.setitem(mpd_module._clients, "kitchen", MPDStubClient())
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES (:f, 'Creep')"),
        {"f": _in_music("creep.mp3")})
    await db_session.commit()
    resp = await h_add(db_session, Context(room_id="kitchen", online=True), "creep")
    assert "nothing's playing in here" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_add_after_this_inserts_into_the_queue_not_the_playlist(db_session, monkeypatch) -> None:
    from domovoi.clients import mpd as mpd_module
    from domovoi.clients.mpd import MPDStubClient

    pid = await _make_playlist_with_tracks(db_session, n=2)   # positions 0,1
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES (:f, 'New Song')"),
        {"f": _in_music("new.mp3")})
    await db_session.commit()
    before = (await db_session.execute(text(
        "SELECT track_id, position FROM playlist_tracks WHERE playlist_id = :p ORDER BY position"),
        {"p": pid})).all()
    stub = MPDStubClient()
    monkeypatch.setitem(mpd_module._clients, "kitchen", stub)
    await stub.prepare_files(["a.mp3", "b.mp3", "c.mp3"])
    await stub.resume()
    ctx = Context(room_id="kitchen", online=True)
    resp = await h_add(db_session, ctx, "New Song")
    assert "right after this one" in resp.text.lower()
    assert [e["file"] for e in await stub.queue_list()] == ["a.mp3", "new.mp3", "b.mp3", "c.mp3"]
    after = (await db_session.execute(text(
        "SELECT track_id, position FROM playlist_tracks WHERE playlist_id = :p ORDER BY position"),
        {"p": pid})).all()
    assert after == before


@requires_db
@pytest.mark.asyncio
async def test_add_after_this_from_a_phone(db_session) -> None:
    ctx = Context(room_id="kitchen", online=True, device_kind="phone")
    resp = await h_add(db_session, ctx, "New Song")
    assert "queue screen" in resp.text


async def h_add(session, ctx, query):
    return await PlaylistHandler()._add_after_this(query, ctx, session)
