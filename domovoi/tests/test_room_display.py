"""The room display (1.4.0, domovoi/room_display.py): a phone in room mode
showing the room's music, its controls, and its timers and alarms, over the
room's own socket.

Pinned here:

* the negotiation: only a paired device that listed `room_display` gets it
  (`ready.features`, the feed); a Pi, an unpaired socket or a device that
  left it out get nothing;
* the frame for: idle, a library song, the radio (no art, no skip), a
  group (every member shows the host's music), a timer and an alarm, one
  going off, and a reminder that never appears;
* change-only sending, at most one a second;
* `room_control`: every music action, the strict parse, the rate limit,
  and `stop_ringing` on an alarm (acknowledged, its announcement ended);
* voice "stop": with an alarm going off it silences the alarm and leaves
  the music; with nothing going off it stops the music as before;
* `GET /v1/music/cover/{id}`: the dashboard's bytes, library only, 404 for
  an unknown id, no path from the caller.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import music_groups
from domovoi import room_display as rd
from domovoi import satellite_abilities as sa
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.now_playing import NOW_PLAYING
from domovoi.tests.conftest import requires_db
from domovoi.tests.timer_fires_testkit import apply_v018

ROOM = "nursery"


# ─── doubles ──────────────────────────────────────────────────────────────


class FakeSession:
    """What the feed uses of a StreamSession."""

    def __init__(self, room_id: str = ROOM, *, supports: list[str] | None = None,
                 paired: bool = True, app: Any = None) -> None:
        self.room_id = room_id
        hello: dict[str, Any] = {"type": "hello"}
        if supports is not None:
            hello["supports"] = supports
        self.abilities = sa.from_hello(hello)
        self.token_authenticated = paired
        self.sent: list[dict[str, Any]] = []
        self.sent_at: list[float] = []
        self.ws = SimpleNamespace(app=app or _app())

    async def _safe_send_text(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        self.sent_at.append(time.monotonic())


def _app(**state: Any) -> SimpleNamespace:
    base = dict(
        active_sessions={}, resumable_music={}, pending_music_start={},
        satellite_volume={}, current_playlist={}, satellite_voice={},
        probe=SimpleNamespace(online=True),
    )
    base.update(state)
    return SimpleNamespace(state=SimpleNamespace(**base))


def _frame(sent_at_ms: int = 0, *, np: dict | None = None, timers: list | None = None,
           ringing: dict | None = None) -> dict[str, Any]:
    return {"type": "room_display", "sent_at_ms": sent_at_ms, "now_playing": np,
            "timers": timers or [], "ringing": ringing}


@pytest.fixture
def players(monkeypatch):
    """Stub players per room, no groups."""
    clients: dict[str, MPDStubClient] = {}
    monkeypatch.setattr(mpd_module, "_clients", clients)
    monkeypatch.setattr(mpd_module, "_room_ports", {})
    music_groups.reset(persist=False)
    yield clients
    music_groups.reset(persist=False)


async def _library_song(clients: dict, room: str, *uris: str) -> MPDStubClient:
    stub = clients.setdefault(room, MPDStubClient())
    await stub.prepare_files(list(uris))
    await stub.resume()
    return stub


# ─── the ability and the negotiation ──────────────────────────────────────


def test_the_ability_is_never_assumed_for_a_pi():
    assert "room_display" in sa.ABILITIES
    assert "room_display" not in sa.ASSUMED_WITHOUT_SUPPORTS
    pi = sa.from_hello({"type": "hello", "speech_pause": True})
    assert not pi.allows("room_display")
    assert sa.from_hello({"type": "hello", "supports": ["room_display"]}).allows("room_display")
    assert not sa.from_hello({"type": "hello", "supports": ["sounds"]}).allows("room_display")


@pytest.mark.parametrize("supports, paired, wanted", [
    (["room_display"], True, True),
    (["room_display"], False, False),     # an unpaired socket: never
    (["sounds"], True, False),            # a device that left it out
    (None, True, False),                  # a Pi
])
def test_who_gets_the_room_display(supports, paired, wanted):
    from domovoi.streaming import CORE_FEATURES, StreamSession

    s = FakeSession(supports=supports, paired=paired)
    assert rd.wanted(s) is wanted
    session = StreamSession(SimpleNamespace(app=_app()), ROOM)  # type: ignore[arg-type]
    session.abilities = s.abilities
    session.token_authenticated = paired
    features = session._ready_features()
    assert ("room_display" in features) is wanted
    assert features[: len(features) - wanted] == [
        f for f in CORE_FEATURES if paired or f not in ("health", "log_push")
    ]


async def test_room_control_without_the_feature_is_ignored_silently():
    from domovoi.streaming import StreamSession

    sent: list[str] = []

    class WS:
        app = _app()

        async def send_text(self, t):
            sent.append(t)

    session = StreamSession(WS(), ROOM)  # type: ignore[arg-type]
    await session._on_control({"type": "room_control", "action": "pause"})
    assert sent == [], "no error frame, no reply"


# ─── controls and change detection (pure) ─────────────────────────────────


@pytest.mark.parametrize("state, stream, pos, length, want", [
    ("play", False, 0, 3, ["pause", "skip", "previous"]),
    ("pause", False, 1, 3, ["resume", "skip", "previous"]),
    ("play", False, 2, 3, ["pause", "previous"]),       # the last song: no skip
    ("play", True, 0, 1, ["pause"]),                    # the radio
    ("pause", True, 0, 1, ["resume"]),
    ("play", False, None, None, ["pause"]),             # not in a queue
])
def test_controls_are_only_what_works(state, stream, pos, length, want):
    assert rd.controls_for(state, stream, pos, length) == want


def test_time_passing_is_not_a_change_but_a_seek_or_an_edit_is():
    np = {"state": "play", "title": "A", "artist": None, "album": None, "source": "library",
          "art": None, "duration_sec": 200.0, "elapsed_sec": 10.0, "controls": ["pause"]}
    timer = {"id": 1, "kind": "timer", "label": "pasta", "remaining_ms": 60_000, "total_ms": 600_000}
    a = _frame(1_000_000, np=np, timers=[timer])
    later = _frame(1_005_000, np={**np, "elapsed_sec": 15.0}, timers=[{**timer, "remaining_ms": 55_000}])
    assert not rd.differs(a, later)
    assert rd.differs(None, a)
    assert rd.differs(a, _frame(1_005_000, np={**np, "elapsed_sec": 60.0}, timers=[timer]))     # a seek
    assert rd.differs(a, _frame(1_000_000, np={**np, "state": "pause"}, timers=[timer]))
    assert rd.differs(a, _frame(1_000_000, np=np, timers=[{**timer, "label": "rice"}]))
    assert rd.differs(a, _frame(1_000_000, np=np, timers=[{**timer, "remaining_ms": 600_000}]))  # added time
    assert rd.differs(a, _frame(1_000_000, np=np, timers=[]))
    assert rd.differs(a, _frame(1_000_000, np=np, timers=[timer],
                                ringing={"fire_id": 3, "kind": "timer", "label": None}))
    paused = _frame(0, np={**np, "state": "pause"})
    assert not rd.differs(paused, _frame(9_000, np={**np, "state": "pause"}))


# ─── the feed: change only, at most once a second ─────────────────────────


async def test_the_feed_sends_once_then_only_on_change_and_never_faster_than_the_limit():
    s = FakeSession(supports=["room_display"])
    builds = {"n": 0}

    async def build(_room):
        builds["n"] += 1
        return _frame(int(time.monotonic() * 1000), timers=[
            {"id": 1, "kind": "timer", "label": f"t{builds['n']}", "remaining_ms": 5000, "total_ms": None},
        ] if builds["n"] > 3 else [])

    feed = rd.RoomDisplayFeed(s, builder=build, poll_sec=0.02, min_interval_sec=0.25)
    feed.start()
    await asyncio.sleep(0.3)
    # Builds 1-3 are the same: one frame. From 4 every build differs.
    for _ in range(20):
        feed.poke()
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.6)
    await feed.stop()
    assert builds["n"] > 6
    assert s.sent[0]["timers"] == []
    gaps = [b - a for a, b in zip(s.sent_at, s.sent_at[1:])]
    assert gaps and all(g >= 0.24 for g in gaps), gaps
    assert len(s.sent) < builds["n"]


async def test_an_unchanged_room_is_sent_once():
    s = FakeSession(supports=["room_display"])

    async def build(_room):
        return _frame(int(time.monotonic() * 1000))

    feed = rd.RoomDisplayFeed(s, builder=build, poll_sec=0.01, min_interval_sec=0.0)
    feed.start()
    await asyncio.sleep(0.2)
    await feed.stop()
    assert len(s.sent) == 1


async def test_a_room_that_cannot_be_read_sends_nothing_until_it_can():
    s = FakeSession(supports=["room_display"])
    calls = {"n": 0}

    async def build(_room):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("database down")
        return _frame(1)

    feed = rd.RoomDisplayFeed(s, builder=build, poll_sec=0.01, min_interval_sec=0.0)
    feed.start()
    await asyncio.sleep(0.2)
    await feed.stop()
    assert [f["type"] for f in s.sent] == ["room_display"]


# ─── room_control: parse, offer, rate ─────────────────────────────────────


@pytest.mark.parametrize("ctrl, parsed", [
    ({"type": "room_control", "action": "pause"}, ("pause", None)),
    ({"type": "room_control", "action": "resume"}, ("resume", None)),
    ({"type": "room_control", "action": "skip"}, ("skip", None)),
    ({"type": "room_control", "action": "previous"}, ("previous", None)),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": 7}, ("stop_ringing", 7)),
    ({"type": "room_control", "action": "stop"}, None),                  # not a room control
    ({"type": "room_control", "action": "next"}, None),
    ({"type": "room_control", "action": "PAUSE"}, None),
    ({"type": "room_control", "action": "pause", "room_id": "garage"}, None),  # no room in it
    ({"type": "room_control", "action": "stop_ringing"}, None),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": "7"}, None),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": True}, None),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": 0}, None),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": 2**63}, None),
    ({"type": "room_control", "action": "stop_ringing", "fire_id": 1.5}, None),
    ({"type": "room_control", "action": ["pause"]}, None),
    ({"type": "room_controls", "action": "pause"}, None),
    ("pause", None),
])
def test_room_control_is_parsed_strictly(ctrl, parsed):
    assert rd.RoomDisplayFeed.parse_control(ctrl) == parsed


async def test_only_an_offered_control_is_taken_and_fast_presses_are_dropped(monkeypatch):
    s = FakeSession(supports=["room_display"])
    feed = rd.RoomDisplayFeed(s, builder=None)
    done: list[str] = []

    async def music(action):
        done.append(action)

    monkeypatch.setattr(feed, "music", music)
    feed.last_sent = _frame(np={"state": "play", "controls": ["pause"]})
    await feed.on_control({"type": "room_control", "action": "skip"})   # the radio: not offered
    await feed.on_control({"type": "room_control", "action": "teleport"})
    for _ in range(rd.CONTROL_BURST + 3):
        await feed.on_control({"type": "room_control", "action": "pause"})
    await asyncio.sleep(0.05)
    assert done == ["pause"] * (rd.CONTROL_BURST - 1), "two refused frames used two tokens"


# ─── the frame from the real tables ───────────────────────────────────────


async def _timer(s, *, room=ROOM, label=None, message=None, in_sec=600, total=600) -> int:
    row = await s.execute(
        text(
            "INSERT INTO timers (label, message, room_id, created_at, expires_at) "
            "VALUES (:l, :m, :r, now() - make_interval(secs => :ago), "
            "now() + make_interval(secs => :left)) RETURNING id"
        ),
        {"l": label, "m": message, "r": room, "ago": float(total - in_sec), "left": float(in_sec)},
    )
    return int(row.scalar_one())


async def _fire(s, *, room=ROOM, kind="timer", label=None, message=None,
                outcome="spoken", ago=3, origin=True) -> int:
    fid = (await s.execute(
        text(
            "INSERT INTO timer_fires (timer_id, kind, label, message, origin_room_id, "
            "created_at, due_at, fired_at, base_text) VALUES (1, :k, :l, :m, :r, "
            "now() - interval '10 minutes', now() - make_interval(secs => :ago), "
            "now() - make_interval(secs => :ago), 'x') RETURNING id"
        ),
        {"k": kind, "l": label, "m": message, "r": room if origin else "garage", "ago": float(ago)},
    )).scalar_one()
    await s.execute(
        text(
            "INSERT INTO timer_fire_deliveries (fire_id, room_id, is_origin, outcome, started_at, "
            "finished_at) VALUES (:f, :r, :o, :out, now() - make_interval(secs => :ago), "
            "CASE WHEN :out IN ('spoken', 'interrupted') THEN now() - make_interval(secs => :ago) END)"
        ),
        {"f": fid, "r": room, "o": origin, "out": outcome, "ago": float(ago)},
    )
    return int(fid)


@pytest.fixture
async def ledger(db_session):
    await apply_v018()
    yield db_session
    await db_session.rollback()
    await apply_v018()


@requires_db
async def test_an_idle_room(db_session, players):
    frame = await rd.build_frame(ROOM)
    assert frame["type"] == "room_display"
    assert isinstance(frame["sent_at_ms"], int)
    assert (frame["now_playing"], frame["timers"], frame["ringing"]) == (None, [], None)


@requires_db
async def test_a_library_song(db_session, players, tmp_path, monkeypatch):
    music = tmp_path / "music"
    monkeypatch.setattr(settings, "music_dir", str(music))
    path = music / "Hearth Cats" / "Embers" / "01 Ember Waltz.mp3"
    async with _scope() as s:
        tid = (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec) "
            "VALUES (:p, 'Ember Waltz', 'Hearth Cats', 'Embers', 201) RETURNING id"),
            {"p": str(path)})).scalar_one()
    stub = await _library_song(players, ROOM, "Hearth Cats/Embers/01 Ember Waltz.mp3", "other.mp3")
    await stub.seek_to(42)
    frame = await rd.build_frame(ROOM)
    assert frame["now_playing"] == {
        "state": "play", "title": "Ember Waltz", "artist": "Hearth Cats", "album": "Embers",
        "source": "library", "art": f"/v1/music/cover/{tid}", "duration_sec": 201.0,
        "elapsed_sec": 42.0, "controls": ["pause", "skip", "previous"],
    }
    await stub.pause()
    np = (await rd.build_frame(ROOM))["now_playing"]
    assert np["state"] == "pause" and np["controls"] == ["resume", "skip", "previous"]


@requires_db
async def test_the_radio_has_no_art_and_no_skip(db_session, players):
    from domovoi import player_relay
    from domovoi.player_route import PlayerRoute

    stub = players.setdefault(ROOM, MPDStubClient())
    # An internet station plays through the core's relay (L20): the player
    # is handed a relay URL, and the radio stamps that as its stream_url.
    url = player_relay.mint(ROOM, "http://radio.example/jazz",
                            PlayerRoute(host="172.17.0.1", peer="172.17.0.5"), kind="radio")
    await stub.play_url(url, title="Night Jazz")
    owner = "test-room-display"
    if "radio" not in NOW_PLAYING.sources():
        NOW_PLAYING.register_source("radio", owner=owner)
    try:
        NOW_PLAYING.stamp(ROOM, "radio", {"stream_url": url, "title": "Night Jazz FM"})
        np = (await rd.build_frame(ROOM))["now_playing"]
        assert np["source"] == "radio"
        assert np["art"] is None, "never a guessed picture"
        assert np["title"] == "Night Jazz FM"
        assert (np["artist"], np["album"], np["duration_sec"]) == (None, None, None)
        assert np["controls"] == ["pause"]
        # A stamp from an older stream is not this one's.
        NOW_PLAYING.stamp(ROOM, "radio", {"stream_url": "http://radio.example/old", "title": "Old"})
        np = (await rd.build_frame(ROOM))["now_playing"]
        assert np["source"] == "other" and np["title"] != "Old"
    finally:
        NOW_PLAYING.clear(ROOM)
        NOW_PLAYING.unregister_owner(owner)
        player_relay.reset_for_tests()


@requires_db
async def test_in_a_group_every_member_shows_the_hosts_music(db_session, players, monkeypatch):
    host = await _library_song(players, "garage", "a/one.mp3", "a/two.mp3")
    players[ROOM] = MPDStubClient()          # the member's own player: idle
    monkeypatch.setattr(mpd_module, "_room_ports", {"garage": (6650, 8050), ROOM: (6651, 8051)})
    assert (await rd.build_frame(ROOM))["now_playing"] is None
    await music_groups.share(_app(), "garage", [ROOM])
    assert music_groups.host_of(ROOM) == "garage"
    member = (await rd.build_frame(ROOM))["now_playing"]
    hosting = (await rd.build_frame("garage"))["now_playing"]
    assert member == hosting
    assert member["title"] == "one" and member["controls"] == ["pause", "skip", "previous"]
    assert host._state == "play"


@requires_db
async def test_timers_and_alarms_but_never_a_reminder(db_session, players):
    async with _scope() as s:
        pasta = await _timer(s, label="the pasta", in_sec=300, total=600)
        plain = await _timer(s, in_sec=900, total=900)
        alarm = await _timer(s, label="7 AM alarm", message="", in_sec=3600, total=36000)
        wake = await _timer(s, label="20 minute alarm", message="time to wake up", in_sec=1200, total=1200)
        await _timer(s, label="call mom", message="call mom", in_sec=60)          # a reminder
        await _timer(s, label="check the smoke alarm", message="check the smoke alarm", in_sec=90)
        await _timer(s, room="garage", label="garage timer", in_sec=30)         # another room
        await _timer(s, room=None, label="roomless", in_sec=30)
    timers = (await rd.build_frame(ROOM))["timers"]
    assert [(t["id"], t["kind"], t["label"]) for t in timers] == [
        (pasta, "timer", "pasta"),
        (plain, "timer", None),
        (wake, "alarm", "20 minute alarm"),
        (alarm, "alarm", "7 AM alarm"),
    ]
    first = timers[0]
    assert 295_000 <= first["remaining_ms"] <= 300_000
    assert first["total_ms"] == 600_000
    assert "mom" not in repr(timers) and "smoke" not in repr(timers)


@requires_db
async def test_ringing_is_a_timer_or_alarm_going_off_here_and_not_acknowledged(ledger, players):
    async with _scope() as s:
        reminder = await _fire(s, kind="reminder", label="call mom", message="call mom", ago=2)
    assert (await rd.build_frame(ROOM))["ringing"] is None, "a reminder never rings on the screen"
    async with _scope() as s:
        alarm = await _fire(s, kind="reminder", label="7 AM alarm", message="", ago=5)
    assert (await rd.build_frame(ROOM))["ringing"] == {
        "fire_id": alarm, "kind": "alarm", "label": "7 AM alarm"}
    async with _scope() as s:
        timer = await _fire(s, kind="timer", label="the pasta", ago=1)
    assert (await rd.build_frame(ROOM))["ringing"] == {
        "fire_id": timer, "kind": "timer", "label": "pasta"}
    assert reminder < alarm < timer
    # Long over (outside the window): not ringing.
    async with _scope() as s:
        await s.execute(text("UPDATE timer_fire_deliveries SET finished_at = now() - interval '5 minutes', "
                             "started_at = now() - interval '5 minutes'"))
        await s.execute(text("UPDATE timer_fires SET fired_at = now() - interval '5 minutes'"))
    assert (await rd.build_frame(ROOM))["ringing"] is None


@requires_db
async def test_stop_ringing_on_an_alarm_acknowledges_it_and_ends_its_announcement(ledger, players):
    async with _scope() as s:
        alarm = await _fire(s, kind="reminder", label="7 AM alarm", message="", ago=2)
        other = await _fire(s, room="kitchen", kind="timer", ago=2)
    cut: list[tuple[int, str]] = []
    delivery = SimpleNamespace(stop_announcing=lambda f, r: cut.append((f, r)) or True)
    s_ = FakeSession(supports=["room_display"], app=_app(timer_delivery=delivery))
    feed = rd.RoomDisplayFeed(s_)
    # Another room's fire, or a made-up one: nothing happens.
    await feed.on_control({"type": "room_control", "action": "stop_ringing", "fire_id": other})
    await feed.on_control({"type": "room_control", "action": "stop_ringing", "fire_id": 999})
    assert cut == []
    await feed.on_control({"type": "room_control", "action": "stop_ringing", "fire_id": alarm})
    assert cut == [(alarm, ROOM)]
    async with _scope() as s:
        acked = (await s.execute(text("SELECT id, acked_by FROM timer_fires WHERE acked_at IS NOT NULL"))).all()
    assert [tuple(r) for r in acked] == [(alarm, ROOM)]
    assert (await rd.build_frame(ROOM))["ringing"] is None


@requires_db
async def test_stop_ringing_on_a_timer_too(ledger, players):
    async with _scope() as s:
        timer = await _fire(s, kind="timer", label="eggs", ago=1, outcome="pending")
    feed = rd.RoomDisplayFeed(FakeSession(supports=["room_display"]))
    await feed.on_control({"type": "room_control", "action": "stop_ringing", "fire_id": timer})
    async with _scope() as s:
        row = (await s.execute(text(
            "SELECT f.acked_by, d.outcome FROM timer_fires f JOIN timer_fire_deliveries d "
            "ON d.fire_id = f.id WHERE f.id = :f"), {"f": timer})).one()
    assert tuple(row) == (ROOM, "cancelled"), "its waiting announcement is cancelled"


def test_the_delivery_ends_only_that_fires_announcement():
    from domovoi.timer_delivery import TimerDelivery

    cuts: list[str] = []
    sess = SimpleNamespace(_cut_announcement=lambda: cuts.append("cut"), _announce_callers=1)
    app = SimpleNamespace(state=SimpleNamespace(active_sessions={ROOM: sess}))
    d = TimerDelivery(app)
    assert d.stop_announcing(5, ROOM) is False, "nothing is being said"
    d._speaking[ROOM] = 6
    assert d.stop_announcing(5, ROOM) is False, "another fire's announcement is left alone"
    d._speaking[ROOM] = 5
    sess._announce_callers = 2
    assert d.stop_announcing(5, ROOM) is False, "another announcement shares the turn"
    sess._announce_callers = 1
    assert d.stop_announcing(5, ROOM) is True
    assert cuts == ["cut"]


# ─── room_control music actions, through the real route ───────────────────


@requires_db
@pytest.mark.parametrize("action, expect", [
    ("pause", lambda st: st._state == "pause"),
    ("skip", lambda st: st._song["file"] == "b.mp3"),
    ("previous", lambda st: st._song["file"] == "a.mp3" and st._state == "play"),
])
async def test_each_music_action_goes_the_dashboards_way(db_session, players, action, expect):
    stub = await _library_song(players, ROOM, "a.mp3", "b.mp3", "c.mp3")
    if action == "previous":
        await stub.next()
    app = _app()
    feed = rd.RoomDisplayFeed(FakeSession(supports=["room_display"], app=app))
    feed.last_sent = await rd.build_frame(ROOM)
    assert action in feed.last_sent["now_playing"]["controls"]
    await feed.on_control({"type": "room_control", "action": action})
    await asyncio.gather(*feed._actions)
    assert expect(stub), (action, stub._state, stub._song)
    async with _scope() as s:
        said = (await s.execute(text("SELECT transcript FROM intents_log WHERE room_id = :r"),
                                {"r": ROOM})).scalars().all()
    assert said == [rd.MUSIC_TRANSCRIPTS[action]], "the same audit trail as the dashboard's"


@requires_db
async def test_resume_goes_the_dashboards_way(db_session, players):
    stub = await _library_song(players, ROOM, "a.mp3", "b.mp3")
    await stub.pause()
    feed = rd.RoomDisplayFeed(FakeSession(supports=["room_display"], app=_app()))
    feed.last_sent = await rd.build_frame(ROOM)
    await feed.on_control({"type": "room_control", "action": "resume"})
    await asyncio.gather(*feed._actions)
    assert stub._state == "play"


# ─── voice "stop" ─────────────────────────────────────────────────────────


async def _say(transcript: str, app: Any):
    from domovoi.db.session import session_scope
    from domovoi.models import Context, Intent
    from domovoi.room_music import drain
    from domovoi.router import route

    async with session_scope() as s:
        response = await route(Intent(transcript=transcript, room_id=ROOM),
                               Context(room_id=ROOM, online=True, app=app), s)
    await drain()
    return response


@requires_db
async def test_stop_with_an_alarm_going_off_silences_the_alarm_not_the_music(ledger, players):
    stub = await _library_song(players, ROOM, "a.mp3", "b.mp3")
    async with _scope() as s:
        alarm = await _fire(s, kind="reminder", label="7 AM alarm", message="", ago=2)
    response = await _say("stop", _app())
    assert response.text == "Okay."
    assert response.music_action is None
    assert stub._state == "play", "the music plays on"
    async with _scope() as s:
        assert (await s.execute(text("SELECT acked_by FROM timer_fires WHERE id = :f"),
                                {"f": alarm})).scalar_one() == ROOM
    # The next "stop" is the music's again.
    response = await _say("stop", _app())
    assert response.matched_handler == "music" and response.text != "Okay."
    assert response.music_action == "stop"


@requires_db
async def test_stop_with_nothing_going_off_is_the_musics(ledger, players):
    await _library_song(players, ROOM, "a.mp3")
    async with _scope() as s:
        # A reminder going off is not ringing: "stop" stays the music's.
        await _fire(s, kind="reminder", label="call mom", message="call mom", ago=2)
    response = await _say("stop", _app())
    assert response.matched_handler == "music"
    assert response.music_action == "stop"
    async with _scope() as s:
        assert (await s.execute(text("SELECT count(*) FROM timer_fires WHERE acked_at IS NOT NULL"))
                ).scalar_one() == 0


@requires_db
async def test_stop_the_timer_now_also_stops_a_ringing_alarm(ledger, players):
    async with _scope() as s:
        alarm = await _fire(s, kind="reminder", label="6:30 AM alarm", message="", ago=2)
        await _timer(s, label="the pasta", in_sec=300)
    response = await _say("stop the timer", _app())
    assert response.text == "Okay."
    async with _scope() as s:
        assert (await s.execute(text("SELECT acked_by FROM timer_fires WHERE id = :f"),
                                {"f": alarm})).scalar_one() == ROOM
        assert (await s.execute(text("SELECT count(*) FROM timers"))).scalar_one() == 1, \
            "the running pasta timer is not cancelled"


@requires_db
async def test_stop_the_music_never_takes_the_alarm(ledger, players):
    await _library_song(players, ROOM, "a.mp3")
    async with _scope() as s:
        await _fire(s, kind="reminder", label="7 AM alarm", message="", ago=2)
    response = await _say("stop the music", _app())
    assert response.music_action == "stop"
    async with _scope() as s:
        assert (await s.execute(text("SELECT count(*) FROM timer_fires WHERE acked_at IS NOT NULL"))
                ).scalar_one() == 0


# ─── the cover route ──────────────────────────────────────────────────────

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 64


async def _get(app, path: str, headers: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path, headers=headers or {})


@requires_db
async def test_the_cover_route_serves_the_dashboards_bytes_for_a_library_song(db_session, tmp_path, monkeypatch):
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    music = tmp_path / "music"
    album = music / "Hearth Cats" / "Embers"
    album.mkdir(parents=True)
    (album / "01 Ember Waltz.mp3").write_bytes(b"\x00" * 32)
    (album / "cover.jpg").write_bytes(JPEG)
    outside = tmp_path / "elsewhere" / "x.mp3"
    outside.parent.mkdir()
    outside.write_bytes(b"\x00" * 32)
    (outside.parent / "cover.jpg").write_bytes(JPEG)
    monkeypatch.setattr(settings, "music_dir", str(music))
    async with _scope() as s:
        tid = (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title) VALUES (:p, 'Ember Waltz') RETURNING id"),
            {"p": str(album / "01 Ember Waltz.mp3")})).scalar_one()
        stray = (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title) VALUES (:p, 'x') RETURNING id"),
            {"p": str(outside)})).scalar_one()

    core = await _get(core_app, f"/v1/music/cover/{tid}")
    dash = await _get(web_app, f"/api/music/library/{tid}/cover")
    assert core.status_code == dash.status_code == 200
    assert core.content == dash.content == JPEG
    assert core.headers["content-type"] == dash.headers["content-type"] == "image/jpeg"
    assert core.headers["etag"] == dash.headers["etag"]
    assert core.headers["cache-control"] == dash.headers["cache-control"]
    again = await _get(core_app, f"/v1/music/cover/{tid}", {"If-None-Match": core.headers["etag"]})
    assert again.status_code == 304

    # Unknown, out of range, not a number, or a file outside the library.
    assert (await _get(core_app, f"/v1/music/cover/{tid + 100}")).status_code == 404
    assert (await _get(core_app, "/v1/music/cover/0")).status_code == 404
    assert (await _get(core_app, f"/v1/music/cover/{2**40}")).status_code == 404
    for path in ("/v1/music/cover/..%2F..%2Fetc%2Fpasswd", "/v1/music/cover/../../etc/passwd",
                 "/v1/music/cover/1.jpg", "/v1/music/cover/-1"):
        r = await _get(core_app, path)
        assert r.status_code in (404, 422), (path, r.status_code)
        assert r.content != JPEG
    r = await _get(core_app, f"/v1/music/cover/{stray}")
    assert r.status_code == 400 and str(outside) not in r.text and r.content != JPEG


def _scope():
    from domovoi.db.session import session_scope

    return session_scope()
