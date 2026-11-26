"""Rooms with several satellites, slice 3: one turn per wake word
(domovoi/wake_claims.py and its frames in domovoi/streaming.py; contract in
docs/uml/satellite-protocol.md, "One turn per wake word").

The satellite ids here never equal their rooms' (like test_rooms_registry),
so a place that mixes a device up with its room shows up.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from domovoi import room_devices, satellite_abilities as sa, streaming, wake_claims
from domovoi.room_playout.service import RoomPlayout
from domovoi.satellite_abilities import Abilities
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
TURNS = FIXTURES["room_turns"]
FRAME = 480  # 30 ms at 16 kHz
CLAIMING = Abilities(declared=True, names=frozenset({"wake_claim", "capture_control", "speech_pause", "room_audio"}))


# ─── synthetic audio (spike 0a's harness, in small) ───────────────────────


def _level(x: np.ndarray, dbfs: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2))) or 1.0
    return x * (10 ** (dbfs / 20) * 32768 / rms)


def _frames(x: np.ndarray) -> list[bytes]:
    pcm = np.clip(np.round(x), -32768, 32767).astype("<i2")
    return [pcm[i:i + FRAME].tobytes() for i in range(0, len(pcm) - FRAME + 1, FRAME)]


def _scene(rng: np.random.Generator, *, cmd_db: float, bg_db: float, gain_db: float,
           seconds: float = 1.6) -> tuple[float, list[bytes]]:
    """One device's view: its background (pink-ish noise) and the command
    (a voiced burst) on top, all lifted by the device's own gain (AGC, mic
    gain). Returns (its pre-wake floor, the command's frames)."""
    n = int(seconds * 16_000)
    bg = _level(np.cumsum(rng.standard_normal(n)) * 0.02 + rng.standard_normal(n), bg_db)
    t = np.arange(n) / 16_000
    voiced = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) ** 2
    cmd = _level(voiced, cmd_db)
    g = 10 ** (gain_db / 20)
    floor = wake_claims.percentile(
        [streaming.pcm_dbfs(f) for f in _frames(bg * g)], 50) or -120.0
    return floor, _frames((bg + cmd) * g)


# ─── fakes ─────────────────────────────────────────────────────────────────


class _Dev:
    """What the arbiter touches of a session."""

    def __init__(self, sid: str, room: str) -> None:
        self.satellite_id, self.room_id = sid, room
        self.sent: list[dict] = []
        self._response_task: asyncio.Task | None = None
        self.quiet_stream = None
        self.owner_claim = None
        self.cuts = 0
        self.moved = 0
        self.playing = False

    async def _safe_send_text(self, frame: dict) -> None:
        self.sent.append(frame)

    def _cut_announcement(self) -> None:
        self.cuts += 1

    def room_speech_playing(self) -> bool:
        return self.playing

    def mic_moved(self) -> None:
        self.moved += 1


def _house(*devs: tuple[str, str]) -> tuple[Any, wake_claims.Arbiter, dict[str, _Dev]]:
    app = SimpleNamespace(state=SimpleNamespace())
    reg = room_devices.registry(app)
    out = {}
    for sid, room in devs:
        d = _Dev(sid, room)
        reg.register(d)
        out[sid] = d
    cut: list[str] = []
    arb = wake_claims.Arbiter(reg, cut_room_speech=cut.append)
    arb.cut_log = cut  # type: ignore[attr-defined]
    return app, arb, out


def _claim(n: int, floor: float | None = -50.0, **kw: Any) -> dict:
    return {"type": "wake_claim", "claim": n, "word": "hey_jarvis", "score": 0.9,
            "floor_dbfs": floor, **kw}


# ─── arbitration ───────────────────────────────────────────────────────────


async def test_the_first_claim_owns_the_turn_at_once_and_later_ones_are_quiet() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_phone", "kitchen"), ("k_tab", "kitchen"))
    t0 = time.perf_counter()
    grant = await arb.on_claim(d["k_sat"], _claim(1))
    assert time.perf_counter() - t0 < 0.05  # answered at once: no window, no wait
    assert grant == {"type": "wake_grant", "claim": 1, "turn": "t1", "role": "owner"}
    assert d["k_sat"].sent == [grant]
    assert (await arb.on_claim(d["k_phone"], _claim(4)))["stream_ms"] == wake_claims.STREAM_MS
    assert d["k_phone"].quiet_stream is not None
    # Past the join window: stand down, stream nothing.
    arb.turns[0].started -= wake_claims.JOIN_WINDOW_SEC + 0.1
    late = await arb.on_claim(d["k_tab"], _claim(9))
    assert late["role"] == "quiet" and late["stream_ms"] == 0
    assert d["k_tab"].quiet_stream is None


async def test_a_single_satellite_room_keeps_the_direct_path() -> None:
    _, arb, d = _house(("den_sat", "den"), ("k_sat", "kitchen"), ("k_phone", "kitchen"))
    assert arb.claim_flag("den") is False and arb.claim_flag("kitchen") is True
    # An unclaimed capture in a lone room: no turn, nothing to wait on.
    assert await arb.on_capture_start(d["den_sat"], {"trigger": "wake_word", "utt": 1}) is True
    assert arb.turns == []
    await arb.on_owner_audio(d["den_sat"], b"\0" * 960)
    assert await arb.on_owner_end(d["den_sat"]) is None


async def test_linked_rooms_are_one_group() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("l_sat", "living"), ("o_sat", "office"))
    assert arb.claim_flag("kitchen") is False
    arb.set_links([("kitchen", "living")])
    assert arb.claim_flag("kitchen") and arb.claim_flag("living") and not arb.claim_flag("office")
    assert (await arb.on_claim(d["l_sat"], _claim(1)))["role"] == "owner"
    assert (await arb.on_claim(d["k_sat"], _claim(1)))["role"] == "quiet"
    # An unlinked room runs its own turn at the same moment.
    assert (await arb.on_claim(d["o_sat"], _claim(1)))["role"] == "owner"
    assert len(arb.turns) == 2


async def test_the_best_mic_is_picked_by_level_over_its_own_floor() -> None:
    """Spike 0a's best-mic question: near and far satellites, background
    talk, and each device's own gain (+-10 dB). The relative score picks the
    near one where the raw level is fooled by gain."""
    rng = np.random.default_rng(7)
    right_rel = right_raw = runs = 0
    for snr in (10, 5, 0):
        for delta in (6, 10):
            for _ in range(4):
                gains = rng.uniform(-10, 10, 2)
                near = _scene(rng, cmd_db=-30, bg_db=-30 - snr, gain_db=gains[0])
                far = _scene(rng, cmd_db=-30 - delta, bg_db=-30 - snr + rng.uniform(0, 3),
                             gain_db=gains[1])
                lv = [[streaming.pcm_dbfs(f) for f in fr] for _, fr in (near, far)]
                rel = [wake_claims.mic_score(fl, lvl) for (fl, _), lvl in zip((near, far), lv)]
                raw = [wake_claims.percentile(lvl[:50], 90) for lvl in lv]
                right_rel += rel[0] > rel[1]
                right_raw += raw[0] > raw[1]
                runs += 1
    assert right_rel >= 0.9 * runs
    assert right_rel > right_raw


async def _stream(arb: wake_claims.Arbiter, dev: _Dev, frames: list[bytes], *, owner: bool) -> None:
    for f in frames:
        if owner:
            await arb.on_owner_audio(dev, f)
        else:
            await arb.on_quiet_audio(dev, f)


async def test_a_quiet_stream_that_hears_best_is_kept_and_the_others_dropped_unread() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_phone", "kitchen"), ("k_tab", "kitchen"))
    rng = np.random.default_rng(3)
    far_floor, far = _scene(rng, cmd_db=-40, bg_db=-38, gain_db=0)
    near_floor, near = _scene(rng, cmd_db=-25, bg_db=-40, gain_db=0)
    mid_floor, mid = _scene(rng, cmd_db=-34, bg_db=-40, gain_db=0)
    await arb.on_claim(d["k_sat"], _claim(1, far_floor))
    d["k_sat"].owner_claim = 1
    await arb.on_claim(d["k_phone"], _claim(2, near_floor))
    await arb.on_claim(d["k_tab"], _claim(3, mid_floor))
    await _stream(arb, d["k_sat"], far[:30], owner=True)
    await _stream(arb, d["k_tab"], mid[:40], owner=False)
    tab = d["k_tab"].quiet_stream
    await _stream(arb, d["k_phone"], near[:50], owner=False)   # 1.5 s: the pick
    t = arb.turns[0]
    assert t.winner == "k_phone"
    assert d["k_phone"].sent[-1] == {"type": "capture_keep", "claim": 2, "turn": "t1"}
    assert d["k_tab"].sent[-1] == {"type": "end_capture", "claim": 3}
    assert d["k_sat"].moved == 1   # the owner's early copies are of the wrong audio
    # The dropped stream was cleared at the pick, and nothing more is kept.
    assert len(tab.pcm) == 0 and tab.levels == [] and tab.dropped
    await _stream(arb, d["k_tab"], mid[40:45], owner=False)
    assert len(tab.pcm) == 0
    # The kept one goes on to the end of the command.
    await _stream(arb, d["k_phone"], near[50:53], owner=False)
    handover = await arb.on_owner_end(d["k_sat"])
    assert handover is not None and handover.satellite_id == "k_phone"
    arb.on_quiet_end(d["k_phone"], {"claim": 2, "frames": 53, "reason": "vad_silence_after_speech"})
    assert await handover.pcm(timeout=1) == b"".join(near[:53])


async def test_the_owner_keeps_the_turn_on_a_tie_or_with_nothing_to_compare() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_phone", "kitchen"))
    await arb.on_claim(d["k_sat"], _claim(1, None))  # no floor: not comparable
    assert (await arb.on_claim(d["k_phone"], _claim(2)))["stream_ms"] == 0
    assert await arb.on_owner_end(d["k_sat"]) is None


async def test_an_older_device_s_duplicate_capture_is_dropped() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_old", "kitchen"))
    await arb.on_claim(d["k_sat"], _claim(1))
    assert await arb.on_capture_start(d["k_sat"], {"trigger": "wake_word", "claim": 1}) is True
    assert await arb.on_capture_start(d["k_old"], {"trigger": "wake_word", "utt": 5}) is False
    # An older device that wakes first owns the turn; claims after it stand down.
    _, arb, d = _house(("k_sat", "kitchen"), ("k_old", "kitchen"))
    assert await arb.on_capture_start(d["k_old"], {"trigger": "wake_word", "utt": 5}) is True
    assert (await arb.on_claim(d["k_sat"], _claim(1)))["stream_ms"] == 0


async def test_a_claim_during_the_answer_is_a_barge_in_from_any_satellite() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_phone", "kitchen"))
    await arb.on_claim(d["k_sat"], _claim(1))
    await arb.on_owner_end(d["k_sat"])
    reply = asyncio.create_task(asyncio.sleep(30))
    d["k_sat"]._response_task = reply
    d["k_sat"].playing = True
    grant = await arb.on_claim(d["k_phone"], _claim(7, barge_in=True))
    assert grant["role"] == "owner" and grant["turn"] == "t2"
    await asyncio.sleep(0)
    assert reply.cancelled()
    assert arb.cut_log == ["kitchen"]            # the synced answer stops on every speaker
    assert d["k_sat"].cuts == 1 and d["k_phone"].cuts == 1


async def test_an_older_device_s_barge_in_stops_the_room_s_answer() -> None:
    _, arb, d = _house(("k_sat", "kitchen"), ("k_old", "kitchen"))
    await arb.on_claim(d["k_sat"], _claim(1))
    await arb.on_owner_end(d["k_sat"])
    reply = asyncio.create_task(asyncio.sleep(30))
    d["k_sat"]._response_task = reply
    await arb.on_barge_in(d["k_old"])
    await asyncio.sleep(0)
    assert reply.cancelled() and arb.cut_log == ["kitchen"]


# ─── the session: frames end to end ───────────────────────────────────────


class _WS:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.sent: list[dict] = []
        self.bytes: list[bytes] = []
        self.client = SimpleNamespace(host="192.168.1.41")
        self.headers: dict[str, str] = {}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.bytes.append(data)

    async def close(self, code: int = 1000) -> None:
        pass


def _app() -> Any:
    state = SimpleNamespace(satellite_config={}, active_sessions={}, satellite_voice={})
    return SimpleNamespace(state=state)


def _session(app: Any, sid: str, room: str, abilities: Abilities = CLAIMING) -> StreamSession:
    s = StreamSession(_WS(app), sid)  # type: ignore[arg-type]
    s.room_id = room
    s.abilities = abilities
    s._capture_control = abilities.allows("capture_control")
    room_devices.registry(app).register(s)
    return s


@pytest.fixture
def turns(monkeypatch):
    """Every turn the sessions start, with the audio it was given."""
    seen: list[tuple[str, bytes, dict]] = []

    async def fake_turn(self, pcm, **kw):
        seen.append((self.satellite_id, pcm, kw))

    monkeypatch.setattr(StreamSession, "_process_utterance_turn", fake_turn)
    monkeypatch.setattr(streaming.settings, "speculative_stt_enabled", False, raising=False)
    return seen


async def _settle(sess: StreamSession) -> None:
    if sess._response_task is not None:
        await sess._response_task


async def test_session_quiet_stream_wins_and_is_the_one_transcribed(turns) -> None:
    app = _app()
    owner, phone = _session(app, "k_sat", "kitchen"), _session(app, "k_phone", "kitchen")
    await owner._on_control(TURNS["claim"])
    await phone._on_control(TURNS["claim_late"])
    assert owner.ws.sent[-1] == {**TURNS["grant_owner"]}
    assert phone.ws.sent[-1] == {**TURNS["grant_quiet"]}
    await owner._on_control(TURNS["owner_utterance_start"])
    rng = np.random.default_rng(5)
    _, far = _scene(rng, cmd_db=-42, bg_db=-40, gain_db=0)
    _, near = _scene(rng, cmd_db=-24, bg_db=-40, gain_db=0)
    # The claims carried -52 / -47.5; the scenes' floors are about -40.
    owner.owner_claim = 7
    wake_claims_turn = wake_claims.arbiter(app).turns[0]
    wake_claims_turn.streams["k_sat"].floor_dbfs = -40.0
    wake_claims_turn.streams["k_phone"].floor_dbfs = -40.0
    for f in far[:20]:
        await owner._on_audio(f)
    for f in near[:50]:
        await phone._on_audio(f)
    assert phone.ws.sent[-1] == TURNS["capture_keep"]
    assert phone.audio_buf == bytearray()          # never the session's own buffer
    for f in far[20:30]:
        await owner._on_audio(f)
    await owner._on_control({"type": "utterance_end", "utt": 1})
    await phone._on_control(TURNS["quiet_end"])
    await _settle(owner)
    [(who, pcm, kw)] = turns
    assert who == "k_sat"                          # the owner answers ...
    assert pcm == b"".join(near[:50])              # ... from the phone's stream
    assert kw["mic_satellite_id"] == "k_phone"
    assert kw["reuse"] is None


async def test_session_owner_wins_and_the_quiet_stream_is_dropped(turns) -> None:
    app = _app()
    owner, phone = _session(app, "k_sat", "kitchen"), _session(app, "k_phone", "kitchen")
    await owner._on_control({**TURNS["claim"], "floor_dbfs": -40.0})
    await phone._on_control({**TURNS["claim_late"], "floor_dbfs": -40.0})
    await owner._on_control(TURNS["owner_utterance_start"])
    rng = np.random.default_rng(6)
    _, near = _scene(rng, cmd_db=-24, bg_db=-40, gain_db=0)
    _, far = _scene(rng, cmd_db=-42, bg_db=-40, gain_db=0)
    for f in far[:30]:
        await phone._on_audio(f)
    for f in near[:50]:
        await owner._on_audio(f)
    assert phone.ws.sent[-1] == TURNS["end_capture_quiet"]
    stream = phone.quiet_stream
    for f in far[30:40]:
        await phone._on_audio(f)
    assert len(stream.pcm) == 0
    await owner._on_control({"type": "utterance_end", "utt": 1})
    await _settle(owner)
    [(who, pcm, kw)] = turns
    assert who == "k_sat" and pcm == b"".join(near[:50])
    assert "mic_satellite_id" not in kw


async def test_session_an_older_device_s_duplicate_is_never_heard(turns) -> None:
    app = _app()
    owner = _session(app, "k_sat", "kitchen")
    old = _session(app, "k_old", "kitchen",
                   Abilities(declared=True, names=frozenset({"capture_control", "speech_pause"})))
    await owner._on_control(TURNS["claim"])
    await owner._on_control(TURNS["owner_utterance_start"])
    await old._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 9})
    assert old.ws.sent[-1] == {"type": "end_capture", "utt": 9}
    await old._on_audio(b"\1\0" * FRAME)
    assert old.audio_buf == bytearray()
    await old._on_control({"type": "utterance_end", "utt": 9})
    assert old.ws.sent[-1] == TURNS["duplicate_dropped_response_end"]
    assert old._response_task is None and turns == []
    # A claim from a device that never declared the ability is ignored.
    await old._on_control(TURNS["claim"])
    assert old.ws.sent[-1] == TURNS["duplicate_dropped_response_end"]


class _Sink:
    def __init__(self) -> None:
        self.controls: list[dict] = []

    def send_audio(self, frame: bytes) -> None:
        pass

    def send_control(self, msg: dict) -> None:
        self.controls.append(msg)


async def test_answers_in_a_shared_room_play_on_every_speaker() -> None:
    app = _app()
    owner, phone = _session(app, "k_sat", "kitchen"), _session(app, "k_phone", "kitchen")
    reg = room_devices.registry(app)
    app.state.room_playout = pl = RoomPlayout(reg, run_tasks=False)
    sinks = {sid: _Sink() for sid in ("k_sat", "k_phone")}
    for sid, sink in sinks.items():
        pl.attach(sid, sink)
    await owner._safe_send_text({"type": "response_start", "text": "Hi.", "audio_sample_rate": 22050})
    assert owner.ws.sent[-1]["room_audio"] is True
    await owner._send_pcm(b"\1\0" * 2205)
    assert owner.ws.bytes == []                         # nothing down the socket
    assert pl.streamers["kitchen"]._speech              # it is on the room's timeline
    assert owner.room_speech_playing()
    await owner._safe_send_text({"type": "response_end", "interrupted": False})
    assert owner.ws.sent[-1]["play_end_us"] == int(owner._room_play_end_us) > 0
    # A barge-in from the other satellite stops it on every speaker.
    await phone._on_control({"type": "barge_in"})
    assert pl.streamers["kitchen"]._speech == []
    assert all(any(c.get("type") == "flush" for c in s.controls) for s in sinks.values())


async def test_a_single_satellite_room_answers_on_its_own_socket() -> None:
    app = _app()
    lone = _session(app, "den_sat", "den")
    app.state.room_playout = pl = RoomPlayout(room_devices.registry(app), run_tasks=False)
    pl.attach("den_sat", _Sink())
    await lone._safe_send_text({"type": "response_start", "text": "Hi.", "audio_sample_rate": 22050})
    assert "room_audio" not in lone.ws.sent[-1]
    await lone._send_pcm(b"\1\0" * 10)
    assert lone.ws.bytes == [b"\1\0" * 10]
    await lone._safe_send_text({"type": "response_end", "interrupted": False})
    assert "play_end_us" not in lone.ws.sent[-1]


# ─── fixtures and the ready ───────────────────────────────────────────────


def test_the_ability_is_declared_only_and_listed_in_ready() -> None:
    assert "wake_claim" in sa.DECLARED_ONLY and "wake_claim" not in sa.ASSUMED_WITHOUT_SUPPORTS
    assert FIXTURES["frame_gates"]["wake_grant"] == ["wake_claim"]
    assert FIXTURES["frame_gates"]["capture_keep"] == ["wake_claim"]
    s = StreamSession(_WS(_app()), "k_sat")  # type: ignore[arg-type]
    s.token_authenticated = True
    s.abilities = sa.from_hello({"supports": ["wake_claim"]})
    assert TURNS["ready_features_adds"] in s._ready_features()
    s.abilities = sa.from_hello({})   # a Pi from before: never told to claim
    assert "wake_claim" not in s._ready_features()


async def test_ready_room_says_when_to_claim() -> None:
    app = _app()
    a = _session(app, "k_sat", "kitchen")
    a._room_row = {"room_id": "kitchen", "name": "Kitchen", "satellites": 1}
    assert (await a._room_info())["claim"] is False
    a.knows_rooms = True
    b = _session(app, "k_phone", "kitchen")
    b._room_row = {"room_id": "kitchen", "name": "Kitchen", "satellites": 2}
    assert await b._room_info() == TURNS["ready_room"]
    await b.push_claim_flags()      # the first device is told to claim now
    assert a.ws.sent[-1] == {"type": "room", "room": {**TURNS["ready_room"]}}


def test_the_fixture_constants_are_the_core_s() -> None:
    c = TURNS["constants"]
    assert c["stream_ms"] == wake_claims.STREAM_MS
    assert c["join_window_ms"] == wake_claims.JOIN_WINDOW_SEC * 1000
    assert c["score_window_ms"] == wake_claims.SCORE_FRAMES * wake_claims.FRAME_MS
    assert c["min_scored_ms"] == wake_claims.MIN_SCORED_FRAMES * wake_claims.FRAME_MS
    assert c["level_percentile"] == wake_claims.LEVEL_PERCENTILE
    assert not math.isnan(wake_claims.mic_score(-50.0, [-30.0] * 20))


# ─── the history row names both devices ───────────────────────────────────


@requires_db
async def test_the_turn_row_names_the_owner_and_the_mic() -> None:
    from sqlalchemy import text

    from domovoi import turn_history
    from domovoi.db.session import session_scope

    from datetime import datetime, timezone

    row = await turn_history._open_row(
        room_id="kitchen", session_id=None, user_text="what time is it", trigger="wake_word",
        audio_ms=900, at=datetime.now(timezone.utc),
        satellite_id="k_sat", mic_satellite_id="k_phone",
    )
    assert row is not None
    async with session_scope() as s:
        got = (await s.execute(text(
            "SELECT satellite_id, mic_satellite_id FROM conversation_log WHERE id = :i"), {"i": row})).one()
        await s.execute(text("DELETE FROM conversation_log WHERE id = :i"), {"i": row})
    assert tuple(got) == ("k_sat", "k_phone")
