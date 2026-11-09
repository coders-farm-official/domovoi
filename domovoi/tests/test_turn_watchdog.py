"""The voice turn watchdog and the history that records every turn
(incident 2026-11-09: speech recognition wedged and every room sat in its
"thinking" ring for good, with nothing in the history).

Drives ``StreamSession._process_utterance`` with a fake socket, a speech
recognition that never comes back, a router that never returns, and the
real database for the history rows (domovoi/turn_watchdog.py,
domovoi/turn_history.py, V043).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import types

import pytest
from sqlalchemy import text

from domovoi import executors, streaming, turn_history, turn_watchdog
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.models import Context, Intent
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from domovoi.turn_watchdog import (
    STAGE_REPLY,
    STAGE_STT,
    STAGE_STT_SLOT,
    TurnStalled,
)

PCM_1S = b"\x00" * 32_000


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], probe=types.SimpleNamespace(online=True),
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    def types(self) -> list[str]:
        return [f.get("type") for f in self.sent_text]


class _HungWhisper:
    """Speech recognition that never comes back (the 2026-11-09 wedge)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def transcribe(self, pcm_bytes: bytes) -> str:
        self.started.set()
        await asyncio.Event().wait()
        return ""


class _Whisper:
    def __init__(self, text_: str) -> None:
        self.text = text_

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return self.text


class _BrokenWhisper:
    async def transcribe(self, pcm_bytes: bytes) -> str:
        raise RuntimeError("decoder blew up")


@pytest.fixture
def fast_limits(monkeypatch):
    monkeypatch.setattr(settings, "voice_stt_timeout_min_sec", 0.3)
    monkeypatch.setattr(settings, "voice_stt_timeout_max_sec", 0.3)
    monkeypatch.setattr(settings, "voice_reply_timeout_sec", 0.6)
    monkeypatch.setattr(settings, "voice_identify_timeout_sec", 0.3)
    turn_watchdog.reset()
    yield
    turn_watchdog.reset()


@pytest.fixture
def no_identify(monkeypatch):
    """Voice identification answers at once with nobody (it isn't what is
    under test here)."""
    from domovoi import voice_identifier

    async def _identify(*a, **kw):
        return None

    monkeypatch.setattr(voice_identifier, "identify", _identify)


async def _drain_history_writes() -> None:
    for _ in range(50):
        if not streaming._TIMING_WRITES:
            return
        await asyncio.sleep(0.02)
    await asyncio.gather(*list(streaming._TIMING_WRITES), return_exceptions=True)


async def _rows(room: str) -> list[dict]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT user_text, assistant_text, turn_status, no_answer_reason, audio_ms, "
            "matched_handler FROM conversation_log WHERE room_id = :r ORDER BY id"
        ), {"r": room})).mappings().all()
    return [dict(r) for r in rows]


async def _intent_rows(room: str) -> int:
    async with session_scope() as s:
        return int((await s.execute(text(
            "SELECT COUNT(*) FROM intents_log WHERE room_id = :r"), {"r": room})).scalar())


# ── speech recognition that never returns ─────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_hung_speech_recognition_ends_the_turn_within_its_limit(
    monkeypatch, fast_limits, db_session, caplog,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _HungWhisper())

    async def _route(*a, **kw):
        raise AssertionError("nothing was heard: the router must not run")

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-office")  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING, logger="domovoi.streaming")
    t0 = time.monotonic()
    await asyncio.wait_for(sess._process_utterance(PCM_1S * 2, trigger="wake_word"), 10)
    assert time.monotonic() - t0 < 5
    await _drain_history_writes()

    # The room is told — the spoken apology, a whole response — and ends.
    starts = [f for f in ws.sent_text if f.get("type") == "response_start"]
    assert starts and starts[0]["matched_handler"] == "turn_timeout"
    assert starts[0]["text"] == turn_watchdog.APOLOGY_TEXT
    assert ws.types()[-1] == "response_end"
    assert "transcript" not in ws.types()
    # One line naming the room and the stage.
    line = [r.getMessage() for r in caplog.records if "timed out" in r.getMessage()]
    assert line and "wd-office" in line[0] and STAGE_STT in line[0]
    # Health: noted as a recent stall.
    assert turn_watchdog.last_stall().stage == STAGE_STT
    assert turn_watchdog.speech_health()["state"] == "slow"
    # History: "couldn't make out what was said", with the recording length.
    rows = await _rows("wd-office")
    assert len(rows) == 1
    assert rows[0]["turn_status"] == "unheard"
    assert rows[0]["no_answer_reason"] == "timed_out"
    assert rows[0]["user_text"] == ""
    assert rows[0]["audio_ms"] == 2000


@requires_db
@pytest.mark.asyncio
async def test_without_speech_the_room_still_gets_error_and_response_end(
    monkeypatch, fast_limits, db_session,
) -> None:
    """The apology can't be said (TTS down too): error + response_end, so
    the satellite leaves its thinking ring all the same."""
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _HungWhisper())

    def _no_tts():
        raise RuntimeError("tts down")

    monkeypatch.setattr(streaming, "get_tts_client", _no_tts)
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-hall")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(PCM_1S, trigger="wake_word"), 10)
    await _drain_history_writes()
    assert "error" in ws.types()
    end = ws.sent_text[-1]
    assert end == {"type": "response_end", "interrupted": True, "expect_followup": False}


@requires_db
@pytest.mark.asyncio
async def test_a_barge_in_turn_gets_no_apology_and_no_history_row(
    monkeypatch, fast_limits, db_session,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _HungWhisper())
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-den")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(PCM_1S, trigger="barge_in"), 10)
    await _drain_history_writes()
    assert "response_start" not in ws.types()
    assert ws.types()[-2:] == ["error", "response_end"]
    assert await _rows("wd-den") == []


@requires_db
@pytest.mark.asyncio
async def test_failed_speech_recognition_is_recorded_as_not_made_out(
    monkeypatch, fast_limits, db_session,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _BrokenWhisper())
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-attic")  # type: ignore[arg-type]
    await sess._process_utterance(PCM_1S, trigger="wake_word")
    await _drain_history_writes()
    assert ws.types()[-2:] == ["error", "response_end"]
    rows = await _rows("wd-attic")
    assert [(r["turn_status"], r["no_answer_reason"], r["audio_ms"]) for r in rows] == [
        ("unheard", "error", 1000),
    ]


# ── the room's decode slot ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_decode_slot_wait_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_stt_timeout_max_sec", 0.2)
    sess = StreamSession(_FakeWS(), "wd-slot")  # type: ignore[arg-type]
    stuck: asyncio.Future = asyncio.get_running_loop().create_future()
    sess._decode_inflight = stuck
    sess._decode_started_at = time.monotonic()
    started = []

    async def _start():
        started.append(1)
        return "never"

    t0 = time.monotonic()
    with pytest.raises(TurnStalled) as e:
        await sess._one_at_a_time(_start)
    assert e.value.stage == STAGE_STT_SLOT
    assert 0.15 <= time.monotonic() - t0 < 2
    assert started == [], "nothing may start beside a stuck decode"
    # Once the holder is past the limit, the next call fails at once.
    t0 = time.monotonic()
    with pytest.raises(TurnStalled):
        await sess._one_at_a_time(_start)
    assert time.monotonic() - t0 < 0.1
    stuck.cancel()


@pytest.mark.asyncio
async def test_a_slot_freed_in_time_is_used(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_stt_timeout_max_sec", 2.0)
    sess = StreamSession(_FakeWS(), "wd-slot2")  # type: ignore[arg-type]
    prev: asyncio.Future = asyncio.get_running_loop().create_future()
    sess._decode_inflight = prev
    sess._decode_started_at = time.monotonic()
    asyncio.get_running_loop().call_later(0.1, prev.set_result, "done")

    async def _start():
        return "mine"

    assert await sess._one_at_a_time(_start) == "mine"


# ── the reply stage ───────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_router_that_never_returns_ends_in_no_answer(
    monkeypatch, fast_limits, no_identify, db_session, caplog,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("what is the weather"))
    seen_pending: list[dict] = []

    async def _route(intent, ctx, session):
        # The history already shows what was said, the reply pending.
        seen_pending.extend(await _rows("wd-kitchen"))
        await asyncio.Event().wait()

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-kitchen")  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING, logger="domovoi.streaming")
    await asyncio.wait_for(sess._process_utterance(PCM_1S, trigger="wake_word"), 10)
    await _drain_history_writes()

    assert [(r["user_text"], r["turn_status"]) for r in seen_pending] == [
        ("what is the weather", "pending"),
    ]
    assert "transcript" in ws.types()
    assert ws.types()[-1] == "response_end"
    assert any(STAGE_REPLY in r.getMessage() and "wd-kitchen" in r.getMessage()
               for r in caplog.records)
    rows = await _rows("wd-kitchen")
    assert [(r["user_text"], r["turn_status"], r["no_answer_reason"], r["audio_ms"])
            for r in rows] == [("what is the weather", "no_answer", "timed_out", 1000)]


@requires_db
@pytest.mark.asyncio
async def test_the_first_reply_frame_disarms_the_guard(
    monkeypatch, fast_limits, no_identify, db_session,
) -> None:
    """A cold model load: the interim line goes out, then the load takes
    longer than the reply guard. The guard must not cut it off — here the
    router then fails on its own, an ordinary error, not a timeout."""
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("tell me a story"))

    async def _route(intent, ctx, session):
        await ctx.speak_interim("Just a moment.")
        await asyncio.sleep(1.0)   # past voice_reply_timeout_sec (0.6)
        raise RuntimeError("model load failed")

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = StreamSession(ws, "wd-study")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(PCM_1S, trigger="wake_word"), 10)
    await _drain_history_writes()
    errors = [f for f in ws.sent_text if f.get("type") == "error"]
    assert errors and errors[0]["message"] == "model load failed"
    assert turn_watchdog.last_stall() is None
    rows = await _rows("wd-study")
    assert [(r["turn_status"], r["no_answer_reason"]) for r in rows] == [("no_answer", "error")]


@requires_db
@pytest.mark.asyncio
async def test_a_barge_in_before_the_answer_says_interrupted(
    monkeypatch, fast_limits, no_identify, db_session,
) -> None:
    monkeypatch.setattr(settings, "voice_reply_timeout_sec", 30.0)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("play some jazz"))
    in_route = asyncio.Event()

    async def _route(intent, ctx, session):
        in_route.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(streaming, "route", _route)
    sess = StreamSession(_FakeWS(), "wd-porch")  # type: ignore[arg-type]
    task = asyncio.create_task(sess._process_utterance(PCM_1S, trigger="wake_word"))
    await asyncio.wait_for(in_route.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await _drain_history_writes()
    rows = await _rows("wd-porch")
    assert [(r["turn_status"], r["no_answer_reason"]) for r in rows] == [
        ("no_answer", "interrupted"),
    ]


# ── one row per routed turn, filled in ────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_routed_turn_fills_in_its_pending_row(db_session) -> None:
    """Insert-then-update keeps the invariant: one intents_log row and one
    conversation_log row per routed turn — the pending one, now answered."""
    from datetime import datetime, timezone

    from domovoi.router import route

    entry = await turn_history.open_entry(
        room_id="wd-lounge", session_id=None, user_text="what time is it",
        trigger="wake_word", audio_ms=1500, at=datetime.now(timezone.utc),
    )
    assert entry is not None
    assert [r["turn_status"] for r in await _rows("wd-lounge")] == ["pending"]
    ctx = Context(room_id="wd-lounge", history_entry=entry, trigger="wake_word")
    async with session_scope() as s:
        response = await route(
            Intent(transcript="what time is it", room_id="wd-lounge"), ctx, s,
        )
    rows = await _rows("wd-lounge")
    assert len(rows) == 1
    assert rows[0]["turn_status"] == "answered"
    assert rows[0]["assistant_text"] == response.text
    assert rows[0]["audio_ms"] == 1500
    assert await _intent_rows("wd-lounge") == 1
    # The entry is taken once: a failure mark after the answer is a no-op.
    await turn_history.close_entry(entry, turn_history.ERROR, room_id="wd-lounge")
    assert (await _rows("wd-lounge"))[0]["turn_status"] == "answered"


@requires_db
@pytest.mark.asyncio
async def test_an_answer_wins_over_an_earlier_failure_mark(db_session) -> None:
    """A barge-in's "interrupted" can land before a streamed answer's own
    record: the answer still fills in the row, and no second row appears."""
    from datetime import datetime, timezone

    from domovoi.db.repositories import ConversationLogRepository

    entry = await turn_history.open_entry(
        room_id="wd-yard", session_id=None, user_text="tell me a joke",
        trigger="wake_word", audio_ms=900, at=datetime.now(timezone.utc),
    )
    await turn_history.close_entry(entry, turn_history.INTERRUPTED, room_id="wd-yard")
    async with session_scope() as s:
        await ConversationLogRepository(s).record_turn(
            session_id=None, room_id="wd-yard", person_id=None,
            user_text="tell me a joke", assistant_text="Why did the cat…",
            matched_handler="qa", matched_path="llm", presence_tier=None,
            online=True, latency_ms=5, history_entry=entry,
        )
    rows = await _rows("wd-yard")
    assert [(r["turn_status"], r["assistant_text"], r["no_answer_reason"]) for r in rows] == [
        ("answered", "Why did the cat…", None),
    ]


@requires_db
@pytest.mark.asyncio
async def test_a_turn_without_a_pending_row_inserts_answered(db_session) -> None:
    from domovoi.db.repositories import ConversationLogRepository

    async with session_scope() as s:
        await ConversationLogRepository(s).record_turn(
            session_id=None, room_id="wd-typed", person_id=None,
            user_text="hello", assistant_text="Hi.", matched_handler="greeting",
            matched_path="fast", presence_tier=None, online=True, latency_ms=1,
        )
    assert [r["turn_status"] for r in await _rows("wd-typed")] == ["answered"]


@requires_db
@pytest.mark.asyncio
async def test_start_up_closes_rows_the_last_run_left_pending(db_session) -> None:
    from datetime import datetime, timezone

    await turn_history.open_entry(
        room_id="wd-garage", session_id=None, user_text="open the door",
        trigger="wake_word", audio_ms=800, at=datetime.now(timezone.utc),
    )
    assert await turn_history.sweep_pending() == 1
    rows = await _rows("wd-garage")
    assert [(r["turn_status"], r["no_answer_reason"]) for r in rows] == [
        ("no_answer", "restarted"),
    ]


@requires_db
@pytest.mark.asyncio
async def test_the_dashboard_reads_the_states(db_session) -> None:
    """GET /api/satellites/<room>/conversations: status, reason and length;
    a pending row older than the stale limit reads "no answer"."""
    from datetime import datetime, timedelta, timezone

    from web.backend.api import satellites as sat_api

    now = datetime.now(timezone.utc)
    await turn_history.open_entry(
        room_id="wd-web", session_id=None, user_text="still thinking",
        trigger="wake_word", audio_ms=700, at=now,
    )
    await turn_history.open_entry(
        room_id="wd-web", session_id=None, user_text="long gone",
        trigger="wake_word", audio_ms=700, at=now - timedelta(hours=1),
    )
    await turn_history.record_unheard(
        room_id="wd-web", session_id=None, trigger="wake_word", audio_ms=3400,
        reason=turn_history.TIMED_OUT, at=now - timedelta(minutes=1),
    )
    turns = await sat_api.list_conversations("wd-web", limit=50)
    got = [(t.user_text, t.status, t.no_answer_reason, t.audio_ms) for t in turns]
    assert got == [
        ("still thinking", "pending", None, 700),
        ("", "unheard", "timed_out", 3400),
        ("long gone", "no_answer", None, 700),
    ]


# ── stuck speech workers: Health and the automatic restart ───────────


def test_stt_cap_is_three_times_the_recording_within_its_bounds(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_stt_timeout_min_sec", 20.0)
    monkeypatch.setattr(settings, "voice_stt_timeout_audio_factor", 3.0)
    monkeypatch.setattr(settings, "voice_stt_timeout_max_sec", 40.0)
    assert turn_watchdog.stt_cap(2.0) == 20.0
    assert turn_watchdog.stt_cap(10.0) == 30.0
    assert turn_watchdog.stt_cap(30.0) == 40.0


@pytest.mark.asyncio
async def test_a_stuck_speech_worker_is_stalled_on_health_and_restarts(monkeypatch) -> None:
    turn_watchdog.reset()
    release = threading.Event()
    job = asyncio.create_task(executors.run("speech", release.wait, 30))
    try:
        for _ in range(100):
            if executors.running_ages("speech"):
                break
            await asyncio.sleep(0.01)
        now = time.monotonic()
        monkeypatch.setattr(settings, "voice_speech_stuck_after_sec", 120.0)
        monkeypatch.setattr(settings, "voice_speech_restart_after_sec", 600.0)
        assert turn_watchdog.speech_health(now=now)["state"] == "ok"
        later = now + 200
        doc = turn_watchdog.speech_health(now=later)
        assert doc["state"] == "stalled" and doc["stuck_workers"] == 1
        assert doc["workers"] == executors.POOL_SIZES["speech"]
        # One worker stuck, no request has timed out on it: no restart.
        much_later = now + 700
        assert turn_watchdog.restart_reason(now=much_later) is None
        # A request then timed out in speech recognition, and nothing
        # finished since the stuck call began: restart.
        if executors.last_finished("speech") is not None:
            # Another test's call finished after ours started: not stuck
            # house-wide by this rule; make the evidence unambiguous.
            monkeypatch.setattr(executors, "last_finished", lambda name: None)
        turn_watchdog.note_stall(STAGE_STT, "wd-x", now=now + 100)
        assert "speech recognition is stuck" in (turn_watchdog.restart_reason(now=much_later) or "")
        monkeypatch.setattr(settings, "voice_speech_restart_after_sec", 0.0)
        assert turn_watchdog.restart_reason(now=much_later) is None
    finally:
        release.set()
        await job
        turn_watchdog.reset()


@pytest.mark.asyncio
async def test_every_worker_stuck_restarts_without_further_evidence(monkeypatch) -> None:
    turn_watchdog.reset()
    size = executors.POOL_SIZES["speech"]
    release = threading.Event()
    jobs = [asyncio.create_task(executors.run("speech", release.wait, 30)) for _ in range(size)]
    try:
        for _ in range(200):
            if len(executors.running_ages("speech")) >= size:
                break
            await asyncio.sleep(0.01)
        monkeypatch.setattr(settings, "voice_speech_restart_after_sec", 600.0)
        reason = turn_watchdog.restart_reason(now=time.monotonic() + 700)
        assert reason and f"all {size} speech workers" in reason
    finally:
        release.set()
        await asyncio.gather(*jobs)
