"""Up next in the rooms (phase P4, UP-NEXT-QUEUE-PLAN §5).

* the stub player's new queue calls (insert at a place, add by exact file,
  play one entry, the queue generation every replacing call bumps) — no
  database;
* the room engine against a stub player and the database
  (domovoi/up_next/rooms.py): a start queues the song and the first batch;
  the refill at five songs left, only while playing; Autoplay off takes the
  waiting picks out (a list keeps itself); picks removed or jumped past are
  noted; picks a "less like this" took out leave the queue but the song
  playing now stays; trimming past 50 played songs; the session ends when
  the queue is replaced, the room stops, or it sits idle; a cast's picks
  found by their files; the song that ended feeds the session; the
  extender's tick writes the listen with its session and origin;
* every start path: play-track (list / library / none), play-tracks (armed
  continue, the caster's own session moved), voice "play something" and a
  resolved song / artist, play-playlist arming its own session and anything
  else ending it, the
  web's room start and casting move;
* the room queue routes: insert next / end, the real jump, origins and
  reasons in the read, clear ending the session, the room's session read
  (no person for a guest), save as playlist (streams, podcasts and files
  outside the library left out and counted, duplicates once, name taken,
  nothing to save, never a guest);
* the tiers of the new routes.

DB-backed tests carry ``requires_db`` (a lane with V053 applied).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, music_listens
from domovoi import main as core_main
from domovoi.clients import mpd as mpd_mod
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.up_next import rooms, service, store
from domovoi.up_next.session import SeedRef, UpNextSession
from web.backend.api import music as music_api
from web.backend.api import music_queue as queue_api
from web.backend.api import up_next as up_next_api

ROOM = "upn-kitchen"
ARTISTS = ("Nina", "Otis", "Ella", "Miles", "Aretha", "Sam")


def _run(coro):
    return asyncio.run(coro)


# ─── The stub player (no database) ────────────────────────────────────────


def test_stub_inserts_at_a_place_and_adds_files() -> None:
    stub = MPDStubClient()
    _run(stub.prepare_files(["A/1.flac", "A/2.flac", "A/3.flac"]))
    _run(stub.queue_add_tracks([{"title": "x"}, {"title": "y"}], position=1))
    assert [e["title"] for e in _run(stub.queue_list())] == ["1", "x", "y", "2", "3"]
    stub.refused_files = frozenset({"gone.flac"})
    got = _run(stub.queue_add_files(["B/1.flac", "gone.flac", "B/2.flac"], position=0))
    assert got[1] is None and got[0]["file"] == "B/1.flac" and got[2]["id"] > got[0]["id"]
    assert [e["file"] for e in _run(stub.queue_list())][:2] == ["B/1.flac", "B/2.flac"]


def test_stub_play_id_and_the_queue_generation() -> None:
    stub = MPDStubClient()
    g0 = stub.queue_generation
    _run(stub.prepare_files(["A/1.flac", "A/2.flac"]))
    assert stub.queue_generation == g0 + 1
    ids = [e["id"] for e in _run(stub.queue_list())]
    assert _run(stub.play_id(ids[1])) is True
    assert _run(stub.current_song())["id"] == ids[1] and _run(stub.state()) == "play"
    assert _run(stub.play_id(ids[0], paused=True)) is True and _run(stub.state()) == "pause"
    assert _run(stub.play_id(9999)) is False
    # Appending, inserting and jumping don't replace the queue; replacing does.
    _run(stub.queue_add_files(["A/3.flac"]))
    assert stub.queue_generation == g0 + 1
    for replace in (stub.prepare_tracks([{"title": "t"}]), stub.play_search({"title": "t"}),
                    stub.prepare_url("http://radio.example/stream"), stub.queue_clear()):
        before = stub.queue_generation
        _run(replace)
        assert stub.queue_generation == before + 1
    snap = _run(stub.playback_snapshot())
    assert "pos" in snap and "playlistlength" in snap


def test_the_extender_is_registered_in_place_of_the_listen_logger() -> None:
    src = (Path(core_main.__file__)).read_text(encoding="utf-8")
    assert "WORKERS.add_worker(UpNextExtender(), owner=\"core\")" in src
    assert "WORKERS.add_worker(ListenLogger()" not in src
    from domovoi.workers.listen_logger import ListenLogger
    from domovoi.workers.up_next_extender import UpNextExtender

    assert issubclass(UpNextExtender, ListenLogger)
    assert UpNextExtender.interval_setting == "playback_sweeper_interval_sec"
    assert UpNextExtender.stub_suppressed is True


# ─── Fixtures (database) ──────────────────────────────────────────────────


MUSIC = Path(settings.music_dir).expanduser()


def _uri(i: int) -> str:
    return f"{ARTISTS[i % len(ARTISTS)]}/{i:03d}.flac"


@pytest.fixture
async def clean():
    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE up_next_picks, up_next_sessions, music_listens, music_room_listeners, "
                "room_queue_items, playlist_tracks, playlists, devices, library_tracks, people "
                "RESTART IDENTITY CASCADE"))

    await _truncate()
    rooms.SEEN.clear()
    music_listens.SKIP_STAMPS.clear()
    yield
    rooms.SEEN.clear()
    music_listens.SKIP_STAMPS.clear()
    await _truncate()


async def _seed(n: int = 60) -> dict[str, Any]:
    """``n`` library songs by six artists, all inside the music folder (so
    each has an MPD path ``_uri(i)``); a playlist "Road Trip" of the first
    20; two people."""
    async with engine.begin() as conn:
        ids = []
        for i in range(n):
            a = ARTISTS[i % len(ARTISTS)]
            year = (1965, 1985, 1995)[i % 3] + (i % 4)
            genre = ("soul", "jazz")[i % 2]
            ids.append((await conn.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, year, genre, genre_keys) "
                "VALUES (:f, :t, :a, :al, 200, :y, :g, ARRAY[:g]) RETURNING id"),
                {"f": str(MUSIC / _uri(i)), "t": f"Song {i}", "a": a, "al": f"{a} Album",
                 "y": year, "g": genre})).scalar_one())
        pl = (await conn.execute(text("INSERT INTO playlists (name) VALUES ('Road Trip') RETURNING id"))).scalar_one()
        for pos, tid in enumerate(ids[:20]):
            await conn.execute(text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, :o)"),
                {"p": pl, "t": tid, "o": pos})
        alex = (await conn.execute(text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
    return {"ids": ids, "uri": {tid: _uri(i) for i, tid in enumerate(ids)}, "playlist": pl, "alex": alex}


@pytest.fixture
def room(monkeypatch):
    stub = MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, ROOM, stub)
    return stub


@pytest.fixture
def dispatched(monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []

    async def _dispatch(response, room_id, *, start_paused=False):
        calls.append((room_id, response.music_action, start_paused))

    monkeypatch.setattr(core_main, "_admin_dispatch_music", _dispatch)
    return calls


async def _playing(stub: MPDStubClient, uris: list[str]) -> list[int]:
    await stub.prepare_files(uris)
    await stub.resume()
    return [int(e["id"]) for e in await stub.queue_list()]


async def _active(target: str = f"room:{ROOM}") -> store.Stored | None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await store.active_for(s, target)


async def _picks(sid: str) -> list[dict[str, Any]]:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await store.picks(s, sid)


async def _ids(stub: MPDStubClient) -> list[int]:
    return [int(e["id"]) for e in await stub.queue_list()]


async def _look(stub: MPDStubClient, **kw) -> dict[str, Any]:
    return await rooms.look(ROOM, stub, await stub.playback_snapshot(), **kw)


async def _library_start(stub: MPDStubClient, d: dict[str, Any], **kw) -> str:
    await _playing(stub, [d["uri"][d["ids"][0]]])
    sid = await rooms.start(ROOM, mode="library", seed=SeedRef(track_id=d["ids"][0]), client=stub, **kw)
    assert sid
    return sid


# ─── The room engine ──────────────────────────────────────────────────────


@requires_db
async def test_a_start_queues_the_song_and_the_first_batch(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    queue = await room.queue_list()
    picks = await _picks(sid)
    assert 0 < len(picks) <= 30 and len(queue) == 1 + len(picks)
    # Each pick knows the entry it sits in, and that entry is its file.
    by_id = {int(e["id"]): e["file"] for e in queue}
    for p in picks:
        assert by_id[p["mpd_song_id"]] == d["uri"][p["track_id"]]
    assert [e["id"] for e in queue[1:]] == [p["mpd_song_id"] for p in picks]
    view = await rooms.queue_view(ROOM, [dict(song_id=e["id"], file=e["file"]) for e in queue])
    assert view["session_id"] == sid and view["autoplay"] is True
    assert view["up_next"]["mode"] == "library"


@requires_db
async def test_the_refill_comes_at_five_left_and_only_while_playing(clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    ids = await _ids(room)
    assert (await _look(room))["queued"] == 0             # 30 left: nothing
    await room.play_id(ids[-6])                            # 5 left after it
    await room.pause()
    assert (await _look(room))["queued"] == 0             # paused: nothing
    await room.resume()
    did = await _look(room)
    assert did["queued"] > 0
    assert len(await _ids(room)) == len(ids) + did["queued"]
    assert (await _look(room))["queued"] == 0             # topped up


@requires_db
async def test_autoplay_off_takes_the_waiting_picks_out_but_not_the_song_playing(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    ids = await _ids(room)
    await room.play_id(ids[2])                             # a pick is playing now
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        st = await store.lock(s, sid)
        st.sess.autoplay = False
        assert await store.save(s, st)
    did = await _look(room)
    assert await _ids(room) == ids[:3]
    assert did["removed"] == len(ids) - 3
    st = {p["mpd_song_id"]: p["status"] for p in await _picks(sid)}
    assert st[ids[2]] == "queued" and st[ids[1]] == "skipped"   # jumped past
    assert {st[i] for i in ids[3:]} == {"removed"}
    assert (await _look(room))["queued"] == 0             # and nothing comes back


@requires_db
async def test_a_list_keeps_itself_with_autoplay_off(clean, room) -> None:
    d = await _seed()
    await _playing(room, [d["uri"][d["ids"][0]]])
    sid = await rooms.start_from_request(
        ROOM, track_id=d["ids"][0], context={"kind": "playlist", "ref": str(d["playlist"])},
        person_id=None, autoplay=False, client=room)
    picks = await _picks(sid)
    # The rest of Road Trip, once, then the list stops.
    assert sorted(p["track_id"] for p in picks) == sorted(d["ids"][1:20])
    assert {p["reason_code"] for p in picks} == {"from_list"}
    assert (await _look(room))["removed"] == 0
    assert len(await _ids(room)) == 20


@requires_db
async def test_picks_removed_or_taken_out_are_noted_and_leave_the_queue(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    ids = await _ids(room)
    await room.queue_remove([ids[4]])                      # someone removed one
    from domovoi.db.session import session_scope

    async with session_scope() as s:                      # "less like this" took two out,
        seqs = {p["mpd_song_id"]: p["seq"] for p in await store.picks(s, sid)}
        await store.set_status(s, sid, [seqs[ids[10]], seqs[ids[7]]], "removed")
    await room.play_id(ids[7])                             # one of them is playing now
    did = await _look(room)
    left = await _ids(room)
    assert ids[10] not in left and ids[7] in left          # never the song playing
    assert did["removed"] == 1
    status = {p["mpd_song_id"]: p["status"] for p in await _picks(sid)}
    assert status[ids[4]] == "removed"
    assert status[ids[1]] == "skipped"                     # jumped past


@requires_db
async def test_old_played_songs_are_trimmed_past_fifty(clean, room) -> None:
    d = await _seed()
    await _playing(room, [d["uri"][t] for t in d["ids"][:60]])
    sid = await rooms.start(ROOM, mode="continue", seed=SeedRef(track_id=d["ids"][0], kind="ids",
                            track_ids=tuple(d["ids"][:60]), label="from this queue"), client=room)
    ids = await _ids(room)
    await room.play_id(ids[58])
    did = await _look(room)
    assert did["trimmed"] == 8
    left = await _ids(room)
    assert left[0] == ids[8] and left.index(ids[58]) == rooms.TRIM_KEEP_BEHIND
    assert did["queued"] > 0                               # 1 left: the continue session drew
    assert (await _active()).sess.id == sid


@requires_db
async def test_the_session_ends_when_the_queue_is_replaced(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    assert (await _look(room))["ended"] is None
    await room.prepare_url("http://radio.example/stream")   # a stream took the room
    await room.resume()
    assert (await _look(room))["ended"] == "the queue was replaced"
    assert await _active() is None
    assert sid not in rooms.SEEN


@requires_db
async def test_the_session_ends_when_the_room_stops_or_idles(clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    await _look(room)
    await room.stop()
    assert (await _look(room))["ended"] == "the room stopped"
    await _library_start(room, d)
    await room.pause()
    t0 = 1000.0
    assert (await _look(room, now=t0))["ended"] is None
    assert (await _look(room, now=t0 + rooms.IDLE_END_SEC - 1))["ended"] is None
    assert (await _look(room, now=t0 + rooms.IDLE_END_SEC))["ended"] == "idle"


@requires_db
async def test_a_casts_picks_are_found_by_their_files(clean, room) -> None:
    d = await _seed()
    from domovoi.db.session import session_scope

    sess = UpNextSession.start(target="device:browser-cast1", mode="library",
                               seed=SeedRef(track_id=d["ids"][0]))
    async with session_scope() as s:
        st = await store.create(s, sess, name=None)
        _, picks, _ = await service.draw(s, st)
    # The browser cast its song and its picks, as tracks.
    await _playing(room, [d["uri"][d["ids"][0]]] + [d["uri"][p["track_id"]] for p in picks])
    assert await rooms.move_here(ROOM, sess.id, room)
    did = await _look(room)
    assert did["linked"] == len(picks)
    queue = await room.queue_list()
    items = [dict(song_id=e["id"], file=e["file"]) for e in queue]
    view = await rooms.queue_view(ROOM, items)
    assert view["session_id"] == sess.id
    assert items[0]["origin"] == "explicit" and items[0]["track_id"] == d["ids"][0]
    assert all(it["origin"] == "auto" and it["reason_label"] and it["up_next_seq"] for it in items[1:])


@requires_db
async def test_the_song_that_ended_feeds_the_session(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    queue = await room.queue_list()
    picks = {p["mpd_song_id"]: p for p in await _picks(sid)}
    first = picks[queue[1]["id"]]
    got = await rooms.song_ended(ROOM, songid=queue[1]["id"], track_id=first["track_id"],
                                 played_sec=200, duration_sec=200, outcome="finished")
    assert got == (sid, "auto")
    st = await _active()
    assert st.sess.recent_listened[0] == first["track_id"]
    assert {p["seq"]: p["status"] for p in await _picks(sid)}[first["seq"]] == "played"
    # Not a pick: explicit. No session: nothing.
    assert (await rooms.song_ended(ROOM, songid=queue[0]["id"], track_id=d["ids"][0],
                                   played_sec=10, duration_sec=200, outcome="skipped"))[1] == "explicit"
    assert await rooms.song_ended("upn-nowhere", songid=1, track_id=d["ids"][0], played_sec=1,
                                  duration_sec=200, outcome="skipped") == (None, "explicit")


@requires_db
async def test_two_early_skips_of_an_artist_take_their_picks_out(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    by_artist: dict[int, str] = {}
    async with engine.begin() as conn:
        for tid, artist in (await conn.execute(text("SELECT id, artist FROM library_tracks"))).all():
            by_artist[int(tid)] = artist
    picks = await _picks(sid)
    artist = by_artist[picks[0]["track_id"]]
    for _ in range(2):
        await rooms.song_ended(ROOM, songid=None, track_id=picks[0]["track_id"], played_sec=5,
                               duration_sec=200, outcome="skipped")
    waiting = [p for p in await _picks(sid) if by_artist[p["track_id"]] == artist]
    assert waiting and {p["status"] for p in waiting} == {"removed"}
    await _look(room)
    files = {e["file"] for e in await room.queue_list()}
    assert not {d["uri"][p["track_id"]] for p in waiting} & files - {(await room.current_song())["file"]}


@requires_db
async def test_the_extender_writes_the_listen_with_its_session(clean, room, monkeypatch) -> None:
    from domovoi.workers import listen_logger
    from domovoi.workers.up_next_extender import UpNextExtender

    d = await _seed()
    sid = await _library_start(room, d)
    monkeypatch.setattr(listen_logger, "iter_mpd_clients", lambda: [(ROOM, room)])
    clock = [0.0]
    worker = UpNextExtender(clock=lambda: clock[0])
    ids = await _ids(room)
    await room.play_id(ids[1])                             # a pick plays, looked at every 5 s
    for t in range(0, 200, 5):
        clock[0] = float(t)
        room._song["_elapsed"] = float(t)
        await worker.tick()
    await room.next()                                      # it ran out; the next one starts
    clock[0] = 201.0
    assert await worker.tick() == 1
    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT session_id::text, origin, outcome, room_id, player FROM music_listens"))).one()
    assert tuple(row) == (sid, "auto", "finished", ROOM, "room")


# ─── Core routes: every start path ────────────────────────────────────────


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_main.app), base_url="http://test",
                       headers={"X-Requested-With": "t"})


@requires_db
async def test_play_track_starts_a_list_or_library_session(_db, clean, room, dispatched) -> None:
    d = await _seed()
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-track", json={
            "room_id": ROOM, "track_id": d["ids"][0],
            "context": {"kind": "playlist", "ref": str(d["playlist"])}, "person_id": d["alex"]})
        assert r.status_code == 200, r.text
        sid = r.json()["up_next"]["session_id"]
        st = await _active()
        assert st.sess.id == sid and st.sess.mode == "list" and st.sess.person_id == d["alex"]
        picks = await _picks(sid)
        # The song, the rest of Road Trip (then, autoplay on, round again).
        assert len(await room.queue_list()) == 1 + len(picks)
        assert sorted(p["track_id"] for p in picks[:19]) == sorted(d["ids"][1:20])
        # A song on its own: library mode, the old session ended.
        r = await c.post("/v1/admin/music/play-track", json={"room_id": ROOM, "track_id": d["ids"][5]})
        st2 = await _active()
        assert st2.sess.mode == "library" and st2.sess.id != sid and st2.sess.person_id is None
        # up_next false: the song alone, as before.
        r = await c.post("/v1/admin/music/play-track",
                         json={"room_id": ROOM, "track_id": d["ids"][5], "up_next": False})
        assert r.status_code == 200 and r.json()["up_next"] is None
        assert await _active() is None and len(await room.queue_list()) == 1   # the song alone
        # A list the house doesn't have: refused before the room is touched.
        before = await _ids(room)
        bad = await c.post("/v1/admin/music/play-track", json={
            "room_id": ROOM, "track_id": d["ids"][0], "context": {"kind": "playlist", "ref": "777"}})
        assert bad.status_code == 404 and await _ids(room) == before
    assert all(a == "start" for _r, a, _p in dispatched)


@requires_db
async def test_a_cast_arms_continue_or_carries_the_casters_session(_db, clean, room, dispatched) -> None:
    d = await _seed()
    cast = d["ids"][10:16]
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-tracks", json={"room_id": ROOM, "track_ids": cast})
        assert r.status_code == 200, r.text
        st = await _active()
        assert st.sess.mode == "continue" and st.sess.seed.kind == "ids"
        assert list(st.sess.seed.track_ids) == cast and await _picks(st.sess.id) == []
        assert len(await room.queue_list()) == 6          # nothing added until it's nearly over
        # A browser's session carried along.
        from domovoi.db.session import session_scope

        sess = UpNextSession.start(target="device:browser-c1", mode="library",
                                   seed=SeedRef(track_id=d["ids"][0]))
        async with session_scope() as s:
            st_dev = await store.create(s, sess, name=None)
            _, picks, _ = await service.draw(s, st_dev)
        r = await c.post("/v1/admin/music/play-tracks", json={
            "room_id": ROOM, "track_ids": [d["ids"][0]] + [p["track_id"] for p in picks],
            "up_next_session_id": sess.id})
        assert r.json()["up_next"]["session_id"] == sess.id
        moved = await _active()
        assert moved.sess.id == sess.id and moved.sess.target == f"room:{ROOM}"
        assert st.sess.id != moved.sess.id                 # the armed one ended
        await room.resume()
        assert (await _look(room))["linked"] == len(picks)


@requires_db
async def test_play_playlist_replaces_the_rooms_session(_db, clean, room, dispatched) -> None:
    d = await _seed()
    old = await _library_start(room, d)
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-playlist", json={"room_id": ROOM, "playlist_id": d["playlist"]})
        assert r.status_code == 200, r.text
    # The whole playlist is the queue now, with its own continue session
    # (domovoi/tests/test_up_next_room_playlist.py has the rest).
    st = await _active()
    assert st.sess.id != old and st.sess.mode == "continue" and st.sess.seed.kind == "playlist"
    await room.resume()
    assert (await _look(room))["ended"] is None
    assert len(await room.queue_list()) == 20


@requires_db
async def test_other_plays_end_the_rooms_session(_db, clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    await room.play_search({"title": "Song 3"})
    await room.resume()
    assert (await _look(room))["ended"] == "the queue was replaced"


@requires_db
async def test_voice_plays_start_a_session(_db, clean, room) -> None:
    from domovoi.db.session import session_scope
    from domovoi.handlers.music import MusicHandler
    from domovoi.models import Context

    d = await _seed()
    ctx = Context(room_id=ROOM, online=True, bot_name="domovoi", app=core_main.app, person_id=d["alex"])
    h = MusicHandler()
    async with session_scope() as s:
        resp = await h._play_random(ctx, s)
    assert resp.music_action == "start"
    st = await _active()
    assert st.sess.mode == "library" and st.sess.person_id == d["alex"]
    assert len(await room.queue_list()) > 1
    # The skip cascade's random song starts none.
    async with session_scope() as s:
        await h._play_random(ctx, s, up_next=False)
    await room.resume()
    assert (await _look(room))["ended"] == "the queue was replaced"


@requires_db
async def test_voice_play_of_a_song_and_of_an_artist(_db, clean, room) -> None:
    from types import SimpleNamespace

    from domovoi.db.session import session_scope
    from domovoi.handlers.music import MusicHandler
    from domovoi.models import Context

    d = await _seed()
    ctx = Context(room_id=ROOM, online=True, bot_name="domovoi", app=core_main.app)
    h = MusicHandler()

    def cand(kind: str, ids: list[int]):
        ref = SimpleNamespace(type=kind, label="Nina", artist_label="Nina")
        return SimpleNamespace(ref=ref, track_ids=ids, score=1.0, via="exact", speak="Nina",
                               to_dict=lambda: {"type": kind})

    async with session_scope() as s:
        await h.play_candidate(cand("title", [d["ids"][0]]), ctx, s, heard="song 0")
    st = await _active()
    assert st.sess.mode == "library" and st.sess.seed.track_id == d["ids"][0]
    nina = [t for i, t in enumerate(d["ids"]) if i % 6 == 0]
    async with session_scope() as s:
        await h.play_candidate(cand("artist", nina), ctx, s, heard="nina")
    st = await _active()
    assert st.sess.mode == "continue" and sorted(st.sess.seed.track_ids) == sorted(nina)
    assert len(await room.queue_list()) == len(nina)       # armed: nothing added yet


# ─── Core routes: the room's queue ────────────────────────────────────────


@requires_db
async def test_insert_next_and_end_and_the_read(_db, clean, room, dispatched) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    ids = await _ids(room)
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/add",
                         json={"track_ids": [d["ids"][40]], "where": "next"})
        assert r.status_code == 200, r.text and r.json()["position"] == 1
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/add", json={"track_ids": [d["ids"][41], d["ids"][42]]})
        assert r.json()["position"] == 2                   # after what was asked for, before the picks
        q = (await c.get(f"/v1/admin/music/queue/{ROOM}")).json()
    origins = [it["origin"] for it in q["items"]]
    assert origins[:4] == ["explicit"] * 4 and set(origins[4:]) == {"auto"}
    assert [it["song_id"] for it in q["items"][4:]] == ids[1:]
    assert all(it["reason_label"] and it["up_next_seq"] for it in q["items"][4:])
    assert q["items"][0]["track_id"] == d["ids"][0]
    assert q["session_id"] == sid and q["autoplay"] is True and q["up_next"]["mode"] == "library"
    assert "person" not in str(q["up_next"])


@requires_db
async def test_insert_end_appends_without_picks_and_the_room_jumps(_db, clean, room, dispatched) -> None:
    d = await _seed()
    ids = await _playing(room, [d["uri"][t] for t in d["ids"][:3]])
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/add", json={"track_ids": [d["ids"][9]]})
        assert r.json()["position"] is None
        new = await _ids(room)
        assert new[:3] == ids and len(new) == 4
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/play", json={"song_id": new[2]})
        assert r.status_code == 200 and r.json()["started"] is False
        assert (await room.current_song())["id"] == new[2]
        assert music_listens.SKIP_STAMPS.get(ROOM) is not None   # the song it left ends as a skip
        assert (await c.post(f"/v1/admin/music/queue/{ROOM}/play", json={"song_id": 9999})).status_code == 404
        # A stopped room is left paused on the entry and told to play.
        await room.stop()
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/play", json={"song_id": new[0]})
        assert r.json()["started"] is True and await room.state() == "pause"
    assert dispatched[-1] == (ROOM, "start", False)


@requires_db
async def test_clear_ends_the_session_and_the_room_read(_db, clean, room, dispatched) -> None:
    d = await _seed()
    await _library_start(room, d)
    async with _core() as c:
        r = await c.get(f"/v1/admin/music/up-next/room/{ROOM}")
        s = r.json()["session"]
        assert s["target"] == {"kind": "room", "room_id": ROOM} and s["mode"] == "library"
        assert "person" in s and s["picks"]
        assert (await c.post(f"/v1/admin/music/queue/{ROOM}/clear", json={})).status_code == 200
        assert (await c.get(f"/v1/admin/music/up-next/room/{ROOM}")).json() == {"session": None}
    assert await _active() is None


# ─── Web: starts, casting, the session's routes ───────────────────────────


@pytest.fixture
def bridged(monkeypatch):
    """The web's hop to the core, made into the core app itself (in
    process), so a web route drives the real core route."""

    async def _get(path, timeout=10.0, headers=None):
        async with _core() as c:
            r = await c.get(path, headers=headers or {})
        return r.status_code, r.json()

    async def _post(path, body=None, timeout=30.0, headers=None):
        async with _core() as c:
            r = await c.post(path, json=body or {}, headers=headers or {})
        return r.status_code, r.json()

    for mod in (music_api, queue_api, up_next_api):
        monkeypatch.setattr(mod, "get_admin", _get, raising=False)
        monkeypatch.setattr(mod, "post_admin", _post, raising=False)


async def _household() -> str:
    async with web_client() as c:
        await claim_admin(c)
    return await db_device_token()


def _dev(token: str, device_id: str) -> AsyncClient:
    return web_client(headers={"X-Requested-With": "t", HEADER: token, "X-Device-Id": device_id})


@requires_db
async def test_web_play_in_a_room_starts_its_session(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    async with _dev(token, "browser-w1") as c:
        r = await c.post("/api/music/play-track", json={
            "room_id": ROOM, "track_id": d["ids"][2],
            "context": {"kind": "artist", "ref": "Ella"}, "listener_person_id": d["alex"]})
        assert r.status_code == 200, r.text
        st = await _active()
        assert st.sess.mode == "list" and st.sess.seed.kind == "artist" and st.sess.person_id == d["alex"]
        # The up-next start for a room target: the same, answered as a Session.
        r = await c.post("/api/music/up-next/sessions", json={
            "target": {"kind": "room", "room_id": ROOM}, "start": {"track_id": d["ids"][3]}})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["target"] == {"kind": "room", "room_id": ROOM} and s["mode"] == "library" and s["picks"]
        # The room's refill is the server's: "next" on it is refused.
        nxt = await c.post(f"/api/music/up-next/sessions/{s['id']}/next", json={"have_through_seq": 0})
        assert nxt.status_code == 409
        # Its read, on the queue's tier.
        assert (await c.get(f"/api/music/up-next/room/{ROOM}")).json()["id"] == s["id"]
        # Autoplay off from the panel: the extender takes the picks out.
        assert (await c.patch(f"/api/music/up-next/sessions/{s['id']}", json={"autoplay": False})).status_code == 200
    await room.resume()
    await _look(room)
    assert len(await room.queue_list()) == 1               # the picks are gone; the song plays


@requires_db
async def test_web_cast_moves_the_browsers_session_and_back(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    async with _dev(token, "browser-w2") as c:
        s = (await c.post("/api/music/up-next/sessions", json={
            "target": {"kind": "device", "device_id": "browser-w2"}, "start": {"track_id": d["ids"][0]}})).json()
        ids = [d["ids"][0]] + [p["track_id"] for p in s["picks"]]
        r = await c.post("/api/music/play-tracks", json={
            "room_id": ROOM, "track_ids": ids, "up_next_session_id": s["id"]})
        assert r.status_code == 200, r.text
        assert (await _active()).sess.id == s["id"]
    # Another browser can't carry it off, nor hand this browser's own session to a room.
    async with _dev(token, "browser-other") as c:
        r = await c.post(f"/api/music/up-next/sessions/{s['id']}/move",
                         json={"target": {"kind": "device", "device_id": "browser-w2"}})
        assert r.status_code == 403
    async with _dev(token, "browser-w2") as c:
        back = await c.post(f"/api/music/up-next/sessions/{s['id']}/move",
                            json={"target": {"kind": "device", "device_id": "browser-w2"}})
        assert back.status_code == 200, back.text
        assert back.json()["target"] == {"kind": "device", "device_id": "browser-w2"}
        assert await _active() is None
        again = await c.post(f"/api/music/up-next/sessions/{s['id']}/move",
                             json={"target": {"kind": "room", "room_id": ROOM}})
        assert again.status_code == 200 and (await _active()).sess.id == s["id"]


@requires_db
async def test_web_cast_never_carries_another_devices_session(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    async with _dev(token, "browser-w3") as c:
        s = (await c.post("/api/music/up-next/sessions", json={
            "target": {"kind": "device", "device_id": "browser-w3"}, "start": {"track_id": d["ids"][0]}})).json()
    async with _dev(token, "browser-thief") as c:
        r = await c.post("/api/music/play-tracks", json={
            "room_id": ROOM, "track_ids": [d["ids"][0]], "up_next_session_id": s["id"]})
        assert r.status_code == 403
    assert await _active() is None


@requires_db
async def test_web_less_like_this_on_a_rooms_pick(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    sid = await _library_start(room, d)
    picks = await _picks(sid)
    async with _dev(token, "browser-w4") as c:
        r = await c.post(f"/api/music/up-next/sessions/{sid}/feedback",
                         json={"seq": picks[3]["seq"], "kind": "less_like_this"})
        assert r.status_code == 200, r.text
        gone = set(r.json()["removed"])
    assert picks[3]["seq"] in gone
    await _look(room)
    held = {p["mpd_song_id"] for p in await _picks(sid) if p["seq"] in gone}
    assert not held & set(await _ids(room))


@requires_db
async def test_web_insert_and_jump_go_through(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    await _library_start(room, d)
    ids = await _ids(room)
    async with _dev(token, "browser-w5") as c:
        r = await c.post(f"/api/music/queue/{ROOM}/add",
                         json={"track_ids": [d["ids"][50]], "device_id": "browser-w5", "where": "next"})
        assert r.status_code == 200, r.text
        q = (await c.get(f"/api/music/queue/{ROOM}?device_id=browser-w5")).json()
        assert q["items"][1]["title"] == "Song 50" and q["items"][1]["origin"] == "explicit"
        assert q["items"][2]["origin"] == "auto" and q["items"][2]["reason_label"]
        assert q["session_id"] and q["autoplay"] is True
        r = await c.post(f"/api/music/queue/{ROOM}/play", json={"song_id": ids[5], "device_id": "browser-w5"})
        assert r.status_code == 200, r.text
    assert (await room.current_song())["id"] == ids[5]


# ─── Save a room's queue as a playlist ────────────────────────────────────


@requires_db
async def test_save_the_rooms_queue_as_a_playlist(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    await _playing(room, [d["uri"][d["ids"][3]], d["uri"][d["ids"][7]]])
    await room.queue_add_files([
        "http://radio.example/stream",                 # a stream
        "Podcasts/Some Show/ep1.mp3",                  # a podcast: not a library song
        d["uri"][d["ids"][3]],                         # the same song again
        d["uri"][d["ids"][11]],
    ])
    async with _dev(token, "browser-w6") as c:
        r = await c.post(f"/api/music/queue/{ROOM}/save-playlist", json={"name": "  Kitchen   Friday "})
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == "Kitchen Friday"
        assert (out["saved"], out["skipped"], out["duplicates"]) == (3, 2, 1)
        again = await c.post(f"/api/music/queue/{ROOM}/save-playlist", json={"name": "kitchen friday"})
        assert again.status_code == 409
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = :p ORDER BY position"),
            {"p": out["playlist_id"]})).all()
    assert [r[0] for r in rows] == [d["ids"][3], d["ids"][7], d["ids"][11]]
    # Nothing in the queue from the library: nothing to save.
    await room.queue_clear()
    await room.queue_add_files(["http://radio.example/stream"])
    async with _dev(token, "browser-w6") as c:
        r = await c.post(f"/api/music/queue/{ROOM}/save-playlist", json={"name": "Radio"})
        assert r.status_code == 422


@requires_db
async def test_save_as_playlist_is_never_a_guests(_db, clean, room, bridged) -> None:
    await _household()
    async with web_client() as c:                     # no credential: a guest
        r = await c.post(f"/api/music/queue/{ROOM}/save-playlist", json={"name": "Mine"})
    assert r.status_code == 401


async def test_save_maps_files_the_core_named(monkeypatch) -> None:
    """The web hop keeps the core's mapping: an item with no ``track_id``
    (a stream, a podcast, a file the library doesn't know) is skipped."""
    sent: dict[str, Any] = {}

    async def _get(path, timeout=10.0, headers=None):
        return 200, {"items": [{"song_id": 1, "track_id": 5}, {"song_id": 2, "track_id": None},
                               {"song_id": 3, "track_id": 5}], "current_song_id": 1}

    class _S:
        async def execute(self, sql, params=None):
            q = str(sql)

            class R:
                def __init__(self, rows):
                    self.rows = rows

                def first(self):
                    return self.rows[0] if self.rows else None

                def all(self):
                    return self.rows

                def scalar_one(self):
                    return self.rows[0][0]

            if "SELECT id FROM playlists" in q:
                return R([])
            if "INSERT INTO playlists" in q:
                return R([(9,)])
            if "INSERT INTO playlist_tracks" in q:
                sent["ids"] = params["ids"]
                return R([(i,) for i in params["ids"]])
            return R([])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield _S()

    monkeypatch.setattr(queue_api, "get_admin", _get)
    monkeypatch.setattr(queue_api, "session_scope", _scope)
    from web.backend.main import app as web_app

    async def _ok():
        return None

    web_app.dependency_overrides[admin_auth.require_device] = _ok
    try:
        async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                               headers={"X-Requested-With": "t"}) as c:
            r = await c.post(f"/api/music/queue/{ROOM}/save-playlist", json={"name": "x"})
    finally:
        web_app.dependency_overrides.pop(admin_auth.require_device, None)
    assert r.status_code == 201, r.text
    assert sent["ids"] == [5]
    assert (r.json()["saved"], r.json()["skipped"], r.json()["duplicates"]) == (1, 1, 1)


# ─── Tiers ────────────────────────────────────────────────────────────────


def _web_gates(method: str, path: str) -> set:
    from domovoi.tests.test_up_next_api import _gates

    return _gates(method, path)


def test_new_web_route_tiers() -> None:
    from domovoi import guest_queue  # noqa: F401
    from web.backend.api.music_queue import require_device_or_guest

    assert admin_auth.require_device in _web_gates("POST", "/api/music/queue/{room_id}/save-playlist")
    assert require_device_or_guest in _web_gates("POST", "/api/music/queue/{room_id}/play")
    assert admin_auth.require_device in _web_gates("POST", "/api/music/up-next/sessions/{session_id}/move")


def test_new_core_route_tiers() -> None:
    from domovoi import guest_queue
    from domovoi.tests.test_core_route_tiers import _gates_for

    assert guest_queue.require_device_or_active_room_guest in _gates_for(
        "POST", "/v1/admin/music/queue/{room_id}/play")
    assert guest_queue.require_device_read_or_active_room_guest in _gates_for(
        "GET", "/v1/admin/music/up-next/room/{room_id}")
