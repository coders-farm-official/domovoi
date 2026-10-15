"""Music in another room by voice (domovoi/handlers/music_rooms.py).

Incident 2026-10-14: the dining room was asked to "play some music in the
garage" and searched the library for "some music in the garage" in the
dining room. Pinned here:

* the house's rooms and the place a request names (ids, labels, "the",
  plural / possessive slips, word order; here; everywhere; a room the
  house doesn't have; a song that merely ends in a place);
* routing: the room paths sit in MusicHandler's band, before its "by"
  split and its catch-all, and never commit early; lyric requests stay
  whole;
* the play runs in the target room's player, its music_start goes to the
  target's satellite, and the asking room hears "Playing X in the garage";
* the edge cases: here, everywhere, an unknown room, an offline room, a
  room with no music player yet (started on demand, by its own id), a
  satellite without a pairing token (a guest's controls on a playing room
  only), the control commands, the tool call, a "did you mean" asked about
  another room;
* the song-or-room decision, through the resolver: "In the Garage" by
  Weezer stays a song when that is what was said (fake resolver here; the
  real one over a real library in the DB half at the end).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Mapping
from uuid import uuid4

import pytest

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers import HANDLER_BY_NAME, music_rooms
from domovoi.handlers.music import MusicHandler
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared import rooms as rooms_mod
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.handlers.shared.rooms import Room, read_place, room_key, spoken_room
from domovoi.models import Context, Response
from domovoi.room_music import drain
from domovoi.router import normalize_transcript, plan_route, strip_leading_filler
from domovoi.tests.conftest import requires_db

# ─── the house ────────────────────────────────────────────────────────────

# (room_id, label, sat_type, seen) — what the rooms query returns.
HOUSE = [
    ("dining_room", "Dining Room", "voice", True),
    ("garage", None, "voice", True),
    ("kamron_office", "Kamron's Office", "voice", True),
    ("kids_room", None, "voice", True),
    ("den_tablet", "Den", "voice", True),        # a room-mode phone (board android_room)
    ("study_laptop", "Study", "voice", True),    # a laptop satellite (board linux_pc)
    ("living_room", "Living Room", "voice", True),
    ("living_room_tv", "Living Room", "video", True),
    ("porch", None, "voice", False),             # adopted, never connected
]
PORTS = {
    "dining_room": (6650, 8050), "garage": (6651, 8051), "kamron_office": (6652, 8052),
    "kids_room": (6653, 8053), "den_tablet": (6654, 8054), "study_laptop": (6655, 8055),
    "living_room": (6656, 8056), "living_room_tv": (6657, 8057),
}
ONLINE = ["dining_room", "garage", "kamron_office", "den_tablet", "study_laptop",
          "living_room", "living_room_tv"]  # kids_room is offline


def _rooms(**over) -> list[Room]:
    out = []
    for room_id, label, sat_type, seen in HOUSE:
        out.append(Room(room_id=room_id, label=label, sat_type=sat_type, seen=seen,
                        online=room_id in ONLINE, has_player=room_id in PORTS))
    return out


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """The two reads these paths make without the resolver: the house's
    rooms, and a random library track. Anything else is "no database"."""

    def __init__(self, house=HOUSE) -> None:
        self.house = house

    async def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        if "UNION SELECT room_id FROM mpd_rooms" in q:
            return _Result(self.house)
        if "ORDER BY RANDOM()" in q:
            return _Result([SimpleNamespace(id=7, title="Ember Waltz", artist="Hearth Cats",
                                            file_path="/music/ember_waltz.mp3")])
        raise RuntimeError("no database in this test")


class FakeSat:
    """A connected room in app.state.active_sessions."""

    def __init__(self, room_id: str, *, paired: bool = True) -> None:
        self.room_id = room_id
        self.token_authenticated = paired
        self.started: list[str] = []
        self.frames: list[dict] = []
        self.volumes: list[int] = []

    async def start_music(self, url: str) -> bool:
        self.started.append(url)
        return True

    async def _safe_send_text(self, frame: dict) -> None:
        self.frames.append(frame)

    async def set_output_volume(self, level: int) -> None:
        self.volumes.append(level)


def _app(online=ONLINE, unpaired=()) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(
        active_sessions={r: FakeSat(r, paired=r not in unpaired) for r in online},
        resumable_music={}, pending_music_start={}, satellite_volume={},
        current_playlist={},
    ))


# ─── a fake resolver over a tiny library ─────────────────────────────────

WEEZER = EntityRef(type="artist", key="weezer", label="Weezer")
IN_THE_GARAGE = EntityRef(type="title", key="inthegarage|weezer", label="In the Garage",
                          artist_label="Weezer")
DANCING_TITLE = EntityRef(type="title", key="dancinginthekitchen|analark",
                          label="Dancing in the Kitchen", artist_label="Ana Lark")
DANCING_ARTIST = EntityRef(type="artist", key="dancing", label="Dancing")


def _fold(text: str) -> str:
    t = " ".join((text or "").lower().replace("'", "").split())
    for carrier in ("the song ", "the track "):
        if t.startswith(carrier):
            t = t[len(carrier):]
    return t.replace(" ", "")


@dataclass
class Library:
    names: dict[str, EntityRef] = field(default_factory=lambda: {
        "weezer": WEEZER, "inthegarage": IN_THE_GARAGE,
    })
    asked: list[str] = field(default_factory=list)
    played: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def resolve_request(self, session, query: Mapping[str, str], *, kinds=None) -> Resolution:
        heard = (query.get("any") or query.get("title") or query.get("artist") or "").strip()
        self.asked.append(heard)
        ref = self.names.get(_fold(heard))
        if ref is None:
            return Resolution.none("below_ask", heard=heard)
        return Resolution("play", (Candidate(ref=ref, score=1.0, via="exact", speak=ref.label,
                                             track_ids=(1,)),), heard=heard, reason="exact")

    async def candidate_from_ref(self, session, ref: Any) -> Candidate | None:
        if not isinstance(ref, EntityRef):
            ref = EntityRef.from_dict(ref)
        return Candidate(ref=ref, score=1.0, via="ref", speak=ref.label, track_ids=(1,))


@pytest.fixture
def house(monkeypatch):
    """Stub players for the provisioned rooms, the fake library, no
    provisioning (a test that wants it patches ensure_room)."""
    clients = {r: MPDStubClient() for r in PORTS}
    monkeypatch.setattr(mpd_module, "_clients", clients)
    monkeypatch.setattr(mpd_module, "_room_ports", dict(PORTS))
    lib = Library()
    monkeypatch.setattr(library_match, "resolve_request", lib.resolve_request)
    monkeypatch.setattr(library_match, "candidate_from_ref", lib.candidate_from_ref)

    async def _play_candidate(self, candidate, ctx, session, *, heard="", lyrics_note=False):
        lib.played.append((candidate.ref.label, ctx.room_id, ctx.asked_from_room))
        await mpd_module.get_mpd_client_for(ctx.room_id).prepare_files([f"{candidate.ref.label}.mp3"])
        by = f" by {candidate.ref.artist_label}" if candidate.ref.artist_label else ""
        text = (f"Playing {candidate.ref.label}, shuffled." if candidate.ref.type == "artist"
                else f"Playing {candidate.ref.label}{by}.")
        return Response(text=text, session_id=ctx.session_id, matched_handler="music",
                        music_action="start",
                        music_stream_url=mpd_module.mpd_stream_url_for(ctx.room_id))

    monkeypatch.setattr(MusicHandler, "play_candidate", _play_candidate)

    async def _no_provisioning(room_id):
        raise AssertionError(f"provisioned {room_id!r}")

    import domovoi.mpd_provisioner as prov

    monkeypatch.setattr(prov, "ensure_room", _no_provisioning)
    from domovoi import guest_queue

    guest_queue.clear_activity_cache()
    return SimpleNamespace(clients=clients, lib=lib)


def _music() -> MusicHandler:
    return HANDLER_BY_NAME["music"]  # the registered instance, as the router has it


def _ctx(room="dining_room", app=None, **kw) -> Context:
    return Context(room_id=room, app=app if app is not None else _app(), **kw)


async def _say(transcript: str, ctx: Context, db=None) -> Response | None:
    """What the router's fast-path step does with ``transcript``."""
    t = strip_leading_filler(normalize_transcript(transcript))
    plan = plan_route(t)
    assert plan is not None and plan.handler.name == "music", (transcript, plan)
    response = await plan.fast_path.method(plan.handler, plan.match, ctx, db or FakeDB())
    await drain()
    return response


def _sat(ctx: Context, room: str) -> FakeSat:
    return ctx.app.state.active_sessions[room]


# ─── reading the place (pure) ─────────────────────────────────────────────


@pytest.mark.parametrize("said, room_id", [
    ("in the garage", "garage"),
    ("in garage", "garage"),
    ("in the Garage", "garage"),
    ("in the garages", "garage"),              # a plural slip
    ("in the garage's", "garage"),             # a possessive slip
    ("in the garage speaker", "garage"),
    ("in the dining room", "dining_room"),     # id and label agree
    ("in the diningroom", "dining_room"),
    ("in kamron's office", "kamron_office"),   # the label
    ("in kamrons office", "kamron_office"),
    ("in the kid's room", "kids_room"),        # the id, possessive slip
    ("in the kids room", "kids_room"),
    ("in the den", "den_tablet"),              # a room-mode phone, by its label
    ("in the study", "study_laptop"),          # a laptop satellite, by its label
    ("on the porch", "porch"),
])
def test_a_place_names_one_of_the_houses_rooms(said, room_id) -> None:
    place = read_place(said, _rooms())
    assert place is not None and place.kind == "rooms", said
    assert [r.room_id for r in place.rooms] == [room_id]


def test_a_label_two_satellites_share_names_both_and_the_voice_one_is_picked() -> None:
    place = read_place("in the living room", _rooms())
    assert sorted(r.room_id for r in place.rooms) == ["living_room", "living_room_tv"]
    assert MusicHandler._pick_room(place.rooms).room_id == "living_room"


def test_word_order_is_forgiven_but_nothing_fuzzier() -> None:
    rooms = [Room(room_id="bathroom_upstairs", label="Upstairs Bathroom", online=True)]
    assert read_place("in the upstairs bathroom", rooms).kind == "rooms"
    assert read_place("in the bathroom upstairs", rooms).kind == "rooms"
    # A near miss is a room the house doesn't have, never the garage.
    assert read_place("in the garbage", _rooms()) is None
    assert read_place("in the garag", _rooms()) is None


@pytest.mark.parametrize("said, kind", [
    ("in here", "here"), ("in this room", "here"), ("right here", "here"),
    ("everywhere", "everywhere"), ("in every room", "everywhere"),
    ("in all the rooms", "everywhere"), ("throughout the house", "everywhere"),
    ("in the whole house", "everywhere"), ("all over the house", "everywhere"),
    ("in the attic", "unknown"), ("in the guest room", "unknown"),
    ("in the master bedroom", "unknown"), ("on the patio", "unknown"),
])
def test_here_everywhere_and_rooms_the_house_does_not_have(said, kind) -> None:
    place = read_place(said, _rooms())
    assert place is not None and place.kind == kind, said


@pytest.mark.parametrize("said", [
    "in the band", "at carnegie hall", "in the key of life", "on repeat",
    "on me", "in the moon in the kitchen", "in the time of cholera",
])
def test_a_song_that_merely_ends_in_a_place_names_no_place(said) -> None:
    assert read_place(said, _rooms()) is None


def test_how_a_room_is_said() -> None:
    assert spoken_room("garage") == "the garage"
    assert spoken_room("dining_room", "Dining Room") == "the dining room"
    assert spoken_room("kamron_office", "Kamron's Office") == "Kamron's Office"
    assert spoken_room("x", "the Annex") == "the Annex"
    assert room_key("The Kids' Room") == room_key("kids_room") == "kidroom"


def test_phone_ids_are_never_rooms() -> None:
    import asyncio

    app = _app(online=["garage", "phone-abc123"])
    rooms = asyncio.run(rooms_mod.household_rooms(FakeDB([("phone-x", None, "voice", True)]), app))
    assert [r.room_id for r in rooms] == ["garage"]


@pytest.mark.parametrize("text, said", [
    ("Playing Weezer, shuffled.", "Playing Weezer in the garage, shuffled."),
    ("Playing In the Garage by Weezer.", "Playing In the Garage by Weezer in the garage."),
    ("Playing X by Y, the song with those words.", "Playing X by Y in the garage, the song with those words."),
    ("Shuffling Road Trip.", "Shuffling Road Trip in the garage."),
    ("Paused.", "Paused the music in the garage."),
    ("Resuming.", "Resuming the music in the garage."),
    ("Stopped.", "Stopped the music in the garage."),
    ("Next track.", "Next track in the garage."),
    ("Previous track.", "Previous track in the garage."),
    ("This is Ember Waltz by Hearth Cats.", "The garage is playing Ember Waltz by Hearth Cats."),
    ("Nothing is playing right now.", "Nothing is playing in the garage right now."),
    ("Volume up to 7.", "Volume up to 7 in the garage."),
    ("Volume set to 4.", "Volume set to 4 in the garage."),
    ("I couldn't reach the music player.", "I couldn't reach the music player in the garage."),
    ("Did you mean Glass Harbor?", "Did you mean Glass Harbor?"),
])
def test_the_target_rooms_reply_is_said_where_it_was_asked(text, said) -> None:
    assert music_rooms.say_in_room(text, "the garage") == said


# ─── routing ──────────────────────────────────────────────────────────────


def _path(transcript: str) -> str | None:
    plan = plan_route(transcript)
    return None if plan is None else f"{plan.handler.name}.{plan.fast_path.method.__name__}"


@pytest.mark.parametrize("transcript, path", [
    ("play some music in the garage", "music._room_play_from_match"),
    ("Play music in the garage.", "music._room_play_from_match"),
    ("play weezer in the garage", "music._room_play_from_match"),
    ("put weezer on in the office", "music._room_play_from_match"),
    ("put on weezer in the office", "music._room_play_from_match"),
    ("can you play something in the dining room", "music._room_play_from_match"),
    ("play some jazz in here", "music._room_play_from_match"),
    ("play some music everywhere", "music._room_play_from_match"),
    ("play my road trip playlist in the garage", "music._room_play_from_match"),
    ("stop the music in the garage", "music._room_control_from_match"),
    ("pause in the garage", "music._room_control_from_match"),
    ("skip this song in the garage", "music._room_control_from_match"),
    ("turn it up in the garage", "music._room_control_from_match"),
    ("set the volume to 5 in the garage", "music._room_control_from_match"),
    ("what's playing in the garage", "music._room_control_from_match"),
    ("turn off the music in the garage", "music._room_control_from_match"),
    ("skip the song in the garage", "music._room_control_from_match"),
    ("shuffle in the garage", "music._room_control_from_match"),
    ("in the garage, play some jazz", "music._room_first_from_match"),
    # Not a place at the end: today's paths, unchanged.
    ("play in the garage", "music._play_any_from_match"),
    ("play in the garage by weezer", "music._play_artist_from_match"),
    ("play the song that goes we were in the garage", "music._lyrics_play_from_match"),
    ("stop the music", "music._stop_from_match"),
    ("what's playing", "music._now_playing_from_match"),
])
def test_where_each_phrasing_routes(transcript, path) -> None:
    assert _path(transcript) == path


def test_the_room_paths_are_musics_and_keep_the_bands() -> None:
    from domovoi.handlers import HANDLERS

    music = HANDLER_BY_NAME["music"]
    assert music.priority_band == 300
    names = [fp.method.__name__ for fp in music.fast_paths]
    room = names.index("_room_play_from_match")
    # After the lyric paths (a lyric is read whole), before the "by" split
    # and the catch-all.
    assert names.index("_lyrics_play_by_from_match") < room
    assert room < names.index("_play_artist_from_match") < names.index("_play_any_from_match")
    assert names.index("_room_control_from_match") < names.index("_stop_from_match")
    bands = [h.priority_band for h in HANDLERS]
    assert bands == sorted(bands)


@pytest.mark.parametrize("transcript", [
    "stop the music in the garage", "pause in the garage", "what's playing in the garage",
    "turn it up in the garage", "in the garage, stop the music", "play some jazz in the garage",
])
def test_a_room_command_never_ends_its_capture_early(transcript) -> None:
    from domovoi.early_commit import early_commit_for

    assert early_commit_for(transcript) is None


# ─── playing in another room ──────────────────────────────────────────────


async def test_the_incident_some_music_in_the_garage_plays_in_the_garage(house) -> None:
    ctx = _ctx()
    resp = await _say("Play some music in the garage.", ctx)
    # (a random library track; the stub player names every artist so)
    assert resp.text == "Playing Ember Waltz by Stub Artist in the garage."
    # The garage's player has it and the garage's satellite got the start;
    # the dining room's reply carries no music for the dining room.
    garage = house.clients["garage"]
    assert garage._state == "pause" and garage._song["file"].startswith("stub/")
    assert _sat(ctx, "garage").started == [mpd_module.mpd_stream_url_for("garage")]
    assert house.clients["dining_room"]._state == "stop"
    assert _sat(ctx, "dining_room").started == []
    assert resp.music_action is None and resp.music_stream_url is None
    assert resp.data["room_id"] == "garage"
    assert ctx.app.state.resumable_music == {"garage": mpd_module.mpd_stream_url_for("garage")}


@pytest.mark.parametrize("said", [
    "play weezer in the garage", "put weezer on in the garage", "put on weezer in the garage",
    "can you play weezer in the garage please", "in the garage, play weezer",
    "play weezer on the garage speaker",
])
async def test_an_artist_in_another_room(house, said) -> None:
    ctx = _ctx()
    resp = await _say(said, ctx)
    assert house.lib.played == [("Weezer", "garage", "dining_room")]
    assert resp.text == "Playing Weezer in the garage, shuffled."
    assert len(_sat(ctx, "garage").started) == 1


async def test_a_room_by_its_label_and_a_room_mode_phone_and_a_laptop(house) -> None:
    for said, room in (("play weezer in kamron's office", "kamron_office"),
                       ("play weezer in the den", "den_tablet"),
                       ("play weezer in the study", "study_laptop")):
        house.lib.played.clear()
        ctx = _ctx()
        resp = await _say(said, ctx)
        assert house.lib.played == [("Weezer", room, "dining_room")], said
        assert len(_sat(ctx, room).started) == 1


async def test_a_playlist_in_another_room(house, monkeypatch) -> None:
    from domovoi.handlers import playlist as playlist_mod

    seen = {}

    async def _play_by_name(self, name, ctx, session, *, mode):
        seen.update(name=name, room=ctx.room_id, session=ctx.session_id, mode=mode)
        if name == "two":  # several playlists match: the handler asks
            return Response(text="I have 2 playlists matching — Two A or Two B?",
                            expect_followup=True)
        return Response(text=f"Playing {name}.", music_action="start",
                        music_stream_url=mpd_module.mpd_stream_url_for(ctx.room_id))

    monkeypatch.setattr(playlist_mod.PlaylistHandler, "_play_by_name", _play_by_name)
    assert plan_route("play my road trip playlist").handler.name == "playlist"
    ctx = _ctx(session_id=uuid4())
    resp = await _say("play my road trip playlist in the garage", ctx)
    # In the garage, and with no session: a question it asked could not be
    # answered in the room that asked.
    assert seen == {"name": "road trip", "room": "garage", "session": None, "mode": "ordered"}
    assert resp.text == "Playing road trip in the garage."
    assert resp.music_action is None and len(_sat(ctx, "garage").started) == 1
    resp = await _say("shuffle the two playlist in the garage", ctx)
    assert seen["mode"] == "shuffle"
    assert resp.expect_followup is False
    assert resp.text == (
        "I have 2 playlists matching — Two A or Two B? Ask again with its full name and the room."
    )


# ─── here, everywhere, and rooms the house does not have ─────────────────


async def test_in_here_and_the_asking_room_by_name_play_here(house) -> None:
    for said in ("play weezer in here", "play weezer in the dining room"):
        house.lib.played.clear()
        ctx = _ctx()
        resp = await _say(said, ctx)
        assert house.lib.played == [("Weezer", "dining_room", None)], said
        assert resp.text == "Playing Weezer, shuffled."
        assert resp.music_action == "start"  # the dining room's own reply plays it
        assert _sat(ctx, "dining_room").started == []


async def test_a_satellite_that_shares_a_label_with_the_asker_is_here(house) -> None:
    ctx = _ctx(room="living_room_tv")
    resp = await _say("play weezer in the living room", ctx)
    assert house.lib.played == [("Weezer", "living_room_tv", None)]
    assert resp.music_action == "start"


async def test_everywhere_is_one_room_at_a_time_for_a_play(house) -> None:
    ctx = _ctx()
    resp = await _say("play some music everywhere", ctx)
    assert resp.text == "I can only play music in one room at a time. Ask again with the room's name."
    assert house.lib.played == [] and all(not s.started for s in ctx.app.state.active_sessions.values())


async def test_stop_everywhere_stops_every_playing_room(house) -> None:
    for room in ("garage", "kamron_office", "dining_room"):
        await house.clients[room].prepare_files([f"{room}.mp3"])
        await house.clients[room].resume()
    ctx = _ctx()
    resp = await _say("stop the music everywhere", ctx)
    assert resp.text == "Stopped the music in the dining room, the garage and Kamron's Office."
    assert resp.music_action == "stop"  # the asking room's own, on its own reply
    assert {"type": "music_stop"} in _sat(ctx, "garage").frames
    assert {"type": "music_stop"} in _sat(ctx, "kamron_office").frames
    assert all(house.clients[r]._state == "stop" for r in ("garage", "kamron_office", "dining_room"))


async def test_nothing_playing_anywhere(house) -> None:
    resp = await _say("pause the music everywhere", _ctx())
    assert resp.text == "Nothing is playing anywhere right now."


@pytest.mark.parametrize("said, room", [
    ("play some music in the attic", "the attic"),
    ("stop the music in the attic", "the attic"),
    ("in the attic, play some jazz", "the attic"),
    ("play weezer in the guest room", "the guest room"),
])
async def test_a_room_the_house_does_not_have_is_named_with_the_ones_it_has(house, said, room) -> None:
    resp = await _say(said, _ctx())
    assert resp.text == (
        f"I don't have a room called {room}. I have the den, the dining room, the garage, "
        "Kamron's Office, the kids room and 3 more."
    )
    assert house.lib.played == []


async def test_a_song_that_ends_in_a_place_is_still_searched_whole(house) -> None:
    ctx = _ctx()
    resp = await _say("play the boys in the band", ctx)
    # today's search, in the asking room, for the whole phrase
    assert house.clients["dining_room"]._song["title"] == "the boys in the band"
    assert resp.music_action == "start"


# ─── song or room ─────────────────────────────────────────────────────────


async def test_in_the_garage_by_weezer_is_the_song_here(house) -> None:
    for said in ("play in the garage", "play in the garage by weezer", "play the song in the garage"):
        house.lib.played.clear()
        ctx = _ctx()
        resp = await _say(said, ctx)
        assert house.lib.played == [("In the Garage", "dining_room", None)], said
        assert resp.music_action == "start" and _sat(ctx, "garage").started == []


async def test_the_song_in_the_garage_room(house) -> None:
    ctx = _ctx()
    resp = await _say("play in the garage in the garage", ctx)
    assert house.lib.played == [("In the Garage", "garage", "dining_room")]
    assert resp.text == "Playing In the Garage by Weezer in the garage."


async def test_a_whole_name_that_ends_in_a_room_word_plays_here(house) -> None:
    """"play dancing in the kitchen" with a song of that name: the song,
    here — the house has no kitchen anyway, and the words before the place
    are no name."""
    house.lib.names["dancinginthekitchen"] = DANCING_TITLE
    ctx = _ctx()
    await _say("play dancing in the kitchen", ctx)
    assert house.lib.played == [("Dancing in the Kitchen", "dining_room", None)]


async def test_both_readings_names_asks(house, monkeypatch) -> None:
    house.lib.names["dancinginthegarage"] = DataclassesReplace(DANCING_TITLE, "Dancing in the Garage")
    house.lib.names["dancing"] = DANCING_ARTIST
    parked = []

    async def _park(session, session_id, **kw):
        parked.append(kw)

    import domovoi.confirmations as confirmations

    monkeypatch.setattr(confirmations, "request_confirmation", _park)
    ctx = _ctx(session_id=uuid4())
    resp = await _say("play dancing in the garage", ctx)
    assert resp.text == (
        "Did you mean Dancing in the Garage by Ana Lark? Say no to play dancing in the garage instead."
    )
    assert resp.expect_followup is True and house.lib.played == []
    (park,) = parked
    assert park["kind"] == music_rooms.ROOM_OR_TITLE_KIND and park["handler"] == "music"
    music = _music()
    # yes: the song, here
    yes = await music.handle_confirmation(park["kind"], park["data"], True, ctx, FakeDB())
    assert house.lib.played == [("Dancing in the Garage", "dining_room", None)]
    assert yes.music_action == "start"
    # no: the artist Dancing, in the garage
    house.lib.played.clear()
    no = await music.handle_confirmation(park["kind"], park["data"], False, ctx, FakeDB())
    await drain()
    assert house.lib.played == [("Dancing", "garage", "dining_room")]
    assert no.text == "Playing Dancing in the garage, shuffled."


def DataclassesReplace(ref: EntityRef, label: str) -> EntityRef:
    import dataclasses

    return dataclasses.replace(ref, key=label.lower().replace(" ", ""), label=label)


async def test_where_nobody_can_answer_the_name_wins(house) -> None:
    house.lib.names["dancinginthegarage"] = DataclassesReplace(DANCING_TITLE, "Dancing in the Garage")
    house.lib.names["dancing"] = DANCING_ARTIST
    ctx = _ctx(session_id=uuid4(), answerable=False)  # the dashboard's play box
    await _say("play dancing in the garage", ctx)
    assert house.lib.played == [("Dancing in the Garage", "dining_room", None)]


# ─── what can't happen there, and why ─────────────────────────────────────


async def test_an_offline_room_says_so(house) -> None:
    resp = await _say("play weezer in the kids room", _ctx())
    assert resp.text == "The speaker in the kids room is offline right now, so I can't play music there."
    assert house.lib.played == []


async def test_a_room_never_connected_says_so(house) -> None:
    resp = await _say("play weezer on the porch", _ctx())
    assert resp.text == "The satellite in the porch hasn't connected yet, so I can't play music there."


async def test_a_room_with_no_music_player_gets_one_started_by_its_own_id(house, monkeypatch) -> None:
    import domovoi.mpd_provisioner as prov

    started = []

    async def _ensure(room_id):
        started.append(room_id)
        mpd_module._room_ports[room_id] = (6660, 8060)
        mpd_module._clients[room_id] = MPDStubClient()
        return (6660, 8060)

    monkeypatch.setattr(prov, "ensure_room", _ensure)
    house_rows = HOUSE + [("workshop", "Workshop", "voice", True)]
    ctx = _ctx(app=_app(online=ONLINE + ["workshop"]))
    resp = await _say("play weezer in the workshop", ctx, db=FakeDB(house_rows))
    assert started == ["workshop"]
    assert house.lib.played == [("Weezer", "workshop", "dining_room")]
    assert resp.text == "Playing Weezer in the workshop, shuffled."


async def test_a_player_that_cannot_be_started_says_why(house, monkeypatch) -> None:
    import domovoi.mpd_provisioner as prov

    async def _ensure(room_id):
        raise RuntimeError("docker is not running")

    monkeypatch.setattr(prov, "ensure_room", _ensure)
    house_rows = HOUSE + [("workshop", None, "voice", True)]
    ctx = _ctx(app=_app(online=ONLINE + ["workshop"]))
    resp = await _say("play weezer in the workshop", ctx, db=FakeDB(house_rows))
    assert resp.text == (
        "I couldn't start the music player for the workshop. "
        "Check that Docker is running on the Domovoi server."
    )
    assert house.lib.played == []


async def test_controls_never_start_a_player(house) -> None:
    house_rows = HOUSE + [("workshop", None, "voice", True)]
    ctx = _ctx(app=_app(online=ONLINE + ["workshop"]))
    resp = await _say("stop the music in the workshop", ctx, db=FakeDB(house_rows))
    assert resp.text == "Nothing is playing in the workshop right now."


async def test_from_v1_intent_there_is_no_satellite_to_reach(house) -> None:
    ctx = Context(room_id="dining_room")  # /v1/intent: no app
    resp = await _say("play weezer in the garage", ctx)
    assert resp.text == "I can only reach another room's speaker from a satellite or the app."
    assert house.lib.played == []


async def test_an_unpaired_satellite_gets_a_guests_controls_on_a_playing_room_only(house) -> None:
    app = _app(unpaired=("dining_room",))
    # A play, a stop or a volume change in another room: refused.
    for said in ("play weezer in the garage", "stop the music in the garage",
                 "turn it up in the garage"):
        resp = await _say(said, _ctx(app=app))
        assert resp.text.startswith("This speaker isn't paired with the house"), said
    # Pause / skip / what's playing: only while the garage is playing.
    resp = await _say("pause the music in the garage", _ctx(app=app))
    assert resp.text.startswith("This speaker isn't paired with the house")
    await house.clients["garage"].prepare_files(["a.mp3", "b.mp3"])
    await house.clients["garage"].resume()
    from domovoi import guest_queue

    guest_queue.clear_activity_cache()
    resp = await _say("pause the music in the garage", _ctx(app=app))
    assert resp.text == "Paused the music in the garage."
    assert house.clients["garage"]._state == "pause"
    # Its own room is its own business.
    assert (await _say("play weezer in here", _ctx(app=app))).music_action == "start"


# ─── the controls ─────────────────────────────────────────────────────────


async def test_stop_pause_skip_and_whats_playing_in_another_room(house) -> None:
    garage = house.clients["garage"]
    await garage.prepare_files(["one.mp3", "two.mp3", "three.mp3"])
    await garage.resume()
    ctx = _ctx()
    assert (await _say("what's playing in the garage", ctx)).text == "The garage is playing one."
    resp = await _say("skip in the garage", ctx)
    assert resp.text == "Next track in the garage."
    assert garage._song["title"] == "two"
    resp = await _say("Skip the song in the garage.", ctx)  # as Whisper heard "skip this song"
    assert resp.text == "Next track in the garage."
    assert garage._song["title"] == "three"
    assert (await _say("pause the music in the garage", ctx)).text == "Paused the music in the garage."
    assert garage._state == "pause"
    assert (await _say("resume the music in the garage", ctx)).text == "Resuming the music in the garage."
    resp = await _say("turn off the music in the garage", ctx)
    assert resp.text == "Stopped the music in the garage."
    assert resp.music_action is None
    assert {"type": "music_stop"} in _sat(ctx, "garage").frames
    assert garage._state == "stop"
    # The dining room's own player was never touched.
    assert house.clients["dining_room"]._state == "stop"


async def test_volume_in_another_room_goes_to_that_rooms_satellite(house) -> None:
    app = _app()
    app.state.satellite_volume["garage"] = 72      # level 5
    ctx = _ctx(app=app)
    resp = await _say("turn it up in the garage", ctx)
    assert resp.text == "Volume up to 6 in the garage."
    assert resp.satellite_volume is None           # not the dining room's
    assert _sat(ctx, "garage").volumes == [78]
    resp = await _say("set the volume to 3 in the garage", ctx)
    assert resp.text == "Volume set to 3 in the garage."
    assert _sat(ctx, "garage").volumes[-1] == 61


async def test_a_phone_can_play_in_a_room(house) -> None:
    app = _app()
    ctx = Context(room_id="phone-abc", device_kind="phone", app=app)
    resp = await _say("play weezer in the garage", ctx)
    assert house.lib.played == [("Weezer", "garage", "phone-abc")]
    assert resp.device_actions == [] and len(_sat(ctx, "garage").started) == 1


# ─── the language model's tool ────────────────────────────────────────────


async def test_the_tool_takes_a_room(house) -> None:
    music = _music()
    ctx = _ctx()
    resp = await music.execute_from_tool({"action": "play", "artist": "weezer", "room": "garage"},
                                         ctx, FakeDB())
    await drain()
    assert house.lib.played == [("Weezer", "garage", "dining_room")]
    assert resp.text == "Playing Weezer in the garage, shuffled."
    resp = await music.execute_from_tool({"action": "stop", "room": "the attic"}, ctx, FakeDB())
    assert resp.text.startswith("I don't have a room called the attic.")
    resp = await music.execute_from_tool({"action": "play", "title": "x", "room": "everywhere"},
                                         ctx, FakeDB())
    assert resp.text.startswith("I can only play music in one room at a time")


def test_the_tool_schema_offers_the_room() -> None:
    schema = _music().tool_schema["parameters"]
    assert "room" in schema["properties"] and "room" not in schema["required"]


# ─── a "did you mean" asked about another room plays there ───────────────


async def test_a_did_you_mean_about_the_garage_plays_the_answer_in_the_garage(house, monkeypatch) -> None:
    from domovoi.handlers import music_choice

    glass = EntityRef(type="artist", key="glassharbor", label="Glass Harbor")

    async def _ask_resolve(session, query, *, kinds=None):
        heard = query.get("any") or ""
        if _fold(heard) == "glassharber":
            return Resolution("ask", (Candidate(ref=glass, score=0.83, via="fuzzy",
                                                speak="Glass Harbor", track_ids=(1,)),),
                              heard=heard, reason="fuzzy")
        return Resolution.none("below_ask", heard=heard)

    monkeypatch.setattr(library_match, "resolve_request", _ask_resolve)
    parked = []

    async def _park(session, session_id, **kw):
        parked.append(kw)

    monkeypatch.setattr(music_choice, "request_confirmation", _park)
    ctx = _ctx(session_id=uuid4())
    resp = await _say("play glass harber in the garage", ctx)
    assert resp.text == "Did you mean Glass Harbor?"
    assert resp.expect_followup is True
    (park,) = parked
    assert park["data"]["play_room"] == "garage" and park["data"]["room_id"] == "garage"
    answer = await _music().handle_choice_reply(park["kind"], park["data"], "yes", ctx, FakeDB())
    await drain()
    assert house.lib.played == [("Glass Harbor", "garage", "dining_room")]
    assert answer.text == "Playing Glass Harbor in the garage, shuffled."
    assert len(_sat(ctx, "garage").started) == 1


async def test_an_ordinary_did_you_mean_is_answered_here(house, monkeypatch) -> None:
    from domovoi.handlers import music_choice

    data = {"candidates": [{"ref": WEEZER.to_dict(), "score": 0.8, "via": "fuzzy", "speak": "Weezer"}],
            "query": {"any": "weezr"}, "heard": "weezr", "room_id": "dining_room",
            "expires_at": time.time() + 60}
    ctx = _ctx(session_id=uuid4())
    resp = await _music().handle_choice_reply(music_choice.MUSIC_CHOICE_KIND, data, "yes", ctx, FakeDB())
    assert house.lib.played == [("Weezer", "dining_room", None)]
    assert resp.music_action == "start"


# ─── the real resolver over a real library (the lane's database) ─────────


@requires_db
async def test_with_the_real_resolver_weezer_in_the_garage(db_session, monkeypatch, tmp_path) -> None:
    """The incident library (functional-testing/incident-1014/lib_garage.py):
    "In the Garage" by Weezer, "Garage Band Anthem" by The Garage, "Some
    Music" by Garage Kids on the album "In the Garage" — decided by the
    spoken-name resolver itself, over library_tracks."""
    from sqlalchemy import text

    from domovoi.config import settings

    monkeypatch.setattr(mpd_module, "_clients", {r: MPDStubClient() for r in PORTS})
    monkeypatch.setattr(mpd_module, "_room_ports", dict(PORTS))
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    library_match.reset_for_tests()
    for i, (title, artist, album) in enumerate([
        ("In the Garage", "Weezer", "Weezer (Blue Album)"),
        ("Garage Band Anthem", "The Garage", "Some Music"),
        ("Some Music", "Garage Kids", "In the Garage"),
        ("Undone", "Weezer", "Weezer (Blue Album)"),
        ("Ember Waltz", "Hearth Cats", "Kindling"),
    ], start=1):
        await db_session.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, source, "
            "source_id, added_at, added_via, favorited) VALUES (:p, :t, :a, :al, 200, 'indexed', "
            ":sid, now(), 'manual', false)"),
            {"p": str(tmp_path / f"t{i}.mp3"), "t": title, "a": artist, "al": album, "sid": f"crm-{i}"})
    for room_id, label, sat_type, _seen in HOUSE:
        await db_session.execute(text(
            "INSERT INTO satellites (room_id, room_label, sat_type, adopted_via) "
            "VALUES (:r, :l, :t, 'manual')"), {"r": room_id, "l": label, "t": sat_type})
    await db_session.commit()
    try:
        async def say(said):
            ctx = _ctx()
            resp = await _say(said, ctx, db=db_session)
            return resp, resp.text.lower(), ctx

        # The song, here — no words before the place. (The resolver says an
        # exact name back in the words it heard, title-cased.)
        resp, said, ctx = await say("play in the garage")
        assert said == "playing in the garage by weezer."
        assert resp.music_action == "start" and _sat(ctx, "garage").started == []
        resp, said, ctx = await say("play in the garage by weezer")
        assert said == "playing in the garage by weezer."
        assert resp.music_action == "start" and _sat(ctx, "garage").started == []
        # Weezer, in the garage room.
        resp, said, ctx = await say("play weezer in the garage")
        assert said == "playing weezer in the garage, shuffled."
        assert resp.music_action is None and len(_sat(ctx, "garage").started) == 1
        # The incident: any music, in the garage room.
        resp, said, ctx = await say("play some music in the garage")
        assert said.startswith("playing ") and said.endswith(" in the garage.")
        assert resp.music_action is None and len(_sat(ctx, "garage").started) == 1
        # The song "In the Garage", in the garage room.
        resp, said, ctx = await say("play in the garage in the garage")
        assert said == "playing in the garage by weezer in the garage."
        assert len(_sat(ctx, "garage").started) == 1
        # The artist The Garage, in the garage room.
        resp, said, ctx = await say("play the garage in the garage")
        assert said.endswith(" in the garage.") and "garage band anthem" in said
        # An unknown room, listed with the rooms the house has.
        resp, said, ctx = await say("play some music in the attic")
        assert said.startswith("i don't have a room called the attic. i have the den, the dining room")
    finally:
        library_match.reset_for_tests()
