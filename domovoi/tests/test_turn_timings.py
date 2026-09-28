"""Where a voice turn's time goes — early-endpointing phase 1.

``intents_log.latency_ms`` only ever measured ``route()``: speech-to-text,
voice identification and the first reply audio were invisible, and the
early-endpointing design (design notes 2026-09-28) has to be sized on the
real Domovoi server's numbers. What is pinned here:

* a voice turn records ``capture_audio_ms``, ``stt_ms``, ``identify_ms``
  (and the Whisper that ran) in the SAME insert as its ``intents_log`` row,
  and ``route_ms``, ``tts_first_ms``, ``total_ms`` are merged into that row
  once the reply is playing — end to end through ``/v1/stream`` against a
  real database, with a Whisper, a voice identifier and a TTS whose delays
  the test controls;
* none of it carries text or identity, and a server whose database hasn't
  had V015 keeps routing turns;
* the latency summary's arithmetic, its filters, what it leaves out, and
  its dashboard proxy;
* ``whisper_cpu_threads``: auto is one thread per physical core, it reaches
  faster-whisper on cpu (the configured rung and the cpu fallback alike)
  and never on cuda;
* V015 applies, and applies again cleanly.

DB-free wherever the thing under test allows it; the rest is ``requires_db``.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import sys
import time
import types
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import streaming, turn_timings
from domovoi.clients import whisper as whisper_mod
from domovoi.config import Settings, settings
from domovoi.db.repositories import IntentLogRepository
from domovoi.db.session import SessionLocal, engine, session_scope
from domovoi.models import Context, Intent, Response
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db
from domovoi.turn_timings import (
    POST_ROUTE_STAGES,
    STAGES,
    TurnTimings,
    percentile,
    summarize,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "domovoi" / "db" / "migrations"

# Words that must never surface in a timing record or the summary.
SECRET = "remind me to call the pharmacy about my prescription"

# asyncio schedules sleeps on time.monotonic(), which ticks every ~15.6 ms
# on Windows before Python 3.13; a stage measured on perf_counter may come
# in just under the sleep that drove it.
SLACK_MS = 20


def _wav(pcm: bytes, sample_rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _DelayedWhisper:
    def __init__(self, text: str, delay: float) -> None:
        self.text = text
        self.delay = delay

    async def transcribe(self, pcm: bytes) -> str:
        await asyncio.sleep(self.delay)
        return self.text

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        return self.text


class _DelayedTTS:
    def __init__(self, delay: float, pcm: bytes = b"\x01\x02" * 400) -> None:
        self.delay = delay
        self._wav = _wav(pcm)

    async def synthesize(self, text: str, *, engine=None, voice=None) -> bytes:
        await asyncio.sleep(self.delay)
        return self._wav


def _delayed_identify(delay: float):
    async def identify(pcm: bytes):
        await asyncio.sleep(delay)
        return None  # nobody recognised: person_id None, as a stranger's turn
    return identify


# ─── the stopwatch ───────────────────────────────────────────────────────


def test_the_capture_length_comes_from_the_audio_itself() -> None:
    # 2 s of 16 kHz mono int16 is 64,000 bytes, whatever the clock says.
    assert TurnTimings(audio_bytes=64_000).stages == {"capture_audio_ms": 2000}
    assert TurnTimings(audio_bytes=0).stages == {"capture_audio_ms": 0}


def test_stages_first_audio_and_the_post_route_patch(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(turn_timings.time, "perf_counter", lambda: clock[0])
    t = TurnTimings(started=100.0, audio_bytes=32_000)
    clock[0] = 100.250
    t.stage("stt_ms", 100.0)
    clock[0] = 100.300
    t.stage("identify_ms", 100.250)
    t.whisper = {"model": "small.en", "device": "cpu", "compute_type": "int8", "cpu_threads": 8}
    doc = t.row_document()
    assert doc == {
        "capture_audio_ms": 1000, "stt_ms": 250, "identify_ms": 50,
        "whisper": {"model": "small.en", "device": "cpu", "compute_type": "int8", "cpu_threads": 8},
    }
    assert t.post_route_patch() == {}

    clock[0] = 100.320
    t.stage("route_ms", 100.300)
    clock[0] = 100.400
    t.first_audio(100.320)
    clock[0] = 105.0
    t.first_audio(104.0)  # every later chunk: ignored
    assert t.post_route_patch() == {"route_ms": 20, "tts_first_ms": 80, "total_ms": 400}
    line = t.describe()
    assert line.split(" ")[:6] == [
        "capture_audio_ms=1000", "stt_ms=250", "identify_ms=50", "route_ms=20",
        "tts_first_ms=80", "total_ms=400",
    ]
    assert line.endswith("whisper=small.en/cpu/int8/8t")


# ─── the summary's arithmetic (pure) ─────────────────────────────────────


def test_percentile_is_linear_interpolation_like_numpy() -> None:
    np = pytest.importorskip("numpy")
    rng = random.Random(15)
    for n in (1, 2, 3, 7, 10, 101):
        vals = sorted(rng.uniform(0, 3000) for _ in range(n))
        for q in (0.5, 0.95):
            assert percentile(vals, q) == pytest.approx(float(np.percentile(vals, q * 100)))
    with pytest.raises(ValueError):
        percentile([], 0.5)


def _w(model="small.en", threads=8):
    return {"model": model, "device": "cpu", "compute_type": "int8", "cpu_threads": threads}


def test_summarize_known_values() -> None:
    rows = [
        ({"stt_ms": v, "total_ms": 2 * v, "whisper": _w()}, "fast")
        for v in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000)
    ]
    s = summarize(rows)
    assert s["turns"] == 10
    assert s["stages"]["stt_ms"] == {"count": 10, "p50": 550, "p95": 955, "max": 1000}
    assert s["stages"]["total_ms"] == {"count": 10, "p50": 1100, "p95": 1910, "max": 2000}
    assert s["stages"]["identify_ms"] == {"count": 0, "p50": None, "p95": None, "max": None}
    assert list(s["stages"]) == list(STAGES)
    assert s["paths"] == {"fast": 10}
    assert s["whisper_seen"] == [{**_w(), "turns": 10}]


def test_summarize_skips_what_is_not_a_timing() -> None:
    rows = [
        (json.dumps({"stt_ms": 300}), "qa"),         # a JSONB column read back as text
        ({"stt_ms": 500, "identify_ms": -4}, "fast"),  # a negative stage is dropped alone
        ({"stt_ms": True, "identify_ms": 40}, "fast"),  # a bool is not a number here
        ({"stt_ms": "700", "route_ms": float("nan")}, None),
        ("not json {", "fast"),                     # skipped as a row
        ([1, 2, 3], "fast"),                        # skipped as a row
        (None, "fast"),                             # skipped as a row
    ]
    s = summarize(rows)
    assert s["turns"] == 4
    assert s["stages"]["stt_ms"] == {"count": 2, "p50": 400, "p95": 490, "max": 500}
    assert s["stages"]["identify_ms"]["count"] == 1
    assert s["stages"]["route_ms"]["count"] == 0
    assert s["paths"] == {"fast": 2, "qa": 1}
    assert s["whisper_seen"] == []


def test_summarize_groups_the_whisper_settings_most_turns_first() -> None:
    rows = (
        [({"stt_ms": 900, "whisper": _w(threads=None)}, "fast")] * 2
        + [({"stt_ms": 500, "whisper": _w(threads=8)}, "fast")] * 3
        + [({"stt_ms": 500, "whisper": {"model": "x", "cpu_threads": True}}, "fast")]
    )
    seen = summarize(rows)["whisper_seen"]
    assert seen[0] == {**_w(threads=8), "turns": 3}
    assert seen[1] == {**_w(threads=None), "turns": 2}
    # A value of the wrong type is reported as unknown, not passed through.
    assert seen[2] == {"model": "x", "device": None, "compute_type": None,
                       "cpu_threads": None, "turns": 1}


# ─── whisper_cpu_threads ─────────────────────────────────────────────────


def test_the_setting_defaults_to_auto_and_is_a_restart_tier_advanced_int() -> None:
    from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

    assert Settings.model_fields["whisper_cpu_threads"].default == 0
    f = FIELD_BY_NAME["whisper_cpu_threads"]
    assert (f.type, f.tier, f.section, f.group) == ("int", "restart", "advanced", "Speech-to-text")
    assert coerce_and_validate(f, "8") == 8
    assert coerce_and_validate(f, 0) == 0
    with pytest.raises(ValueError):
        coerce_and_validate(f, -1)
    example = (REPO_ROOT / "domovoi" / ".env.example").read_text(encoding="utf-8")
    assert "# WHISPER_CPU_THREADS=0" in example


def test_auto_threads_is_one_per_physical_core(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_physical_cores", lambda: 8)
    assert whisper_mod.resolve_cpu_threads(0) == 8
    assert whisper_mod.resolve_cpu_threads(-3) == 8
    assert whisper_mod.resolve_cpu_threads("junk") == 8
    # A number pins it, even above the core count.
    assert whisper_mod.resolve_cpu_threads(3) == 3
    assert whisper_mod.resolve_cpu_threads(32) == 32
    # No argument: the setting.
    monkeypatch.setattr(settings, "whisper_cpu_threads", 6)
    assert whisper_mod.resolve_cpu_threads() == 6
    monkeypatch.setattr(settings, "whisper_cpu_threads", 0)
    assert whisper_mod.resolve_cpu_threads() == 8


@pytest.mark.parametrize(
    ("logical", "expected"), [(16, 8), (8, 4), (3, 1), (1, 1), (None, 1)],
)
def test_auto_falls_back_to_half_the_logical_count(monkeypatch, logical, expected) -> None:
    monkeypatch.setattr(whisper_mod, "_physical_cores", lambda: None)
    monkeypatch.setattr(whisper_mod.os, "cpu_count", lambda: logical)
    assert whisper_mod.resolve_cpu_threads(0) == expected


def test_physical_cores_reads_psutil() -> None:
    psutil = pytest.importorskip("psutil")
    assert whisper_mod._physical_cores() == (psutil.cpu_count(logical=False) or None)


class _FakeWhisperModel:
    calls: list[tuple[str, dict]] = []
    fail_on_cuda = False

    def __init__(self, model: str, **kwargs) -> None:
        type(self).calls.append((model, kwargs))
        if type(self).fail_on_cuda and kwargs.get("device") == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    _FakeWhisperModel.calls = []
    _FakeWhisperModel.fail_on_cuda = False
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    )
    monkeypatch.setattr(whisper_mod, "_physical_cores", lambda: 8)
    monkeypatch.setattr(settings, "whisper_cpu_threads", 0)
    return _FakeWhisperModel


def test_threads_reach_faster_whisper_on_cpu_only(fake_faster_whisper) -> None:
    cpu = whisper_mod.FasterWhisperClient("small.en", "cpu", "int8")
    assert fake_faster_whisper.calls[-1] == (
        "small.en", {"device": "cpu", "compute_type": "int8", "cpu_threads": 8},
    )
    assert cpu.cpu_threads == 8
    # cuda loads exactly as it always did: no cpu_threads argument at all.
    gpu = whisper_mod.FasterWhisperClient("large-v3", "cuda", "float16")
    assert fake_faster_whisper.calls[-1] == (
        "large-v3", {"device": "cuda", "compute_type": "float16"},
    )
    assert gpu.cpu_threads is None


def test_a_pinned_thread_count_is_passed_as_written(fake_faster_whisper, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_cpu_threads", 6)
    whisper_mod.FasterWhisperClient("small.en", "CPU", "int8")
    assert fake_faster_whisper.calls[-1][1]["cpu_threads"] == 6


def test_the_cpu_fallback_rung_gets_the_threads_too(fake_faster_whisper, monkeypatch) -> None:
    fake_faster_whisper.fail_on_cuda = True
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "whisper_model", "large-v3")
    monkeypatch.setattr(settings, "whisper_device", "cuda")
    monkeypatch.setattr(settings, "whisper_compute_type", "auto")
    monkeypatch.setattr(settings, "whisper_cpu_fallback_model", "small.en")
    monkeypatch.setattr("domovoi.bootstrap.register_nvidia_dlls", lambda: ())
    monkeypatch.setattr(whisper_mod, "_cuda_device_count", lambda: None)

    assert whisper_mod.load_whisper_client() is not None
    assert [c[1].get("cpu_threads") for c in fake_faster_whisper.calls] == [None, 8]
    rt = whisper_mod.whisper_runtime()
    assert rt == {
        "state": "fallback", "model": "small.en", "device": "cpu",
        "compute_type": "int8", "cpu_threads": 8,
    }


def test_the_runtime_of_the_stub(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    whisper_mod.load_whisper_client()
    assert whisper_mod.whisper_runtime() == {
        "state": "stub", "model": None, "device": None,
        "compute_type": None, "cpu_threads": None,
    }


# ─── a turn through _process_utterance, DB-free ──────────────────────────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], resumable_music={}, current_playlist={},
                active_sessions={}, probe=types.SimpleNamespace(online=True),
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def db_free_turn(monkeypatch):
    """A turn with every stage faked: the route stands in for
    router._persist_turn (records what the INSERT would carry and hands
    back a row id), and the post-route merge is recorded, not written."""
    seen: dict = {"inserted": None, "merged": []}

    async def _route(intent, ctx, session):
        seen["inserted"] = ctx.timings.row_document()
        ctx.timings.intents_log_id = 4242
        await asyncio.sleep(0.03)
        return Response(text="Timer set for 5 minutes.", matched_handler="timer",
                        matched_path="fast", online=True)

    async def _merge(s, row_id, patch):
        seen["merged"].append((row_id, patch))

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "merge_post_route", _merge)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _DelayedWhisper(SECRET, 0.12))
    monkeypatch.setattr("domovoi.voice_identifier.identify", _delayed_identify(0.05))
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _DelayedTTS(0.08))
    return seen


@pytest.mark.asyncio
async def test_a_turn_records_every_stage_in_two_writes(db_free_turn, caplog) -> None:
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    t_end = time.perf_counter()
    await sess._process_utterance(b"\x00" * 48_000, trigger="wake_word", received_at=t_end)

    # The turn itself is untouched: transcript, start, audio, end.
    types_sent = [f["type"] for f in ws.sent_text]
    assert types_sent == ["transcript", "response_start", "response_end"]
    assert ws.sent_bytes

    # Write 1 — with the row: the stages known before routing.
    ins = db_free_turn["inserted"]
    assert set(ins) == {"capture_audio_ms", "stt_ms", "stt_wait_ms", "identify_ms", "whisper"}
    assert ins["capture_audio_ms"] == 1500
    assert ins["stt_ms"] >= 120 - SLACK_MS
    # Nothing speculative ran, so the wait was the call itself.
    assert ins["stt_ms"] <= ins["stt_wait_ms"] <= ins["stt_ms"] + SLACK_MS
    assert ins["identify_ms"] >= 50 - SLACK_MS
    assert set(ins["whisper"]) == {"model", "device", "compute_type", "cpu_threads"}

    # Write 2 — merged into that row after the reply started.
    assert len(db_free_turn["merged"]) == 1
    row_id, patch = db_free_turn["merged"][0]
    assert row_id == 4242
    assert set(patch) == set(POST_ROUTE_STAGES)
    assert patch["route_ms"] >= 30 - SLACK_MS
    assert patch["tts_first_ms"] >= 80 - SLACK_MS
    # End of speech to first audio covers every stage in between.
    assert patch["total_ms"] >= ins["stt_ms"] + ins["identify_ms"] + patch["route_ms"] + patch["tts_first_ms"] - 3
    assert patch["total_ms"] <= int((time.perf_counter() - t_end) * 1000) + 1

    # One log line, numbers only.
    lines = [r.getMessage() for r in caplog.records if "turn timings" in r.getMessage()]
    assert len(lines) == 1
    assert "room=kitchen trigger=wake_word path=fast" in lines[0]
    assert "stt_ms=" in lines[0] and "total_ms=" in lines[0]
    assert "pharmacy" not in lines[0]
    assert "pharmacy" not in json.dumps([ins, patch])


@pytest.mark.asyncio
async def test_a_failed_merge_never_touches_the_turn(db_free_turn, monkeypatch, caplog) -> None:
    async def _boom(s, row_id, patch):
        raise RuntimeError("db went away")

    monkeypatch.setattr(streaming, "merge_post_route", _boom)
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3200, trigger="wake_word")
    assert ws.sent_text[-1] == {"type": "response_end", "interrupted": False, "expect_followup": False}
    assert any("could not record the post-route stages" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_turn_whose_tts_fails_records_how_far_it_got(db_free_turn, monkeypatch) -> None:
    class _DeadTTS:
        async def synthesize(self, text, *, engine=None, voice=None):
            raise RuntimeError("every engine failed")

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _DeadTTS())
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 3200, trigger="wake_word")
    assert [f["type"] for f in ws.sent_text][-2:] == ["error", "response_end"]
    (row_id, patch), = db_free_turn["merged"]
    assert set(patch) == {"route_ms"}


@pytest.mark.asyncio
async def test_no_row_no_merge(db_free_turn, monkeypatch) -> None:
    """A route that wrote no timed row (a database without V015, a faked
    router) leaves nothing to merge into."""
    async def _route(intent, ctx, session):
        return Response(text="ok.", matched_path="fast", online=True)

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    await StreamSession(ws, "kitchen")._process_utterance(b"\x00" * 3200)  # type: ignore[arg-type]
    assert db_free_turn["merged"] == []


@pytest.mark.asyncio
async def test_a_barged_in_turn_still_records_its_post_route_stages(
    db_free_turn, monkeypatch, caplog,
) -> None:
    """A satellite that hears someone talk over the reply sends ``barge_in``
    and, right behind it, ``utterance_start``: two cancels of the same turn
    task. The second lands while the turn is writing its post-route stages,
    and must not take them with it — the turns people interrupt are the
    long, slow ones the numbers most need."""
    async def _route(intent, ctx, session):
        db_free_turn["inserted"] = ctx.timings.row_document()
        ctx.timings.intents_log_id = 4242
        return Response(text="Here is a long answer. It goes on for a while.",
                        matched_handler="qa", matched_path="qa", online=True)

    async def _slow_merge(s, row_id, patch):
        await asyncio.sleep(0.05)
        db_free_turn["merged"].append((row_id, patch))

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "merge_post_route", _slow_merge)
    caplog.set_level(logging.INFO, logger="domovoi.streaming")
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    task = asyncio.create_task(sess._process_utterance(b"\x00" * 3200, trigger="wake_word"))
    # The first sentence is playing; the second is still being synthesized.
    while not ws.sent_bytes:
        await asyncio.sleep(0.005)
    task.cancel()                     # barge_in
    await asyncio.sleep(0.01)         # the turn winds down and starts its write
    task.cancel()                     # utterance_start, right behind it
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ws.sent_text[-1]["type"] == "response_end"
    assert ws.sent_text[-1]["interrupted"] is True
    await asyncio.sleep(0.1)          # the write finishes on its own

    (row_id, patch), = db_free_turn["merged"]
    assert row_id == 4242 and set(patch) == set(POST_ROUTE_STAGES)
    lines = [r.getMessage() for r in caplog.records if "turn timings" in r.getMessage()]
    assert len(lines) == 1 and "path=qa" in lines[0] and "total_ms=" in lines[0]


# ─── the core endpoint without a database ────────────────────────────────


async def _core_get(path: str):
    from domovoi.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


@pytest.fixture
def no_v015(monkeypatch):
    """The column probe has just answered no (and won't ask again for a
    minute)."""
    monkeypatch.setattr(turn_timings, "_HAS_TIMINGS_COLUMN", False)
    monkeypatch.setattr(turn_timings, "_column_checked_at", time.monotonic())


@pytest.mark.asyncio
async def test_the_summary_says_when_v015_is_missing(no_v015) -> None:
    r = await _core_get("/v1/stats/latency")
    assert r.status_code == 503
    assert "V015" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_missing_column_is_not_asked_about_on_every_turn(no_v015) -> None:
    # Inside the minute the answer comes from the cache: a None session
    # would raise if the probe touched it.
    assert await turn_timings.has_timings_column(None) is False  # type: ignore[arg-type]
    assert await turn_timings.timings_for_row(None, TurnTimings()) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["since=yesterday", "since=2026-13-40", f"room={'x' * 121}"])
async def test_bad_query_is_422(query) -> None:
    r = await _core_get(f"/v1/stats/latency?{query}")
    assert r.status_code == 422


# ─── the dashboard proxy ─────────────────────────────────────────────────


async def _web_get(monkeypatch, path: str, answer=(200, {"turns": 0})):
    from web.backend.api import stats as stats_api
    from web.backend.main import app as web_app

    seen: list[tuple[str, object]] = []

    async def _get_admin(p, timeout=10.0, headers=None):
        seen.append((p, headers))
        return answer

    monkeypatch.setattr(stats_api, "get_admin", _get_admin)
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        r = await c.get(path, headers={"Authorization": "Bearer caller-token"})
    return r, seen


@pytest.mark.asyncio
async def test_the_proxy_forwards_the_query_and_the_callers_headers(monkeypatch) -> None:
    r, seen = await _web_get(
        monkeypatch, "/api/stats/latency?since=2026-09-28T18:00:00Z&room=living room",
    )
    assert r.status_code == 200 and r.json() == {"turns": 0}
    (path, headers), = seen
    assert path == "/v1/stats/latency?since=2026-09-28T18%3A00%3A00%2B00%3A00&room=living+room"
    # Like every web→core hop (test_web_proxy_credentials), though the core
    # route needs none.
    assert headers["Authorization"] == "Bearer caller-token"


@pytest.mark.asyncio
async def test_the_proxy_needs_no_credential(monkeypatch) -> None:
    from web.backend.api import stats as stats_api
    from web.backend.main import app as web_app

    async def _get_admin(p, timeout=10.0, headers=None):
        return 200, {"turns": 0}

    monkeypatch.setattr(stats_api, "get_admin", _get_admin)
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        r = await c.get("/api/stats/latency")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_the_proxy_without_a_query(monkeypatch) -> None:
    _, seen = await _web_get(monkeypatch, "/api/stats/latency")
    assert seen[0][0] == "/v1/stats/latency"


@pytest.mark.asyncio
async def test_the_proxy_passes_the_core_answer_through(monkeypatch) -> None:
    r, _ = await _web_get(monkeypatch, "/api/stats/latency", answer=(503, {"detail": "V015"}))
    assert (r.status_code, r.json()) == (503, {"detail": "V015"})
    r, _ = await _web_get(monkeypatch, "/api/stats/latency", answer=(0, None))
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_the_proxy_refuses_a_malformed_since_itself(monkeypatch) -> None:
    r, seen = await _web_get(monkeypatch, "/api/stats/latency?since=last-tuesday")
    assert r.status_code == 422
    assert seen == []


def test_the_models_page_reads_the_summary() -> None:
    src = (REPO_ROOT / "web" / "static" / "models.jsx").read_text(encoding="utf-8")
    assert "useApiObject('/api/stats/latency'" in src
    assert "<SpeechTimingsLine" in src


# ─── V015 ────────────────────────────────────────────────────────────────


def test_v015_is_the_next_core_migration_and_tolerates_a_hand_patch() -> None:
    versions = sorted(int(p.name[1:4]) for p in MIGRATIONS.glob("V*__*.sql"))
    assert versions == list(range(1, len(versions) + 1)), "core migrations must be contiguous"
    v015 = MIGRATIONS / "V015__turn_stage_timings.sql"
    assert v015.is_file()
    sql = "\n".join(
        line for line in v015.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()
    assert sql == "ALTER TABLE intents_log ADD COLUMN IF NOT EXISTS timings JSONB;"
    assert b"\r\n" not in v015.read_bytes()


async def _truncate() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )


async def _ensure_v015() -> None:
    """Apply V015 to the test database if nobody has yet. It is one
    ADD COLUMN IF NOT EXISTS, so this is a no-op on a migrated lane and a
    later Flyway run still records V015 cleanly on one patched here."""
    sql = (MIGRATIONS / "V015__turn_stage_timings.sql").read_text(encoding="utf-8")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(sql)


@pytest.fixture
async def clean_db(monkeypatch):
    await _ensure_v015()
    # Forget any "missing" answer a test before this one cached.
    monkeypatch.setattr(turn_timings, "_HAS_TIMINGS_COLUMN", None)
    await _truncate()
    yield
    await _truncate()


@requires_db
@pytest.mark.asyncio
async def test_v015_applies_and_applies_again_cleanly(clean_db) -> None:
    sql = (MIGRATIONS / "V015__turn_stage_timings.sql").read_text(encoding="utf-8")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(sql)
        await conn.exec_driver_sql(sql)
        row = (
            await conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'intents_log' AND column_name = 'timings'"
                )
            )
        ).one()
    assert tuple(row) == ("jsonb", "YES")


# ─── storage against the database ────────────────────────────────────────


async def _timings_of(row_id: int):
    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("SELECT timings, latency_ms FROM intents_log WHERE id = :id"), {"id": row_id}
            )
        ).one()
    doc = row[0]
    return (json.loads(doc) if isinstance(doc, str) else doc), row[1]


@requires_db
@pytest.mark.asyncio
async def test_the_row_takes_the_document_and_the_merge(clean_db) -> None:
    async with session_scope() as s:
        plain = await IntentLogRepository(s).log(
            room_id="kitchen", transcript=SECRET, matched_handler=None,
            matched_path="qa", online=True, latency_ms=12,
        )
        timed = await IntentLogRepository(s).log(
            room_id="kitchen", transcript=SECRET, matched_handler=None,
            matched_path="qa", online=True, latency_ms=12,
            timings={"stt_ms": 640, "whisper": {"model": "small.en"}},
        )
    assert timed == plain + 1
    assert (await _timings_of(plain))[0] is None
    async with session_scope() as s:
        await turn_timings.merge_post_route(s, timed, {"route_ms": 20, "total_ms": 900})
        # Merging into a row with no timings starts the document.
        await turn_timings.merge_post_route(s, plain, {"route_ms": 5})
    assert (await _timings_of(timed))[0] == {
        "stt_ms": 640, "whisper": {"model": "small.en"}, "route_ms": 20, "total_ms": 900,
    }
    assert (await _timings_of(plain))[0] == {"route_ms": 5}


@requires_db
@pytest.mark.asyncio
async def test_the_router_writes_the_timings_with_the_row(clean_db) -> None:
    from domovoi.router import route

    t = TurnTimings(audio_bytes=32_000)
    t.stages["stt_ms"] = 610
    t.whisper = {"model": "small.en", "device": "cpu", "compute_type": "int8", "cpu_threads": 8}
    async with session_scope() as s:
        resp = await route(
            Intent(transcript="what time is it", room_id="kitchen"),
            Context(room_id="kitchen", online=True, timings=t),
            s,
        )
    assert resp.matched_path == "fast"
    assert t.intents_log_id is not None
    doc, latency_ms = await _timings_of(t.intents_log_id)
    assert doc == {"capture_audio_ms": 1000, "stt_ms": 610, "whisper": t.whisper}
    assert latency_ms is not None


@requires_db
@pytest.mark.asyncio
async def test_a_database_without_v015_still_routes(clean_db, no_v015) -> None:
    from domovoi.router import route

    t = TurnTimings(audio_bytes=32_000)
    async with session_scope() as s:
        resp = await route(
            Intent(transcript="what time is it", room_id="kitchen"),
            Context(room_id="kitchen", online=True, timings=t),
            s,
        )
    assert resp.matched_path == "fast"
    assert t.intents_log_id is None  # nothing to merge into
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT timings FROM intents_log"))).all()
    assert [r[0] for r in rows] == [None]


@requires_db
@pytest.mark.asyncio
async def test_flyway_on_a_live_server_starts_the_recording(clean_db, monkeypatch) -> None:
    """A no is asked again once the minute is up, so migrating a running
    server needs no second restart; a yes is then never asked again."""
    monkeypatch.setattr(turn_timings, "_HAS_TIMINGS_COLUMN", False)
    monkeypatch.setattr(turn_timings, "_column_checked_at", time.monotonic() - 61)
    async with SessionLocal() as s:
        assert await turn_timings.has_timings_column(s) is True
    assert await turn_timings.has_timings_column(None) is True  # type: ignore[arg-type]


@requires_db
@pytest.mark.asyncio
async def test_a_typed_turn_carries_no_timings(clean_db) -> None:
    from domovoi.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/v1/intent", json={"transcript": "what time is it", "room_id": "kitchen"})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT timings FROM intents_log"))).all()
    assert [r[0] for r in rows] == [None]


# ─── the summary against the database ────────────────────────────────────


async def _seed(rows: list[tuple[str, str, dict | None, datetime]]) -> None:
    async with session_scope() as s:
        for room, path, doc, at in rows:
            await s.execute(
                text(
                    "INSERT INTO intents_log (room_id, transcript, matched_handler, "
                    "matched_path, online, latency_ms, presence_tier, timings, at) "
                    "VALUES (:room, :t, 'timer', :path, true, 9, 'low', "
                    "CAST(:doc AS jsonb), :at)"
                ),
                {"room": room, "t": SECRET, "path": path,
                 "doc": json.dumps(doc) if doc is not None else None, "at": at},
            )


@requires_db
@pytest.mark.asyncio
async def test_the_summary_math_filters_and_leaks_nothing(clean_db) -> None:
    now = datetime.now(timezone.utc)
    w8 = _w(threads=8)
    await _seed(
        # kitchen, recent: stt 100..500, total 1000..5000
        [("kitchen", "fast", {"stt_ms": v, "total_ms": 10 * v, "whisper": w8},
          now - timedelta(minutes=i)) for i, v in enumerate((100, 200, 300, 400, 500))]
        # office, recent, one QA turn with an older Whisper
        + [("office", "qa", {"stt_ms": 1500, "identify_ms": 60, "whisper": _w(threads=None)},
            now - timedelta(minutes=30))]
        # a typed turn: no timings, never counted
        + [("kitchen", "fast", None, now - timedelta(minutes=2))]
        # too old for the default window
        + [("kitchen", "fast", {"stt_ms": 9000}, now - timedelta(days=8))]
    )

    r = await _core_get("/v1/stats/latency")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"since", "room", "limit", "turns", "stages", "paths",
                         "whisper_seen", "whisper", "speculative"}
    assert body["speculative"] == {"turns": 0, "reused": 0, "decodes": 0}
    assert body["turns"] == 6 and body["room"] is None and body["limit"] == 1000
    assert body["stages"]["stt_ms"] == {"count": 6, "p50": 350, "p95": 1250, "max": 1500}
    assert body["stages"]["identify_ms"] == {"count": 1, "p50": 60, "p95": 60, "max": 60}
    assert body["stages"]["total_ms"]["count"] == 5
    assert body["paths"] == {"fast": 5, "qa": 1}
    assert body["whisper_seen"][0] == {**w8, "turns": 5}
    assert set(body["whisper"]) == {"state", "model", "device", "compute_type", "cpu_threads"}
    since = datetime.fromisoformat(body["since"])
    assert timedelta(days=6, hours=23) < now - since < timedelta(days=7, minutes=1)
    # Numbers only: the transcript, the presence tier and the handler are
    # in those rows, and none of them comes back.
    raw = r.text
    for leaked in ("pharmacy", "low", "timer", "person", "session", "transcript"):
        assert leaked not in raw, leaked

    r = await _core_get("/v1/stats/latency?room=office")
    body = r.json()
    assert body["room"] == "office" and body["turns"] == 1
    assert body["stages"]["stt_ms"]["p50"] == 1500

    # `since` reaches back; with no offset it is UTC.
    old = (now - timedelta(days=9)).replace(tzinfo=None).isoformat()
    body = (await _core_get(f"/v1/stats/latency?since={old}")).json()
    assert body["turns"] == 7 and body["stages"]["stt_ms"]["max"] == 9000
    body = (await _core_get(
        f"/v1/stats/latency?since={(now + timedelta(hours=1)).isoformat().replace('+', '%2B')}"
    )).json()
    assert body["turns"] == 0
    assert body["stages"]["stt_ms"] == {"count": 0, "p50": None, "p95": None, "max": None}


# ─── a live turn through /v1/stream, end to end ──────────────────────────


@requires_db
def test_a_live_stream_turn_records_its_timings(monkeypatch) -> None:
    """The vt.py path: a satellite says a command over the real socket, the
    real router answers it against the real database, and the row it
    leaves carries every stage — then the summary counts it."""
    from fastapi.testclient import TestClient

    from domovoi.main import app

    asyncio.run(_ensure_v015())
    monkeypatch.setattr(turn_timings, "_HAS_TIMINGS_COLUMN", None)
    asyncio.run(_truncate())

    async def _accept(self, ctrl):
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    monkeypatch.setattr(
        streaming, "get_whisper_client",
        lambda: _DelayedWhisper("set a timer for 5 minutes", 0.15),
    )
    monkeypatch.setattr("domovoi.voice_identifier.identify", _delayed_identify(0.04))
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _DelayedTTS(0.08))

    try:
        with TestClient(app) as client:
            with client.websocket_connect("/v1/stream/kitchen") as ws:
                ws.send_text(json.dumps({"type": "hello", "room_id": "kitchen"}))
                assert ws.receive_json()["type"] == "ready"
                ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
                for _ in range(10):
                    ws.send_bytes(b"\x00" * 3200)   # 10 x 100 ms = 1 s of audio
                ws.send_text(json.dumps({"type": "utterance_end"}))
                assert ws.receive_json()["type"] == "transcript"
                start = ws.receive_json()
                assert start["type"] == "response_start"
                assert start["matched_path"] == "fast"
                ws.receive_bytes()
                assert ws.receive_json()["type"] == "response_end"

                # The merge lands just after response_end; the socket stays
                # open until it has (closing it would cancel the turn task).
                deadline = time.monotonic() + 10
                while True:
                    body = client.get("/v1/stats/latency?room=kitchen").json()
                    if body["stages"]["total_ms"]["count"] == 1 or time.monotonic() > deadline:
                        break
                    time.sleep(0.05)

        assert body["turns"] == 1
        # This socket says nothing about its last voiced frame and never
        # reported a silence timeout, so the endpoint silence (and with it
        # the last-word-to-reply figure) is the one thing it can't have.
        unknowable = {"endpoint_silence_ms", "speech_to_reply_ms"}
        for stage in STAGES:
            assert body["stages"][stage]["count"] == (0 if stage in unknowable else 1), stage

        async def _row():
            async with SessionLocal() as s:
                return (
                    await s.execute(text("SELECT id, timings, latency_ms FROM intents_log"))
                ).all()

        (row_id, doc, latency_ms), = asyncio.run(_row())
        doc = json.loads(doc) if isinstance(doc, str) else doc
        assert set(doc) == (set(STAGES) - unknowable) | {"whisper"}
        assert doc["capture_audio_ms"] == 1000
        assert doc["stt_ms"] >= 150 - SLACK_MS
        assert doc["identify_ms"] >= 40 - SLACK_MS
        assert doc["tts_first_ms"] >= 80 - SLACK_MS
        assert doc["total_ms"] >= doc["stt_ms"] + doc["identify_ms"] + doc["tts_first_ms"]
        # latency_ms keeps meaning route() alone, inside route_ms.
        assert latency_ms <= doc["route_ms"] + 1
        assert "timer" not in json.dumps(doc)
    finally:
        asyncio.run(_truncate())
