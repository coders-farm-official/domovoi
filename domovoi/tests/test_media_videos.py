"""Media understanding, slice 3: videos (domovoi/media_understanding/videos.py
and moments.py): the scene scan, keyframes within the frame budget, the
quick pass then the deepen pass with resume, the two-hour transcript cap,
the timeline and summary, search_doc with moments, the faces hook (and the
faces-only re-queue), and the voice turn that cancels a frame's caption.

Vision, Whisper and the summary model are fakes; the clips are a few
seconds long, made with ffmpeg in tmp_path.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import ai_features, voice_activity
from domovoi.db.session import session_scope
from domovoi.media_understanding import budget, moments, switches, videos
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_media_understanding import (  # noqa: F401 — fixtures
    _items,
    _quiet_house,
    _runner,
    _sync,
    house,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="ffmpeg isn't installed")
NIGHT = datetime(2026, 11, 22, 23, 0)
#: Six different pictures (a flat colour would look the same as any other
#: to the near-duplicate check).
COLORS = ["testsrc", "smptebars", "rgbtestsrc", "testsrc2", "yuvtestsrc", "pal75bars"]


def make_clip(dest: Path, scenes: list[str], *, scene_s: float = 3.0, sound: bool = True) -> Path:
    """A tiny clip: one test picture per scene (a scene change at each
    boundary), and a tone that sounds for 2 s out of every 4 (speech for
    the speech finder)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = scene_s * len(scenes)
    argv = ["ffmpeg", "-v", "error", "-y"]
    for c in scenes:
        argv += ["-f", "lavfi", "-i", f"{c}=s=96x64:d={scene_s}:r=5"]
    graph = "".join(f"[{i}]" for i in range(len(scenes))) + f"concat=n={len(scenes)}:v=1:a=0[v]"
    if sound:
        argv += ["-f", "lavfi", "-i",
                 f"aevalsrc='if(between(mod(t,4),1,3),0.5*sin(2*PI*440*t),0)':s=16000:d={total}"]
    argv += ["-filter_complex", graph, "-map", "[v]"]
    if sound:
        argv += ["-map", f"{len(scenes)}", "-c:a", "aac"]
    argv += ["-c:v", "libx264", "-g", "5", "-pix_fmt", "yuv420p", str(dest)]
    subprocess.run(argv, check=True)
    return dest


class Fakes:
    """Vision, Whisper and the summary model."""

    def __init__(self) -> None:
        self.vision_calls = 0
        self.heard: list[int] = []
        self.written: list[str] = []
        self.on_vision = None

    async def vision(self, jpeg: bytes) -> str:
        self.vision_calls += 1
        if self.on_vision is not None:
            await self.on_vision(self.vision_calls)
        n = self.vision_calls
        tags = ["beach", "sand"] if n == 2 else ["kitchen"]
        return json.dumps({"caption": f"a‮ scene {n} on a sunny day", "tags": tags, "has_text": False})

    async def transcribe(self, pcm: bytes) -> str:
        self.heard.append(len(pcm))
        return f"we baked a cake number {len(self.heard)}"

    async def write(self, system: str, user: str, num_predict: int) -> str:
        self.written.append(user)
        return "The video might show a sunny day.\nTitle: A sunny day"


@pytest.fixture
def fakes() -> Fakes:
    f = Fakes()
    videos.install(vision=f.vision, transcribe=f.transcribe, write=f.write)
    return f


async def _gpu_bench() -> None:
    """A measured GPU house: 0.5 s a frame (so a 60 s clip gets 3 quick and
    more deep frames)."""
    async with session_scope() as s:
        await budget.save_benchmark(s, {"version": budget.BENCH_VERSION, "model": "vision:2b",
                                        "s_per_frame": 0.5, "tier": "gpu", "vram_bytes": 8 * 1024 ** 3})


async def _stage(item_id: int, name: str) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT state, pass_kind, progress, capped, error_code FROM media_item_stages "
            "WHERE item_id = :i AND analyser = :a"), {"i": item_id, "a": name})).mappings().first()
    return dict(row) if row else {}


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    async with session_scope() as s:
        return [dict(r) for r in (await s.execute(text(sql), params)).mappings().all()]


# ─── pure ─────────────────────────────────────────────────────────────────

def test_captions_and_tags_are_plain_text_and_a_bad_reply_is_kept_as_a_caption() -> None:
    seen = videos.parse_frame('{"caption": "a dog\\u202e <b>on</b>\\n a beach", "tags": ["Dog", "dog", "<x>", '
                              '"beach"], "has_text": "yes"}')
    assert seen == {"caption": "a dog <b>on</b> a beach", "tags": ["dog", "beach"], "has_text": True}
    assert videos.parse_frame("A cat on a sofa.") == {"caption": "A cat on a sofa.", "tags": [], "has_text": None}
    summary, title = videos.parse_summary("It might show a party.\n**Title:** Birthday ​party", "ABCD")
    assert summary == "It might show a party." and title == "Birthday party"
    assert videos.parse_summary("Just words. ABCD", "ABCD") == ("Just words.", None)


def test_the_face_hook_is_optional() -> None:
    # This branch works without slice 4: no module, no hook.
    if videos._faces_module() is None:
        assert videos.face_hook() is None and videos.faces_ready() is False


# ─── the database ─────────────────────────────────────────────────────────

@requires_db
@needs_ffmpeg
async def test_scenes_are_found_and_keyframes_stay_within_the_budget(house, fakes) -> None:
    make_clip(house.root / "trip.mp4", COLORS, scene_s=5.0)        # 30 s, 6 scenes
    await _sync(house)
    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["trip.mp4"]
    assert item["state"] == "done" and item["frame_pass"] == "deep" and item["duration_s"] == pytest.approx(30, 0.1)
    scan = (await _stage(item["id"], videos.SCAN))["progress"]
    assert [round(t) for t, _sc in scan["scenes"]] == [5, 10, 15, 20, 25]
    want = budget.frame_budget(30.0, 5, "cpu", "deep")[0]
    frames = await _rows("SELECT at_s, caption, tags FROM media_frames WHERE item_id = :i ORDER BY at_s",
                         i=item["id"])
    assert 0 < len(frames) <= want == 3
    assert fakes.vision_calls == len(frames)
    # At scene changes (a moment after each), never two in one scene.
    assert len({int(f["at_s"] // 5) for f in frames}) == len(frames)
    # Untrusted text: the bidi override is gone.
    assert all("‮" not in f["caption"] for f in frames)


@requires_db
@needs_ffmpeg
async def test_the_quick_pass_then_deepen_then_resume_after_a_stop(house, fakes, monkeypatch) -> None:
    monkeypatch.setattr(budget, "QUICK_BACKLOG", 1)        # one video waiting is a "big backlog"
    make_clip(house.root / "a.mp4", COLORS, scene_s=10.0, sound=False)     # 60 s
    await _sync(house)
    await _gpu_bench()
    quick = budget.frame_budget(60.0, 5, budget.Tier("gpu", 0.5, 8 * 1024 ** 3), "quick")[0]
    deep = budget.frame_budget(60.0, 5, budget.Tier("gpu", 0.5, 8 * 1024 ** 3), "deep")[0]
    assert quick < deep

    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["a.mp4"]
    assert item["frame_pass"] == "quick"
    assert fakes.vision_calls == quick
    assert (await _stage(item["id"], videos.FRAMES))["pass_kind"] == "quick"
    assert len(fakes.written) == 1

    # The deepen pass stops part way (the core restarts) ...
    async def stop(n: int) -> None:
        if n == quick + 2:
            raise asyncio.CancelledError()
    fakes.on_vision = stop
    with pytest.raises(asyncio.CancelledError):
        await r.step()
    assert (await _items())["a.mp4"]["state"] == "running"
    saved = await _rows("SELECT at_s FROM media_frames WHERE item_id = :i", i=item["id"])
    assert len(saved) == quick + 1

    # ... and carries on from what it saved: no frame is described twice.
    fakes.on_vision = None
    r2 = _runner(now=lambda: NIGHT)
    assert await r2.step()
    item = (await _items())["a.mp4"]
    frames = await _rows("SELECT idx, at_s FROM media_frames WHERE item_id = :i ORDER BY at_s", i=item["id"])
    assert item["state"] == "done" and item["frame_pass"] == "deep"
    assert len(frames) == deep
    assert sorted(f["idx"] for f in frames) == list(range(deep))
    assert fakes.vision_calls == deep + 1                   # the one cancelled call asked again
    assert all(b["at_s"] - a["at_s"] >= videos.SAME_MOMENT_S for a, b in zip(frames, frames[1:]))
    assert len(fakes.written) == 2                          # the summary written again after deepening
    assert not await r2.step()                              # nothing left


@requires_db
@needs_ffmpeg
async def test_the_transcript_stops_at_the_two_hour_cap(house, fakes, monkeypatch) -> None:
    monkeypatch.setattr(budget, "TRANSCRIPT_MAX_S", 6.0)    # "two hours", scaled to the clip
    monkeypatch.setattr(videos, "AUDIO_CHUNK_S", 4.0)       # and read in pieces
    monkeypatch.setattr(ai_features, "turned_off", lambda: True)   # transcripts aren't an AI feature
    make_clip(house.root / "talk.mp4", COLORS[:4], scene_s=3.0)   # 12 s
    await _sync(house)
    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["talk.mp4"]
    segs = await _rows("SELECT start_s, end_s, text FROM media_segments WHERE item_id = :i ORDER BY idx",
                       i=item["id"])
    assert segs and all(s["end_s"] <= 6.0 + 0.01 for s in segs)
    # The tone sounds from 1 s to 3 s and from 5 s: found by loudness, with times.
    assert segs[0]["start_s"] == pytest.approx(0.85, abs=0.2)
    st = await _stage(item["id"], videos.TRANSCRIPT)
    assert st["state"] == "done" and st["progress"]["capped_at_s"] == 6.0
    # With AI off the transcript still runs; captions and the summary don't.
    assert (await _stage(item["id"], videos.FRAMES))["error_code"] == "ai-off"
    assert fakes.vision_calls == 0 and not fakes.written


@requires_db
@needs_ffmpeg
async def test_the_timeline_summary_and_search_doc_with_moments(house, fakes) -> None:
    make_clip(house.root / "Trips/day.mp4", COLORS[:3], scene_s=3.0)
    await _sync(house)
    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["Trips/day.mp4"]
    async with session_scope() as s:
        line = await videos.timeline(s, item["id"])
        summ = (await s.execute(text("SELECT summary, suggested_title, model FROM media_summaries WHERE item_id = :i"),
                                {"i": item["id"]})).mappings().first()
        hit_words = (await s.execute(text(
            "SELECT search_doc @@ websearch_to_tsquery('english', 'beaches') AND "
            "search_doc @@ websearch_to_tsquery('english', 'cake') AND "
            "search_doc @@ websearch_to_tsquery('english', 'trips') FROM media_items WHERE id = :i"),
            {"i": item["id"]})).scalar()
        hits = [{"id": item["id"], "kind": "video"}, {"id": 999, "kind": "image"}]
        await moments.add_moments(s, hits, "beach")
        by_said = await moments.moments(s, [item["id"]], "cake number 2")
        none = await moments.moments(s, [item["id"]], "")
    assert summ["summary"] == "The video might show a sunny day." and summ["suggested_title"] == "A sunny day"
    assert len(line) == 3 and line[0]["start_s"] == 0.0 and line[-1]["end_s"] == pytest.approx(9.0, 0.1)
    assert all(a["end_s"] == b["start_s"] for a, b in zip(line, line[1:]))
    assert any(sc["said"] for sc in line) and line[0]["might_show"].startswith("a scene")
    assert hit_words is True
    # The frame tagged "beach" (the 2nd described) is where the hit jumps to.
    frames = await _rows("SELECT at_s, tags FROM media_frames WHERE item_id = :i ORDER BY idx", i=item["id"])
    beach = next(f["at_s"] for f in frames if "beach" in f["tags"])
    assert hits[0]["at_s"] == pytest.approx(beach) and hits[0]["moment"]["from"] == "frame"
    assert hits[0]["moment"]["might_show"].startswith("a scene") and "at_s" not in hits[1]
    seg = await _rows("SELECT start_s FROM media_segments WHERE item_id = :i AND text LIKE '%number 2'",
                      i=item["id"])
    assert by_said[item["id"]]["from"] == "transcript" and by_said[item["id"]]["at_s"] == pytest.approx(seg[0]["start_s"])
    assert none == {}
    # The summary model got the transcript and the frames inside the fence.
    assert "might show" in fakes.written[0] and "cake" in fakes.written[0]


class _FakeVideoFaces:
    calls: list[tuple[int, float, int]] = []

    def __init__(self, item_id: int) -> None:
        self.item_id = item_id

    @classmethod
    async def start(cls, item_id: int):
        async with session_scope() as s:
            if not (await switches.read(s))["faces"]:
                return None
        return cls(item_id)

    async def frame(self, at_s: float, image: Any, *, ctx: Any = None) -> int:
        assert isinstance(image, bytes) and image[:2] == b"\xff\xd8"      # a JPEG
        await ctx.model_step(lambda: asyncio.sleep(0), needs_ai=False)    # voice first, uncharged
        self.calls.append((self.item_id, at_s, len(image)))
        return 0


@requires_db
@needs_ffmpeg
async def test_the_face_hook_runs_only_when_faces_are_on_and_it_is_there(house, fakes, monkeypatch) -> None:
    _FakeVideoFaces.calls = []
    mod = SimpleNamespace(VideoFaces=_FakeVideoFaces, face_models=SimpleNamespace(ready=lambda: True))
    make_clip(house.root / "a.mp4", COLORS[:3], scene_s=3.0, sound=False)
    await _sync(house)
    r = _runner(now=lambda: NIGHT)

    # Faces on, but slice 4 isn't there: nothing to call, no stage.
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "faces": True})
    monkeypatch.setattr(videos, "_faces_module", lambda: None)
    assert await r.step()
    item = (await _items())["a.mp4"]
    assert await _stage(item["id"], videos.FACES) == {}
    calls_before = fakes.vision_calls

    # Faces off and the hook there: still nothing, and the re-queue waits.
    monkeypatch.setattr(videos, "_faces_module", lambda: mod)
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "faces": False})
    assert not await r.step()
    assert _FakeVideoFaces.calls == []

    # Faces turned on: the analysed video is queued again for a faces-only
    # pass (backlog: the night only), its keyframes through the hook in
    # time order; no caption, transcript or summary again.
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "faces": True})
        r.sw = await switches.read(s)
    await videos.faces_housekeeping(r)
    item = (await _items())["a.mp4"]
    assert item["state"] == "pending" and not item["new_arrival"]
    day = _runner(now=lambda: datetime(2026, 11, 22, 12, 0))
    assert not await day.step()                              # not in the daytime trickle
    assert await r.step()
    item = (await _items())["a.mp4"]
    assert item["state"] == "done" and item["frame_pass"] == "deep"
    times = [t for _i, t, _n in _FakeVideoFaces.calls]
    assert times and times == sorted(times) and len(times) >= 3
    assert fakes.vision_calls == calls_before and len(fakes.written) == 1
    assert (await _stage(item["id"], videos.FACES))["state"] == "done"
    await videos.faces_housekeeping(r)
    assert (await _items())["a.mp4"]["state"] == "done"      # queued once, not every step

    # Faces off: the faces passes are forgotten, so on again looks again.
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "faces": False})
        r.sw = await switches.read(s)
    await videos.faces_housekeeping(r)
    assert await _stage(item["id"], videos.FACES) == {}


@requires_db
@needs_ffmpeg
async def test_a_quick_passed_video_keeps_its_pass_through_a_faces_only_requeue(house, fakes, monkeypatch) -> None:
    monkeypatch.setattr(budget, "QUICK_BACKLOG", 1)
    make_clip(house.root / "a.mp4", COLORS[:2], scene_s=3.0, sound=False)
    await _sync(house)
    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["a.mp4"]
    assert item["frame_pass"] == "quick"
    _FakeVideoFaces.calls = []
    mod = SimpleNamespace(VideoFaces=_FakeVideoFaces, face_models=SimpleNamespace(ready=lambda: True))
    monkeypatch.setattr(videos, "_faces_module", lambda: mod)
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, "faces": True})
        r.sw = await switches.read(s)
    await videos.faces_housekeeping(r)
    # Another video waits for a first look, so this one isn't deepened yet.
    make_clip(house.root / "b.mp4", COLORS[:1], scene_s=2.0, sound=False)
    await _sync(house)
    async with session_scope() as s:
        await s.execute(text("UPDATE media_items SET mtime = 1 WHERE rel_path = 'b.mp4'"))
    assert await r.step()
    item = (await _items())["a.mp4"]
    assert _FakeVideoFaces.calls and item["frame_pass"] == "quick"


@requires_db
@needs_ffmpeg
async def test_a_voice_turn_cancels_a_frames_caption_which_is_asked_again(house, fakes) -> None:
    make_clip(house.root / "a.mp4", COLORS[:2], scene_s=3.0, sound=False)
    await _sync(house)
    started = asyncio.Event()

    async def slow(n: int) -> None:
        if n == 1:
            started.set()
            await asyncio.sleep(0.5)
    fakes.on_vision = slow
    r = _runner(now=lambda: NIGHT)

    async def a_turn():
        await started.wait()
        async with voice_activity.turn("satellite"):
            await asyncio.sleep(0.1)

    await asyncio.gather(r.step(), a_turn())
    item = (await _items())["a.mp4"]
    frames = await _rows("SELECT idx FROM media_frames WHERE item_id = :i", i=item["id"])
    assert r.voice_cancels >= 1
    assert fakes.vision_calls == len(frames) + 1            # the cancelled one asked again, saved once
    assert item["state"] == "done"


@requires_db
@needs_ffmpeg
async def test_an_unreadable_video_is_skipped_with_a_code(house, fakes) -> None:
    (house.root / "broken.mp4").write_bytes(b"not a video at all" * 100)
    await _sync(house)
    r = _runner(now=lambda: NIGHT)
    assert await r.step()
    item = (await _items())["broken.mp4"]
    assert item["state"] == "done"
    assert (await _stage(item["id"], videos.SCAN))["error_code"] == videos.CODE_UNREADABLE
    assert (await _stage(item["id"], videos.FRAMES))["state"] == "skipped"
    assert fakes.vision_calls == 0 and not fakes.heard


# ─── security review 2026-11-23: L51, L53 ────────────────────────────────


def test_library_videos_keep_to_an_allow_list_of_containers_and_codecs() -> None:
    from types import SimpleNamespace

    from domovoi.media_understanding import videos as V

    ok = SimpleNamespace(container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="h264")
    old = SimpleNamespace(container="avi", video_codec="mpeg4")
    assert V.library_format_ok(ok) and V.library_format_ok(old)
    for bad in (SimpleNamespace(container="concat", video_codec="h264"),
                SimpleNamespace(container="hls", video_codec="h264"),
                SimpleNamespace(container="image2", video_codec="png"),
                SimpleNamespace(container="mov,mp4", video_codec="prores_raw_exotic")):
        assert not V.library_format_ok(bad)


def test_library_pictures_go_only_to_a_local_vision_model(monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.media_understanding import photos as P

    monkeypatch.setattr(settings, "ollama_url", "http://localhost:11434")
    assert P.vision_is_local("qwen2.5vl:7b")
    assert not P.vision_is_local("qwen3-vl:235b-cloud") and not P.vision_is_local("x:cloud")
    for url in ("http://ollama:11434", "http://192.168.1.20:11434", "http://nas.local:11434"):
        monkeypatch.setattr(settings, "ollama_url", url)
        assert P.vision_is_local("llava"), url
    for url in ("https://ollama.example.com", "http://8.8.8.8:11434"):
        monkeypatch.setattr(settings, "ollama_url", url)
        assert not P.vision_is_local("llava"), url


async def test_a_remote_vision_model_skips_the_photo(monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.media_understanding import photos as P
    from domovoi.media_understanding import videos as V
    from domovoi.media_understanding.runner import Skip

    monkeypatch.setattr(settings, "ollama_vision_model", "llava")
    monkeypatch.setattr(settings, "ollama_url", "https://ollama.example.com")
    for fn in (P.ollama_vision, V.ollama_frame):
        with pytest.raises(Skip) as e:
            await fn(b"jpeg")
        assert e.value.code == P.CODE_VISION_NOT_LOCAL
