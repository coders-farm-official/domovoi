"""Videos (media understanding slice 3, design MEDIA-UNDERSTANDING §2):
the scenes of each library video, a transcript of what is said, a caption
and tags for keyframes chosen at scene changes, a timeline, and a summary.

The chat video passes (:mod:`domovoi.chat_video`), with library limits.
Five analysers on the runner (:mod:`.runner`), cheap work first:

* :class:`VideoScanAnalyser` (``video_scan``, order 10): ffprobe, then
  ffmpeg's scene filter (:func:`domovoi.chat_video.media.scene_changes`)
  in :data:`SCAN_CHUNK_S` pieces, each saved as it ends, so a restart
  carries on from the next piece. Sets ``media_items.duration_s``.
* :class:`VideoFacesAnalyser` (``video_faces``, order 15): only while the
  household's faces switch is on and slice 4's hook is there
  (:func:`face_hook`): every candidate frame through it.
* :class:`VideoTranscriptAnalyser` (``video_transcript``, order 20): the
  sound in :data:`AUDIO_CHUNK_S` pieces, the speech found in each
  (:func:`media.speech_spans`, no model) sent to the house's Whisper path
  in short pieces (as chat video does), each line saved with its times.
  The first :data:`budget.TRANSCRIPT_MAX_S` (two hours) of a video only.
  Transcripts run with AI features off (speech recognition isn't one).
* :class:`VideoFramesAnalyser` (``video_frames``, order 30, ``deepens``):
  keyframes within ``ctx.frame_budget`` for the pass: the quick pass
  (about a quarter of the frames while 25 or more videos wait for a first
  look), then the deepen pass adds frames up to the full budget, keeping
  the ones already described. Each keyframe gets one vision call (caption,
  tags, has-text) through ``ctx.model_step(vision=True)``: memory-gated,
  cancelled by a voice turn and asked again after it, counted against the
  per-video time budget. The frames are the timeline (:func:`timeline`).
* :class:`VideoSummaryAnalyser` (``video_summary``, order 40, ``deepens``):
  a whole-video summary and a suggested title by the chat model, written
  again after a deepen pass.

**Time.** The per-video time budget (``ctx.frame_budget``) is for the
frames, which is where the hours go on a CPU-only house. The transcript
has its own cap (two hours of video), and the faces and the one summary
call are small, so those three run outside it (:class:`_Uncharged`): a
long transcript never leaves a video with no frames.

**Disk.** ffmpeg reads the file itself (at ``ionice -c 3``, ``nice 19``,
one thread, held still during a voice turn). Each pass is paced through
the runner's 20 MB/s read bucket first (:func:`_pace`): the bytes a piece
of the video is expected to take are paid for before ffmpeg reads them,
so a two-hour film is read over minutes, never in one burst.

**Untrusted text.** A caption, a tag and a transcript line are words a
model made from a video anybody could have put in the library. They are
cleaned of control and format characters (:func:`clean_text`), cut to
length, stored as plain text, searched as words and shown as text, never
HTML; a caption is shown as "might show". They reach the summary's model
inside the chat video's fenced block (:func:`context.summary_prompt`).

Core-internal.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from domovoi import ai_features
from domovoi.chat_video import context, media, plan
from domovoi.db.session import session_scope
from domovoi.media_understanding import budget
from domovoi.media_understanding.runner import Item, Skip, StepContext, register

log = logging.getLogger(__name__)

#: The scene scan and the sound are read in pieces this long (seconds),
#: each saved as it ends (resume) and paced through the read bucket.
SCAN_CHUNK_S = 600.0
AUDIO_CHUNK_S = 300.0
#: A scene-scan piece starts this much before its window, so a change right
#: at the edge has a frame before it to compare with.
SCAN_OVERLAP_S = 1.0
#: The scan reads at most this much of a file, whatever its header says.
SCAN_MAX_S = 6 * 3600.0
#: A pass over a piece of a long video may take this long per minute of it.
PASS_TIMEOUT_PER_MIN_S = 60.0
#: Faces look at most this many candidate frames per video (the hook itself
#: samples one per 2 s at most).
FACE_CANDIDATES_MAX = 900
#: The deepen and quick passes grab this many candidates per frame they
#: keep, to drop the near-duplicates among them.
POOL_FACTOR = 2
#: Two frames this close (seconds) are the same moment.
SAME_MOMENT_S = 1.5
#: A frame grab reads about this many seconds of the video (pacing).
GRAB_READ_S = 2.0

CAPTION_MAX = 300
TAGS_MAX = 12
TAG_MAX = 40
SEGMENT_MAX = 2000
SUMMARY_MAX = 1500
TITLE_MAX = 80
FRAME_NUM_PREDICT = 200

CODE_UNREADABLE = "video-unreadable"
CODE_NO_TOOLS = "video-no-tools"
CODE_NO_PICTURE = "video-no-picture"
CODE_TOO_BIG = "video-too-big"
CODE_STT_OFF = "stt-off"
CODE_VISION_MISSING = "vision-model-missing"
CODE_FACES_OFF = "faces-off"

SCAN = "video_scan"
FACES = "video_faces"
TRANSCRIPT = "video_transcript"
FRAMES = "video_frames"
SUMMARY = "video_summary"

#: The vision model's instruction for one keyframe: the photo prompt's
#: keys, said of a frame of a home video.
FRAME_PROMPT = (
    "This is one frame from a home video, for a family's video search. Answer only with JSON with these keys: "
    '"caption": one short plain sentence saying what the frame shows; '
    '"tags": up to 12 plain lowercase nouns for the things, animals, places and events in it '
    '(like "dog", "beach", "cake", "snow"); '
    '"has_text": true if the frame has readable writing in it, otherwise false. '
    "Say only what you can see. Never guess who people are or give names. If the frame is blurry or dark, say so."
)


# ─── untrusted text ───────────────────────────────────────────────────────

def clean_text(value: Any, limit: int) -> str:
    """Plain text from a model: control and format characters gone (bidi
    overrides, zero-width marks), spaces collapsed, cut to ``limit``."""
    if not isinstance(value, str):
        return ""
    out = []
    for ch in value:
        if ch in "\t\r\n":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    return s[:limit].rstrip() if len(s) > limit else s


_TAG_OK = re.compile(r"^[\w][\w '\-]*$", re.UNICODE)


def clean_tags(value: Any) -> list[str]:
    """At most 12 short plain tags, lowercased, without repeats."""
    if isinstance(value, str):
        value = re.split(r"[,;]", value)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        t = clean_text(v, TAG_MAX).lower().strip(" .'-")
        if not t or not _TAG_OK.match(t) or len(t.split()) > 3 or t in out:
            continue
        out.append(t)
        if len(out) >= TAGS_MAX:
            break
    return out


def parse_frame(raw: str) -> dict[str, Any]:
    """``{"caption", "tags", "has_text"}`` from the model's reply. A reply
    that isn't the JSON asked for is kept as the caption alone."""
    raw = (raw or "").strip()
    doc: Any = None
    try:
        doc = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                doc = json.loads(m.group(0))
            except ValueError:
                doc = None
    if not isinstance(doc, dict):
        return {"caption": clean_text(raw, CAPTION_MAX), "tags": [], "has_text": None}
    has_text = doc.get("has_text")
    if isinstance(has_text, str):
        has_text = has_text.strip().lower() in ("true", "yes", "1")
    return {"caption": clean_text(doc.get("caption"), CAPTION_MAX), "tags": clean_tags(doc.get("tags")),
            "has_text": bool(has_text) if has_text is not None else None}


def parse_summary(raw: str, code: str) -> tuple[str, str | None]:
    """(summary, suggested title) from the summary model's reply: the
    title is its last line when it starts ``Title:``."""
    body = context.strip_fence(raw or "", code)
    title = None
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if lines and re.match(r"^\s*\**\s*title\s*\**\s*:", lines[-1], re.I):
        title = clean_text(lines[-1].split(":", 1)[1].strip(" *\"'"), TITLE_MAX) or None
        lines = lines[:-1]
    return clean_text(" ".join(lines), SUMMARY_MAX), title


# ─── the face hook (slice 4) ──────────────────────────────────────────────

def _faces_module() -> Any:
    try:
        from domovoi.media_understanding import faces  # type: ignore[attr-defined]
    except ImportError:
        return None
    return faces


def face_hook() -> Any:
    """Slice 4's per-keyframe hook, or None when it isn't there.

    The contract (wf/media-s4-faces)::

        vf = await faces.VideoFaces.start(item_id)    # None: faces off / no models
        await vf.frame(at_s, image, ctx=ctx)          # per keyframe, in time order;
                                                      # image: JPEG bytes or a Pillow image

    It samples at most one frame per 2 s itself, keeps one row per
    appearance (sightings within 30 s), and resumes after its last saved
    moment. This is the one place the call is made from."""
    mod = _faces_module()
    hook = getattr(mod, "VideoFaces", None) if mod is not None else None
    return hook if hook is not None and hasattr(hook, "start") else None


def faces_ready() -> bool:
    """The face models are on disk (slice 4's ``face_models.ready()``)."""
    mod = _faces_module()
    ready = getattr(getattr(mod, "face_models", None), "ready", None)
    return bool(ready()) if callable(ready) else mod is not None


# ─── helpers ──────────────────────────────────────────────────────────────

class _Uncharged:
    """A ``ctx`` whose model steps stay voice-first (wait for quiet,
    cancelled by a turn, asked again after it) but don't count against the
    per-video frame budget: the transcript (its own two-hour cap), the
    faces and the summary."""

    def __init__(self, ctx: StepContext) -> None:
        self._ctx = ctx

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    async def model_step(self, make: Callable[[], Awaitable[Any]], *, vision: bool = False,
                         needs_ai: bool = True) -> Any:
        ctx = self._ctx
        if needs_ai and ai_features.turned_off():
            raise Skip("ai-off")
        if vision:
            await ctx.runner.wait_for_memory(ctx.item)
            ctx.runner.vision_used = True
        return await ctx.runner.model_step(ctx.item, make)


async def _stage_progress(item_id: int, analyser: str) -> dict[str, Any] | None:
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT state, progress FROM media_item_stages WHERE item_id = :i AND analyser = :a"),
            {"i": item_id, "a": analyser})).first()
    if row is None:
        return None
    p = row[1]
    return p if isinstance(p, dict) else json.loads(p or "{}")


async def _scan(item: Item) -> dict[str, Any]:
    """The scan's saved result; :class:`Skip` with its code when the video
    couldn't be read."""
    p = await _stage_progress(item.id, SCAN) or {}
    if p.get("skip"):
        raise Skip(str(p["skip"]))
    if not p.get("done"):
        raise Skip(CODE_UNREADABLE)
    return p


async def _pace(ctx: Any, seconds: float, duration: float) -> None:
    """Pay the read bucket for what ffmpeg is about to read: ``seconds`` of
    a ``duration``-long file, at its average rate."""
    if duration <= 0:
        return
    n = int(ctx.item.size_bytes * min(1.0, max(0.0, seconds) / duration))
    await ctx.runner.bucket.take(n)


def _timeout(seconds: float) -> float:
    return max(media.PASS_TIMEOUT_SEC, seconds / 60.0 * PASS_TIMEOUT_PER_MIN_S)


async def _grab(ctx: Any, scan: dict[str, Any], at: float, dest: Path, *, charge: bool = True) -> bytes | None:
    """One frame at ``at`` as JPEG (at most 768 px wide), paced and held
    still during a voice turn; None when it couldn't be grabbed. With
    ``charge`` the grab counts against the per-video time budget."""
    if not dest.is_file():
        assert ctx.item.path is not None
        await _pace(ctx, GRAB_READ_S, float(scan["duration"]))
        await ctx.runner.wait_for_quiet(ctx.item)
        dest.parent.mkdir(parents=True, exist_ok=True)
        grab = media.grab_frame(ctx.item.path, at, dest, video_index=scan.get("video_index"), hold=ctx.hold())
        if charge:
            async with ctx.charge():
                ok = await grab
        else:
            ok = await grab
        if not ok:
            return None
    return dest.read_bytes()


async def _refresh_doc(s: Any, item_id: int) -> None:
    """Rebuild the item's ``search_doc`` (slice 2's writer: frames, words, summary)."""
    from domovoi.media_understanding.search import refresh_doc

    await refresh_doc(s, item_id)


# ─── the vision call ──────────────────────────────────────────────────────

Vision = Callable[[bytes], Awaitable[str]]


def vision_model() -> str:
    from domovoi.config import settings

    return (settings.ollama_vision_model or "").strip()


async def ollama_frame(jpeg: bytes) -> str:
    """The household's vision slot on one keyframe (:data:`FRAME_PROMPT`)."""
    from domovoi.clients import ollama
    from domovoi.config import settings

    model = vision_model()
    if not model:
        raise Skip(CODE_VISION_MISSING)
    msgs = [{"role": "user", "content": FRAME_PROMPT, "images": [base64.b64encode(jpeg).decode("ascii")]}]
    out: list[str] = []
    try:
        async for d in ollama.chat_stream(msgs, model=model, think=False,
                                          num_ctx=settings.ollama_vision_num_ctx or None,
                                          num_predict=FRAME_NUM_PREDICT, temperature=0.2):
            out.append(d)
    except ollama.OllamaChatError as e:
        if e.status == 404 or "not found" in e.detail.lower():
            raise Skip(CODE_VISION_MISSING) from None
        raise
    return "".join(out)


# ─── the analysers ────────────────────────────────────────────────────────

class VideoScanAnalyser:
    """ffprobe, then the scene changes, a piece at a time."""

    name = SCAN
    kinds = frozenset({"video"})
    order = 10
    deepens = False

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def run(self, item: Item, ctx: StepContext) -> None:
        assert item.path is not None
        p = dict(ctx.progress)
        if "duration" not in p:
            try:
                async with ctx.charge():
                    probed = await media.probe(item.path)
            except media.ProbeFailed as e:
                code = CODE_NO_TOOLS if e.missing_tools else CODE_UNREADABLE
                await ctx.save({"skip": code})
                raise Skip(code) from None
            sizes = probed.sizes or ((probed.width or 0, probed.height or 0),)
            code = None
            if probed.video_codec is None or probed.duration <= 0:
                code = CODE_NO_PICTURE
            elif not all(media.picture_fits(sz) for sz in sizes):
                code = CODE_TOO_BIG
            if code:
                await ctx.save({"skip": code})
                raise Skip(code)
            p = {"duration": round(min(probed.duration, SCAN_MAX_S), 2), "video_index": probed.video_index,
                 "audio_index": probed.audio_index, "has_audio": probed.has_audio,
                 "scenes": [], "scanned_to": 0.0}
            async with session_scope() as s:
                await s.execute(text("UPDATE media_items SET duration_s = :d WHERE id = :i"),
                                {"d": p["duration"], "i": item.id})
            item.duration_s = p["duration"]
            await ctx.save(p)
        duration = float(p["duration"])
        while float(p["scanned_to"]) < duration:
            a = float(p["scanned_to"])
            b = min(duration, a + SCAN_CHUNK_S)
            start = max(0.0, a - SCAN_OVERLAP_S) if a > 0 else 0.0
            await _pace(ctx, b - start, duration)
            await ctx.runner.wait_for_quiet(item)
            try:
                async with ctx.charge():
                    found = await media.scene_changes(item.path, video_index=p.get("video_index"),
                                                      hold=ctx.hold(), start=start, seconds=b - start,
                                                      timeout=_timeout(b - start))
            except FileNotFoundError:
                await ctx.save({"skip": CODE_NO_TOOLS})
                raise Skip(CODE_NO_TOOLS) from None
            except RuntimeError:
                await ctx.save({"skip": CODE_UNREADABLE})
                raise Skip(CODE_UNREADABLE) from None
            p["scenes"] += [[t, sc] for t, sc in found if a <= t < b]
            p["scanned_to"] = b
            await ctx.save(p)
        p["done"] = True
        await ctx.save(p)


class VideoFacesAnalyser:
    """Every candidate frame through slice 4's hook (faces on only)."""

    name = FACES
    kinds = frozenset({"video"})
    order = 15
    deepens = False

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return bool(sw.get("faces")) and face_hook() is not None and faces_ready()

    async def run(self, item: Item, ctx: StepContext) -> None:
        scan = await _scan(item)
        hook = face_hook()
        vf = await hook.start(item.id) if hook is not None else None
        if vf is None:
            raise Skip(CODE_FACES_OFF)
        times = plan.spread(plan.candidate_times(float(scan["duration"]), scan["scenes"]), FACE_CANDIDATES_MAX)
        done_to = ctx.progress.get("done_to")
        free = _Uncharged(ctx)
        looked = int(ctx.progress.get("frames") or 0)
        for t in times:
            if done_to is not None and t <= float(done_to):
                continue
            dest = ctx.work_dir / f"face-{t:.2f}.jpg"
            jpeg = await _grab(ctx, scan, t, dest, charge=False)
            if jpeg is not None:
                await vf.frame(t, jpeg, ctx=free)
                looked += 1
            dest.unlink(missing_ok=True)
            await ctx.save({"done_to": t, "frames": looked})


class VideoTranscriptAnalyser:
    """What is said, with times: the first two hours."""

    name = TRANSCRIPT
    kinds = frozenset({"video"})
    order = 20
    deepens = False

    def __init__(self, transcribe: Callable[[bytes], Awaitable[str]] | None = None) -> None:
        self.transcribe = transcribe

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def run(self, item: Item, ctx: StepContext) -> None:
        assert item.path is not None
        scan = await _scan(item)
        if not scan.get("has_audio"):
            await ctx.save({"no_sound": True})
            return
        duration = float(scan["duration"])
        end = min(duration, budget.TRANSCRIPT_MAX_S)
        p = dict(ctx.progress)
        a = float(p.get("chunk_from") or 0.0)
        skip = int(p.get("pieces_done") or 0)
        idx = int(p.get("next_idx") or 0)
        transcribe = self.transcribe or ctx.runner.brains.transcribe
        free = _Uncharged(ctx)
        while a < end:
            b = min(end, a + AUDIO_CHUNK_S)
            pcm_file = ctx.work_dir / "sound.pcm"
            ctx.work_dir.mkdir(parents=True, exist_ok=True)
            await _pace(ctx, b - a, duration)
            await ctx.runner.wait_for_quiet(item)
            ok = await media.extract_audio(item.path, pcm_file, audio_index=scan.get("audio_index"), hold=ctx.hold(),
                                           start=a, seconds=b - a, timeout=_timeout(b - a))
            pieces: list[tuple[float, float]] = []
            pcm = b""
            if ok:
                pcm = await asyncio.to_thread(media.read_pcm, pcm_file, seconds=b - a)
                pcm_file.unlink(missing_ok=True)
                levels = await asyncio.to_thread(media.window_levels, pcm)
                pieces = media.speech_pieces(media.speech_spans(levels), levels)
            for j, (s0, s1) in enumerate(pieces):
                if j < skip:
                    continue
                piece = media.pcm_slice(pcm, s0, s1)
                try:
                    said = await free.model_step(lambda piece=piece: transcribe(piece), needs_ai=False)
                except Exception as e:  # noqa: BLE001
                    from domovoi.clients.whisper import SttUnavailableError

                    if isinstance(e, SttUnavailableError):
                        raise Skip(CODE_STT_OFF) from None
                    raise
                words = clean_text(said, SEGMENT_MAX)
                if words:
                    async with session_scope() as s:
                        await s.execute(text(
                            "INSERT INTO media_segments (item_id, idx, start_s, end_s, text) "
                            "VALUES (:i, :n, :a, :b, :t) ON CONFLICT (item_id, idx) DO UPDATE SET "
                            "start_s = EXCLUDED.start_s, end_s = EXCLUDED.end_s, text = EXCLUDED.text"),
                            {"i": item.id, "n": idx, "a": round(a + s0, 2), "b": round(a + s1, 2), "t": words})
                    idx += 1
                await ctx.save({"chunk_from": a, "pieces_done": j + 1, "next_idx": idx})
            async with session_scope() as s:
                await _refresh_doc(s, item.id)
            a, skip = b, 0
            await ctx.save({"chunk_from": a, "pieces_done": 0, "next_idx": idx})
        out = {"chunk_from": a, "pieces_done": 0, "next_idx": idx, "lines": idx}
        if duration > budget.TRANSCRIPT_MAX_S:
            out["capped_at_s"] = budget.TRANSCRIPT_MAX_S
        await ctx.save(out)


class VideoFramesAnalyser:
    """Keyframes at scene changes within the pass's frame budget, each
    described by the vision model."""

    name = FRAMES
    kinds = frozenset({"video"})
    order = 30
    deepens = True

    def __init__(self, vision: Vision | None = None, model_name: Callable[[], str] | None = None) -> None:
        self.vision = vision or ollama_frame
        self.model_name = model_name or vision_model

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def _described(self, item_id: int) -> dict[int, float]:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT idx, at_s FROM media_frames WHERE item_id = :i AND at_s IS NOT NULL"),
                {"i": item_id})).all()
        return {int(r[0]): float(r[1]) for r in rows}

    async def _pick(self, ctx: StepContext, scan: dict[str, Any], want: int, have: list[float]) -> list[float]:
        """``want`` keyframes at scene changes (a fill frame across long
        stretches without one), away from the ``have`` already described,
        near-duplicates dropped."""
        need = want - len(have)
        if need <= 0:
            return []
        times = [t for t in plan.candidate_times(float(scan["duration"]), scan["scenes"])
                 if all(abs(t - h) >= SAME_MOMENT_S for h in have)]
        pool = plan.spread(times, need * POOL_FACTOR)
        hashes: list[int | None] = []
        for t in pool:
            dest = ctx.work_dir / f"frame-{t:.2f}.jpg"
            jpeg = await _grab(ctx, scan, t, dest)
            hashes.append(await asyncio.to_thread(media.dhash, dest) if jpeg is not None else None)
        return plan.spread(plan.drop_same(pool, hashes), need)

    async def run(self, item: Item, ctx: StepContext) -> None:
        scan = await _scan(item)
        kind = ctx.pass_kind or "deep"
        want, _time_s = await ctx.frame_budget(float(scan["duration"]), len(scan["scenes"]))
        described = await self._described(item.id)
        p = dict(ctx.progress)
        if p.get("pass") != kind or not isinstance(p.get("picked"), list):
            picked = await self._pick(ctx, scan, want, sorted(described.values()))
            p = {"pass": kind, "budget": want, "picked": picked, "described": len(described)}
            await ctx.save(p)
        idx = max(described, default=-1) + 1
        done = [round(t, 2) for t in described.values()]
        model = self.model_name() or None
        for t in p["picked"]:
            if round(t, 2) in done:
                continue
            dest = ctx.work_dir / f"frame-{t:.2f}.jpg"
            jpeg = await _grab(ctx, scan, t, dest)
            if jpeg is None:
                continue
            reply = await ctx.model_step(lambda jpeg=jpeg: self.vision(jpeg), vision=True)
            seen = parse_frame(reply)
            async with session_scope() as s:
                await s.execute(text(
                    "INSERT INTO media_frames (item_id, idx, at_s, caption, tags, has_text, model) "
                    "VALUES (:i, :n, :t, :c, :g, :h, :m) ON CONFLICT (item_id, idx) DO UPDATE SET "
                    "at_s = EXCLUDED.at_s, caption = EXCLUDED.caption, tags = EXCLUDED.tags, "
                    "has_text = EXCLUDED.has_text, model = EXCLUDED.model"),
                    {"i": item.id, "n": idx, "t": t, "c": seen["caption"], "g": seen["tags"],
                     "h": seen["has_text"], "m": model})
                if model:
                    await s.execute(text("UPDATE media_items SET vision_model = :m WHERE id = :i"),
                                    {"m": model, "i": item.id})
                await _refresh_doc(s, item.id)
            dest.unlink(missing_ok=True)
            idx += 1
            done.append(round(t, 2))
            p["described"] = len(done)
            await ctx.save(p)


class VideoSummaryAnalyser:
    """A summary of the whole video and a suggested title."""

    name = SUMMARY
    kinds = frozenset({"video"})
    order = 40
    deepens = True

    def __init__(self, write: Callable[[str, str, int], Awaitable[str]] | None = None,
                 model_name: Callable[[], str] | None = None) -> None:
        self.write = write
        self.model_name = model_name

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def run(self, item: Item, ctx: StepContext) -> None:
        scan = await _scan(item)
        async with session_scope() as s:
            segs = (await s.execute(text(
                "SELECT start_s, end_s, text FROM media_segments WHERE item_id = :i ORDER BY idx"),
                {"i": item.id})).mappings().all()
            frames = (await s.execute(text(
                "SELECT at_s, caption FROM media_frames WHERE item_id = :i AND at_s IS NOT NULL ORDER BY at_s"),
                {"i": item.id})).mappings().all()
        if not segs and not any(f["caption"] for f in frames):
            await ctx.save({"nothing": True})
            return
        name = item.rel_path.rsplit("/", 1)[-1]
        system, user, code = context.summary_prompt(
            name, float(scan["duration"]), [dict(r) for r in segs],
            [{"at_s": f["at_s"], "description": f["caption"]} for f in frames])
        user += "\nThen, on its own last line, write \"Title:\" and a short title for the video of at most eight words."
        write = self.write or ctx.runner.brains.write
        raw = await _Uncharged(ctx).model_step(lambda: write(system, user, context.SUMMARY_NUM_PREDICT + 30))
        summary, title = parse_summary(raw, code)
        if not summary:
            await ctx.save({"nothing": True})
            return
        model = self.model_name() if self.model_name else _chat_model()
        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO media_summaries (item_id, summary, suggested_title, model) VALUES (:i, :s, :t, :m) "
                "ON CONFLICT (item_id) DO UPDATE SET summary = EXCLUDED.summary, "
                "suggested_title = EXCLUDED.suggested_title, model = EXCLUDED.model, created_at = now()"),
                {"i": item.id, "s": summary, "t": title, "m": model})
            await _refresh_doc(s, item.id)
        await ctx.save({"frames": len(frames), "lines": len(segs)})


def _chat_model() -> str | None:
    from domovoi.config import settings

    return (settings.ollama_model or "").strip() or None


# ─── reading: the timeline ────────────────────────────────────────────────

async def timeline(s: Any, item_id: int) -> list[dict[str, Any]]:
    """A video's scenes in order: each described keyframe starts one,
    which lasts until the next (or the end). ``might_show`` and ``tags``
    are a model's words; ``said`` is the transcript inside the scene. All
    untrusted text, shown as text."""
    dur = (await s.execute(text("SELECT duration_s FROM media_items WHERE id = :i"), {"i": item_id})).scalar()
    frames = (await s.execute(text(
        "SELECT at_s, caption, tags FROM media_frames WHERE item_id = :i AND at_s IS NOT NULL ORDER BY at_s"),
        {"i": item_id})).mappings().all()
    segs = (await s.execute(text(
        "SELECT start_s, end_s, text FROM media_segments WHERE item_id = :i ORDER BY start_s"),
        {"i": item_id})).mappings().all()
    out = []
    for n, f in enumerate(frames):
        start = 0.0 if n == 0 else float(f["at_s"])
        end = float(frames[n + 1]["at_s"]) if n + 1 < len(frames) else float(dur or f["at_s"])
        last = n + 1 == len(frames)
        said = [g["text"] for g in segs
                if start <= float(g["start_s"]) and (last or float(g["start_s"]) < end)]
        out.append({"start_s": round(start, 2), "end_s": round(end, 2), "at_s": float(f["at_s"]),
                    "might_show": f["caption"] or None, "tags": list(f["tags"] or []), "said": said})
    return out


# ─── faces turned on: the videos already analysed get a faces-only pass ───

async def requeue_for_faces(s: Any) -> int:
    """Videos analysed while faces were off (or before the models came):
    queued again as backlog (night only: not new arrivals); only the
    ``video_faces`` stage runs, the others are done."""
    r = await s.execute(text(
        "UPDATE media_items i SET state = 'pending', attempts = 0, updated_at = now() "
        "WHERE i.kind = 'video' AND i.state = 'done' AND NOT EXISTS ("
        "SELECT 1 FROM media_item_stages st WHERE st.item_id = i.id AND st.analyser = :a)"),
        {"a": FACES})
    return int(r.rowcount or 0)


async def faces_housekeeping(runner: Any) -> None:
    """Each library step: faces on (hook and models there) queues the
    videos without a faces pass; faces off forgets which videos had one,
    so turning faces on again looks at them all again (slice 4 deletes
    the face rows themselves)."""
    async with session_scope() as s:
        if not runner.sw.get("faces"):
            await s.execute(text("DELETE FROM media_item_stages WHERE analyser = :a"), {"a": FACES})
            return
        if face_hook() is None or not faces_ready():
            return
        n = await requeue_for_faces(s)
    if n:
        log.info("faces: %d video%s queued for a faces pass", n, "" if n == 1 else "s")


def install(vision: Vision | None = None, transcribe: Callable[[bytes], Awaitable[str]] | None = None,
            write: Callable[[str, str, int], Awaitable[str]] | None = None) -> None:
    """Register the video analysers on the runner (the worker does at
    start), and the faces re-queue where the runner has housekeeping."""
    from domovoi.media_understanding import runner

    register(VideoScanAnalyser())
    register(VideoFacesAnalyser())
    register(VideoTranscriptAnalyser(transcribe=transcribe))
    register(VideoFramesAnalyser(vision=vision))
    register(VideoSummaryAnalyser(write=write))
    hk = getattr(runner, "register_housekeeping", None)
    if callable(hk):
        hk("video_faces", faces_housekeeping)
