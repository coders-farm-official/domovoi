"""The room's queue and Up next by voice (phase P6, UP-NEXT-QUEUE-PLAN §7).

* which handler and path each phrasing reaches — the new ones, and the
  bait that must stay where it was ("play next" is a skip, "add milk to the
  shopping list" a list, "add this to my road trip playlist" a playlist);
* the room-suffixed forms ("what's next in the kitchen") and how a command
  for another room is checked (reads a guest may make, edits never);
* the "why is this playing" sentences, from the pick's own reason;
* the commands against a stub room player and the database (``requires_db``,
  a lane with V053): what's next, play X next / add X to the queue (and
  playing X when the room is idle), clear, skip this artist, no more
  <artist>, autoplay off / on (on with no session arms one), why, more like
  this, and a phone's turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from domovoi import main as core_main
from domovoi.handlers import HANDLERS
from domovoi.handlers.base import as_fast_path
from domovoi.handlers.music import MusicHandler
from domovoi.handlers.music_queue_voice import why_text
from domovoi.handlers.music_rooms import _ACTION_BY_METHOD
from domovoi.handlers.room_commands import GUEST_ACTIONS
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_up_next_rooms import (  # noqa: F401 — fixtures
    ROOM,
    _ids,
    _library_start,
    _picks,
    _seed,
    clean,
    room,
)
from domovoi.up_next import rooms, store


def _winner(text_: str) -> tuple[str, str] | None:
    for h in HANDLERS:
        for e in h.fast_paths:
            fp = as_fast_path(e)
            if fp.pattern.match(text_):
                return h.name, getattr(fp.method, "__name__", "")
    return None


# ─── Routing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("said,method", [
    ("what's next", "_whats_next_from_match"),
    ("what's up next", "_whats_next_from_match"),
    ("what's playing after this", "_whats_next_from_match"),
    ("what's after this song", "_whats_next_from_match"),
    ("play bohemian rhapsody next", "_play_next_from_match"),
    ("queue bohemian rhapsody next", "_play_next_from_match"),
    ("put on hello next", "_play_next_from_match"),
    ("add bohemian rhapsody to the queue", "_add_to_queue_from_match"),
    ("add hello to my queue", "_add_to_queue_from_match"),
    ("queue up bohemian rhapsody", "_add_to_queue_from_match"),
    ("queue bohemian rhapsody", "_add_to_queue_from_match"),
    ("clear the queue", "_clear_queue_from_match"),
    ("empty the queue", "_clear_queue_from_match"),
    ("skip this artist", "_skip_artist_from_match"),
    ("no more queen", "_no_more_artist_from_match"),
    ("turn autoplay off", "_autoplay_from_match"),
    ("turn on autoplay", "_autoplay_from_match"),
    ("autoplay off", "_autoplay_from_match"),
    ("stop after this list", "_autoplay_from_match"),
    ("why is this playing", "_why_from_match"),
    ("why this song", "_why_from_match"),
    ("why did you play this", "_why_from_match"),
    ("play something like this", "_more_like_from_match"),
    ("more like this", "_more_like_from_match"),
    ("play more like this", "_more_like_from_match"),
    ("what's next in the kitchen", "_room_control_from_match"),
    ("clear the queue in the office", "_room_control_from_match"),
    # Bait: a skip, never "play X next" nor a search for a song "next".
    ("play next", "_next_from_match"),
    ("play the next song", "_next_from_match"),
    ("next", "_next_from_match"),
])
def test_each_phrasing_reaches_its_path(said: str, method: str) -> None:
    assert _winner(said) == ("music", method)


@pytest.mark.parametrize("said,owner", [
    ("add milk to the shopping list", "lists"),
    ("clear the shopping list", "lists"),
    ("add this to my road trip playlist", "playlist"),
    ("add bohemian rhapsody after this", "playlist"),
    ("what's next on my calendar", "calendar"),
])
def test_the_bait_keeps_its_owner(said: str, owner: str) -> None:
    assert _winner(said)[0] == owner


@pytest.mark.parametrize("said", [
    "queue it up", "queue", "which song goes next on the playlist",
])
def test_no_queue_path_takes_these(said: str) -> None:
    w = _winner(said)
    assert w is None or not w[1].startswith(("_add_to_queue", "_play_next", "_whats_next"))


def test_play_x_next_never_reads_a_pointer_word_as_a_song() -> None:
    for said in ("play this next", "play that one next", "queue it next"):
        w = _winner(said)
        assert w is None or w[1] != "_play_next_from_match", said


def test_a_command_for_another_room_is_checked_as_a_read_or_an_edit() -> None:
    reads = {"_whats_next_from_match", "_why_from_match"}
    edits = {"_clear_queue_from_match", "_skip_artist_from_match", "_autoplay_from_match",
             "_more_like_from_match"}
    assert all(_ACTION_BY_METHOD[m] in GUEST_ACTIONS for m in reads)
    assert all(_ACTION_BY_METHOD[m] not in GUEST_ACTIONS for m in edits)
    h = MusicHandler()
    assert h._action_of("what's next") == "now_playing"
    assert h._action_of("clear the queue") == "queue"


def test_nothing_new_needs_the_internet() -> None:
    assert MusicHandler.requires_network == "no"


# ─── Why is this playing ──────────────────────────────────────────────────


def _st(mode: str = "library", seed: int = 1, name: str | None = None):
    sess = SimpleNamespace(mode=mode, seed=SimpleNamespace(track_id=seed))
    return SimpleNamespace(sess=sess, list_name=name)


@pytest.mark.parametrize("code,label,want", [
    ("same_artist", "same artist", "Up next picked it: same artist as the song you started with."),
    ("same_era", "90s", "Up next picked it: it's from the 90s, like the song you started with."),
    ("same_era", "from 1997-ish", "Up next picked it: it's from 1997-ish, like the song you started with."),
    ("same_genre", "soul", "Up next picked it: it's soul, like the song you started with."),
    ("played_together", "from road trip",
     "Up next picked it: it's on your road trip playlist with the song you started with."),
    ("played_together", "played together",
     "Up next picked it: it's often played together with the song you started with."),
    ("similar_artist", "similar to nina simone", "Up next picked it: it's by an artist similar to nina simone."),
])
def test_why_says_the_picks_own_reason(code: str, label: str, want: str) -> None:
    pick = {"reason_code": code, "reason_label": label, "seed_track_id": 1}
    assert why_text(pick, {"id": 1, "title": "Song"}, _st()) == want


def test_why_names_a_later_seed_and_never_a_person() -> None:
    pick = {"reason_code": "same_artist", "reason_label": "same artist", "seed_track_id": 7}
    text_ = why_text(pick, {"id": 7, "title": "Feeling Good"}, _st(seed=1))
    assert text_ == "Up next picked it: same artist as Feeling Good."
    assert "wildcard" in why_text({"reason_code": "wildcard", "reason_label": "wildcard"}, None, _st())


def test_why_for_a_song_somebody_asked_for() -> None:
    assert why_text(None, None, None) == "It was asked for. It isn't an Up next pick."
    assert why_text(None, None, _st("continue", name="Road Trip")) == (
        "It's part of Road Trip, which was asked for.")


# ─── Against a room (database) ────────────────────────────────────────────


def _ctx(person_id: int | None = None, **kw: Any) -> Context:
    return Context(room_id=ROOM, online=True, bot_name="domovoi", app=core_main.app,
                   person_id=person_id, **kw)


async def _say(text_: str, ctx: Context):
    from domovoi.db.session import session_scope

    h = MusicHandler()
    for entry in h.fast_paths:
        fp = as_fast_path(entry)
        m = fp.pattern.match(text_)
        if m is not None:
            async with session_scope() as s:
                return await fp.method(h, m, ctx, s)
    raise AssertionError(f"no music fast path for {text_!r}")


async def _active():
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await store.active_for(s, rooms.target_for(ROOM))


@requires_db
async def test_whats_next_says_the_next_two(clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    queue = await room.queue_list()
    resp = await _say("what's next", _ctx())
    assert resp.text.startswith("Next is ") and ", then " in resp.text
    assert queue[1]["title"] in resp.text


@requires_db
async def test_nothing_playing(clean, room) -> None:
    await _seed()
    assert (await _say("what's next", _ctx())).text == "Nothing is playing in here right now."
    assert (await _say("why is this playing", _ctx())).text == "Nothing is playing in here right now."


@requires_db
async def test_play_next_and_add_to_the_queue_place_the_song(clean, room, monkeypatch) -> None:
    d = await _seed()
    await _library_start(room, d)
    before = await _ids(room)

    async def resolved(self, query, session):
        return {"forty": ([d["ids"][40]], "Song 40"), "forty one": ([d["ids"][41]], "Song 41")}[query]

    monkeypatch.setattr(MusicHandler, "_resolve_tracks", resolved)
    resp = await _say("play forty next", _ctx())
    assert resp.text == "Okay, Song 40 plays next."
    q = await room.queue_list()
    assert q[1]["file"] == d["uri"][d["ids"][40]]
    resp = await _say("add forty one to the queue", _ctx())
    assert resp.text == "Added Song 41 to the queue."
    q = await room.queue_list()
    # After what was asked for, before the first Up next pick.
    assert q[2]["file"] == d["uri"][d["ids"][41]]
    assert [int(e["id"]) for e in q[3:]] == before[1:]


@requires_db
async def test_queue_up_plays_when_the_room_is_idle(clean, room, monkeypatch) -> None:
    await _seed()
    played: list[dict] = []

    async def fake_play(self, query, ctx, session, **kw):
        played.append(query)
        return SimpleNamespace(text="Playing it.")

    monkeypatch.setattr(MusicHandler, "_play", fake_play)
    resp = await _say("queue up song 5", _ctx())
    assert resp.text == "Playing it." and played == [{"any": "song 5"}]


@requires_db
async def test_an_unknown_song_is_said_plainly(clean, room, monkeypatch) -> None:
    d = await _seed()
    await _library_start(room, d)

    async def unresolved(self, query, session):
        return f"I couldn't find {query} in your library."

    monkeypatch.setattr(MusicHandler, "_resolve_tracks", unresolved)
    resp = await _say("add zzz to the queue", _ctx())
    assert resp.text == "I couldn't find zzz in your library."


@requires_db
async def test_clear_keeps_the_song_playing_and_says_up_next_goes_on(clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    first = (await _ids(room))[0]
    resp = await _say("clear the queue", _ctx())
    assert resp.text == "Cleared. Up next will keep going."
    assert await _ids(room) == [first]
    st = await _active()
    st.sess.autoplay = False
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        locked = await store.lock(s, st.sess.id)
        locked.sess.autoplay = False
        await store.save(s, locked)
    assert (await _say("clear the queue", _ctx())).text == "Cleared."


@requires_db
async def test_skip_this_artist_skips_and_leaves_them_out(clean, room) -> None:
    d = await _seed()
    sid = await _library_start(room, d)
    playing = (await room.current_song())["file"]
    artist = playing.split("/")[0]
    resp = await _say("skip this artist", _ctx(person_id=d["alex"]))
    assert resp.text == f"Okay, no more {artist} in this mix."
    st = await _active()
    assert st.sess.id == sid and artist.lower() in st.sess.excluded_artists
    assert st.sess.person_id == d["alex"]                  # the speaker's mix now
    files = [e["file"] for e in await room.queue_list()]
    current = (await room.current_song())["file"]
    assert not current.startswith(f"{artist}/")
    waiting = [p for p in await _picks(sid) if p["status"] == "queued"]
    assert all(p["artist"] != artist for p in waiting)
    assert not [f for f in files[files.index(current) + 1:] if f.startswith(f"{artist}/")]


@requires_db
async def test_no_more_an_artist_by_name(clean, room, monkeypatch) -> None:
    from domovoi.handlers.shared import library_match

    d = await _seed()
    sid = await _library_start(room, d)
    otis = [t for i, t in enumerate(d["ids"]) if i % 6 == 1]

    async def resolve(session, query, *, kinds=None):
        if query.get("artist") == "otis":
            cand = SimpleNamespace(track_ids=tuple(otis), speak="Otis",
                                   ref=SimpleNamespace(type="artist"))
            return SimpleNamespace(decision="play", candidates=(cand,))
        return SimpleNamespace(decision="none", candidates=())

    monkeypatch.setattr(library_match, "resolve_request", resolve)
    resp = await _say("no more otis", _ctx())
    assert resp.text == "Okay, no more Otis in this mix."
    st = await _active()
    assert "otis" in st.sess.excluded_artists
    assert all(p["artist"] != "Otis" for p in await _picks(sid) if p["status"] == "queued")
    # Not an artist in the library: the turn is someone else's.
    assert await _say("no more timers", _ctx()) is None


@requires_db
async def test_autoplay_off_and_on(clean, room) -> None:
    d = await _seed()
    await _library_start(room, d)
    resp = await _say("turn autoplay off", _ctx())
    assert resp.text == "Autoplay is off. The music stops when the queue ends."
    assert (await _active()).sess.autoplay is False
    resp = await _say("turn on autoplay", _ctx())
    assert resp.text.startswith("Autoplay is on.")
    assert (await _active()).sess.autoplay is True
    assert (await _say("stop after this list", _ctx())).text == "Okay, the music stops when the queue ends."


@requires_db
async def test_autoplay_on_with_no_session_arms_one_for_the_queue(clean, room) -> None:
    d = await _seed()
    await room.prepare_files([d["uri"][d["ids"][i]] for i in (0, 1, 2)])
    await room.resume()
    resp = await _say("turn on autoplay", _ctx())
    assert resp.text.startswith("Autoplay is on.")
    st = await _active()
    assert st.sess.mode == "continue" and list(st.sess.seed.track_ids) == d["ids"][:3]
    assert st.list_name == "this queue"


@requires_db
async def test_why_this_pick_and_more_like_it(clean, room) -> None:
    d = await _seed()
    old = await _library_start(room, d)
    ids = await _ids(room)
    await room.play_id(ids[3])
    resp = await _say("why is this playing", _ctx())
    assert resp.text.startswith("Up next picked it")
    # "More like this": a new library session seeded by the song playing now;
    # the old waiting picks make way, the song plays on.
    cur = await room.current_song()
    resp = await _say("play something like this", _ctx())
    assert resp.text.startswith("Okay. Songs like ")
    st = await _active()
    assert st.sess.id != old and st.sess.mode == "library"
    track_by_uri = {u: t for t, u in d["uri"].items()}
    assert st.sess.seed.track_id == track_by_uri[cur["file"]]
    after = await room.queue_list()
    assert (await room.current_song())["id"] == cur["id"]
    pos = [e["id"] for e in after].index(cur["id"])
    old_waiting = set(ids[4:])
    assert not old_waiting & {e["id"] for e in after[pos + 1:]}
    assert len(after) > pos + 1                            # the new mix is queued


@requires_db
async def test_more_like_a_stream_says_it_cant(clean, room) -> None:
    await _seed()
    await room.prepare_url("http://radio.example/stream")
    await room.resume()
    resp = await _say("more like this", _ctx())
    assert resp.text == "This isn't a song from your library, so I can't find more like it."


@requires_db
async def test_a_phone_is_pointed_at_its_queue_screen(clean, room) -> None:
    await _seed()
    ctx = _ctx(device_kind="phone")
    if not ctx.from_phone:
        pytest.skip("this Context has no phone marker")
    resp = await _say("what's next", ctx)
    assert "queue screen" in resp.text
