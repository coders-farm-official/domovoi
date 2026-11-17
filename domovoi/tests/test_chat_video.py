"""Video in chat (1.4.0): attach a short video, the core watches it, and the
chat answers from what was kept (domovoi/chat_video, chat_videos.py).

The videos are tiny ones made here with ffmpeg's test sources (never real
footage); the speech and vision models are fakes at the runner's
``Brains`` boundary (or the USE_STUBS stand-ins). What matters most:

* **voice first**: a step waits while a voice turn is under way (from the
  wake word: a live capture counts), a frame being described when a turn
  starts is cancelled and asked again after it, and an ffmpeg pass is held
  still while a turn runs;
* each result is saved as it finishes, and a restart carries on from the
  last saved step, or fails cleanly (too many starts, the video gone);
* the video and every frame grab are deleted when the job ends, however it
  ends, and only text stays, which goes with the chat;
* the limits: 3 minutes (refused in plain words), the byte cap, the
  formats; AI features off hides and refuses it; the routes' tiers are
  chat's.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import admin_auth, ai_features, voice_activity
from domovoi.chat_video import context, limits, media, plan
from domovoi.chat_video import runner as runner_mod
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# ─── Test videos (ffmpeg test sources, a few seconds each) ─────────────
def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> dict[str, Path]:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("clips")
    out = {}
    # 12 s: a test pattern, then colour bars (one scene change at 6 s), with a tone.
    out["short"] = d / "party.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=10",
            "-f", "lavfi", "-i", "smptebars=duration=6:size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out["short"]))
    # 3 min 1 s, tiny and at 1 frame a second: one second too long.
    out["long"] = d / "long.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=181:size=64x48:rate=1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(out["long"]))
    # A codec phones don't record (MPEG-4 Part 2) in an .mp4.
    out["mpeg4"] = d / "old.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=64x48:rate=5", "-c:v", "mpeg4", str(out["mpeg4"]))
    # No sound at all.
    out["silent"] = d / "silent.webm"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=3:size=64x48:rate=5", "-c:v", "libvpx-vp9",
            "-b:v", "50k", str(out["silent"]))
    return out


@pytest.fixture(autouse=True)
def video_dirs(tmp_path, monkeypatch):
    """The videos' folder, in this test's own home."""
    vids = tmp_path / "chat_uploads" / "videos"
    monkeypatch.setattr(limits, "VIDEOS_DIR", vids)
    monkeypatch.setattr(limits, "WORK_DIR", vids / "work")
    voice_activity._reset_for_tests()
    yield vids
    voice_activity._reset_for_tests()


# ─── No database ────────────────────────────────────────────────────────
def test_three_minutes_is_the_limit_in_plain_words() -> None:
    long = media.ProbeResult(duration=181.0, container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="h264",
                             audio_codec="aac", width=1920, height=1080)
    code, sentence = media.refusal(long)
    assert code == "video_too_long"
    assert sentence.startswith("Videos up to 3 minutes can be attached.")
    assert "3:01" in sentence
    ok = media.ProbeResult(duration=180.2, container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="hevc",
                           audio_codec="aac", width=3840, height=2160)
    assert media.refusal(ok) is None
    odd = media.ProbeResult(duration=10.0, container="avi", video_codec="mpeg4", audio_codec=None,
                            width=64, height=48)
    assert media.refusal(odd)[0] == "video_unsupported"


def test_the_byte_cap_takes_three_minutes_of_4k60_phone_video() -> None:
    # 85 Mbit/s for 180 s: the heaviest ordinary phone recording.
    assert 85e6 / 8 * 180 < limits.MAX_BYTES < 3 * 1024 ** 3


def test_frames_are_capped_and_near_duplicates_dropped() -> None:
    times = plan.candidate_times(180, [(t, 0.5) for t in range(5, 180, 5)])
    assert len(times) > limits.MAX_FRAMES
    assert len(plan.spread(times)) == limits.MAX_FRAMES
    # No scene changes in 3 minutes: the start, and a frame every 30 s.
    still = plan.candidate_times(180, [])
    assert still[0] == 1.0 and len(still) == 6
    # Two frames that look the same: the second goes.
    a = 0b1010 << 40
    assert plan.drop_same([1.0, 2.0, 3.0], [a, a ^ 0b11, ~a & (2 ** 64 - 1)]) == [1.0, 3.0]
    assert plan.drop_same([1.0, 2.0], [None, None]) == [1.0, 2.0]


def test_speech_is_cut_into_short_pieces_at_quiet_moments() -> None:
    win = media.WINDOW / media.SAMPLE_RATE
    # 20 s of speech with a dip every 4 s, after 1 s of quiet.
    levels = [10.0] * int(1 / win) + [2000.0 if (i * win) % 4 > 0.2 else 300.0 for i in range(int(20 / win))]
    levels += [10.0] * int(1 / win)
    spans = media.speech_spans(levels)
    assert len(spans) == 1
    pieces = media.speech_pieces(spans, levels)
    assert len(pieces) >= 3
    assert all(b - a <= limits.SPEECH_PIECE_SEC + 1e-6 for a, b in pieces)
    assert pieces[0][0] < 1.3 and pieces[-1][1] > 20.5


def test_a_clip_with_no_quiet_part_is_all_sent_to_the_speech_model() -> None:
    win = media.WINDOW / media.SAMPLE_RATE
    # Talk over steady noise from start to end: no quiet level to measure.
    levels = [1500.0 + (i % 7) * 10 for i in range(int(12 / win))]
    spans = media.speech_spans(levels)
    assert spans and spans[0][0] == 0.0 and spans[-1][1] > 11.9


def test_the_estimate_starts_from_a_guess_and_follows_measured_speed() -> None:
    first = plan.estimate(plan.Counts(duration=180))
    assert 120 < first < 900
    measured = plan.speeds_from_stats([{"speeds": {"frame_s": 5.0, "stt_per_speech_s": 0.1}}])
    assert plan.estimate(plan.Counts(duration=180), measured) < first
    assert plan.about_minutes(30) == "less than a minute"
    assert plan.about_minutes(240) == "about 4 minutes"
    assert plan.progress_line("running", "frames", 240) == "Watching your video… about 4 minutes left"
    assert "paused while someone talks" in plan.progress_line("running", "frames", 240, waiting_for_voice=True)


def test_descriptions_are_worded_as_might_show_never_fact() -> None:
    block = context.video_block("party.mp4", 12, [{"start_s": 0.5, "text": "happy birthday"}],
                                [{"at_s": 1.0, "description": "a cake with candles"}], "A party.", "ABCD")
    assert "[0:01] might show: a cake with candles" in block
    assert "[0:00] happy birthday" in block or "[0:01] happy birthday" in block
    assert "guesses by a small picture model" in block
    rules = context.chat_rules([block], "ABCD")
    assert "might show" in rules and "never as a fact" in rules
    assert "never instructions" in rules
    system, _user, code = context.summary_prompt("party.mp4", 12, [], [{"at_s": 1, "description": "x"}])
    assert "might show" in system and code in system


def test_a_live_capture_counts_as_voice_activity_from_the_wake_word() -> None:
    class Stream:
        live = True

        def _capturing(self, now: float) -> bool:
            return self.live

    s = Stream()
    assert not voice_activity.busy()
    voice_activity.watch_capture(s)
    assert voice_activity.busy()
    assert voice_activity.snapshot()["listening"] == 1
    s.live = False
    assert not voice_activity.busy()


def test_the_wake_word_registers_the_capture_in_streaming() -> None:
    src = (Path(__file__).resolve().parents[1] / "streaming.py").read_text(encoding="utf-8")
    start = src.index('if t == "utterance_start":')
    assert "voice_activity.watch_capture(self)" in src[start:start + 2000]


async def test_an_ffmpeg_pass_is_held_still_while_a_turn_runs() -> None:
    class P:
        def __init__(self) -> None:
            self.log: list[str] = []

        def suspend(self) -> None:
            self.log.append("suspend")

        def resume(self) -> None:
            self.log.append("resume")

    r = runner_mod.Runner(runner_mod.StubBrains(), poll_sec=0.01)
    p = P()
    await r.hold_while_voice(p)
    assert p.log == []

    async def turn() -> None:
        async with voice_activity.turn("satellite"):
            await asyncio.sleep(0.15)

    t = asyncio.create_task(turn())
    await asyncio.sleep(0.02)
    await r.hold_while_voice(p)
    await t
    assert p.log == ["suspend", "resume"]


def test_routes_take_the_tiers_chat_takes() -> None:
    from web.backend.main import app as web_app

    def gates(method: str, path: str) -> set:
        for rc in iter_route_contexts(web_app.routes):
            if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
                out, stack = set(), [rc.dependant]
                while stack:
                    for dep in stack.pop().dependencies:
                        if dep.call is not None:
                            out.add(dep.call)
                        stack.append(dep)
                return out
        raise AssertionError(f"{method} {path} is not a web route")

    def calls(names: set) -> set:
        return {getattr(c, "__name__", "") for c in names}

    send = gates("POST", "/api/chat/threads/{thread_id}/messages")
    up = gates("POST", "/api/chat/videos")
    assert admin_auth.require_device in up and admin_auth.require_device in send
    assert "_require_ai" in calls(up) and "_require_ai" in calls(send)
    read = gates("GET", "/api/chat/threads/{thread_id}/messages")
    for path in ("/api/chat/video-jobs/{job_id}", "/api/chat/video-jobs/{job_id}/results"):
        g = gates("GET", path)
        assert admin_auth.require_device_read in g and admin_auth.require_device_read in read
        assert admin_auth.require_device not in g
    stop = gates("POST", "/api/chat/video-jobs/{job_id}/cancel")
    assert admin_auth.require_device in stop and "_require_ai" not in calls(stop)


def test_the_transport_caps_let_a_2_gb_video_through() -> None:
    from domovoi import transport_guard
    from web.backend import middleware

    assert transport_guard.body_limit_for("/api/chat/videos") >= limits.MAX_BYTES
    assert middleware.body_limit_for("/api/chat/videos") >= limits.MAX_BYTES


def test_every_video_code_has_help() -> None:
    from domovoi.help_desk.error_codes import ERROR_CODES

    for code in ("video_too_long", "video_too_large", "video_unsupported", "video_unreadable", "video_no_room",
                 "video_no_tools", "video_missing", "video_interrupted", "video_failed"):
        assert ERROR_CODES[code].piece == "ask-video"


# ─── The upload (web) ───────────────────────────────────────────────────
def _client() -> TestClient:
    from web.backend.main import app

    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _upload(c: TestClient, path: Path, name: str | None = None):
    return c.post("/api/chat/videos", params={"name": name or path.name}, content=path.read_bytes(),
                  headers={"Content-Type": "video/mp4"})


@needs_ffmpeg
def test_a_short_video_is_accepted_with_its_length(clips, video_dirs) -> None:
    with _client() as c:
        r = _upload(c, clips["short"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "party.mp4" and 11.5 < body["duration_s"] < 12.5 and body["has_audio"]
    assert body["estimate_text"]
    assert limits.video_path(body["token"]) is not None


@needs_ffmpeg
def test_a_video_over_three_minutes_is_refused_and_not_kept(clips, video_dirs) -> None:
    with _client() as c:
        r = _upload(c, clips["long"])
    assert r.status_code == 422
    assert r.json()["code"] == "video_too_long"
    assert r.json()["detail"].startswith("Videos up to 3 minutes can be attached.")
    assert not any(video_dirs.glob("*.mp4"))


@needs_ffmpeg
def test_formats_chat_doesnt_take_are_refused(clips, video_dirs) -> None:
    with _client() as c:
        r = _upload(c, clips["mpeg4"])
        assert r.status_code == 415 and r.json()["code"] == "video_unsupported"
        r = _upload(c, clips["short"], name="party.avi")
        assert r.status_code == 415 and r.json()["code"] == "video_unsupported"
        r = c.post("/api/chat/videos", params={"name": "junk.mp4"}, content=b"not a video at all" * 100)
        assert r.status_code == 422 and r.json()["code"] == "video_unreadable"
    assert not any(p for p in video_dirs.glob("*") if p.is_file())


@needs_ffmpeg
def test_the_byte_cap_refuses_as_it_streams(clips, video_dirs, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_BYTES", 5000)
    with _client() as c:
        r = _upload(c, clips["short"])
    assert r.status_code == 413 and r.json()["code"] == "video_too_large"
    assert not any(p for p in video_dirs.glob("*") if p.is_file())


def test_ai_off_hides_videos_and_refuses_them() -> None:
    with ai_features.override(False):
        from web.backend.api import chat_videos

        assert chat_videos.availability()["available"] is False
        with _client() as c:
            r = c.post("/api/chat/videos", params={"name": "a.mp4"}, content=b"x")
            assert r.status_code == 409
            assert r.headers.get(ai_features.REFUSAL_HEADER) == ai_features.REFUSAL_VALUE
    with ai_features.override(True):
        from web.backend.api import chat_videos

        assert chat_videos.availability()["available"] is HAVE_FFMPEG


def test_the_dashboard_offers_videos_only_when_the_server_says_so() -> None:
    root = Path(__file__).resolve().parents[2] / "web" / "static"
    chat = (root / "chat.jsx").read_text(encoding="utf-8")
    vids = (root / "chat_videos.jsx").read_text(encoding="utf-8")
    assert "modelsInfo.video && modelsInfo.video.available" in vids
    assert "accept={videoOffered ?" in chat
    assert "if (!videoOffered)" in chat
    assert 'src="chat_videos.jsx"' in (root / "index.html").read_text(encoding="utf-8")


# ─── The job (core) ─────────────────────────────────────────────────────
class FakeBrains:
    """The models: ``gate`` (an Event) unset holds each description until
    it is set; every call is logged; a cancelled description counted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self.gate: asyncio.Event | None = None
        self.described = asyncio.Event()
        self.cancelled = 0
        self.on_describe = None

    async def transcribe(self, pcm: bytes) -> str:
        self.calls.append(("stt", time.monotonic()))
        return "happy birthday to you"

    async def describe(self, jpeg: bytes) -> str:
        self.calls.append(("vision", time.monotonic()))
        if self.on_describe is not None:
            await self.on_describe(self)
        self.described.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return "a cake with candles on a table"

    async def write(self, system: str, user: str, num_predict: int) -> str:
        self.calls.append(("write", time.monotonic()))
        if "Write the summary" in user:
            return "Someone sings happy birthday, and the video might show a cake."
        return "They sing happy birthday."

    async def release_vision(self) -> None:
        return None

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)


async def _q(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with session_scope() as s:
        res = await s.execute(text(sql), params or {})
        return [dict(r) for r in res.mappings()] if res.returns_rows else []


@pytest.fixture
async def no_other_jobs():
    """This lane's live jobs from earlier tests can't be claimed first."""
    await _q("DELETE FROM chat_video_jobs")
    yield
    await _q("DELETE FROM chat_video_jobs")


async def _stage(clip: Path, question: str = "") -> dict[str, Any]:
    """A thread, the person's message, a staged copy of ``clip`` and its job
    row, as a send makes them."""
    from web.backend.api import chat_videos

    limits.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    token = __import__("uuid").uuid4().hex
    shutil.copyfile(clip, limits.VIDEOS_DIR / f"{token}{clip.suffix}")
    probed = await media.probe(clip)
    meta = {"token": token, "name": clip.name, "duration_s": probed.duration, "size": clip.stat().st_size,
            "probe": probed.to_json(), "estimate_s": 60.0}
    limits.meta_path(token).write_text(json.dumps(meta), encoding="utf-8")
    async with session_scope() as s:
        tid = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('video test') RETURNING id"))).scalar_one()
        mid = (await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', :c) RETURNING id"),
            {"t": tid, "c": question})).scalar_one()
        jobs = await chat_videos.queue_jobs(s, tid, mid, [meta], question)
    return {"thread_id": tid, "message_id": mid, "token": token, "job_id": jobs[0]["job_id"]}


async def _job(job_id: str) -> dict[str, Any]:
    return (await _q("SELECT * FROM chat_video_jobs WHERE id = :id", {"id": job_id}))[0]


async def _until(pred, timeout: float = 10.0) -> None:
    t0 = time.monotonic()
    while not await pred():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


def _files_left(token: str, job_id: str) -> list[Path]:
    out = [p for p in limits.VIDEOS_DIR.glob(f"{token}*")]
    wd = limits.work_dir(job_id)
    if wd.exists():
        out += list(wd.rglob("*")) or [wd]
    return out


@requires_db
@needs_ffmpeg
async def test_the_job_runs_every_pass_saves_text_and_deletes_the_video(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"], question="what song is it?")
    brains = FakeBrains()
    r = runner_mod.Runner(brains, quiet_sec=0.05, poll_sec=0.01)
    assert await r.run_next() == j["job_id"]
    row = await _job(j["job_id"])
    assert row["state"] == "done" and row["error"] is None
    assert row["summary"].startswith("Someone sings")
    plan_ = row["plan"]
    assert len(plan_["scenes"]) >= 1 and plan_["has_audio"] and plan_["pieces"]
    assert all(b - a <= limits.SPEECH_PIECE_SEC + 0.01 for a, b in plan_["pieces"])
    segs = await _q("SELECT * FROM chat_video_segments WHERE job_id = :id", {"id": j["job_id"]})
    frames = await _q("SELECT * FROM chat_video_frames WHERE job_id = :id ORDER BY idx", {"id": j["job_id"]})
    assert len(segs) == len(plan_["pieces"]) and segs[0]["text"] == "happy birthday to you"
    assert 1 <= len(frames) <= limits.MAX_FRAMES and frames[0]["description"].startswith("a cake")
    # Stages in order: speech before frames before the summary.
    kinds = [k for k, _ in brains.calls]
    assert kinds.index("stt") < kinds.index("vision") < kinds.index("write")
    # The reply, and the answer to the question sent with the video.
    msgs = await _q("SELECT content, stats FROM chat_messages WHERE thread_id = :t AND role = 'assistant' "
                    "ORDER BY id", {"t": j["thread_id"]})
    assert msgs[0]["content"].startswith("I've watched party.mp4.")
    assert "might be wrong" in msgs[0]["content"]
    assert msgs[1]["content"] == "They sing happy birthday."
    assert msgs[0]["stats"]["model_role"] == "video"
    # Only text is kept: the video, its staging note, every frame grab and
    # the sound are gone, and nothing was ever written outside its folder.
    assert _files_left(j["token"], j["job_id"]) == []
    assert row["files_deleted_at"] is not None
    assert set(plan.GUESS) <= set(row["stats"]["speeds"])


@requires_db
@needs_ffmpeg
async def test_a_voice_turn_mid_job_cancels_the_frame_and_it_runs_again_after(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    brains = FakeBrains()
    brains.gate = asyncio.Event()
    r = runner_mod.Runner(brains, quiet_sec=0.3, poll_sec=0.01)
    task = asyncio.create_task(r.run_next())
    await asyncio.wait_for(brains.described.wait(), 90)
    first_frame_calls = brains.count("vision")
    cm = voice_activity.turn("satellite")
    await cm.__aenter__()
    turn_opened = time.monotonic()
    # The frame being described stops at once …
    await _until(lambda: _true(brains.cancelled == 1), timeout=2)
    assert time.monotonic() - turn_opened < 0.5
    assert r.voice_cancels == 1
    await _until(lambda: _job_flag(j["job_id"]), timeout=3)
    # … and nothing else starts while the turn runs.
    calls_during = len(brains.calls)
    await asyncio.sleep(0.6)
    assert len(brains.calls) == calls_during
    await cm.__aexit__(None, None, None)
    turn_ended = time.monotonic()
    brains.gate.set()
    await asyncio.wait_for(task, 90)
    # The same frame was asked again, only after the house was quiet.
    restarted = [t for k, t in brains.calls if k == "vision"][first_frame_calls]
    assert restarted - turn_ended >= 0.25
    row = await _job(j["job_id"])
    assert row["state"] == "done" and row["waiting_for_voice"] is False
    frames = await _q("SELECT idx FROM chat_video_frames WHERE job_id = :id", {"id": j["job_id"]})
    assert len(frames) == len(row["plan"]["frames"])


async def _true(v: bool) -> bool:
    return v


async def _job_flag(job_id: str) -> bool:
    return bool((await _job(job_id))["waiting_for_voice"])


@requires_db
@needs_ffmpeg
async def test_nothing_starts_while_a_room_is_listening_after_its_wake_word(clips, no_other_jobs) -> None:
    class Capture:
        live = True

        def _capturing(self, now: float) -> bool:
            return self.live

    cap = Capture()
    voice_activity.watch_capture(cap)
    j = await _stage(clips["short"])
    brains = FakeBrains()
    r = runner_mod.Runner(brains, quiet_sec=0.1, poll_sec=0.01)
    task = asyncio.create_task(r.run_next())
    await _until(lambda: _job_flag(j["job_id"]), timeout=5)
    await asyncio.sleep(0.3)
    assert brains.calls == []
    assert (await _job(j["job_id"]))["stage"] == "scan"
    cap.live = False
    await asyncio.wait_for(task, 90)
    assert (await _job(j["job_id"]))["state"] == "done"


@requires_db
@needs_ffmpeg
async def test_a_restart_carries_on_from_the_last_saved_step(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    brains = FakeBrains()
    r = runner_mod.Runner(brains, quiet_sec=0.01, poll_sec=0.01)
    stopped = asyncio.Event()

    async def stop_on_second(b: FakeBrains) -> None:
        if b.count("vision") == 2:
            stopped.set()
            await asyncio.sleep(3600)

    brains.on_describe = stop_on_second
    task = asyncio.create_task(r.run_next())
    await asyncio.wait_for(stopped.wait(), 90)
    task.cancel()          # the core shutting down mid-frame
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await _job(j["job_id"])
    assert row["state"] == "running" and row["stage"] == "frames" and row["attempts"] == 1
    saved = await _q("SELECT idx FROM chat_video_frames WHERE job_id = :id", {"id": j["job_id"]})
    assert [s["idx"] for s in saved] == [0]
    assert limits.video_path(j["token"]) is not None
    # After the restart: no speech again, only the frames not saved yet.
    again = FakeBrains()
    assert await runner_mod.Runner(again, quiet_sec=0.01, poll_sec=0.01).run_next() == j["job_id"]
    row = await _job(j["job_id"])
    assert row["state"] == "done" and row["attempts"] == 2
    assert again.count("stt") == 0
    assert again.count("vision") == len(row["plan"]["frames"]) - 1
    assert _files_left(j["token"], j["job_id"]) == []


@requires_db
@needs_ffmpeg
async def test_a_job_that_keeps_stopping_the_server_fails_cleanly(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    await _q("UPDATE chat_video_jobs SET state = 'running', attempts = :n WHERE id = :id",
             {"n": limits.MAX_ATTEMPTS, "id": j["job_id"]})
    await runner_mod.Runner(FakeBrains(), quiet_sec=0.01).run_next()
    row = await _job(j["job_id"])
    assert row["state"] == "failed" and row["error_code"] == "video_interrupted"
    msg = (await _q("SELECT content, stats, error FROM chat_messages WHERE thread_id = :t AND role = 'assistant'",
                    {"t": j["thread_id"]}))[0]
    assert msg["content"].startswith("I couldn't finish watching party.mp4.")
    assert msg["stats"]["error_code"] == "video_interrupted"
    assert _files_left(j["token"], j["job_id"]) == []


@requires_db
@needs_ffmpeg
async def test_a_video_gone_after_a_restart_fails_with_a_plain_reason(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    for p in limits.VIDEOS_DIR.glob(f"{j['token']}*.mp4"):
        p.unlink()
    await runner_mod.Runner(FakeBrains(), quiet_sec=0.01).run_next()
    row = await _job(j["job_id"])
    assert row["state"] == "failed" and row["error_code"] == "video_missing"
    assert "Attach it again" in row["error"]


@requires_db
@needs_ffmpeg
async def test_stop_ends_a_running_job_and_deletes_everything(clips, no_other_jobs) -> None:
    from web.backend.api import chat_videos

    j = await _stage(clips["short"])
    brains = FakeBrains()
    brains.gate = asyncio.Event()
    r = runner_mod.Runner(brains, quiet_sec=0.01, poll_sec=0.01)
    task = asyncio.create_task(r.run_next())
    await asyncio.wait_for(brains.described.wait(), 90)
    v = await chat_videos.cancel_video_job(j["job_id"])
    assert v["line"] == "Stopping…"
    await asyncio.wait_for(task, 5)
    row = await _job(j["job_id"])
    assert row["state"] == "cancelled"
    assert await _q("SELECT 1 FROM chat_video_segments WHERE job_id = :id", {"id": j["job_id"]}) == []
    assert await _q("SELECT 1 FROM chat_video_frames WHERE job_id = :id", {"id": j["job_id"]}) == []
    assert _files_left(j["token"], j["job_id"]) == []


@requires_db
@needs_ffmpeg
async def test_stop_on_a_queued_job_ends_it_at_once(clips, no_other_jobs) -> None:
    from web.backend.api import chat_videos

    j = await _stage(clips["short"])
    v = await chat_videos.cancel_video_job(j["job_id"])
    assert v["state"] == "cancelled" and v["line"] == "Stopped"
    assert _files_left(j["token"], j["job_id"]) == []
    assert await runner_mod.Runner(FakeBrains()).run_next() is None


@requires_db
@needs_ffmpeg
async def test_a_silent_video_is_watched_and_says_it_has_no_sound(clips, no_other_jobs) -> None:
    j = await _stage(clips["silent"])
    brains = FakeBrains()
    await runner_mod.Runner(brains, quiet_sec=0.01, poll_sec=0.01).run_next()
    row = await _job(j["job_id"])
    assert row["state"] == "done" and brains.count("stt") == 0
    assert "The video has no sound." in row["notes"]


@requires_db
@needs_ffmpeg
async def test_the_stub_models_run_a_whole_job(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    r = runner_mod.Runner(quiet_sec=0.01, poll_sec=0.01)
    assert isinstance(r.brains, runner_mod.StubBrains)   # USE_STUBS
    await r.run_next()
    assert (await _job(j["job_id"]))["state"] == "done"


@requires_db
@needs_ffmpeg
async def test_the_results_go_with_the_chat(clips, no_other_jobs) -> None:
    from web.backend.api import chat as chat_api

    j = await _stage(clips["short"])
    await runner_mod.Runner(FakeBrains(), quiet_sec=0.01, poll_sec=0.01).run_next()
    assert await _q("SELECT 1 FROM chat_video_frames WHERE job_id = :id", {"id": j["job_id"]})
    await chat_api.delete_thread(j["thread_id"])
    assert await _q("SELECT 1 FROM chat_video_jobs WHERE id = :id", {"id": j["job_id"]}) == []
    assert await _q("SELECT 1 FROM chat_video_segments WHERE job_id = :id", {"id": j["job_id"]}) == []
    assert await _q("SELECT 1 FROM chat_video_frames WHERE job_id = :id", {"id": j["job_id"]}) == []


@requires_db
@needs_ffmpeg
async def test_deleting_a_chat_mid_job_deletes_the_video_too(clips, no_other_jobs) -> None:
    from web.backend.api import chat as chat_api

    j = await _stage(clips["short"])
    brains = FakeBrains()
    brains.gate = asyncio.Event()
    task = asyncio.create_task(runner_mod.Runner(brains, quiet_sec=0.01, poll_sec=0.01).run_next())
    await asyncio.wait_for(brains.described.wait(), 90)
    await chat_api.delete_thread(j["thread_id"])
    brains.gate.set()
    await asyncio.wait_for(task, 10)
    assert _files_left(j["token"], j["job_id"]) == []


@requires_db
@needs_ffmpeg
async def test_a_question_about_a_watched_video_is_answered_from_its_text(clips, no_other_jobs) -> None:
    from web.backend.api import chat_videos

    j = await _stage(clips["short"])
    await runner_mod.Runner(FakeBrains(), quiet_sec=0.01, poll_sec=0.01).run_next()
    rules = await chat_videos.chat_rules(j["thread_id"])
    assert "happy birthday to you" in rules
    assert "might show: a cake with candles" in rules
    res = await chat_videos.get_video_results(j["job_id"])
    assert res["frames"][0]["might_show"].startswith("a cake")
    assert res["transcript"][0]["text"] == "happy birthday to you"
    assert res["summary"].startswith("Someone sings")


# ─── The send and the progress (web, end to end) ───────────────────────
def _sse(body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


@requires_db
@needs_ffmpeg
def test_sending_a_video_queues_it_and_the_reply_says_so(clips, video_dirs, monkeypatch) -> None:
    captured: list[list[dict[str, Any]]] = []

    async def fake_stream(messages, model, **kw):
        captured.append(messages)
        yield "It might show a cake."

    from web.backend.api import chat as chat_api

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", fake_stream)

    async def tags_or_none(*a, **k):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", tags_or_none)
    with _client() as c:
        c.portal.call(_q, "DELETE FROM chat_video_jobs")
        up = _upload(c, clips["short"]).json()
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "", "videos": [{"token": up["token"]}]})
        assert r.status_code == 200
        events = _sse(r.text)
        done = [d for e, d in events if e == "done"][0]
        assert done["content"].startswith("I'm watching party.mp4 (0:12) now.")
        assert "keep chatting" in done["content"]
        lst = c.get(f"/api/chat/threads/{tid}/messages").json()
        (v,) = lst["videos"]
        assert v["state"] == "queued" and v["line"] == "Waiting to start…"
        user_row = [m for m in lst["messages"] if m["role"] == "user"][0]
        assert v["message_id"] == user_row["id"]
        assert c.get(f"/api/chat/video-jobs/{v['id']}").json()["id"] == v["id"]
        # The core watches it (stub models), and the chat answers from it.
        c.portal.call(runner_mod.Runner(FakeBrains(), quiet_sec=0.01, poll_sec=0.01).run_next)
        v = c.get(f"/api/chat/video-jobs/{v['id']}").json()
        assert v["state"] == "done" and v["line"] == "Watched"
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "what did they sing?"})
        assert r.status_code == 200
        system = captured[-1][0]["content"]
        assert "happy birthday to you" in system and "might show: a cake with candles" in system
        c.portal.call(_q, "DELETE FROM chat_video_jobs")


@requires_db
@needs_ffmpeg
async def test_a_core_stop_ends_the_job_at_once_and_it_stays_resumable(clips, no_other_jobs) -> None:
    j = await _stage(clips["short"])
    brains = FakeBrains()
    brains.gate = asyncio.Event()
    shutdown = asyncio.Event()
    task = asyncio.create_task(runner_mod.Runner(brains, quiet_sec=0.01, poll_sec=0.01).run_forever(shutdown))
    await asyncio.wait_for(brains.described.wait(), 90)
    t0 = time.monotonic()
    shutdown.set()
    await asyncio.wait_for(task, 5)
    assert time.monotonic() - t0 < 2
    assert brains.cancelled == 1
    row = await _job(j["job_id"])
    assert row["state"] == "running" and row["stage"] == "frames"
    assert limits.video_path(j["token"]) is not None
