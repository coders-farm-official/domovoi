"""Wake-word takes APPEND (owner report, 2026-11).

Pressing Record again after Stop used to wipe the word's clips (an rmtree
and a clip_count reset on every take). Now:

* a take adds clips after the existing ones, numbering on, from any room,
  and each clip's sidecar keeps its room and take time;
* "Delete all clips" is the explicit way to start over (admin), and a
  per-clip delete keeps clip_count in step with the files;
* a trained ("ready") word records more and trains again; its row counts
  the clips new since the last training, and the retrain's staged set is a
  different set, so the trainer recomputes its features;
* one take at a time per word (a second room gets told which room has it),
  and nothing records while the word trains.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import streaming
from domovoi import wake_clip_quality as wq
from domovoi.config import settings
from domovoi.streaming import StreamSession, WakeRecordingState
from domovoi.tests.conftest import requires_db

_PCM = b"\x11\x22" * 6000  # ~0.75 s, above the near-empty drop floor


def _plant(path: Path, *, good: bool = True, seed: int = 0) -> None:
    import numpy as np

    sr = wq.SAMPLE_RATE
    n = int(2.0 * sr)
    rng = np.random.default_rng(seed)
    sig = rng.normal(0.0, 30.0, n)
    if good:
        start, tlen = int(0.7 * sr), int(0.6 * sr)
        t = np.arange(tlen)
        sig[start : start + tlen] += 9830.0 * np.sin(2 * np.pi * 220.0 * t / sr)
    wq._write_wav_int16(path, sig)


def _sidecar(slug_dir: Path, name: str) -> dict:
    return json.loads((slug_dir / ".analysis" / f"{Path(name).stem}.json").read_text("utf-8"))


async def _truncate() -> None:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE wake_words RESTART IDENTITY CASCADE"))


async def _create(name: str, slug: str) -> int:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await WakeWordsRepository(s).create(name=name, slug=slug, phrase=name.lower())


async def _row(wid: int) -> dict:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await WakeWordsRepository(s).get(wid)


async def _set(wid: int, **cols: Any) -> None:
    from domovoi.db.session import session_scope

    sets = ", ".join(f"{k} = :{k}" for k in cols)
    async with session_scope() as s:
        await s.execute(text(f"UPDATE wake_words SET {sets} WHERE id = :id"), {"id": wid, **cols})


class _FakeWS:
    def __init__(self) -> None:
        self.sent_text: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        pass


async def _clip(sess: StreamSession) -> None:
    await sess._on_control({"type": "utterance_start", "trigger": "wake_clip"})
    await sess._on_audio(_PCM)
    await sess._on_control({"type": "utterance_end"})


async def _unused_route(intent, ctx, session):  # pragma: no cover - guard
    raise AssertionError("route() must not run for a wake_clip utterance")


# ─── the clip-quality store: numbering, origin, new-since-training ────────


def test_roster_is_in_clip_number_order_past_999(tmp_path):
    for n in (1, 2, 999, 1000, 1001):
        (tmp_path / f"clip_{n:03d}.wav").write_bytes(b"x")
    assert [p.name for p in wq.iter_raw_clips(tmp_path)] == [
        "clip_001.wav", "clip_002.wav", "clip_999.wav", "clip_1000.wav", "clip_1001.wav"]
    assert wq.max_clip_number(tmp_path) == 1001
    assert wq.next_clip_number(tmp_path) == 1002


def test_numbering_continues_past_the_last_trained_clip_after_delete_all(tmp_path):
    for n in (1, 2, 3):
        _plant(tmp_path / f"clip_{n:03d}.wav", seed=n)
    wq.analyze_dir(tmp_path)
    wq.stage_selected_clips(tmp_path)
    wq.record_trained(tmp_path, last_clip=3, clips=3)
    assert wq.next_clip_number(tmp_path) == 4

    assert wq.remove_all_clips(tmp_path) == 3
    assert wq.iter_raw_clips(tmp_path) == []
    assert not (tmp_path / ".analysis").exists() and not (tmp_path / ".training").exists()
    # The marker stays: the next take is numbered after the trained set and
    # counts as new.
    assert wq.read_trained(tmp_path)["last_clip"] == 3
    assert wq.next_clip_number(tmp_path) == 4
    _plant(tmp_path / "clip_004.wav", seed=4)
    assert wq.new_since_training(tmp_path) == 1


def test_new_since_training_counts_clips_above_the_marker(tmp_path):
    for n in range(1, 6):
        (tmp_path / f"clip_{n:03d}.wav").write_bytes(b"x")
    assert wq.new_since_training(tmp_path) == 0          # never trained
    wq.record_trained(tmp_path, last_clip=2, clips=2)
    assert wq.new_since_training(tmp_path) == 3
    (tmp_path / "clip_004.wav").unlink()                 # a deleted new clip
    assert wq.new_since_training(tmp_path) == 2


def test_new_since_training_without_a_marker_uses_the_model_time(tmp_path):
    """A word trained before the marker existed: clips newer than its model."""
    clips = tmp_path / "clips"
    clips.mkdir()
    old = clips / "clip_001.wav"
    old.write_bytes(b"x")
    model = tmp_path / "w.onnx"
    model.write_bytes(b"m")
    t = time.time()
    os.utime(old, (t - 100, t - 100))
    os.utime(model, (t - 50, t - 50))
    (clips / "clip_002.wav").write_bytes(b"x")
    os.utime(clips / "clip_002.wav", (t, t))
    assert wq.new_since_training(clips, model) == 1
    assert wq.new_since_training(clips, tmp_path / "missing.onnx") == 0


def test_origin_survives_a_recompute_and_new_clips_default_from_their_verdict(tmp_path):
    good, poor = tmp_path / "clip_001.wav", tmp_path / "clip_002.wav"
    _plant(good, good=True, seed=1)
    _plant(poor, good=False, seed=2)
    origin = {"room": "kitchen", "take_started_at": "2026-11-10T09:00:00Z",
              "recorded_at": "2026-11-10T09:00:05Z"}
    rec = wq.ensure_analysis(good, origin=origin)
    assert rec["room"] == "kitchen" and rec["selected"] is True
    assert wq.ensure_analysis(poor, origin=dict(origin, room="garage"))["selected"] is False

    again = wq.ensure_analysis(good, force=True)
    assert again["room"] == "kitchen"
    assert again["take_started_at"] == "2026-11-10T09:00:00Z"
    assert wq.set_selected(good, False)["room"] == "kitchen"


# ─── the streaming write: two takes, two rooms, one growing set ──────────


@requires_db
@pytest.mark.asyncio
async def test_two_takes_in_two_rooms_append(tmp_path, monkeypatch):
    await _truncate()
    wid = await _create("Hey Domovoi", "hey_domovoi")
    clips_dir = tmp_path / "wake_clips"
    monkeypatch.setattr(streaming.settings, "wake_clips_dir", str(clips_dir))
    monkeypatch.setattr(streaming, "route", _unused_route)

    kitchen = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await kitchen.start_wake_recording(wake_word_id=wid, slug="hey_domovoi", clip_seconds=2.0,
                                       target_count=100, take_started_at="2026-11-10T09:00:00Z")
    await _clip(kitchen)
    await _clip(kitchen)
    await kitchen.stop_wake_recording()

    garage = StreamSession(_FakeWS(), "garage")  # type: ignore[arg-type]
    await garage.start_wake_recording(wake_word_id=wid, slug="hey_domovoi", clip_seconds=2.0,
                                      target_count=100, take_started_at="2026-11-10T10:00:00Z")
    await _clip(garage)
    await _clip(garage)

    slug_dir = clips_dir / "hey_domovoi"
    assert [p.name for p in wq.iter_raw_clips(slug_dir)] == [
        "clip_001.wav", "clip_002.wav", "clip_003.wav", "clip_004.wav"]
    rooms = [_sidecar(slug_dir, f"clip_{n:03d}.wav")["room"] for n in (1, 2, 3, 4)]
    assert rooms == ["kitchen", "kitchen", "garage", "garage"]
    assert _sidecar(slug_dir, "clip_001.wav")["take_started_at"] == "2026-11-10T09:00:00Z"
    assert _sidecar(slug_dir, "clip_004.wav")["take_started_at"] == "2026-11-10T10:00:00Z"
    assert _sidecar(slug_dir, "clip_004.wav")["recorded_at"]
    assert (await _row(wid))["clip_count"] == 4


@pytest.mark.asyncio
async def test_a_take_never_writes_over_an_existing_clip(tmp_path, monkeypatch):
    """Numbering starts after the highest clip, and skips a name that is taken."""
    clips_dir = tmp_path / "wake_clips"
    slug_dir = clips_dir / "w"
    slug_dir.mkdir(parents=True)
    (slug_dir / "clip_007.wav").write_bytes(b"keep")
    monkeypatch.setattr(streaming.settings, "wake_clips_dir", str(clips_dir))
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.wake_recording = WakeRecordingState(wake_word_id=1, slug="w", clip_seconds=2.0,
                                             target_count=5)

    class _NoDb:
        async def __aenter__(self):
            raise RuntimeError("no db here")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(streaming, "session_scope", lambda: _NoDb())
    await sess._save_wake_clip(_PCM)
    sess.wake_recording.next_index = 9
    (slug_dir / "clip_009.wav").write_bytes(b"also keep")
    await sess._save_wake_clip(_PCM)
    assert (slug_dir / "clip_007.wav").read_bytes() == b"keep"
    assert (slug_dir / "clip_008.wav").is_file()
    assert (slug_dir / "clip_009.wav").read_bytes() == b"also keep"
    assert (slug_dir / "clip_010.wav").is_file()


# ─── the core's Record: append, ready words, one take at a time ──────────


class _Sat:
    """A connected Pi as far as record/start looks at it."""

    def __init__(self, room_id: str, recording: WakeRecordingState | None = None) -> None:
        self.room_id = room_id
        self.wake_recording = recording
        self.started: list[dict] = []

    async def start_wake_recording(self, **kw) -> None:
        self.started.append(kw)
        self.wake_recording = WakeRecordingState(
            wake_word_id=kw["wake_word_id"], slug=kw["slug"],
            clip_seconds=kw["clip_seconds"], target_count=kw["target_count"])


async def _record_start(sessions: dict, body: dict):
    from domovoi.main import app as core_app

    async with core_app.router.lifespan_context(core_app):
        core_app.state.active_sessions = sessions
        core_app.state.satellite_mic_enabled = {}
        core_app.state.active_dropins = {}
        async with AsyncClient(transport=ASGITransport(app=core_app),
                               base_url="http://test") as client:
            return await client.post("/v1/admin/wake/record/start", json=body)


@requires_db
@pytest.mark.asyncio
async def test_record_on_a_ready_word_keeps_its_clips_and_its_model(tmp_path, monkeypatch):
    await _truncate()
    wid = await _create("Hey Domo", "hey_domo")
    await _set(wid, status="ready", model_ref="hey_domo", clip_count=2,
               error="Training stopped from the dashboard.")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))
    slug_dir = tmp_path / "hey_domo"
    slug_dir.mkdir()
    for n in (1, 2, 3):                        # one more on disk than counted
        _plant(slug_dir / f"clip_{n:03d}.wav", seed=n)

    sat = _Sat("garage")
    r = await _record_start({"garage": sat}, {"room_id": "garage", "wake_word_id": wid})
    assert r.status_code == 200, r.text
    assert [p.name for p in wq.iter_raw_clips(slug_dir)] == [
        "clip_001.wav", "clip_002.wav", "clip_003.wav"]
    row = await _row(wid)
    assert row["status"] == "ready" and row["model_ref"] == "hey_domo"
    assert row["clip_count"] == 3 and row["error"] is None
    assert sat.started and sat.started[0]["take_started_at"].endswith("Z")


@requires_db
@pytest.mark.asyncio
async def test_record_on_a_failed_word_goes_back_to_recording(tmp_path, monkeypatch):
    await _truncate()
    wid = await _create("Failing", "failing")
    await _set(wid, status="failed", error="boom")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))
    r = await _record_start({"den": _Sat("den")}, {"room_id": "den", "wake_word_id": wid})
    assert r.status_code == 200, r.text
    row = await _row(wid)
    assert row["status"] == "recording" and row["error"] is None


@requires_db
@pytest.mark.asyncio
async def test_record_is_refused_while_the_word_trains(tmp_path, monkeypatch):
    await _truncate()
    wid = await _create("Busy", "busy")
    await _set(wid, status="training")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))
    sat = _Sat("den")
    r = await _record_start({"den": sat}, {"room_id": "den", "wake_word_id": wid})
    assert r.status_code == 409
    assert "is training" in r.json()["detail"]
    assert sat.started == []


@requires_db
@pytest.mark.asyncio
async def test_a_second_room_is_told_which_room_is_recording(tmp_path, monkeypatch):
    await _truncate()
    wid = await _create("Shared", "shared")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))
    kitchen = _Sat("kitchen", WakeRecordingState(wake_word_id=wid, slug="shared",
                                                 clip_seconds=2.0, target_count=9))
    garage = _Sat("garage")
    sessions = {"kitchen": kitchen, "garage": garage}
    r = await _record_start(sessions, {"room_id": "garage", "wake_word_id": wid})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "already recording in kitchen" in detail and "Shared" in detail
    assert garage.started == []
    # The same room again: "this room".
    r = await _record_start(sessions, {"room_id": "kitchen", "wake_word_id": wid})
    assert r.status_code == 409 and "this room" in r.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_a_room_recording_another_word_is_refused(tmp_path, monkeypatch):
    await _truncate()
    a = await _create("Alpha", "alpha")
    b = await _create("Bravo", "bravo")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))
    den = _Sat("den", WakeRecordingState(wake_word_id=a, slug="alpha",
                                         clip_seconds=2.0, target_count=9))
    r = await _record_start({"den": den}, {"room_id": "den", "wake_word_id": b})
    assert r.status_code == 409 and "already recording another wake word" in r.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_two_rooms_pressing_record_together_get_one_take(tmp_path, monkeypatch):
    from domovoi.main import app as core_app

    await _truncate()
    wid = await _create("Race", "race")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path))

    class _SlowSat(_Sat):
        async def start_wake_recording(self, **kw) -> None:
            await asyncio.sleep(0.05)
            await super().start_wake_recording(**kw)

    sats = {"kitchen": _SlowSat("kitchen"), "garage": _SlowSat("garage")}
    async with core_app.router.lifespan_context(core_app):
        core_app.state.active_sessions = sats
        core_app.state.satellite_mic_enabled = {}
        core_app.state.active_dropins = {}
        async with AsyncClient(transport=ASGITransport(app=core_app),
                               base_url="http://test") as client:
            r1, r2 = await asyncio.gather(*(
                client.post("/v1/admin/wake/record/start",
                            json={"room_id": room, "wake_word_id": wid})
                for room in ("kitchen", "garage")))
    assert sorted((r1.status_code, r2.status_code)) == [200, 409]
    assert sum(len(s.started) for s in sats.values()) == 1


# ─── the web: delete all, per-clip delete, new since training, Train again ─


def _client() -> TestClient:
    from web.backend.main import app

    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture
def web_word(tmp_path, monkeypatch):
    """A word with three planted clips, its count in step."""
    import web.backend.api.wake_words as wake_api

    async def _unreachable(path, body=None, timeout=30.0, headers=None):
        return 0, None

    monkeypatch.setattr(wake_api, "post_admin", _unreachable)
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path / "clips"))
    monkeypatch.setattr(settings, "wake_models_dir", str(tmp_path / "models"))
    asyncio.run(_truncate())
    wid = asyncio.run(_create("Hey Domovoi", "hey_domovoi"))
    slug_dir = tmp_path / "clips" / "hey_domovoi"
    slug_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        _plant(slug_dir / f"clip_{n:03d}.wav", seed=n)
    asyncio.run(_set(wid, clip_count=3))
    return wid, slug_dir, tmp_path


@requires_db
def test_delete_all_clips_names_nothing_left_and_keeps_the_word(web_word):
    wid, slug_dir, _ = web_word
    wq.record_trained(slug_dir, last_clip=2, clips=2)
    with _client() as client:
        r = client.delete(f"/api/wake-words/{wid}/clips")
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": 3, "clip_count": 0}
        assert client.get(f"/api/wake-words/{wid}/clips").json()["count"] == 0
        assert [w["id"] for w in client.get("/api/wake-words").json()] == [wid]
    assert wq.iter_raw_clips(slug_dir) == []
    assert (asyncio.run(_row(wid)))["clip_count"] == 0
    # The next take numbers after the last trained clip.
    assert wq.next_clip_number(slug_dir) == 3


@requires_db
def test_delete_all_clips_is_refused_while_training(web_word):
    wid, slug_dir, _ = web_word
    asyncio.run(_set(wid, status="training"))
    with _client() as client:
        r = client.delete(f"/api/wake-words/{wid}/clips")
    assert r.status_code == 409 and "training" in r.json()["detail"]
    assert len(wq.iter_raw_clips(slug_dir)) == 3


@requires_db
def test_per_clip_delete_keeps_the_count_in_step(web_word):
    wid, slug_dir, _ = web_word
    with _client() as client:
        assert client.delete(f"/api/wake-words/{wid}/clips/clip_002.wav").status_code == 204
    assert (asyncio.run(_row(wid)))["clip_count"] == 2


def test_clip_deletes_are_admin_gated():
    from domovoi import admin_auth
    from domovoi.tests.test_wake_trainer_api import _gates
    from web.backend.main import app

    for path in ("/api/wake-words/{wake_word_id}/clips",
                 "/api/wake-words/{wake_word_id}/clips/{name}"):
        assert admin_auth.require_admin_mutation in _gates(app, "DELETE", path), path


@requires_db
def test_the_clip_list_carries_the_room(web_word):
    wid, slug_dir, _ = web_word
    wq.ensure_analysis(slug_dir / "clip_002.wav",
                       origin={"room": "garage", "take_started_at": "2026-11-10T10:00:00Z"})
    with _client() as client:
        clips = client.get(f"/api/wake-words/{wid}/clips").json()["clips"]
    by_name = {c["name"]: c for c in clips}
    assert by_name["clip_002.wav"]["room"] == "garage"
    assert by_name["clip_002.wav"]["take_started_at"] == "2026-11-10T10:00:00Z"
    assert by_name["clip_001.wav"]["room"] is None


@requires_db
def test_a_ready_word_counts_new_clips_and_trains_again(web_word, monkeypatch):
    wid, slug_dir, tmp_path = web_word
    monkeypatch.setattr(settings, "wake_word_min_clips", 3)
    with _client() as client:
        assert client.get("/api/wake-words").json()[0]["new_clips"] == 0
        asyncio.run(_set(wid, status="ready", model_ref="hey_domovoi"))
        wq.record_trained(slug_dir, last_clip=1, clips=1)
        assert client.get("/api/wake-words").json()[0]["new_clips"] == 2

        r = client.post(f"/api/wake-words/{wid}/train")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "training"
        # Already training: not queued twice.
        assert client.post(f"/api/wake-words/{wid}/train").status_code == 409


# ─── the retrain: the grown set, the marker, a failed retrain stays ready ─


@requires_db
@pytest.mark.asyncio
async def test_a_failed_or_stopped_retrain_leaves_the_word_ready():
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    await _truncate()
    wid = await _create("Again", "again")
    await _set(wid, status="training", model_ref="again", clip_count=20)
    async with session_scope() as s:
        await WakeWordsRepository(s).mark_failed(wid, "exited 137")
    row = await _row(wid)
    assert row["status"] == "ready" and row["error"] == "exited 137"

    await _set(wid, status="training")
    async with session_scope() as s:
        await WakeWordsRepository(s).mark_stopped(wid)
    assert (await _row(wid))["status"] == "ready"

    # A first training (no model yet) still fails / goes back to recording.
    fresh = await _create("Fresh", "fresh")
    await _set(fresh, status="training")
    async with session_scope() as s:
        await WakeWordsRepository(s).mark_failed(fresh, "boom")
    assert (await _row(fresh))["status"] == "failed"


def _entrypoint():
    import importlib.util

    path = (Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "docker"
            / "entrypoint.py")
    spec = importlib.util.spec_from_file_location("_wake_entrypoint_append", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_grown_set_is_staged_afresh_and_its_features_recomputed(tmp_path):
    """Train, record more, Train again: .training/ is rebuilt with every
    selected clip, and the entrypoint's fingerprint of that set changes, so
    the stored features are dropped and computed again."""
    ep = _entrypoint()
    slug_dir = tmp_path / "clips" / "w"
    slug_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        _plant(slug_dir / f"clip_{n:03d}.wav", seed=n)
    staged, count = wq.stage_selected_clips(slug_dir)
    assert count == 3
    model_dir = tmp_path / "run"
    (model_dir / "positive_train").mkdir(parents=True)
    for name in ep.FEATURE_FILES:
        (model_dir / name).write_bytes(b"features")
    ep.mark_features_current(model_dir, sorted(staged.glob("*.wav")))

    for n in (4, 5):                                  # a later take
        _plant(slug_dir / f"clip_{n:03d}.wav", seed=n)
    staged, count = wq.stage_selected_clips(slug_dir)
    assert count == 5 and len(list(staged.glob("*.wav"))) == 5
    assert ep.reset_for_real_clips(model_dir, sorted(staged.glob("*.wav"))) is True
    assert not any((model_dir / n).exists() for n in ep.FEATURE_FILES)


@pytest.mark.asyncio
async def test_a_successful_training_records_which_clips_it_had(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from domovoi import egress
    from domovoi import wake_trainer_setup as wts
    from domovoi.db import repositories
    from domovoi.workers import wake_word_trainer as wwt

    ready: list[int] = []

    class _Queue:
        rows = [{"id": 7, "slug": "w", "phrase": "hey w"}]

        def __init__(self, _s) -> None:
            pass

        async def next_training(self):
            return _Queue.rows.pop(0) if _Queue.rows else None

        async def mark_ready(self, wid, ref):
            ready.append(wid)

        async def mark_failed(self, wid, error):  # pragma: no cover - not expected
            raise AssertionError(error)

    class _S:
        async def execute(self, *a, **k):
            return None

    @asynccontextmanager
    async def scope():
        yield _S()

    clips = tmp_path / "clips"
    models = tmp_path / "models"
    slug_dir = clips / "w"
    slug_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        _plant(slug_dir / f"clip_{n:03d}.wav", seed=n)
    monkeypatch.setattr(wwt, "session_scope", scope)
    monkeypatch.setattr(repositories, "WakeWordsRepository", _Queue)
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", True)
    monkeypatch.setattr(settings, "wake_word_train_command",
                        'trainer "{clips_dir}" "{phrase}" "{slug}" "{out}"')
    monkeypatch.setattr(settings, "wake_clips_dir", str(clips))
    monkeypatch.setattr(settings, "wake_models_dir", str(models))
    monkeypatch.setattr(wwt, "_SWEPT", True)
    monkeypatch.setattr(wwt, "phrase_pronunciations", lambda p: "")
    monkeypatch.setattr(wwt, "synthesis_ipa", lambda p: "")
    wts.reset_for_tests()

    def fake_run(argv, **kw):
        # A clip that lands mid-run is numbered above the staged set.
        _plant(slug_dir / "clip_004.wav", seed=4)
        Path(argv[-1]).write_bytes(b"onnx")
        return wwt.TrainRun(0, "ok")

    monkeypatch.setattr(wwt, "run_train_command", fake_run)
    try:
        with egress.override_policy("always"):
            assert await wwt.WakeWordTrainer().tick() == 1
    finally:
        wts.reset_for_tests()
    assert ready == [7]
    marker = wq.read_trained(slug_dir)
    assert marker["last_clip"] == 3 and marker["clips"] == 3
    assert wq.new_since_training(slug_dir) == 1
