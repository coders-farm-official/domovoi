"""A drop-in never shares the room's microphone with a follow-up listen.

The case behind this file (a garage satellite, a house on main): "Drop into
the dining room" was answered "Dropping in on the dining room. By the way,
was that you, X?" — a voice-ID question tacked onto the turn that opened
the call. The question's follow-up capture held the garage's microphone
out of the call (its frames went to the turn, not the relay), and heard
the other room from the garage's speaker ("Yes it was. Yes. Baby."). The
yes started "count from one to ten" during the call, "Stop." went to that
introduction instead of the call, and the satellite then sat waiting for a
reply's end that a call never delivers (its output stream stays open for
the relay), deaf to the wake word: a spoken "hang up" could not end the
call, only the dashboard's Hang up.

Pinned here:

1. a turn that opens a drop-in or an announcement asks nothing back and
   opens no follow-up listen;
2. in a call: no follow-up listen, no introduction, no voice learning, and
   the other room's audio never reaches a command capture;
3. "stop" / "hang up" said in a room in a call end the call, ahead of a
   parked question; a bare "stop" outside one is still the music's;
4. a follow-up capture open when a call starts is dropped, so the room is
   never half in the call; every reply said in a call is followed by an
   empty one that lets the satellite stop waiting;
5. "drop into X" is a fast path (no tool model, so no cold-model line),
   and an unknown room is answered with the rooms the house knows.

Placeholder rooms and names only.
"""

from __future__ import annotations

import asyncio
import json
import types

import numpy as np
import pytest

from domovoi import streaming
from domovoi.clients import mpd as mpd_module
from domovoi.config import settings
from domovoi.dropin_common import end_call_phrase, pretty_room, room_in_call, starts_live_audio
from domovoi.handlers.dropin import _START_RE, DropInHandler
from domovoi.handlers.intercom import _resolve_target_rooms
from domovoi.models import Context, Response
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_under_capture import FRAME, _Socket, _wav

GARAGE, KITCHEN = "garage", "kitchen"


def _app(**extra):
    state = types.SimpleNamespace(
        satellite_voice={}, wifi_status={}, satellite_volume={},
        satellite_config={}, greeting_phrases=[], greeting_clips={},
        probe=types.SimpleNamespace(online=True),
        active_sessions={}, resumable_music={}, current_playlist={},
        pending_music_start={}, satellite_full_duplex={GARAGE: True, KITCHEN: True},
        active_dropins={}, pending_dropins={}, dropin_lock=asyncio.Lock(),
    )
    for k, v in extra.items():
        setattr(state, k, v)
    return types.SimpleNamespace(state=state)


def _texts(ws) -> list[dict]:
    return [f for _t, k, f in ws.sent if k == "text"]


def _pair(app=None) -> tuple[StreamSession, StreamSession]:
    app = app or _app()
    garage = StreamSession(_Socket(app), GARAGE)  # type: ignore[arg-type]
    kitchen = StreamSession(_Socket(app), KITCHEN)  # type: ignore[arg-type]
    app.state.active_sessions[GARAGE] = garage
    app.state.active_sessions[KITCHEN] = kitchen
    for s in (garage, kitchen):
        s.token_authenticated = True          # paired satellites (CORE-11)
    return garage, kitchen


@pytest.fixture
def _quiet_calls(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)
    monkeypatch.setattr(settings, "dropin_relay_gate_dbfs", -120.0, raising=False)
    monkeypatch.setattr(settings, "dropin_accept_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "dropin_enabled", True, raising=False)


@pytest.fixture
def _rooms():
    mpd_module._room_ports = {GARAGE: (6650, 8050), "dining-room": (6651, 8051)}
    try:
        yield
    finally:
        mpd_module._room_ports = {}


# ─── 5. the fast path, the room names ─────────────────────────────────────


def test_drop_into_is_the_start_fast_path() -> None:
    for s in ("drop into the dining room", "drop into kitchen", "dropinto the garage",
              "connect me with the kitchen"):
        assert _START_RE.match(s), s
    assert _START_RE.match("drop into the dining room").group("room") == "the dining room"


def test_drop_into_never_reaches_the_tool_model() -> None:
    """Routed on the fast path: no tool model is asked, so a cold one can
    never say "I'm waking up my language model" before the answer."""
    from domovoi.router import goes_to_the_tool_model, plan_route

    for said in ("Drop into the kitchen.", "Drop into the dining room."):
        plan = plan_route(said)
        assert plan is not None and plan.handler.name == "dropin", said
        assert goes_to_the_tool_model(said) is False


def test_spoken_room_matches_hyphen_and_space(_rooms) -> None:
    assert _resolve_target_rooms("the dining room") == ["dining-room"]
    assert _resolve_target_rooms("dining-room") == ["dining-room"]
    assert pretty_room("dining-room") == "dining room"


def test_no_fuzzy_guess_at_an_unknown_room(_rooms) -> None:
    assert _resolve_target_rooms("the kitchen") is None
    assert _resolve_target_rooms("dining") is None


def test_unknown_room_lists_the_rooms_it_knows(_rooms) -> None:
    app = _app(active_sessions={GARAGE: 1, "dining-room": 1})
    ctx = Context(room_id=GARAGE, app=app)
    r = DropInHandler()._start("the kitchen", ctx)
    assert r.dropin_action is None
    assert r.text.startswith("I don't know which room you mean by 'the kitchen'.")
    assert "the dining room" in r.text
    assert "garage" not in r.text            # not the room it was said in


def test_a_polite_tail_is_not_part_of_the_room(_rooms) -> None:
    app = _app(active_sessions={GARAGE: 1, "dining-room": 1},
               satellite_full_duplex={GARAGE: True, "dining-room": True})
    r = DropInHandler()._start("the dining room please", Context(room_id=GARAGE, app=app))
    assert r.dropin_action == "request" and r.dropin_room == "dining-room"
    assert r.text == "Dropping in on the dining room."


# ─── 1. a turn that opens a call asks nothing back ────────────────────────


def _ident(best: float = 0.55, person: int = 7):
    from domovoi.voice_identifier import OUTCOME_NO_MATCH, IdentificationResult

    v = np.ones(192, dtype=np.float32)
    return IdentificationResult(
        person_id=None, presence_tier="low", embedding=v / np.linalg.norm(v),
        best_similarity=best, outcome=OUTCOME_NO_MATCH, best_person_id=person,
        speech_seconds=2.0,
    )


def test_room_check_is_never_asked_on_a_turn_that_opens_a_call_or_an_announcement() -> None:
    from domovoi.handlers.voice_profile import _ROOM_CHECK_ASKED, _room_check_wanted

    _ROOM_CHECK_ASKED.clear()
    ctx = Context(room_id=GARAGE)
    ident = _ident(best=(0.50 + settings.voice_profile_match_threshold) / 2)
    plain = Response(text="It's noon.")
    assert _room_check_wanted(ctx, plain, ident) is not None      # it would ask here
    for r in (
        Response(text="Dropping in on the kitchen.", dropin_action="request", dropin_room=KITCHEN),
        Response(text="Okay, connecting you with the garage.", dropin_action="accept"),
        Response(text="Hanging up.", dropin_action="end"),
        Response(text="Announcing in the kitchen.", announce_to_rooms=[KITCHEN],
                 announce_text="dinner"),
    ):
        assert starts_live_audio(r)
        assert _room_check_wanted(ctx, r, ident) is None, r.text
    # Heard during a call: never asked either.
    assert _room_check_wanted(Context(room_id=GARAGE, in_call=True), plain, ident) is None


async def test_voice_profile_hooks_skip_a_call_turn_entirely(monkeypatch) -> None:
    """Nothing of the hooks runs (s=None would fail if anything did): no
    buffering for an introduction, no third-party ask, no room check."""
    from domovoi.handlers import voice_profile

    called: list[str] = []

    async def _spy(*_a, **_kw):
        called.append("hook")
        return False

    for name in ("buffer_unknown_voice_turn", "maybe_ask_room_check",
                 "maybe_inject_third_party_ask"):
        monkeypatch.setattr(voice_profile, name, _spy)
    start = Response(text="Dropping in on the kitchen.", dropin_action="request")
    await streaming.run_voice_profile_hooks(
        None, response=start, ctx=Context(room_id=GARAGE), ident=_ident(),
        person_id=None, transcript="drop into the kitchen", audio_seconds=2.0,
    )
    plain = Response(text="It's noon.")
    await streaming.run_voice_profile_hooks(
        None, response=plain, ctx=Context(room_id=GARAGE, in_call=True), ident=_ident(),
        person_id=None, transcript="what time is it", audio_seconds=2.0,
    )
    assert called == []


# ─── 3. "stop" / "hang up" in a call ───────────────────────────────────────


def test_end_call_phrases() -> None:
    for s in ("stop", "stop.", "Stop!", "stop stop", "stop it", "hang up", "hang up.",
              "okay, hang up please", "end the drop-in", "end the drop in", "end drop-in",
              "stop the call", "end it", "that's enough", "disconnect",
              "hang up the call", "leave the call"):
        assert end_call_phrase(s.lower()), s
    for s in ("stop the music", "play jazz", "stop the timer", "what time is it",
              "hang on", "end of story", "yes it was"):
        assert not end_call_phrase(s), s


def test_end_call_kinds() -> None:
    from domovoi.dropin_common import END_EXPLICIT, END_STOP, END_WEAK, end_call_kind

    for s in ("stop", "stop.", "stop stop", "stop it please"):
        assert end_call_kind(s) == END_STOP, s
    for s in ("hang up", "okay, hang up please", "end the drop-in", "stop the call",
              "leave the drop in", "cancel the call"):
        assert end_call_kind(s) == END_EXPLICIT, s
    for s in ("cancel", "that's enough", "end it", "disconnect"):
        assert end_call_kind(s) == END_WEAK, s


def test_room_in_call_reads_the_live_registry() -> None:
    app = _app(active_dropins={GARAGE: {"peer": KITCHEN}})
    assert room_in_call(app, GARAGE) and not room_in_call(app, KITCHEN)
    assert not room_in_call(None, GARAGE)


def test_voice_profile_is_refused_in_a_call() -> None:
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.router import CALL_REFUSAL_TEXT, call_refusal

    vp, dropin = HANDLER_BY_NAME["voice_profile"], HANDLER_BY_NAME["dropin"]
    assert vp.works_in_call is False
    r = call_refusal(vp, Context(room_id=GARAGE, in_call=True))
    assert r is not None and r.text == CALL_REFUSAL_TEXT and not r.expect_followup
    assert call_refusal(vp, Context(room_id=GARAGE)) is None
    assert call_refusal(dropin, Context(room_id=GARAGE, in_call=True)) is None


# ─── 4. never half in a call ───────────────────────────────────────────────


async def test_a_followup_capture_open_when_the_call_starts_is_dropped(_quiet_calls) -> None:
    garage, kitchen = _pair()
    garage._capture_control = True
    await garage._on_control({"type": "utterance_start", "trigger": "followup", "utt": 3})
    assert garage.utterance_active
    await garage._begin_dropin(kitchen)
    assert garage.utterance_active is False
    assert {"type": "end_capture", "utt": 3} in _texts(garage.ws)
    # The garage's mic is the call's now: relayed, not buffered for a turn.
    await garage._on_audio(b"\x01\x02" * 8)
    assert (b"\x01\x02" * 8) not in garage.audio_buf
    assert any(k == "bytes" and n == 16 for _t, k, n in kitchen.ws.sent)
    # Its utterance_end gets an empty reply, never a turn.
    before = len(_texts(garage.ws))
    await garage._on_control({"type": "utterance_end", "utt": 3})
    assert garage._response_task is None
    tail = _texts(garage.ws)[before:]
    assert [f["type"] for f in tail] == ["response_start", "response_end"]
    assert tail[0]["text"] == "" and tail[0]["audio_sample_rate"] == 16_000
    await garage._end_dropin(ended_by=GARAGE)


async def test_a_followup_listen_during_a_call_is_dropped(_quiet_calls) -> None:
    garage, kitchen = _pair()
    garage._capture_control = True
    await garage._begin_dropin(kitchen)
    await garage._on_control({"type": "utterance_start", "trigger": "followup", "utt": 9})
    assert garage.utterance_active is False
    assert {"type": "end_capture", "utt": 9} in _texts(garage.ws)
    n = len(kitchen.ws.sent)
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n + 1          # relayed
    await garage._end_dropin(ended_by=GARAGE)


async def test_the_other_room_stays_off_a_command_capture(_quiet_calls) -> None:
    """While the kitchen captures "hey jarvis, hang up", the garage's
    voice is not played into it (it would reach the kitchen's transcript
    and voice ID)."""
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    await kitchen._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 1})
    n = len(kitchen.ws.sent)
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n               # held back
    await kitchen._on_control({"type": "utterance_end", "utt": 1})
    await _cancelled(kitchen._response_task)
    kitchen.utterance_active = False
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n + 1           # back once it ended
    await garage._end_dropin(ended_by=GARAGE)


async def _cancelled(task) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 — cancelled on purpose
        pass


async def test_a_stale_capture_holds_nothing(_quiet_calls) -> None:
    """A capture that ended with no utterance_end (a phone muted
    mid-command) does not keep the other room off its speaker, and the
    room's own audio, once it comes again, goes back to the call."""
    garage, kitchen = _pair()
    kitchen.board = "android_room"
    await garage._begin_dropin(kitchen)
    await kitchen._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 1})
    kitchen._last_audio_at -= streaming.ANNOUNCE_CAPTURE_FRESH_SEC + 0.1   # went quiet
    n = len(kitchen.ws.sent)
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n + 1           # not held
    m = len(garage.ws.sent)
    await kitchen._on_audio(FRAME)                 # the kitchen's mic again
    assert kitchen.utterance_active is False
    assert len(kitchen.audio_buf) == 0
    assert len(garage.ws.sent) == m + 1            # relayed to the garage
    await garage._end_dropin(ended_by=GARAGE)


async def test_a_pi_command_survives_a_wifi_gap(_quiet_calls) -> None:
    """A Pi always ends its capture with utterance_end: two seconds without
    frames mid-"hang up" is the link, and the command is kept."""
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    await kitchen._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 1})
    await kitchen._on_audio(FRAME)
    kitchen._last_audio_at -= 2.0                  # a 2 s Wi-Fi gap
    await kitchen._on_audio(FRAME)                 # frames resume
    assert kitchen.utterance_active is True
    assert len(kitchen.audio_buf) == 2 * len(FRAME)
    # Gone far longer than any hiccup: the capture is over.
    kitchen._last_audio_at -= streaming.QUIET_CAPTURE_OVER_SEC + 0.1
    await kitchen._on_audio(FRAME)
    assert kitchen.utterance_active is False
    await garage._end_dropin(ended_by=GARAGE)


async def test_the_relay_is_held_while_the_room_is_answered(_quiet_calls) -> None:
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    gate = asyncio.Event()
    kitchen._response_task = asyncio.create_task(gate.wait())
    n = len(kitchen.ws.sent)
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n               # held: the kitchen's reply
    gate.set()
    await kitchen._response_task
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n + 1
    # ...and while its silent response goes out.
    kitchen._releasing_reply = True
    await garage._on_audio(FRAME)
    assert len(kitchen.ws.sent) == n + 1
    kitchen._releasing_reply = False
    await garage._end_dropin(ended_by=GARAGE)


async def test_no_call_audio_between_the_silent_start_and_end(_quiet_calls) -> None:
    """The garage's mic frame arriving while the kitchen's silent response
    is half sent is not relayed between its two frames."""
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    real_send = kitchen.ws.send_text

    async def send_text(data):
        await real_send(data)
        if json.loads(data).get("text") == "":
            await garage._on_audio(FRAME)          # lands mid-pair
    kitchen.ws.send_text = send_text
    before = len(kitchen.ws.sent)
    await kitchen._safe_send_text({"type": "response_end", "interrupted": False,
                                   "expect_followup": False})
    kinds = [k if k == "bytes" else f["type"] for _t, k, f in kitchen.ws.sent[before:]]
    assert kinds == ["response_end", "response_start", "response_end"]
    await garage._end_dropin(ended_by=GARAGE)


@pytest.mark.parametrize("board", ["android_room", "ios_room", "some_new_device"])
async def test_phones_and_unknown_devices_get_no_silent_response(_quiet_calls, board) -> None:
    """A phone's response_start resets its speaker and would flush the
    reply still queued there; it needs no release. Unknown boards don't
    get it either."""
    garage, kitchen = _pair()
    garage.board = board
    await garage._begin_dropin(kitchen)
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": False,
                                  "expect_followup": False})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == ["response_end"]
    # A follow-up capture dropped in the call gets the bare end instead.
    garage._capture_control = True
    await garage._on_control({"type": "utterance_start", "trigger": "followup", "utt": 4})
    before = len(_texts(garage.ws))
    await garage._on_control({"type": "utterance_end", "utt": 4})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == ["response_end"]
    await garage._end_dropin(ended_by=GARAGE)


@pytest.mark.parametrize("board", [None, "linux_pc", "windows_pc"])
async def test_python_satellites_get_the_silent_response(_quiet_calls, board) -> None:
    garage, kitchen = _pair()
    garage.board = board
    await garage._begin_dropin(kitchen)
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": False,
                                  "expect_followup": False})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == [
        "response_end", "response_start", "response_end"]
    await garage._end_dropin(ended_by=GARAGE)


async def test_no_silent_response_after_an_interrupted_reply_or_into_a_capture(
    _quiet_calls,
) -> None:
    """Talked over: the satellite released its own wait. A capture open:
    an extra response_start would count as one from before it and end
    that capture's wait early."""
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": True,
                                  "expect_followup": False})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == ["response_end"]
    await garage._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 2})
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": False,
                                  "expect_followup": False})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == ["response_end"]
    garage.utterance_active = False
    await garage._end_dropin(ended_by=GARAGE)


async def test_every_reply_in_a_call_releases_the_satellite(_quiet_calls) -> None:
    garage, kitchen = _pair()
    await garage._begin_dropin(kitchen)
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": False,
                                  "expect_followup": False})
    tail = _texts(garage.ws)[before:]
    assert [f["type"] for f in tail] == ["response_end", "response_start", "response_end"]
    assert tail[1]["text"] == "" and tail[1]["audio_sample_rate"] == 16_000
    assert tail[2]["expect_followup"] is False
    # Outside a call nothing extra is sent.
    await garage._end_dropin(ended_by=GARAGE)
    before = len(_texts(garage.ws))
    await garage._safe_send_text({"type": "response_end", "interrupted": False,
                                  "expect_followup": False})
    assert [f["type"] for f in _texts(garage.ws)[before:]] == ["response_end"]


# ─── the turn layer: follow-ups forced off ────────────────────────────────


@pytest.fixture
def turn(monkeypatch, _quiet_calls):
    """Two rooms whose turns run for real in streaming, with route()
    returning ``state.reply`` (no database)."""
    from contextlib import asynccontextmanager

    app = _app()
    garage, kitchen = _pair(app)
    state = types.SimpleNamespace(app=app, garage=garage, kitchen=kitchen, reply=None,
                                  identify_kw=[])

    class _Whisper:
        async def transcribe(self, pcm):
            return "anything"

        async def transcribe_wav_bytes(self, wav):
            return "anything"

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            return _wav(0.1)

    async def _route(intent, ctx, session):
        return state.reply

    async def _identify(pcm, embedding=None, **kw):
        state.identify_kw.append(kw)
        return None

    async def _any_voice(name):
        return (None, None)

    async def _hooks(self, s, *, response, **kw):
        return None

    @asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "resolve_voice", _any_voice)
    monkeypatch.setattr(streaming.StreamSession, "_voice_profile_hooks", _hooks)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    yield state
    for s in (garage, kitchen):
        if s._response_task is not None and not s._response_task.done():
            s._response_task.cancel()


async def _say(sess: StreamSession, trigger: str = "wake_word", utt: int = 1) -> list[dict]:
    before = len(_texts(sess.ws))
    await sess._on_control({"type": "utterance_start", "trigger": trigger, "utt": utt})
    for _ in range(20):
        await sess._on_audio(FRAME)
    await sess._on_control({"type": "utterance_end", "utt": utt})
    assert sess._response_task is not None
    await sess._response_task
    return _texts(sess.ws)[before:]


async def test_a_turn_that_opens_a_call_opens_no_followup(turn) -> None:
    """Even a reply that asked for one (a handler's question appended to
    the drop-in turn) goes out without a follow-up listen."""
    turn.reply = Response(text="Dropping in on the kitchen.", dropin_action="request",
                          dropin_room=KITCHEN, expect_followup=True)
    frames = await _say(turn.garage)
    end = next(f for f in frames if f["type"] == "response_end")
    assert end["expect_followup"] is False
    assert turn.garage.dropin_peer is turn.kitchen
    await turn.garage._end_dropin(ended_by=GARAGE)


async def test_an_announcement_turn_opens_no_followup(turn) -> None:
    turn.reply = Response(text="Announcing in the kitchen.", announce_to_rooms=[KITCHEN],
                          announce_text="dinner", expect_followup=True)
    turn.kitchen.announce = lambda *a, **k: asyncio.sleep(0)  # type: ignore[assignment]
    frames = await _say(turn.garage)
    assert next(f for f in frames if f["type"] == "response_end")["expect_followup"] is False


async def test_a_turn_in_a_call_opens_no_followup_and_learns_nothing(turn) -> None:
    await turn.garage._begin_dropin(turn.kitchen)
    turn.reply = Response(text="Did you mean the album?", expect_followup=True)
    frames = await _say(turn.garage)
    kinds = [f["type"] for f in frames]
    ends = [f for f in frames if f["type"] == "response_end"]
    assert ends[0]["expect_followup"] is False
    # ...and the empty reply that lets the satellite stop waiting.
    assert kinds[-2:] == ["response_start", "response_end"]
    assert frames[-2]["text"] == ""
    assert turn.identify_kw[-1] == {"room_id": GARAGE, "satellite_id": GARAGE,
                                    "learn": False}
    await turn.garage._end_dropin(ended_by=GARAGE)


async def test_a_turn_outside_a_call_still_learns(turn) -> None:
    turn.reply = Response(text="It's noon.")
    await _say(turn.garage)
    assert "learn" not in turn.identify_kw[-1]


# ─── 7. the whole sequence, on the real router ────────────────────────────


@pytest.fixture
async def house(monkeypatch, db_session, _quiet_calls):
    """garage + kitchen satellites, the REAL router and voice-profile hooks
    on the lane's database. Whisper hears ``said``; voice ID scores every
    turn just under the bar for Alex (``_ident``), as in the case."""
    from domovoi.confirmations import CHOICE_PARKED
    from domovoi.db.repositories import PeopleRepository
    from domovoi.db.session import session_scope
    from domovoi.handlers.voice_profile import _ROOM_CHECK_ASKED

    async with session_scope() as s:
        alex = await PeopleRepository(s).create(name="Alex")
    _ROOM_CHECK_ASKED.clear()
    CHOICE_PARKED.clear()
    mpd_module._room_ports = {GARAGE: (6650, 8050), KITCHEN: (6651, 8051)}
    app = _app()
    garage, kitchen = _pair(app)
    garage._capture_control = True
    h = types.SimpleNamespace(app=app, garage=garage, kitchen=kitchen, said="", alex=alex,
                              saved=[])

    class _Whisper:
        async def transcribe(self, pcm):
            return h.said

        async def transcribe_wav_bytes(self, wav):
            return h.said

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            return _wav(0.1)

    async def _identify(pcm, embedding=None, **kw):
        return _ident(best=(0.50 + settings.voice_profile_match_threshold) / 2, person=alex)

    async def _any_voice(name):
        return (None, None)

    from domovoi.handlers.voice_profile import VoiceProfileHandler

    async def _no_save(self, data, person_id, clips, session):
        h.saved.append(clips)
        return None

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "resolve_voice", _any_voice)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(VoiceProfileHandler, "_save_learned_room", _no_save)
    yield h
    for s in (garage, kitchen):
        if s.dropin_peer is not None:
            await s._end_dropin(ended_by="test")
        if s._response_task is not None and not s._response_task.done():
            s._response_task.cancel()
    mpd_module._room_ports = {}
    _ROOM_CHECK_ASKED.clear()
    CHOICE_PARKED.clear()


async def _heard(h, sess, said: str, *, trigger: str = "wake_word", utt: int = 1) -> list[dict]:
    h.said = said
    return await _say(sess, trigger=trigger, utt=utt)


def _reply_text(frames: list[dict]) -> str:
    return " ".join(f.get("text") or "" for f in frames if f["type"] == "response_start").strip()


async def _pending(sess):
    from domovoi.db.repositories import SessionRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        ctx = await SessionRepository(s).get_context(sess.session_id) or {}
    return ctx.get("pending_confirmation")


async def _park_room_learning(h) -> None:
    """An introduction collecting its clips ("count from one to ten"), as
    the case's yes started one — parked the way _start_room_learning
    parks it, to show that nothing a call hears can feed it."""
    import time

    from domovoi.confirmations import request_confirmation
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await request_confirmation(
            s, h.garage.session_id, kind="core.self_intro", handler="voice_profile",
            data={"stage": "count", "name": "Alex", "person_id": h.alex, "sample_id": None,
                  "clips": [["AAAA", 1.5]], "learn_room": GARAGE, "replace_ids": [],
                  "have_secs": 0.0, "origin_room": GARAGE,
                  "expires_at": time.time() + 120},
        )


@requires_db
async def test_the_case_replayed_ends_with_a_spoken_hang_up(house) -> None:
    h = house
    # Precondition: this voice, on an ordinary turn here, IS asked about.
    frames = await _heard(h, h.garage, "What time is it?")
    assert "was that you, Alex?" in _reply_text(frames)
    await _heard(h, h.garage, "No.", trigger="followup", utt=2)
    from domovoi.handlers.voice_profile import _ROOM_CHECK_ASKED

    _ROOM_CHECK_ASKED.clear()

    # 1. The drop-in: no question, no follow-up, the call is up both ways.
    frames = await _heard(h, h.garage, "Drop into the kitchen.", utt=3)
    assert _reply_text(frames) == "Dropping in on the kitchen."
    end = next(f for f in frames if f["type"] == "response_end")
    assert end["expect_followup"] is False
    assert await _pending(h.garage) is None
    assert h.garage.dropin_peer is h.kitchen and h.kitchen.dropin_peer is h.garage
    assert set(h.app.state.active_dropins) == {GARAGE, KITCHEN}

    # 2. "Yes it was. Yes. Baby." — a follow-up listen left armed on the
    # satellite is dropped (the call keeps the mic); said after the wake
    # word, it is an ordinary turn: no introduction, nothing learned.
    await h.garage._on_control({"type": "utterance_start", "trigger": "followup", "utt": 4})
    assert h.garage.utterance_active is False
    await h.garage._on_control({"type": "utterance_end", "utt": 4})
    frames = await _heard(h, h.garage, "Yes it was. Yes. Baby.", utt=5)
    assert "count from one to ten" not in _reply_text(frames)
    assert all(not f.get("expect_followup") for f in frames if f["type"] == "response_end")
    assert [f["type"] for f in frames][-2:] == ["response_start", "response_end"]
    assert await _pending(h.garage) is None

    # 3. An introduction parked anyway (as the case's yes started one):
    # "Stop." ends the CALL, on both sides, and the introduction is dropped
    # unsaved.
    await _park_room_learning(h)
    frames = await _heard(h, h.garage, "Stop.", utt=6)
    assert _reply_text(frames) == "Hanging up."
    assert h.garage.dropin_peer is None and h.kitchen.dropin_peer is None
    assert h.app.state.active_dropins == {}
    for sess in (h.garage, h.kitchen):
        assert any(f["type"] == "dropin_end" for f in _texts(sess.ws)), sess.room_id
    assert await _pending(h.garage) is None
    assert h.saved == []

    # 4. "Hang up" now: the call is over, and the room still answers (an
    # ordinary turn again, which may ask its question once more).
    frames = await _heard(h, h.garage, "Hang up.", utt=7)
    assert _reply_text(frames).startswith("You're not in a call right now.")


@requires_db
async def test_hang_up_ends_a_call_even_with_an_introduction_parked(house) -> None:
    h = house
    await _heard(h, h.garage, "Drop into the kitchen.", utt=1)
    assert h.garage.dropin_peer is h.kitchen
    await _park_room_learning(h)
    frames = await _heard(h, h.garage, "Hang up.", utt=2)
    assert _reply_text(frames) == "Hanging up."
    assert h.garage.dropin_peer is None and h.kitchen.dropin_peer is None
    for sess in (h.garage, h.kitchen):
        ends = [f for f in _texts(sess.ws) if f["type"] == "dropin_end"]
        assert len(ends) == 1, sess.room_id
    assert h.saved == []


@requires_db
async def test_hang_up_from_the_other_room_ends_it_too(house) -> None:
    h = house
    await _heard(h, h.garage, "Drop into the kitchen.", utt=1)
    frames = await _heard(h, h.kitchen, "Stop the drop-in.", utt=1)
    assert _reply_text(frames) == "Hanging up."
    assert h.app.state.active_dropins == {}
    assert h.garage.dropin_peer is None and h.kitchen.dropin_peer is None


@requires_db
async def test_an_introduction_is_refused_during_a_call(house) -> None:
    h = house
    await _heard(h, h.garage, "Drop into the kitchen.", utt=1)
    frames = await _heard(h, h.garage, "My name is Sam.", utt=2)
    from domovoi.router import CALL_REFUSAL_TEXT

    assert _reply_text(frames) == CALL_REFUSAL_TEXT
    assert await _pending(h.garage) is None
    assert h.garage.dropin_peer is h.kitchen


@requires_db
async def test_bare_stop_outside_a_call_is_not_the_drop_in(house) -> None:
    h = house
    frames = await _heard(h, h.garage, "Stop.", utt=1)
    assert _reply_text(frames) != "You're not in a call right now."
    assert all(json.dumps(f).find("dropin_end") < 0 for f in frames)


# ─── 6. which "stop" ends a call, from where ─────────────────────────────


async def _route(db_session, said: str, *, in_call: bool, session_id=None, app=None):
    from domovoi.models import Intent
    from domovoi.router import route

    ctx = Context(room_id=GARAGE, session_id=session_id, app=app, in_call=in_call)
    return await route(Intent(transcript=said, room_id=GARAGE, session_id=session_id), ctx,
                       db_session)


def _call_app():
    return _app(active_dropins={GARAGE: {"peer": KITCHEN}, KITCHEN: {"peer": GARAGE}})


@pytest.fixture
async def fire_ledger(db_session):
    from domovoi.tests.timer_fires_testkit import apply_v018

    await apply_v018()
    yield db_session
    await db_session.rollback()
    await apply_v018()


async def _fire(s, *, outcome: str, ago: float = 3.0) -> int:
    """A timer set in the garage that went off ``ago`` seconds ago, and the
    garage's delivery row: ``pending`` (held, waiting for the room to be
    free — a call holds it), or ``spoken`` (heard there)."""
    from sqlalchemy import text

    fid = (await s.execute(
        text(
            "INSERT INTO timer_fires (timer_id, kind, label, message, origin_room_id, "
            "created_at, due_at, fired_at, base_text) VALUES (1, 'timer', NULL, NULL, :r, "
            "now() - interval '10 minutes', now() - make_interval(secs => :ago), "
            "now() - make_interval(secs => :ago), 'x') RETURNING id"
        ),
        {"r": GARAGE, "ago": ago},
    )).scalar_one()
    await s.execute(
        text(
            "INSERT INTO timer_fire_deliveries (fire_id, room_id, is_origin, outcome, started_at, "
            "finished_at) VALUES (:f, :r, true, :out, "
            "CASE WHEN :out = 'pending' THEN NULL ELSE now() - make_interval(secs => :ago) END, "
            "CASE WHEN :out IN ('spoken', 'interrupted') THEN now() - make_interval(secs => :ago) END)"
        ),
        {"f": fid, "r": GARAGE, "out": outcome, "ago": ago},
    )
    return int(fid)


async def _fire_state(s, fid: int) -> tuple:
    from sqlalchemy import text

    return (await s.execute(
        text(
            "SELECT f.acked_at IS NULL, d.outcome FROM timer_fires f "
            "JOIN timer_fire_deliveries d ON d.fire_id = f.id WHERE f.id = :f"
        ),
        {"f": fid},
    )).one()


@requires_db
async def test_a_held_timer_nobody_heard_does_not_take_the_calls_stop(fire_ledger) -> None:
    """The garage's timer went off during the call: its announcement is
    held (pending) until the room is free. A bare "stop" there is for the
    call — it ends it, and the timer is neither acknowledged nor cancelled:
    it is still due to ring once the call is over."""
    from domovoi.timer_delivery import ringing_fire

    s = fire_ledger
    fid = await _fire(s, outcome="pending")
    r = await _route(s, "stop", in_call=True, app=_call_app())
    assert r.dropin_action == "end"
    assert tuple(await _fire_state(s, fid)) == (True, "pending")
    # Still the room's ringing timer, so it announces when the call is over.
    ringing = await ringing_fire(s, GARAGE)
    assert ringing is not None and ringing.fire_id == fid


@requires_db
async def test_a_timer_just_heard_is_silenced_before_the_call_ends(fire_ledger) -> None:
    """One actually announced in the room seconds ago (it rang just before
    the call opened): the first "stop" silences it, the next hangs up."""
    s = fire_ledger
    fid = await _fire(s, outcome="spoken", ago=3.0)
    r = await _route(s, "stop", in_call=True, app=_call_app())
    assert r.text == "Okay." and r.dropin_action is None
    assert (await _fire_state(s, fid))[0] is False          # acknowledged
    r = await _route(s, "stop", in_call=True, app=_call_app())
    assert r.dropin_action == "end"


@requires_db
async def test_a_timer_heard_long_ago_does_not_take_the_calls_stop(fire_ledger) -> None:
    s = fire_ledger
    await _fire(s, outcome="spoken", ago=20.0)    # inside the 30 s ack window, not "just"
    r = await _route(s, "stop", in_call=True, app=_call_app())
    assert r.dropin_action == "end"


@requires_db
async def test_cancel_goes_to_a_parked_question_not_the_call(db_session) -> None:
    from domovoi.confirmations import request_confirmation

    app = _call_app()
    first = await _route(db_session, "what time is it", in_call=True, app=app)
    sid = first.session_id
    await request_confirmation(db_session, sid, kind="core.pending_memory_offer",
                               handler="memory", data={"body": "the oven is gas"})
    r = await _route(db_session, "cancel", in_call=True, session_id=sid, app=app)
    assert r.dropin_action is None and r.matched_handler != "dropin"
    # Nothing parked any more: "cancel" ends the call.
    from domovoi.confirmations import PENDING_CONFIRMATION_KEY
    from domovoi.db.repositories import SessionRepository

    await SessionRepository(db_session).set_context_key(sid, PENDING_CONFIRMATION_KEY, None)
    r = await _route(db_session, "cancel", in_call=True, session_id=sid, app=app)
    assert r.dropin_action == "end"
    # "Hang up" ends it even with a question parked.
    await request_confirmation(db_session, sid, kind="core.pending_memory_offer",
                               handler="memory", data={"body": "the oven is gas"})
    r = await _route(db_session, "hang up", in_call=True, session_id=sid, app=app)
    assert r.dropin_action == "end"


@requires_db
async def test_a_bare_stop_from_elsewhere_never_ends_the_call(db_session) -> None:
    """A turn not made by the call's own satellite (a phone's chat mic, the
    dashboard with the room picked: in_call comes from ctx.room_id) ends
    the call only with an explicit "hang up"."""
    app = _call_app()
    r = await _route(db_session, "stop", in_call=False, app=app)
    assert r.dropin_action is None and r.matched_handler != "dropin"
    r = await _route(db_session, "that's enough", in_call=False, app=app)
    assert r.dropin_action is None
    r = await _route(db_session, "hang up", in_call=False, app=app)
    assert r.dropin_action == "end"
