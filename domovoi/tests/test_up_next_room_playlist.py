"""A playlist played in a room is the room's whole queue (owner decision
2026-11-12, domovoi/handlers/shared/room_playlist.py).

* the core's play-playlist: the whole playlist in order, or shuffled; the
  first song first; a continue Up next session with the playlist as its
  source; Favorites as the virtual list;
* picks join when 5 or fewer songs are left, and not with Autoplay off;
* an edited queue (remove, reorder, add) still extends at its end;
* the queue is a COPY: removing, reordering, adding, jumping and clearing
  never change the saved playlist (owner requirement);
* songs outside the music folder and files MPD refuses are skipped and
  counted;
* a long playlist starts on its first songs and the rest follow in the
  background (no picks meanwhile; a replaced queue stops the loader);
* the voice path ("play the road trip playlist" in a room) and the web hop;
* the old one-song-at-a-time playlist state is gone.

Uses the P4 fixtures and the stub player (domovoi/tests/test_up_next_rooms.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from domovoi import main as core_main
from domovoi.db.session import engine
from domovoi.handlers.shared import room_playlist
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_up_next_rooms import (  # noqa: F401 — fixtures
    MUSIC,
    ROOM,
    _active,
    _core,
    _dev,
    _household,
    _ids,
    _library_start,
    _look,
    _picks,
    _seed,
    bridged,
    clean,
    dispatched,
    room,
)
from domovoi.up_next import rooms


async def _play(c, d: dict[str, Any], **kw) -> dict[str, Any]:
    r = await c.post("/v1/admin/music/play-playlist",
                     json={"room_id": ROOM, "playlist_id": d["playlist"], **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _road_trip(d: dict[str, Any]) -> list[str]:
    return [d["uri"][t] for t in d["ids"][:20]]


async def _files(stub) -> list[str]:
    return [e["file"] for e in await stub.queue_list()]


async def _playlist_rows(pid: int) -> list[tuple[int, int]]:
    async with engine.begin() as conn:
        return [tuple(r) for r in (await conn.execute(text(
            "SELECT track_id, position FROM playlist_tracks WHERE playlist_id = :p "
            "ORDER BY position, id"), {"p": pid})).all()]


# ─── The whole playlist, in order or shuffled ─────────────────────────────


@requires_db
async def test_the_whole_playlist_is_the_rooms_queue_in_order(_db, clean, room, dispatched) -> None:
    d = await _seed()
    old = await _library_start(room, d)
    async with _core() as c:
        out = await _play(c, d)
    assert await _files(room) == _road_trip(d)
    assert (await room.current_song())["file"] == _road_trip(d)[0]
    assert out["track_id"] == d["ids"][0] and out["title"] == "Song 0"
    assert (out["total"], out["queued"], out["skipped"], out["loading"]) == (20, 20, 0, False)
    assert out["playlist_name"] == "Road Trip" and out["mode"] == "ordered"
    # A continue session, its source the playlist; nothing added yet.
    st = await _active()
    assert st.sess.id != old and out["up_next"] == {"session_id": st.sess.id}
    assert st.sess.mode == "continue" and st.sess.autoplay is True
    assert (st.sess.seed.kind, st.sess.seed.ref) == ("playlist", str(d["playlist"]))
    assert st.sess.seed.label == "from road trip" and await _picks(st.sess.id) == []
    queue = await room.queue_list()
    view = await rooms.queue_view(ROOM, [dict(song_id=e["id"], file=e["file"]) for e in queue])
    assert view["up_next"]["mode"] == "continue"
    assert view["up_next"]["seed"]["list"] == {"kind": "playlist", "name": "Road Trip"}
    assert dispatched[-1] == (ROOM, "start", False)


@requires_db
async def test_a_shuffled_playlist_holds_every_song_once(_db, clean, room, dispatched) -> None:
    d = await _seed()
    async with _core() as c:
        out = await _play(c, d, shuffle=True)
    files = await _files(room)
    assert sorted(files) == sorted(_road_trip(d)) and len(set(files)) == 20
    assert out["mode"] == "shuffle" and out["queued"] == 20
    # The song it starts on is the queue's first.
    assert d["uri"][out["track_id"]] == files[0]
    assert (await _active()).sess.seed.kind == "playlist"


@requires_db
async def test_favorites_play_as_their_own_list(_db, clean, room, dispatched) -> None:
    d = await _seed()
    favs = [d["ids"][7], d["ids"][3], d["ids"][40]]
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE library_tracks SET favorited = true WHERE id = ANY(:i)"),
                           {"i": favs})
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-playlist", json={"room_id": ROOM, "playlist_id": 0})
    assert r.status_code == 200, r.text
    assert r.json()["playlist_name"] == "Favorites"
    assert await _files(room) == [d["uri"][t] for t in sorted(favs)]
    st = await _active()
    assert st.sess.mode == "continue" and st.sess.seed.kind == "ids"
    assert sorted(st.sess.seed.track_ids) == sorted(favs) and st.sess.seed.label == "from favorites"


# ─── Up next at the end ───────────────────────────────────────────────────


@requires_db
async def test_picks_join_when_five_are_left(_db, clean, room, dispatched) -> None:
    d = await _seed()
    async with _core() as c:
        await _play(c, d)
    ids = await _ids(room)
    await room.resume()
    assert (await _look(room))["queued"] == 0             # 19 left
    await room.play_id(ids[-6])                            # 5 left after it
    did = await _look(room)
    assert did["queued"] > 0
    files = await _files(room)
    assert files[:20] == _road_trip(d)                     # the playlist stays as it was
    picks = await _picks((await _active()).sess.id)
    assert [e["id"] for e in (await room.queue_list())[20:]] == [p["mpd_song_id"] for p in picks]


@requires_db
async def test_autoplay_off_stops_at_the_end(_db, clean, room, dispatched) -> None:
    d = await _seed()
    async with _core() as c:
        await _play(c, d, autoplay=False)
    assert (await _active()).sess.autoplay is False
    ids = await _ids(room)
    await room.play_id(ids[-1])                            # the last song
    did = await _look(room)
    assert did["queued"] == 0 and did["removed"] == 0
    assert await _files(room) == _road_trip(d)


@requires_db
async def test_up_next_off_plays_the_playlist_alone(_db, clean, room, dispatched) -> None:
    d = await _seed()
    await _library_start(room, d)
    async with _core() as c:
        out = await _play(c, d, up_next=False)
    assert out["up_next"] is None and await _active() is None
    assert await _files(room) == _road_trip(d)


@requires_db
async def test_an_edited_queue_still_extends_at_its_end(_db, clean, room, dispatched) -> None:
    d = await _seed()
    async with _core() as c:
        await _play(c, d)
        ids = await _ids(room)
        await room.resume()
        assert (await c.post(f"/v1/admin/music/queue/{ROOM}/remove",
                             json={"song_ids": [ids[3], ids[4], ids[5]]})).status_code == 200
        assert (await c.post(f"/v1/admin/music/queue/{ROOM}/move",
                             json={"song_id": ids[19], "to_position": 1})).status_code == 200
        assert (await c.post(f"/v1/admin/music/queue/{ROOM}/add",
                             json={"track_ids": [d["ids"][50]]})).status_code == 200
        queue = await room.queue_list()
        assert len(queue) == 18 and queue[1]["id"] == ids[19]
        # Near the end of the edited queue: the picks go after its last song.
        last = queue[-1]["id"]
        await room.play_id(queue[-6]["id"])
        did = await _look(room)
        assert did["queued"] > 0
        after = await room.queue_list()
        assert [e["id"] for e in after[:18]] == [e["id"] for e in queue]
        assert after[17]["id"] == last and len(after) == 18 + did["queued"]
        picks = await _picks((await _active()).sess.id)
        assert {p["status"] for p in picks} == {"queued"}


# ─── The queue is a copy: the playlist never changes ──────────────────────


@requires_db
async def test_editing_the_rooms_queue_never_changes_the_playlist(_db, clean, room, dispatched) -> None:
    d = await _seed()
    before = await _playlist_rows(d["playlist"])
    async with engine.begin() as conn:
        stamp = (await conn.execute(text("SELECT updated_at, resume_position FROM playlists WHERE id = :p"),
                                    {"p": d["playlist"]})).one()
    async with _core() as c:
        await _play(c, d)
        await room.resume()
        ids = await _ids(room)
        for path, body in (
            ("remove", {"song_ids": [ids[2], ids[9]]}),
            ("move", {"song_id": ids[15], "to_position": 0}),
            ("add", {"track_ids": [d["ids"][45], d["ids"][46]], "where": "next"}),
            ("add", {"track_ids": [d["ids"][47]]}),
            ("play", {"song_id": ids[12]}),
        ):
            r = await c.post(f"/v1/admin/music/queue/{ROOM}/{path}", json=body)
            assert r.status_code == 200, (path, r.text)
            assert await _playlist_rows(d["playlist"]) == before, path
        await room.play_id((await _ids(room))[-3])
        await _look(room)                                  # picks joined the queue
        assert await _playlist_rows(d["playlist"]) == before
        r = await c.post(f"/v1/admin/music/queue/{ROOM}/clear", json={})
        assert r.status_code == 200
    assert await _files(room) == []
    assert await _playlist_rows(d["playlist"]) == before
    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT updated_at, resume_position FROM playlists WHERE id = :p"),
                                   {"p": d["playlist"]})).one() == stamp
        # No playlist was made along the way either.
        assert (await conn.execute(text("SELECT count(*) FROM playlists"))).scalar() == 1


# ─── Skipped songs ────────────────────────────────────────────────────────


@requires_db
async def test_missing_and_refused_songs_are_skipped_and_counted(_db, clean, room, dispatched) -> None:
    d = await _seed()
    outside = d["ids"][4]
    async with engine.begin() as conn:                    # a file outside the music folder
        await conn.execute(text("UPDATE library_tracks SET file_path = :f WHERE id = :t"),
                           {"f": str(MUSIC.parent / "elsewhere" / "x.flac"), "t": outside})
    refused = d["uri"][d["ids"][9]]
    room.refused_files = frozenset({refused})
    async with _core() as c:
        out = await _play(c, d)
    assert (out["total"], out["queued"], out["skipped"]) == (20, 18, 2)
    assert await _files(room) == [u for t, u in zip(d["ids"][:20], _road_trip(d))
                                  if t != outside and u != refused]


@requires_db
async def test_a_playlist_with_nothing_playable_leaves_the_room_alone(_db, clean, room, dispatched) -> None:
    d = await _seed()
    await _library_start(room, d)
    before = await _ids(room)
    room.refused_files = frozenset(_road_trip(d))
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-playlist", json={"room_id": ROOM, "playlist_id": d["playlist"]})
        assert r.status_code == 404 and "rescan" in r.json()["detail"]
        missing = await c.post("/v1/admin/music/play-playlist", json={"room_id": ROOM, "playlist_id": 999})
        assert missing.status_code == 404
    assert await _ids(room) == before


# ─── A long playlist ──────────────────────────────────────────────────────


def _gate(monkeypatch, stub) -> "asyncio.Event":
    """Hold the stub's appends until the test opens the gate (the stub
    answers at once, so the loader would otherwise be done before the
    test looks)."""
    import asyncio

    gate = asyncio.Event()
    add = stub.queue_add_files

    async def _held(uris, **kw):
        await gate.wait()
        return await add(uris, **kw)

    monkeypatch.setattr(stub, "queue_add_files", _held)
    return gate


@requires_db
async def test_a_long_playlist_starts_at_once_and_the_rest_follows(_db, clean, room, dispatched,
                                                                   monkeypatch) -> None:
    d = await _seed()
    monkeypatch.setattr(room_playlist, "FIRST_CHUNK", 4)
    monkeypatch.setattr(room_playlist, "REST_CHUNK", 5)
    gate = _gate(monkeypatch, room)
    async with _core() as c:
        out = await _play(c, d)
        assert out["queued"] == 4 and out["loading"] is True
        assert ROOM in rooms.LOADING
        # Near the end of what is in so far: no picks while it loads.
        await room.resume()
        await room.play_id((await _ids(room))[-1])
        assert (await _look(room))["queued"] == 0
        gate.set()
        await room_playlist.LOADERS[ROOM]
    assert await _files(room) == _road_trip(d)
    assert ROOM not in rooms.LOADING and ROOM not in room_playlist.LOADERS


@requires_db
async def test_a_replaced_queue_stops_the_loader(_db, clean, room, dispatched, monkeypatch) -> None:
    d = await _seed()
    monkeypatch.setattr(room_playlist, "FIRST_CHUNK", 4)
    monkeypatch.setattr(room_playlist, "REST_CHUNK", 5)
    gate = _gate(monkeypatch, room)
    async with _core() as c:
        await _play(c, d)
        task = room_playlist.LOADERS[ROOM]
        gate.set()
        await room.prepare_files(["Other/1.flac"])         # something else took the room
        await task
    assert await _files(room) == ["Other/1.flac"]
    assert ROOM not in rooms.LOADING


# ─── Voice and the web ────────────────────────────────────────────────────


@requires_db
async def test_voice_play_of_a_playlist_in_a_room(_db, clean, room) -> None:
    from domovoi.db.session import session_scope
    from domovoi.handlers.playlist import PlaylistHandler
    from domovoi.models import Context

    d = await _seed()
    room.refused_files = frozenset({d["uri"][d["ids"][2]]})
    ctx = Context(room_id=ROOM, online=True, bot_name="domovoi", app=core_main.app,
                  person_id=d["alex"])
    async with session_scope() as s:
        resp = await PlaylistHandler()._play_by_name("road trip", ctx, s, mode="ordered")
    assert resp.music_action == "start"
    assert resp.text == "Playing Road Trip. I skipped 1 song I couldn't find."
    assert len(await _files(room)) == 19
    st = await _active()
    assert st.sess.mode == "continue" and st.sess.person_id == d["alex"]
    assert resp.data["up_next"] == {"session_id": st.sess.id}
    # "next" is the queue's own next now.
    from domovoi.handlers.music import MusicHandler

    await room.resume()
    async with session_scope() as s:
        nxt = await MusicHandler()._smart_skip(ctx, s)
    assert nxt.text == "Next track."
    assert (await room.current_song())["file"] == _road_trip(d)[1]


@requires_db
async def test_web_play_playlist_carries_the_listener(_db, clean, room, dispatched, bridged) -> None:
    d = await _seed()
    token = await _household()
    async with _dev(token, "browser-pl1") as c:
        r = await c.post("/api/music/play-playlist", json={
            "room_id": ROOM, "playlist_id": d["playlist"], "listener_person_id": d["alex"]})
        assert r.status_code == 200, r.text
        assert r.json()["queued"] == 20
    st = await _active()
    assert st.sess.person_id == d["alex"] and st.sess.seed.kind == "playlist"


# ─── The old mechanism is gone ────────────────────────────────────────────


def test_the_one_song_at_a_time_playlist_state_is_gone() -> None:
    from domovoi.handlers.music import MusicHandler

    assert not hasattr(MusicHandler, "_maybe_skip_in_playlist")
    src = open(core_main.__file__, encoding="utf-8").read()
    assert "current_playlist" not in src
