"""Podcasts and audiobooks in another room (domovoi/handlers/spoken_audio_rooms.py).

"play the latest episode of The Daily in the garage", "play my audiobook in
the office", "resume the podcast in the kitchen": SpokenAudioHandler (band
270) used to read "… in the garage" as part of the show's or the book's
name, or ignore it, and play in the room that asked. Pinned here, with the
spoken-audio store faked (domovoi.spoken_audio) and stub players: the
routing, the play in the target room with its own resume position, the
confirmation said in the asking room, and the same room answers as music
(here, everywhere, unknown, offline, a satellite without a pairing token,
a whole name that ends in a place).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from domovoi import spoken_audio as sa
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.models import Context, Response
from domovoi.room_music import drain
from domovoi.router import normalize_transcript, plan_route, strip_leading_filler
from domovoi.tests.test_music_rooms import HOUSE, PORTS, FakeDB, _app, _Result

EPISODE = {"id": 31, "title": "The Morning Edition", "file_path": "/pod/daily/ep31.mp3",
           "duration_sec": 1500, "chapters": None, "download_status": "downloaded",
           "show_title": "The Daily"}
BOOK = {"id": 4, "title": "Dune", "author": "Frank Herbert", "file_path": "/books/dune.m4b",
        "duration_sec": 70000, "chapters": None, "is_folder": False}


class _Mapped(_Result):
    def mappings(self):
        return self


class SpokenDB(FakeDB):
    """FakeDB plus the item lookup a resume makes."""

    async def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        if "FROM audiobooks WHERE id" in q:
            return _Mapped([{k: BOOK[k] for k in ("title", "file_path", "chapters", "duration_sec")}])
        if "FROM podcast_episodes WHERE id" in q:
            return _Mapped([{**{k: EPISODE[k] for k in ("title", "file_path", "chapters",
                                                          "duration_sec", "download_status")}}])
        return await super().execute(stmt, params)


@pytest.fixture
def store(monkeypatch):
    clients = {r: MPDStubClient() for r in PORTS}
    monkeypatch.setattr(mpd_module, "_clients", clients)
    monkeypatch.setattr(mpd_module, "_room_ports", dict(PORTS))
    st = SimpleNamespace(
        clients=clients, shows={"the daily": EPISODE}, books={"dune": BOOK},
        in_progress={}, positions=[], asked=[],
    )

    def _find(table):
        async def find(session, query):
            q = query.lower().strip()
            st.asked.append(q)
            rows = table()  # substring match, an exact one first (as the SQL orders it)
            return rows.get(q) or next((v for k, v in rows.items() if q in k), None)
        return find

    monkeypatch.setattr(sa, "latest_episode_for_show", _find(lambda: st.shows))
    monkeypatch.setattr(sa, "subscription_for_show", _find(
        lambda: {k: {"id": v["id"], "title": v["show_title"]} for k, v in st.shows.items()}))
    monkeypatch.setattr(sa, "find_audiobook", _find(lambda: st.books))

    async def _in_progress(session, *, device_id, person_id, item_type=None):
        return st.in_progress.get((device_id, item_type))

    async def _get_position(session, **kw):
        return None

    async def _upsert(session, **kw):
        st.positions.append(kw)

    monkeypatch.setattr(sa, "most_recent_in_progress", _in_progress)
    monkeypatch.setattr(sa, "get_position", _get_position)
    monkeypatch.setattr(sa, "upsert_position", _upsert)
    monkeypatch.setattr(sa, "mpd_uri_for", lambda item_type, path: f"spoken{path}")

    async def _no_provisioning(room_id):
        raise AssertionError(f"provisioned {room_id!r}")

    import domovoi.mpd_provisioner as prov

    monkeypatch.setattr(prov, "ensure_room", _no_provisioning)
    from domovoi import guest_queue

    guest_queue.clear_activity_cache()
    return st


def _spoken():
    return HANDLER_BY_NAME["spoken_audio"]


def _ctx(room="dining_room", app=None, **kw) -> Context:
    return Context(room_id=room, app=app if app is not None else _app(), person_id=7, **kw)


async def _say(transcript: str, ctx: Context) -> Response | None:
    t = strip_leading_filler(normalize_transcript(transcript))
    plan = plan_route(t)
    assert plan is not None, transcript
    response = await plan.fast_path.method(plan.handler, plan.match, ctx, SpokenDB())
    await drain()
    return response


def _started(ctx, room):
    return ctx.app.state.active_sessions[room].started


# ─── routing ──────────────────────────────────────────────────────────────


def _path(transcript: str) -> str | None:
    plan = plan_route(transcript)
    return None if plan is None else f"{plan.handler.name}.{plan.fast_path.method.__name__}"


@pytest.mark.parametrize("transcript, path", [
    ("play the latest episode of the daily in the garage", "spoken_audio._room_spoken_from_match"),
    ("play the newest daily podcast in the garage", "spoken_audio._room_spoken_from_match"),
    ("play the audiobook dune in the garage", "spoken_audio._room_spoken_from_match"),
    ("read me the book dune in the garage", "spoken_audio._room_spoken_from_match"),
    ("resume the podcast in the kitchen", "spoken_audio._room_spoken_from_match"),
    ("resume my book in the office", "spoken_audio._room_spoken_from_match"),
    ("continue my audiobook in the garage", "spoken_audio._room_spoken_from_match"),
    ("keep reading in the garage", "spoken_audio._room_spoken_from_match"),
    ("play my audiobook in the office", "spoken_audio._room_spoken_from_match"),
    ("Play the audio book Dune in the Garage.", "spoken_audio._room_spoken_from_match"),
    ("play my audio book", "spoken_audio._play_mine_from_match"),
    ("resume my audio book", "spoken_audio._resume_from_match"),
    # without a place: the paths as they were (and "play my audiobook" is
    # a resume now, not a music search for "my audiobook")
    ("play the latest episode of the daily", "spoken_audio._play_latest_from_match"),
    ("play the audiobook dune", "spoken_audio._play_book_from_match"),
    ("resume my book", "spoken_audio._resume_from_match"),
    ("play my audiobook", "spoken_audio._play_mine_from_match"),
    ("play the podcast", "spoken_audio._play_mine_from_match"),
    # music's room commands are still music's
    ("play weezer in the garage", "music._room_play_from_match"),
    ("stop the music in the garage", "music._room_control_from_match"),
    ("play the last of the mohicans in the den", "music._room_play_from_match"),
])
def test_where_each_phrasing_routes(transcript, path) -> None:
    assert _path(transcript) == path


def test_the_room_path_comes_first_and_never_commits_early() -> None:
    from domovoi.early_commit import early_commit_for

    paths = _spoken().fast_paths
    assert paths[0].method.__name__ == "_room_spoken_from_match"
    assert paths[0].early_commit is None
    for said in ("resume my book in the garage", "play my audiobook in the office"):
        assert early_commit_for(said) is None


# ─── in another room ──────────────────────────────────────────────────────


async def test_the_latest_episode_plays_in_the_garage(store) -> None:
    ctx = _ctx()
    resp = await _say("Play the latest episode of The Daily in the garage.", ctx)
    assert resp.text == "Playing The Morning Edition in the garage."
    assert "the daily" in store.asked and "the daily in the garage" in store.asked
    garage = store.clients["garage"]
    assert "spoken/pod/daily/ep31.mp3" in garage._song["file"] and garage._state == "pause"
    assert _started(ctx, "garage") == [mpd_module.mpd_stream_url_for("garage")]
    assert _started(ctx, "dining_room") == [] and resp.music_action is None
    # the garage's resume position, this person's
    assert store.positions[-1]["device_id"] == "garage" and store.positions[-1]["person_id"] == 7
    assert resp.data["room_id"] == "garage"


async def test_an_audiobook_by_name_in_a_room_by_its_label(store) -> None:
    ctx = _ctx()
    resp = await _say("play the audiobook dune in kamron's office", ctx)
    assert resp.text == "Playing Dune in Kamron's Office."
    assert "spoken/books/dune.m4b" in store.clients["kamron_office"]._song["file"]
    assert len(_started(ctx, "kamron_office")) == 1


@pytest.mark.parametrize("said, kind", [
    ("resume the podcast in the garage", sa.ITEM_PODCAST),
    ("resume my book in the garage", sa.ITEM_AUDIOBOOK),
    ("play my audiobook in the garage", sa.ITEM_AUDIOBOOK),
    ("keep reading in the garage", sa.ITEM_AUDIOBOOK),
    ("continue my audio book in the garage", sa.ITEM_AUDIOBOOK),
])
async def test_resume_picks_up_the_garages_own_place(store, said, kind) -> None:
    item_id = EPISODE["id"] if kind == sa.ITEM_PODCAST else BOOK["id"]
    store.in_progress[("garage", kind)] = {
        "item_type": kind, "item_id": item_id, "position_sec": 125, "speed": 1.0,
    }
    ctx = _ctx()
    resp = await _say(said, ctx)
    title = EPISODE["title"] if kind == sa.ITEM_PODCAST else BOOK["title"]
    assert resp.text == f"Playing {title} in the garage. Resuming at 2 minutes."
    assert len(_started(ctx, "garage")) == 1


async def test_nothing_in_progress_there_says_where(store) -> None:
    resp = await _say("resume my book in the garage", _ctx())
    assert resp.text == "I don't have a book in progress for you in the garage."


# ─── here, everywhere, unknown, offline, unpaired ─────────────────────────


async def test_in_here_and_the_asking_room_play_here(store) -> None:
    for said in ("play the audiobook dune in here", "play the audiobook dune in the dining room"):
        ctx = _ctx()
        resp = await _say(said, ctx)
        assert resp.text == "Playing Dune.", said
        assert resp.music_action == "start" and _started(ctx, "garage") == []


async def test_everywhere_is_one_room_at_a_time(store) -> None:
    resp = await _say("play the latest episode of the daily everywhere", _ctx())
    assert resp.text.startswith("I can only play that in one room at a time.")


async def test_a_room_the_house_does_not_have(store) -> None:
    resp = await _say("resume the podcast in the attic", _ctx())
    assert resp.text.startswith("I don't have a room called the attic. I have the den, ")


async def test_an_offline_room(store) -> None:
    resp = await _say("play the audiobook dune in the kids room", _ctx())
    assert resp.text == "The speaker in the kids room is offline right now, so I can't play that there."


async def test_an_unpaired_satellite_cannot_start_a_book_elsewhere(store) -> None:
    resp = await _say("play the audiobook dune in the garage",
                      _ctx(app=_app(unpaired=("dining_room",))))
    assert resp.text.startswith("This speaker isn't paired with the house")
    assert store.clients["garage"]._song is None


async def test_from_v1_intent(store) -> None:
    resp = await _say("play the audiobook dune in the garage", Context(room_id="dining_room"))
    assert resp.text == "I can only reach another room's speaker from a room device or the app."


# ─── names that end in a place ───────────────────────────────────────────


async def test_a_show_whose_whole_name_ends_in_a_room_plays_here(store) -> None:
    store.shows = {"life in the garage": {**EPISODE, "title": "Ep 1", "show_title": "Life in the Garage"}}
    ctx = _ctx()
    resp = await _say("play the latest episode of life in the garage", ctx)
    assert resp.text == "Playing Ep 1."
    assert resp.music_action == "start" and _started(ctx, "garage") == []


async def test_words_before_the_place_that_name_another_show_mean_the_room(store) -> None:
    store.shows = {
        "life in the garage": {**EPISODE, "id": 40, "title": "Ep 1", "show_title": "Life in the Garage"},
        "life": {**EPISODE, "id": 41, "title": "Life Ep 9", "show_title": "Life"},
    }
    ctx = _ctx()
    resp = await _say("play the latest episode of life in the garage", ctx)
    assert len(_started(ctx, "garage")) == 1 and resp.text.endswith(" in the garage.")


async def test_a_book_that_ends_in_a_place_that_is_no_room(store) -> None:
    store.books = {"love in the time of cholera": {**BOOK, "title": "Love in the Time of Cholera"}}
    ctx = _ctx()
    resp = await _say("play the audiobook love in the time of cholera", ctx)
    assert resp.text == "Playing Love in the Time of Cholera."
    assert "love in the time of cholera" in store.asked


async def test_no_place_and_no_path_for_the_whole(store) -> None:
    store.in_progress[("dining_room", sa.ITEM_AUDIOBOOK)] = {
        "item_type": sa.ITEM_AUDIOBOOK, "item_id": 4, "position_sec": 0, "speed": 1.0,
    }
    resp = await _say("play my audiobook on repeat", _ctx())
    assert resp.text == "Playing Dune."


# ─── the language model's tool ────────────────────────────────────────────


async def test_the_tool_takes_a_room(store) -> None:
    spoken = _spoken()
    ctx = _ctx()
    resp = await spoken.execute_from_tool(
        {"action": "play_book", "query": "dune", "room": "garage"}, ctx, SpokenDB(),
    )
    await drain()
    assert resp.text == "Playing Dune in the garage."
    assert len(_started(ctx, "garage")) == 1
    resp = await spoken.execute_from_tool({"action": "next_chapter", "room": "garage"}, ctx, SpokenDB())
    assert resp.text.startswith("I can start a podcast or an audiobook in another room")
    assert "room" in spoken.tool_schema["parameters"]["properties"]
