from __future__ import annotations

import pytest
from sqlalchemy import text as sql_text

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers.playlist import (
    PlaylistHandler,
    _ADD_TO_PLAYLIST_RE,
    _MAKE_PLAYLIST_RE,
    _PLAY_FAVORITES_RE,
    _PLAY_PLAYLIST_RE,
    _SHUFFLE_FAVORITES_RE,
    _SHUFFLE_PLAYLIST_RE,
)
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── Pure regex tests (no DB / MPD) ─────────────────────────────────────


def test_play_favorites_regex() -> None:
    assert _PLAY_FAVORITES_RE.match("play my favorites")
    assert not _PLAY_FAVORITES_RE.match("play favorites")
    assert not _PLAY_FAVORITES_RE.match("play my favorite song")


def test_shuffle_favorites_regex() -> None:
    assert _SHUFFLE_FAVORITES_RE.match("shuffle my favorites")
    assert not _SHUFFLE_FAVORITES_RE.match("shuffle favorites")


def test_play_playlist_regex() -> None:
    m = _PLAY_PLAYLIST_RE.match("play the workout playlist")
    assert m and m.group(1) == "workout"
    m = _PLAY_PLAYLIST_RE.match("play my workout playlist")
    assert m and m.group(1) == "workout"
    m = _PLAY_PLAYLIST_RE.match("play workout playlist")
    assert m and m.group(1) == "workout"


def test_shuffle_playlist_regex() -> None:
    m = _SHUFFLE_PLAYLIST_RE.match("shuffle the workout playlist")
    assert m and m.group(1) == "workout"


def test_make_playlist_regex() -> None:
    m = _MAKE_PLAYLIST_RE.match("make a new playlist called chill")
    assert m and m.group(1) == "chill"
    m = _MAKE_PLAYLIST_RE.match("make playlist named chill")
    assert m and m.group(1) == "chill"


def test_add_to_playlist_regex() -> None:
    m = _ADD_TO_PLAYLIST_RE.match("add this to my workout playlist")
    assert m and m.group(1) == "workout"
    m = _ADD_TO_PLAYLIST_RE.match("add the current song to my workout playlist")
    assert m and m.group(1) == "workout"
    m = _ADD_TO_PLAYLIST_RE.match("add it to the workout playlist")
    assert m and m.group(1) == "workout"


def test_play_playlist_regex_does_not_swallow_music() -> None:
    """`play X playlist` requires the trailing ' playlist' suffix —
    plain 'play creep' must not match (that's MusicHandler's territory)."""
    assert not _PLAY_PLAYLIST_RE.match("play creep")
    assert not _PLAY_PLAYLIST_RE.match("play creep by radiohead")


# ─── Happy-path play ─────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_play_playlist_ordered_picks_first_position(db_session) -> None:
    """`play the X playlist` (ordered mode) starts at the lowest
    position. Sets app.state.current_playlist with mode=ordered."""
    mpd = MPDStubClient()
    mpd_module._clients = {"kitchen": mpd}

    # Seed a playlist with two tracks at positions 1 and 0 (out of
    # insertion order on purpose so we can verify ORDER BY position
    # ASC, not insertion order, drives the pick).
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (id, file_path, title, artist) VALUES "
            "(100, '/music/A.mp3', 'A-track', 'A'), "
            "(200, '/music/B.mp3', 'B-track', 'B')"
        )
    )
    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (50, 'workout')")
    )
    await db_session.execute(
        sql_text(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES "
            "(50, 100, 1), (50, 200, 0)"
        )
    )
    await db_session.commit()

    class _FakeApp:
        class state:
            current_playlist: dict = {}

    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._play_by_name(
        "workout", ctx, db_session, mode="ordered"
    )
    assert response.music_action == "start"
    entry = _FakeApp.state.current_playlist["kitchen"]
    # Position 0 (track id 200, "B-track") should be picked first.
    assert entry["mode"] == "ordered"
    assert entry["last_track_id"] == 200
    assert entry["last_position"] == 0
    assert entry["playlist_id"] == 50


@requires_db
@pytest.mark.asyncio
async def test_play_playlist_shuffle_marks_state_as_shuffle(db_session) -> None:
    mpd = MPDStubClient()
    mpd_module._clients = {"kitchen": mpd}

    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (id, file_path, title, artist) VALUES "
            "(300, '/music/C.mp3', 'C', 'C')"
        )
    )
    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (60, 'chill')")
    )
    await db_session.execute(
        sql_text(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES "
            "(60, 300, 0)"
        )
    )
    await db_session.commit()

    class _FakeApp:
        class state:
            current_playlist: dict = {}

    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._play_by_name(
        "chill", ctx, db_session, mode="shuffle"
    )
    assert response.music_action == "start"
    entry = _FakeApp.state.current_playlist["kitchen"]
    assert entry["mode"] == "shuffle"
    assert entry["last_track_id"] == 300


@requires_db
@pytest.mark.asyncio
async def test_play_playlist_unknown_name_polite_error(db_session) -> None:
    mpd_module._clients = {"kitchen": MPDStubClient()}
    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)
    response = await handler._play_by_name(
        "nonexistent", ctx, db_session, mode="ordered"
    )
    assert "don't have a playlist" in response.text.lower()
    assert response.music_action is None


@requires_db
@pytest.mark.asyncio
async def test_play_empty_playlist_polite_error(db_session) -> None:
    """An empty playlist (no playlist_tracks rows) should respond
    politely instead of MPD-erroring."""
    mpd_module._clients = {"kitchen": MPDStubClient()}
    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (70, 'empty')")
    )
    await db_session.commit()

    class _FakeApp:
        class state:
            current_playlist: dict = {}

    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._play_by_name(
        "empty", ctx, db_session, mode="ordered"
    )
    assert "empty" in response.text.lower()


# ─── Voice create + auto-create ──────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_make_playlist_creates_row(db_session) -> None:
    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)
    response = await handler._create_playlist("Driving", ctx, db_session)
    assert "created" in response.text.lower()
    row = (
        await db_session.execute(
            sql_text("SELECT name FROM playlists WHERE LOWER(name) = LOWER(:n)"),
            {"n": "Driving"},
        )
    ).first()
    assert row is not None and row[0] == "Driving"


@requires_db
@pytest.mark.asyncio
async def test_make_playlist_rejects_case_insensitive_dup(db_session) -> None:
    await db_session.execute(
        sql_text("INSERT INTO playlists (name) VALUES ('Workout')")
    )
    await db_session.commit()
    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)
    response = await handler._create_playlist("workout", ctx, db_session)
    assert "already" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_add_to_unknown_playlist_auto_creates(db_session) -> None:
    """'Add this to my X playlist' creates X if missing, then adds
    the now-playing track to it. Verifies the auto-create path."""
    mpd = MPDStubClient()
    # Stub MPD into "playing a local track" state. The stub's
    # play_filename writes a song dict whose `file` is the stub URL,
    # not the library file_path; we work around by also seeding a
    # library_tracks row whose file_path matches what
    # library_path_for_mpd_file will produce.
    await mpd.play_filename("Artist/One.mp3")
    mpd_module._clients = {"kitchen": mpd}

    # Use the core's music_dir setting so the expected_path
    # exactly matches the indexer's str(Path(music_dir) / mpd_file)
    # output.
    from pathlib import Path

    from domovoi.config import settings as core_settings

    expected = str(Path(core_settings.music_dir).expanduser() / "Artist/One.mp3")
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (id, file_path, title, artist) VALUES "
            "(900, :fp, 'One', 'Artist')"
        ),
        {"fp": expected},
    )
    # Make MPD's currentsong report the same forward-slash relative
    # path so the resolver hits the seeded row.
    mpd._song = {"file": "Artist/One.mp3", "title": "One", "artist": "Artist"}
    await db_session.commit()

    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)
    response = await handler._add_current_to_named(
        "Cycling", ctx, db_session,
    )
    assert "added" in response.text.lower()
    # Playlist exists.
    pl = (
        await db_session.execute(
            sql_text("SELECT id FROM playlists WHERE LOWER(name) = LOWER(:n)"),
            {"n": "Cycling"},
        )
    ).first()
    assert pl is not None
    pid = int(pl[0])
    # Track is in it at position 0.
    pt = (
        await db_session.execute(
            sql_text(
                "SELECT track_id, position FROM playlist_tracks WHERE playlist_id = :pid"
            ),
            {"pid": pid},
        )
    ).first()
    assert pt is not None
    assert pt[0] == 900 and pt[1] == 0


@requires_db
@pytest.mark.asyncio
async def test_add_to_playlist_refuses_http_source(db_session) -> None:
    """When MPD is streaming HTTP (an external stream), the add path
    refuses with a "add to library first" hint instead of failing
    the path-resolver."""
    mpd = MPDStubClient()
    # play_url stub gives us an http:// file.
    await mpd.play_url("https://stream.example/stream", title="WBEZ")
    mpd_module._clients = {"kitchen": mpd}

    handler = PlaylistHandler()
    ctx = Context(room_id="kitchen", online=True)
    response = await handler._add_current_to_named(
        "anything", ctx, db_session,
    )
    assert "library first" in response.text.lower()


# ─── Multi-match disambiguation ─────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_play_playlist_multi_match_parks_confirmation(db_session) -> None:
    """Two playlists whose names both contain the substring should
    trigger pending_confirmation (the standard disambiguation flow)."""
    from uuid import uuid4

    from domovoi.db.repositories import SessionRepository

    await db_session.execute(
        sql_text(
            "INSERT INTO playlists (id, name) VALUES "
            "(80, 'Workout Morning'), (81, 'Workout Evening')"
        )
    )
    await db_session.commit()

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    handler = PlaylistHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._play_by_name(
        "workout", ctx, db_session, mode="ordered"
    )
    assert response.expect_followup
    assert "matching" in response.text.lower()
    ctx_data = (
        await SessionRepository(db_session).get_context(sid)
    ) or {}
    pending = ctx_data.get("pending_confirmation")
    assert pending is not None
    assert pending["handler"] == "playlist"
    assert pending["kind"] == "core.playlist_choice"
    assert len(pending["candidates"]) == 2


# ─── Fetch-missing-track → media acquisition seam (design §4.8/§10.2) ────


def _playing_playlist_app(playlist_id: int = 70, name: str = "roadtrip"):
    class _FakeApp:
        class state:
            current_playlist = {
                "kitchen": {
                    "playlist_id": playlist_id,
                    "name": name,
                    "mode": "ordered",
                    "last_track_id": 1,
                    "last_position": 0,
                    "last_file_path": "X.mp3",
                }
            }

    return _FakeApp


@requires_db
@pytest.mark.asyncio
async def test_add_after_missing_track_enqueues_acquisition_graceful_absence(
    db_session,
) -> None:
    """'add <unknown song> after this' with no library hit becomes a
    'query'-kind media_acquisitions row soft-attached to the playlist,
    and — with NO acquisition fulfiller registered — the reply is the
    canonical graceful-absence copy (locked 6)."""
    from domovoi.capabilities import CAPABILITIES, MEDIA_ACQUISITION_FULFILLER

    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (70, 'roadtrip')")
    )
    await db_session.commit()

    assert CAPABILITIES.absent(MEDIA_ACQUISITION_FULFILLER)
    handler = PlaylistHandler()
    ctx = Context(
        session_id=None, room_id="kitchen", online=True,
        app=_playing_playlist_app(),
    )
    response = await handler._add_after_this("uncatalogued banger", ctx, db_session)
    await db_session.commit()

    assert "no media provider is installed" in response.text.lower()
    row = (
        await db_session.execute(
            sql_text(
                "SELECT kind, text, requested_by, attach_to_playlist_id, status "
                "FROM media_acquisitions"
            )
        )
    ).first()
    assert row is not None
    assert row[0] == "query"
    assert row[1] == "uncatalogued banger"
    assert row[2] == "voice:playlist"
    assert row[3] == 70
    assert row[4] == "pending"


@requires_db
@pytest.mark.asyncio
async def test_add_after_missing_track_promises_download_when_fulfiller_present(
    db_session,
) -> None:
    """Same flow with a registered acquisition fulfiller: the row is
    identical (the queue is provider-agnostic) but the voice reply
    promises the download instead of apologizing."""
    from domovoi.capabilities import CAPABILITIES, MEDIA_ACQUISITION_FULFILLER

    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (71, 'roadtrip')")
    )
    await db_session.commit()

    class _FakeFulfiller:
        slug = "extfetch"

    CAPABILITIES.register(
        MEDIA_ACQUISITION_FULFILLER, _FakeFulfiller(), slug="extfetch"
    )
    try:
        handler = PlaylistHandler()
        ctx = Context(
            session_id=None, room_id="kitchen", online=True,
            app=_playing_playlist_app(playlist_id=71),
        )
        response = await handler._add_after_this(
            "uncatalogued banger", ctx, db_session
        )
        await db_session.commit()
    finally:
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug="extfetch")

    assert "queued it to download" in response.text.lower()
    assert "roadtrip" in response.text.lower()
    count = (
        await db_session.execute(
            sql_text("SELECT COUNT(*) FROM media_acquisitions WHERE attach_to_playlist_id = 71")
        )
    ).scalar()
    assert count == 1
