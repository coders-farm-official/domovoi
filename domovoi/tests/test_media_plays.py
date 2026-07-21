"""Tests for the media_plays history: the record_media_play helper
(insert + consecutive-dedup) and the Recently-played read endpoint's
in_library EXISTS logic."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.handlers.shared.play_history import record_media_play
from domovoi.tests.conftest import requires_db


async def _count(db_session) -> int:
    return (
        await db_session.execute(text("SELECT count(*) FROM media_plays"))
    ).scalar_one()


@requires_db
@pytest.mark.asyncio
async def test_record_media_play_inserts_a_row(db_session) -> None:
    await record_media_play(
        db_session,
        room_id="kitchen",
        source="acmecast",
        title="Creep",
        channel="Radiohead",
        video_id="abc12345678",
        url="https://youtu.be/abc12345678",
        stream_url="https://stream/abc",
    )
    row = (
        await db_session.execute(
            text(
                "SELECT room_id, source, title, channel, video_id, url "
                "FROM media_plays"
            )
        )
    ).first()
    assert row is not None
    assert row.room_id == "kitchen"
    assert row.source == "acmecast"
    assert row.title == "Creep"
    assert row.channel == "Radiohead"
    assert row.video_id == "abc12345678"


@requires_db
@pytest.mark.asyncio
async def test_consecutive_same_video_is_deduped(db_session) -> None:
    # Same room + same identity twice in a row (a "next" re-stream or a
    # double-clicked play) records only once.
    for _ in range(2):
        await record_media_play(
            db_session,
            room_id="kitchen",
            source="acmecast",
            title="Creep",
            video_id="abc12345678",
            url="https://youtu.be/abc12345678",
        )
    assert await _count(db_session) == 1


@requires_db
@pytest.mark.asyncio
async def test_distinct_videos_both_recorded(db_session) -> None:
    await record_media_play(
        db_session, room_id="kitchen", source="acmecast",
        title="Creep", video_id="abc12345678",
    )
    await record_media_play(
        db_session, room_id="kitchen", source="acmecast",
        title="No Surprises", video_id="def91357246",
    )
    assert await _count(db_session) == 2


@requires_db
@pytest.mark.asyncio
async def test_same_video_different_room_both_recorded(db_session) -> None:
    # Dedup is per-room — the same video in two rooms is two plays.
    await record_media_play(
        db_session, room_id="kitchen", source="acmecast",
        title="Creep", video_id="abc12345678",
    )
    await record_media_play(
        db_session, room_id="garage", source="acmecast",
        title="Creep", video_id="abc12345678",
    )
    assert await _count(db_session) == 2


@requires_db
@pytest.mark.asyncio
async def test_library_play_records_with_track_id(db_session) -> None:
    tid = (
        await db_session.execute(
            text(
                "INSERT INTO library_tracks (file_path, title, artist) "
                "VALUES ('/music/song.mp3', 'Song', 'Artist') RETURNING id"
            )
        )
    ).scalar_one()
    await record_media_play(
        db_session, room_id="kitchen", source="library",
        title="Song", artist="Artist", library_track_id=tid,
    )
    row = (
        await db_session.execute(
            text("SELECT source, library_track_id FROM media_plays")
        )
    ).first()
    assert row.source == "library"
    assert row.library_track_id == tid


@requires_db
@pytest.mark.asyncio
async def test_in_library_exists_logic(db_session) -> None:
    """The read endpoint's EXISTS join flips in_library true only for a
    Provider play whose video_id is already a source='acmecast' library
    track."""
    # A provider play that HAS been added to the library.
    await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, source, source_id) "
            "VALUES ('/music/added.mp3', 'Added', 'acmecast', 'inlib00000a')"
        )
    )
    await record_media_play(
        db_session, room_id="kitchen", source="acmecast",
        title="Added", video_id="inlib00000a",
        url="https://youtu.be/inlib00000a",
    )
    # A provider play NOT in the library.
    await record_media_play(
        db_session, room_id="kitchen", source="acmecast",
        title="Fresh", video_id="fresh00000b",
        url="https://youtu.be/fresh00000b",
    )

    rows = (
        await db_session.execute(
            text(
                """
                SELECT mp.video_id,
                       EXISTS (
                           SELECT 1 FROM library_tracks lt
                           WHERE lt.source = 'acmecast'
                             AND lt.source_id = mp.video_id
                       ) AS in_library
                FROM media_plays mp
                WHERE mp.room_id = :room
                """
            ),
            {"room": "kitchen"},
        )
    ).all()
    by_vid = {r.video_id: r.in_library for r in rows}
    assert by_vid["inlib00000a"] is True
    assert by_vid["fresh00000b"] is False
