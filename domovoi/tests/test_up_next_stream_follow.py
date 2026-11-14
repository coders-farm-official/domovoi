"""Up next after a stream (owner, 2026-11-12; UP-NEXT-QUEUE-PLAN §5).

A song a streaming-search provider streams into a room
(``sdk.playback.play_url``) arms the room's session as it is queued
(:func:`domovoi.up_next.rooms.follow_stream`); the extender's look adds the
first batch after the stream once it was heard and is nearly over, so the
room's player simply moves on into the library:

* the stream's artist matched in the library seeds a ``continue`` mix over
  their songs ("more like <artist>"); the stream's own song in the library
  is never picked, and no pick is ever a stream;
* an artist the library doesn't have: the listener's taste (``library``
  with no seed);
* nothing follows with Autoplay off, after a stop, for a stream that never
  reached the player or died at once, nor for a radio station's stream
  (not a streaming-search provider);
* a room paused by a turn holds the picks until it plays again;
* the voice path names whose taste follows (``listening_as``).

Uses the stub player and the database of test_up_next_rooms.py
(``requires_db``: a lane with V053 applied).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from domovoi.capabilities import CAPABILITIES, STREAMING_SEARCH_PROVIDER
from domovoi.db.session import engine
from domovoi.now_playing import NOW_PLAYING
from domovoi.sdk.playback import PlaybackAPI
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_up_next_rooms import (  # noqa: F401 — fixtures
    MUSIC,
    ROOM,
    _active,
    _ids,
    _look,
    _picks,
    _seed,
    clean,
    room,
)
from domovoi.up_next import rooms
from domovoi.up_next.session import SeedRef

PROVIDER = "upn-streamer"
RADIO = "upn-radio"
URL = "http://192.168.1.20:9000/stream/abc.m4a"


@pytest.fixture
def streamer():
    """A streaming-search provider plugin (its slug owns a now-playing
    source and the capability), and a radio-like plugin that is not one."""
    NOW_PLAYING.register_source(PROVIDER, owner=PROVIDER)
    NOW_PLAYING.register_source(RADIO, owner=RADIO)
    CAPABILITIES.register(STREAMING_SEARCH_PROVIDER, object(), slug=PROVIDER)
    yield PlaybackAPI(PROVIDER)
    CAPABILITIES.unregister(STREAMING_SEARCH_PROVIDER, slug=PROVIDER)
    NOW_PLAYING.unregister_owner(PROVIDER)
    NOW_PLAYING.unregister_owner(RADIO)


async def _stream(api: PlaybackAPI, stub, *, title: str = "Nina - Midnight", artist: str | None = "Nina",
                  duration: float | None = 240.0, source: str = PROVIDER):
    resp = await api.play_url(ROOM, URL, title=title, artist=artist, source=source)
    if resp.music_action == "start":
        await stub.resume()                     # the music_ready handshake
        if duration is not None:
            stub._song["duration"] = duration
    return resp


async def _at(stub, sec: float) -> dict[str, Any]:
    await stub.seek_to(sec)
    return await _look(stub)


async def _files(stub) -> list[str]:
    return [str(e["file"]) for e in await stub.queue_list()]


async def _nina_midnight() -> int:
    """The stream's own song, also in the library."""
    async with engine.begin() as conn:
        return (await conn.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, year, genre, genre_keys) "
            "VALUES (:f, 'Midnight (Remastered)', 'Nina', 'Nina Album', 200, 1966, 'soul', ARRAY['soul']) "
            "RETURNING id"), {"f": str(MUSIC / "Nina/midnight.flac")})).scalar_one()


@requires_db
async def test_a_stream_ends_into_picks_like_its_artist(clean, room, streamer) -> None:
    d = await _seed()
    own = await _nina_midnight()
    resp = await _stream(streamer, room)
    assert resp.music_action == "start"
    st = await _active()
    assert st is not None and st.sess.mode == "continue" and st.list_name == "Nina"
    assert own in st.sess.excluded_tracks
    assert (await _look(room))["queued"] == 0              # just started
    assert (await _at(room, 30))["queued"] == 0            # heard, 3½ min left
    did = await _at(room, 200)                             # 40 s left
    assert did["queued"] > 0 and did["ended"] is None
    files = await _files(room)
    assert files[0] == URL and not any("://" in f for f in files[1:])
    picks = await _picks(st.sess.id)
    nina = {t for i, t in enumerate(d["ids"]) if i % 6 == 0}
    assert any(p["track_id"] in nina for p in picks)
    assert own not in {p["track_id"] for p in picks}
    # The queue's head reads "more like Nina" (the clients' upNextBasis).
    view = await rooms.queue_view(ROOM, [dict(song_id=e["id"], file=e["file"]) for e in await room.queue_list()])
    assert view["up_next"]["mode"] == "continue"
    assert view["up_next"]["seed"]["list"] == {"kind": "ids", "name": "Nina"}
    # The stream ends: the player moves on into the library.
    await room.next()
    assert (await room.playback_snapshot())["state"] == "play"
    assert (await _look(room))["ended"] is None


@requires_db
async def test_the_artist_is_matched_from_an_artist_dash_song_title(clean, room, streamer) -> None:
    await _seed()
    await _stream(streamer, room, title="Otis - Some Song (Live)", artist="Otis Fan Uploads")
    st = await _active()
    assert st.sess.mode == "continue" and st.list_name == "Otis"


@requires_db
async def test_an_artist_the_library_lacks_follows_the_listeners_taste(clean, room, streamer) -> None:
    d = await _seed()
    with rooms.listening_as(d["alex"]):
        await _stream(streamer, room, title="Far Away Song", artist="Nobody We Know", duration=None)
    st = await _active()
    assert st.sess.mode == "library" and st.sess.seed.track_id is None and st.sess.seed.kind is None
    assert st.sess.person_id == d["alex"]
    assert (await _at(room, 5))["queued"] == 0             # not heard yet
    did = await _at(room, 20)                              # length unknown: once heard
    assert did["queued"] > 0
    assert not any("://" in f for f in (await _files(room))[1:])


@requires_db
async def test_autoplay_off_follows_with_nothing(clean, room, streamer) -> None:
    d = await _seed()
    await room.prepare_files([d["uri"][d["ids"][0]]])
    await room.resume()
    await rooms.start(ROOM, mode="library", seed=SeedRef(track_id=d["ids"][0]), client=room,
                      autoplay=False, person_id=d["alex"])
    await _stream(streamer, room)
    st = await _active()
    assert st.sess.autoplay is False and st.sess.person_id == d["alex"]   # the room's, carried over
    assert (await _at(room, 200))["queued"] == 0
    assert await _files(room) == [URL]
    await room.next()                                      # the stream ends: quiet
    assert (await room.playback_snapshot())["state"] == "stop"


@requires_db
async def test_a_stop_on_purpose_follows_with_nothing(clean, room, streamer) -> None:
    await _seed()
    await _stream(streamer, room)
    await _at(room, 100)
    await room.stop()                                      # voice "stop", the dashboard, the ×
    assert (await _look(room))["ended"] == "the room stopped"
    assert await _active() is None
    assert await _files(room) == [URL]


@requires_db
async def test_a_failed_stream_follows_with_nothing(clean, room, streamer, monkeypatch) -> None:
    d = await _seed()
    await rooms.start(ROOM, mode="library", seed=SeedRef(track_id=d["ids"][0]))
    before = (await _active()).sess.id

    async def _refuse(*a, **kw):
        return False

    monkeypatch.setattr(room, "prepare_url", _refuse)
    resp = await _stream(streamer, room)
    assert resp.music_action is None                       # never reached the player
    assert (await _active()).sess.id == before             # nothing armed for it
    monkeypatch.undo()


@requires_db
async def test_a_stream_that_dies_at_once_follows_with_nothing(clean, room, streamer) -> None:
    await _seed()
    await _stream(streamer, room, duration=None)
    assert (await _at(room, 3))["queued"] == 0
    await room.next()                                      # MPD gives up on it: stopped
    assert (await _look(room))["ended"] == "the room stopped"
    assert await _files(room) == [URL]


@requires_db
async def test_the_stream_is_never_picked_again(clean, room, streamer) -> None:
    await _seed()
    own = await _nina_midnight()
    await _stream(streamer, room)
    await _at(room, 220)
    sid = (await _active()).sess.id
    for _ in range(4):                                     # play on through refills
        ids = await _ids(room)
        await room.play_id(ids[-2])
        await _look(room)
    files = await _files(room)
    assert files.count(URL) == 1 and files[0] == URL or URL not in files   # trimmed at most
    assert not any("://" in f for f in files if f != URL)
    assert own not in {p["track_id"] for p in await _picks(sid)}


@requires_db
async def test_a_turn_holds_the_picks_until_the_room_plays_again(clean, room, streamer) -> None:
    await _seed()
    await _stream(streamer, room)
    await room.seek_to(200)
    await room.pause()                                     # a turn, a call, an announcement
    did = await _look(room)
    assert did["queued"] == 0 and did["ended"] is None
    await room.resume()
    assert (await _look(room))["queued"] > 0


@requires_db
async def test_a_radio_stream_arms_nothing(clean, room, streamer) -> None:
    await _seed()
    resp = await _stream(PlaybackAPI(RADIO), room, title="Jazz FM", artist=None, source=RADIO)
    assert resp.music_action == "start"
    assert await _active() is None


def test_stream_nearly_over() -> None:
    assert not rooms.stream_nearly_over({"elapsed": 5.0, "duration": 30.0})
    assert not rooms.stream_nearly_over({"elapsed": 20.0, "duration": 240.0})
    assert rooms.stream_nearly_over({"elapsed": 200.0, "duration": 240.0})
    assert rooms.stream_nearly_over({"elapsed": 20.0, "duration": None})
    assert rooms._artist_names("Nina", "Nina – Midnight") == ["Nina"]
    assert rooms._artist_names(None, "Otis - Song") == ["Otis"]
    assert rooms._song_key("Midnight (Remastered)") == rooms._song_key("midnight")
