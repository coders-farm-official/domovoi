"""The core's chat-video jobs: one at a time, low priority, voice first.

**Voice answers must never slow down.** That is this module's first job,
before watching any video:

* Before every step (a scan, one 9-second piece of speech, one frame, the
  summary) the job waits until no voice turn is under way
  (:func:`domovoi.voice_activity.busy`, which counts from the wake word to
  the end of the reply), then for :data:`QUIET_SEC` more, because a
  follow-up question often comes straight after a reply.
* A step that asks a model the house's voice turns also need (a frame's
  description, the summary) is **cancelled** the moment a turn starts, and
  asked again after it: Ollama stops working on a request whose caller
  went, so the turn never waits behind a frame. A cancelled step is never
  saved, so nothing half-done is kept.
* An ffmpeg pass (the scene scan, the sound) runs at the lowest CPU
  priority and is **held still** (suspended) while a turn runs, then let go.
* **Stop works at every step** (DOC-9): before each step, quiet house or
  not, and about once a second while an ffmpeg pass runs (the pass is
  ended at once).
* **What a pass reads is bounded by what was accepted** (DOC-7/DOC-8): each
  ffmpeg pass reads at most :func:`limits.pass_seconds` of the job's
  accepted duration and only the streams the upload's probe checked, runs
  memory-capped on one thread (:mod:`.media`), and the sound file is read
  only up to the size that duration makes.
* A piece of speech is short enough (:data:`limits.SPEECH_PIECE_SEC`) to
  finish within about a second, while the person is still talking after
  the wake word, so the turn's own speech recognition doesn't wait for it.

**One job at a time** across the house: the runner takes the oldest queued
job, and the next only when that one has ended.

**Every result is saved the moment it is done**: pass 0's plan, each
transcript piece, each frame's description, the summary. A job the core
lost to a restart is taken again at boot and carries on from the last
saved step (its ``attempts`` counts the starts; past
:data:`limits.MAX_ATTEMPTS` it fails cleanly with a plain message instead
of looping on a video that stops the server). A job whose video is gone
fails cleanly too.

**When it ends** (done, failed or stopped), the video file and every frame
grab and sound file are deleted, and the reply is written into the chat.

This is a core-internal job runner. It is not offered to plugins.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from domovoi import ai_features
from domovoi.chat_video import context, limits, media, plan
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.voice_first import POLL_SEC, QUIET_SEC, STEP_TRIES, VoiceFirst  # noqa: F401

log = logging.getLogger(__name__)

#: How often a running step checks whether the person pressed stop.
CANCEL_CHECK_SEC = 1.0
#: How often the runner looks for a new job when it has none.
IDLE_POLL_SEC = 2.0


class Brains(Protocol):
    """The models a job asks (stubbed under ``USE_STUBS``)."""

    async def transcribe(self, pcm: bytes) -> str: ...

    async def describe(self, jpeg: bytes) -> str: ...

    async def write(self, system: str, user: str, num_predict: int) -> str: ...

    async def release_vision(self) -> None: ...


class RealBrains:
    """The server's speech model (clients/whisper.py) and Ollama: the chat's
    vision slot for frames, the chat model for the summary."""

    async def transcribe(self, pcm: bytes) -> str:
        from domovoi.clients.whisper import get_whisper_client

        return (await get_whisper_client().transcribe(pcm)).strip()

    async def describe(self, jpeg: bytes) -> str:
        from domovoi.clients import ollama

        model = settings.ollama_vision_model
        if not (model or "").strip():
            raise ModelMissing("vision_model_missing")
        msgs = [{"role": "user", "content": context.FRAME_PROMPT,
                 "images": [base64.b64encode(jpeg).decode("ascii")]}]
        out: list[str] = []
        try:
            async for d in ollama.chat_stream(msgs, model=model, think=False,
                                              num_ctx=settings.ollama_vision_num_ctx or None,
                                              num_predict=context.FRAME_NUM_PREDICT, temperature=0.2):
                out.append(d)
        except ollama.OllamaChatError as e:
            if e.status == 404 or "not found" in e.detail.lower():
                raise ModelMissing("vision_model_missing") from None
            raise
        return "".join(out).strip()

    async def write(self, system: str, user: str, num_predict: int) -> str:
        from domovoi.clients import ollama

        model = settings.ollama_model
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out: list[str] = []
        try:
            async for d in ollama.chat_stream(msgs, model=model, num_predict=num_predict):
                out.append(d)
        except ollama.OllamaChatError as e:
            if e.status == 404 or "not found" in e.detail.lower():
                raise ModelMissing("model_missing") from None
            raise
        return "".join(out).strip()

    async def release_vision(self) -> None:
        """Let the vision model go from memory once the frames are done, so
        it doesn't crowd out the voice models on a small server. Best
        effort; the voice and chat models are never touched."""
        model = (settings.ollama_vision_model or "").strip()
        if not model or model in {settings.ollama_model, settings.ollama_tool_model}:
            return
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as c:
                await c.post(f"{settings.ollama_url.rstrip('/')}/api/generate",
                             json={"model": model, "keep_alive": 0})
        except Exception as e:  # noqa: BLE001
            log.debug("chat video: couldn't release the vision model: %s", type(e).__name__)


class StubBrains:
    """Deterministic stand-ins (``USE_STUBS=true``): no model is asked."""

    async def transcribe(self, pcm: bytes) -> str:
        return f"stub words for {len(pcm) // 32000} seconds"

    async def describe(self, jpeg: bytes) -> str:
        return f"a stub picture of {len(jpeg) // 1024} KB"

    async def write(self, system: str, user: str, num_predict: int) -> str:
        return "A stub summary of the video."

    async def release_vision(self) -> None:
        return None


def default_brains() -> Brains:
    return StubBrains() if settings.use_stubs else RealBrains()


class ModelMissing(Exception):
    """A model the job needs isn't installed (its help-desk code)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JobFailed(Exception):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or context.failure_text(code)


class _Cancelled(Exception):
    """The person pressed stop."""


class _Gone(Exception):
    """The job's row went (its chat was deleted)."""


@dataclass
class Job:
    id: str
    thread_id: int
    message_id: int | None
    token: str
    name: str
    question: str
    duration: float
    probe: dict[str, Any]
    stage: str | None
    plan: dict[str, Any] | None
    progress: dict[str, Any]
    attempts: int
    stats: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    estimate_s: float | None = None
    #: Measured seconds for each kind of step (this job).
    timings: dict[str, list[float]] = field(default_factory=dict)
    #: Speeds this server measured on earlier jobs.
    server_speeds: dict[str, float] = field(default_factory=dict)
    last_cancel_check: float = 0.0
    waiting: bool = False

    @classmethod
    def from_row(cls, r: Any) -> "Job":
        def js(v: Any, default: Any) -> Any:
            if v is None:
                return default
            return json.loads(v) if isinstance(v, str) else v
        return cls(id=r["id"], thread_id=int(r["thread_id"]), message_id=r["message_id"], token=r["token"],
                   name=r["name"], question=r["question"] or "", duration=float(r["duration_s"]),
                   probe=js(r["probe"], {}), stage=r["stage"], plan=js(r["plan"], None),
                   progress=js(r["progress"], {}), attempts=int(r["attempts"] or 0), stats=js(r["stats"], {}),
                   notes=list(js(r["notes"], [])), estimate_s=r["estimate_s"])

    # ── speed ───────────────────────────────────────────────────────────
    def note_time(self, kind: str, seconds: float, per: float = 1.0) -> None:
        if per > 0:
            self.timings.setdefault(kind, []).append(seconds / per)

    def speeds(self) -> dict[str, float]:
        out = {**plan.GUESS, **self.server_speeds}
        for k, vals in self.timings.items():
            # A step too quick for the clock to see (0 s) says nothing.
            if vals and sum(vals) > 0:
                out[k] = sum(vals) / len(vals)
        return out

    def counts(self) -> plan.Counts:
        p = self.plan or {}
        frames = len(p["frames"]) if isinstance(p.get("frames"), list) else None
        speech = float(p.get("speech_s") or 0.0) if p else None
        return plan.Counts(duration=self.duration, speech_s=speech, frames=frames)

    def eta(self) -> float:
        p = self.plan or {}
        pieces = p.get("pieces") or []
        done_n = int(self.progress.get("pieces_done") or 0)
        done_s = sum(b - a for a, b in pieces[:done_n])
        return plan.estimate(self.counts(), self.speeds(), done={
            "scan": self.plan is not None,
            "transcribed_s": done_s,
            "frames": int(self.progress.get("frames_done") or 0),
            "combine": self.stage == "done",
        })


# ─── The database ───────────────────────────────────────────────────────
_COLS = ("id, thread_id, message_id, token, name, question, duration_s, probe, stage, plan, progress, "
         "attempts, stats, notes, estimate_s, state, cancel_requested")


async def server_speeds() -> dict[str, float]:
    """The speeds this server measured on its last finished jobs."""
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT stats FROM chat_video_jobs WHERE state = 'done' AND stats ? 'speeds' "
                "ORDER BY finished_at DESC NULLS LAST LIMIT 10"))).scalars().all()
    except Exception as e:  # noqa: BLE001 — no estimate beats no job
        log.warning("chat video: speeds unreadable: %s", type(e).__name__)
        return {}
    return plan.speeds_from_stats([r if isinstance(r, dict) else json.loads(r) for r in rows])


async def _update(job: Job, **cols: Any) -> None:
    """Save ``cols`` on the job's row; :class:`_Gone` when it's gone."""
    sets = []
    params: dict[str, Any] = {"id": job.id}
    for k, v in cols.items():
        if k in ("plan", "progress", "stats", "notes"):
            sets.append(f"{k} = CAST(:{k} AS JSONB)")
            params[k] = json.dumps(v)
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    sets.append("updated_at = now()")
    async with session_scope() as s:
        r = await s.execute(text(f"UPDATE chat_video_jobs SET {', '.join(sets)} WHERE id = :id"), params)
        await s.execute(text("SELECT pg_notify('chat_video_changed', :id)"), {"id": job.id})
    if (r.rowcount or 0) == 0:
        raise _Gone()


async def _save_progress(job: Job) -> None:
    await _update(job, stage=job.stage, plan=job.plan, progress=job.progress, eta_s=round(job.eta(), 1),
                  stats={**job.stats, "speeds": job.speeds()}, notes=job.notes)


async def _cancel_requested(job: Job, force: bool = False) -> bool:
    now = time.monotonic()
    if not force and now - job.last_cancel_check < CANCEL_CHECK_SEC:
        return False
    job.last_cancel_check = now
    async with session_scope() as s:
        row = (await s.execute(text("SELECT cancel_requested FROM chat_video_jobs WHERE id = :id"),
                               {"id": job.id})).first()
    if row is None:
        raise _Gone()
    return bool(row[0])


# ─── The runner ─────────────────────────────────────────────────────────
class Runner(VoiceFirst):
    """Runs the queued jobs one at a time (:meth:`run_forever`). The
    voice-first rules (wait for quiet, a model step a turn cancels, ffmpeg
    held still) are :class:`domovoi.voice_first.VoiceFirst`'s, shared with
    the scan reader."""

    label = "chat video"

    def __init__(self, brains: Brains | None = None, *, quiet_sec: float = QUIET_SEC,
                 poll_sec: float = POLL_SEC) -> None:
        super().__init__(quiet_sec=quiet_sec, poll_sec=poll_sec)
        self.brains = brains or default_brains()
        #: The job being watched now (None between jobs).
        self.current: str | None = None

    # ── voice first: this job's hooks ───────────────────────────────────
    passthrough = (ModelMissing, _Cancelled, _Gone)
    stopped = _Cancelled

    async def _mark_waiting(self, job: Job, waiting: bool) -> None:
        """The row says ``waiting_for_voice`` (the attachment's "paused
        while someone talks to Domovoi")."""
        await _update(job, waiting_for_voice=waiting)

    async def _stop_requested(self, job: Job) -> bool:
        return await _cancel_requested(job)

    def _before_model_step(self) -> None:
        self._check_ai()

    @staticmethod
    def _check_ai() -> None:
        if ai_features.turned_off():
            raise JobFailed("ai-off")

    # ── the loop ────────────────────────────────────────────────────────
    async def run_forever(self, shutdown: asyncio.Event) -> None:
        """Take jobs until ``shutdown``: a job under way at shutdown stops
        where it is and carries on after the restart."""
        try:
            await sweep_at_boot()
        except Exception as e:  # noqa: BLE001 — never stops the runner
            log.warning("chat video: the boot sweep failed: %s", type(e).__name__)
        while not shutdown.is_set():
            # The job runs beside the shutdown signal: a stop cancels it at
            # once (its row says running, so the next start carries on from
            # the last saved step) instead of holding the core's stop up.
            job = asyncio.ensure_future(self.run_next())
            stop = asyncio.ensure_future(shutdown.wait())
            try:
                await asyncio.wait({job, stop}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
                if not job.done():
                    job.cancel()
                    try:
                        await job
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — stopping
                        pass
            if not job.done() or job.cancelled():
                break
            try:
                ran = job.result()
            except Exception as e:  # noqa: BLE001 — the next round tries again
                log.warning("chat video: the runner stumbled: %s", type(e).__name__)
                ran = None
            if ran is None:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=IDLE_POLL_SEC)
                except asyncio.TimeoutError:
                    pass

    async def run_next(self) -> str | None:
        """Claim the oldest live job and run it to its end; its id, or None
        when there was none."""
        async with session_scope() as s:
            row = (await s.execute(text(
                f"UPDATE chat_video_jobs SET state = 'running', attempts = attempts + 1, "
                f"started_at = COALESCE(started_at, now()), waiting_for_voice = false, updated_at = now() "
                f"WHERE id = (SELECT id FROM chat_video_jobs WHERE state IN ('queued', 'running') "
                f"ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING {_COLS}"))).mappings().first()
            if row is not None:
                await s.execute(text("SELECT pg_notify('chat_video_changed', :id)"), {"id": row["id"]})
        if row is None:
            return None
        job = Job.from_row(row)
        self.current = job.id
        try:
            await self.run_job(job, cancel_requested=bool(row["cancel_requested"]))
        finally:
            self.current = None
        return job.id

    async def run_job(self, job: Job, *, cancel_requested: bool = False) -> None:
        log.info("chat video %s: watching %.0f s (start %d, stage %s)", job.id[:8], job.duration,
                 job.attempts, job.stage or "scan")
        job.server_speeds = await server_speeds()
        try:
            if cancel_requested:
                raise _Cancelled()
            if job.attempts > limits.MAX_ATTEMPTS:
                raise JobFailed(limits.CODE_INTERRUPTED)
            self._check_ai()
            await self._watch(job)
        except _Cancelled:
            await self._end_cancelled(job)
        except _Gone:
            log.info("chat video %s: its chat is gone; stopping", job.id[:8])
            delete_files(job.token, job.id)
        except JobFailed as e:
            await self._end_failed(job, e.code, e.detail)
        except ModelMissing as e:
            await self._end_failed(job, e.code, context.failure_text(e.code))
        except asyncio.CancelledError:
            # Shutting down: the row says running; the next start carries on.
            raise
        except Exception as e:  # noqa: BLE001
            code = _code_for(e)
            log.warning("chat video %s failed (%s): %s", job.id[:8], code, type(e).__name__)
            await self._end_failed(job, code, context.failure_text(code))

    # ── the passes ──────────────────────────────────────────────────────
    async def _watch(self, job: Job) -> None:
        work = limits.work_dir(job.id)
        work.mkdir(parents=True, exist_ok=True)
        if job.plan is None:
            await self._pass0(job, work)
        if job.stage == "transcribe":
            await self._pass1(job, work)
        if job.stage == "frames":
            await self._pass2(job, work)
        if job.stage == "combine":
            await self._combine(job)

    def _streams(self, job: Job) -> tuple[int | None, int | None]:
        """(video, audio) stream indexes the upload's probe checked (None for
        a job queued before they were kept)."""
        def idx(v: Any) -> int | None:
            return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
        return idx(job.probe.get("video_index")), idx(job.probe.get("audio_index"))

    def _video(self, job: Job) -> Path:
        p = limits.video_path(job.token)
        if p is None:
            raise JobFailed(limits.CODE_MISSING)
        return p

    async def _pass0(self, job: Job, work: Path) -> None:
        """Scene changes, and where someone may be speaking."""
        job.stage = "scan"
        await _update(job, stage="scan", eta_s=round(job.eta(), 1))
        video = self._video(job)
        v_index, a_index = self._streams(job)
        await self.wait_for_quiet(job)
        t0 = time.monotonic()
        try:
            scenes = await media.scene_changes(video, duration=job.duration, video_index=v_index,
                                               hold=self.pass_hold(job))
        except FileNotFoundError:
            raise JobFailed(limits.CODE_FAILED, limits.NO_TOOLS) from None
        except RuntimeError:
            raise JobFailed(limits.CODE_UNREADABLE) from None
        job.note_time("scan_per_video_s", time.monotonic() - t0, job.duration)
        pieces: list[tuple[float, float]] = []
        speech_s = 0.0
        has_audio = bool(job.probe.get("audio_codec"))
        if has_audio:
            await self.wait_for_quiet(job)
            t0 = time.monotonic()
            pcm_file = work / "audio.pcm"
            if await media.extract_audio(video, pcm_file, duration=job.duration, audio_index=a_index,
                                         hold=self.pass_hold(job)):
                pcm = await asyncio.to_thread(media.read_pcm, pcm_file, job.duration)
                levels = await asyncio.to_thread(media.window_levels, pcm)
                spans = media.speech_spans(levels)
                pieces = media.speech_pieces(spans, levels)
                speech_s = round(sum(b - a for a, b in pieces), 2)
            else:
                has_audio = False
            job.note_time("audio_per_video_s", time.monotonic() - t0, job.duration)
        job.plan = {"scenes": [list(s) for s in scenes], "pieces": [list(p) for p in pieces],
                    "speech_s": speech_s, "has_audio": has_audio, "frames": None}
        job.progress = {"pieces_done": 0, "pieces": len(pieces), "frames_done": 0}
        job.stage = "transcribe"
        await _save_progress(job)

    async def _pcm(self, job: Job, work: Path) -> bytes:
        f = work / "audio.pcm"
        if not f.is_file():
            # The sound file went (a cleanup during a restart): made again.
            if not await media.extract_audio(self._video(job), f, duration=job.duration,
                                             audio_index=self._streams(job)[1], hold=self.pass_hold(job)):
                raise JobFailed(limits.CODE_UNREADABLE)
        return await asyncio.to_thread(media.read_pcm, f, job.duration)

    async def _pass1(self, job: Job, work: Path) -> None:
        """The transcript, a short piece at a time, each saved as it ends."""
        pieces = ((job.plan or {}).get("pieces") or [])[:limits.MAX_SPEECH_PIECES]
        done = await _saved_indexes(job.id, "chat_video_segments")
        pcm: bytes | None = None
        for i, (a, b) in enumerate(pieces):
            if i in done:
                continue
            if pcm is None:
                pcm = await self._pcm(job, work)
            await self.wait_for_quiet(job)
            # Speech recognition isn't a language model, but with AI turned
            # off the whole job stops, as everything AI does.
            self._check_ai()
            t0 = time.monotonic()
            try:
                said = await self.brains.transcribe(media.pcm_slice(pcm, a, b))
            except Exception as e:  # noqa: BLE001
                from domovoi.clients.whisper import SttUnavailableError

                if isinstance(e, SttUnavailableError):
                    _add_note(job, context.STT_OFF_NOTE)
                    break
                raise
            job.note_time("stt_per_speech_s", time.monotonic() - t0, max(0.1, b - a))
            await _save_row(job, "chat_video_segments", i,
                            {"start_s": a, "end_s": b, "text": _clean_said(said)})
            job.progress["pieces_done"] = len(done) + 1
            done.add(i)
        job.stage = "frames"
        await _save_progress(job)
        if (work / "audio.pcm").is_file():
            (work / "audio.pcm").unlink(missing_ok=True)

    async def _pass2(self, job: Job, work: Path) -> None:
        """Frames at scene changes, near-duplicates dropped, each described
        and saved as it ends."""
        p = job.plan or {}
        if not isinstance(p.get("frames"), list):
            video = self._video(job)
            times = plan.candidate_times(job.duration, [tuple(s) for s in p.get("scenes") or []])
            hashes: list[int | None] = []
            spent = 0.0
            for k, t in enumerate(times):
                if spent >= limits.FRAME_GRAB_BUDGET_SEC:
                    # The grabs have had their time: the rest are skipped.
                    log.info("chat video %s: frame grabs over budget; %d of %d candidates kept",
                             job.id[:8], k, len(times))
                    times = times[:k]
                    break
                await self.wait_for_quiet(job)
                dest = work / f"cand-{k:03d}.jpg"
                t0 = time.monotonic()
                ok = await media.grab_frame(video, t, dest, video_index=self._streams(job)[0],
                                            hold=self.pass_hold(job))
                spent += time.monotonic() - t0
                # A frame that couldn't be grabbed has no fingerprint: it is
                # kept, and grabbed again (or skipped) when its turn comes.
                hashes.append(media.dhash(dest) if ok else None)
            picked = plan.pick_frames(times, hashes)
            for k, t in enumerate(times):
                src = work / f"cand-{k:03d}.jpg"
                if t in picked and src.is_file():
                    src.replace(work / f"frame-{picked.index(t):03d}.jpg")
                else:
                    src.unlink(missing_ok=True)
            p["frames"] = picked
            job.plan = p
            job.progress["frames"] = len(picked)
            await _save_progress(job)
        frames = p["frames"]
        done = await _saved_indexes(job.id, "chat_video_frames")
        for i, t in enumerate(frames):
            if i in done:
                continue
            img = work / f"frame-{i:03d}.jpg"
            t0 = time.monotonic()
            if not img.is_file():
                await self.wait_for_quiet(job)
                if not await media.grab_frame(self._video(job), t, img, video_index=self._streams(job)[0],
                                              hold=self.pass_hold(job)):
                    done.add(i)
                    continue
            jpeg = img.read_bytes()
            said = await self.model_step(job, lambda jpeg=jpeg: self.brains.describe(jpeg))
            job.note_time("frame_s", time.monotonic() - t0)
            await _save_row(job, "chat_video_frames", i, {"at_s": t, "description": _clean_said(said)})
            img.unlink(missing_ok=True)
            done.add(i)
            job.progress["frames_done"] = len(done)
            await _save_progress(job)
        await self.brains.release_vision()
        job.stage = "combine"
        await _save_progress(job)

    async def _combine(self, job: Job) -> None:
        segments, frames = await results(job.id)
        t0 = time.monotonic()
        summary = ""
        if segments or frames:
            system, user, code = context.summary_prompt(job.name, job.duration, segments, frames)
            summary = context.strip_fence(
                await self.model_step(job, lambda: self.brains.write(system, user, context.SUMMARY_NUM_PREDICT)),
                code)
        job.note_time("combine_s", time.monotonic() - t0)
        answer = ""
        if job.question.strip():
            code = context._fence()
            block = context.video_block(job.name, job.duration, segments, frames, summary, code, notes=job.notes)
            system, user = context.answer_prompt(job.question, block, code)
            answer = context.strip_fence(
                await self.model_step(job, lambda: self.brains.write(system, user, context.ANSWER_NUM_PREDICT)),
                code)
        job.stage = "done"
        heard = any(str(s.get("text") or "").strip() for s in segments)
        if job.plan and not job.plan.get("has_audio"):
            _add_note(job, "The video has no sound.")
        reply = context.done_reply(job.name, summary, heard_speech=heard or not (job.plan or {}).get("has_audio"),
                                   frames=len(frames), notes=job.notes)
        await self._finish(job, "done", summary=summary, replies=[reply] + ([answer] if answer else []))

    # ── endings ─────────────────────────────────────────────────────────
    async def _finish(self, job: Job, state: str, *, summary: str | None = None, replies: list[str] | None = None,
                      error: str | None = None, code: str | None = None) -> None:
        """The row's end, the chat's reply, and the files deleted, in one go
        (the files go even when the chat went meanwhile)."""
        try:
            stats = {**job.stats, "speeds": job.speeds()}
            async with session_scope() as s:
                r = await s.execute(text(
                    "UPDATE chat_video_jobs SET state = :st, stage = :stage, summary = COALESCE(:sum, summary), "
                    "notes = CAST(:notes AS JSONB), error = :err, error_code = :code, eta_s = NULL, "
                    "waiting_for_voice = false, stats = CAST(:stats AS JSONB), progress = CAST(:prog AS JSONB), "
                    "finished_at = now(), updated_at = now(), files_deleted_at = now() WHERE id = :id"),
                    {"st": state, "stage": job.stage, "sum": summary, "notes": json.dumps(job.notes), "err": error,
                     "code": code, "stats": json.dumps(stats), "prog": json.dumps(job.progress), "id": job.id})
                if state == "cancelled":
                    await s.execute(text("DELETE FROM chat_video_segments WHERE job_id = :id"), {"id": job.id})
                    await s.execute(text("DELETE FROM chat_video_frames WHERE job_id = :id"), {"id": job.id})
                if (r.rowcount or 0) and replies:
                    for content in replies:
                        st = {"model_role": "video", "video_job": job.id}
                        if code:
                            st["error_code"] = code
                        await s.execute(text(
                            "INSERT INTO chat_messages (thread_id, role, content, model, error, stats) "
                            "VALUES (:t, 'assistant', :c, :m, :e, CAST(:st AS JSONB))"),
                            {"t": job.thread_id, "c": content, "m": settings.ollama_model if not code else None,
                             "e": error, "st": json.dumps(st)})
                    await s.execute(text("UPDATE chat_threads SET updated_at = now() WHERE id = :t"),
                                    {"t": job.thread_id})
                await s.execute(text("SELECT pg_notify('chat_changed', 'message')"))
        finally:
            delete_files(job.token, job.id)
        log.info("chat video %s: %s; the video and its frames are deleted", job.id[:8], state)

    async def _end_failed(self, job: Job, code: str, detail: str) -> None:
        await self._finish(job, "failed", replies=[context.failed_reply(job.name, detail)], error=detail, code=code)

    async def _end_cancelled(self, job: Job) -> None:
        await self._finish(job, "cancelled")


def _add_note(job: Job, note: str) -> None:
    if note not in job.notes:
        job.notes.append(note)


def _code_for(e: BaseException) -> str:
    try:
        import httpx

        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.TransportError)):
            return "model_unreachable"
    except ImportError:  # pragma: no cover
        pass
    return limits.CODE_FAILED


def _clean_said(s: str) -> str:
    return " ".join(str(s or "").split())[:2000]


async def _saved_indexes(job_id: str, table: str) -> set[int]:
    assert table in ("chat_video_segments", "chat_video_frames")
    async with session_scope() as s:
        rows = (await s.execute(text(f"SELECT idx FROM {table} WHERE job_id = :id"), {"id": job_id})).scalars().all()
    return {int(r) for r in rows}


async def _save_row(job: Job, table: str, idx: int, values: dict[str, Any]) -> None:
    assert table in ("chat_video_segments", "chat_video_frames")
    cols = ", ".join(values)
    params = {"id": job.id, "idx": idx, **values}
    async with session_scope() as s:
        exists = (await s.execute(text("SELECT 1 FROM chat_video_jobs WHERE id = :id"), {"id": job.id})).first()
        if exists is None:
            raise _Gone()
        await s.execute(text(
            f"INSERT INTO {table} (job_id, idx, {cols}) VALUES (:id, :idx, {', '.join(':' + k for k in values)}) "
            f"ON CONFLICT (job_id, idx) DO NOTHING"), params)


async def results(job_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(transcript pieces, frame descriptions) saved for a job, in order."""
    async with session_scope() as s:
        segs = (await s.execute(text(
            "SELECT idx, start_s, end_s, text FROM chat_video_segments WHERE job_id = :id ORDER BY idx"),
            {"id": job_id})).mappings().all()
        frames = (await s.execute(text(
            "SELECT idx, at_s, description FROM chat_video_frames WHERE job_id = :id ORDER BY idx"),
            {"id": job_id})).mappings().all()
    return [dict(r) for r in segs], [dict(r) for r in frames]


# ─── Files ──────────────────────────────────────────────────────────────
def delete_files(token: str, job_id: str | None = None) -> None:
    """The video, its staging note and the job's frames and sound: gone."""
    if limits.valid_token(token):
        for p in limits.VIDEOS_DIR.glob(f"{token}*"):
            media.remove_quietly(p)
    if job_id and limits.valid_job_id(job_id):
        media.remove_quietly(limits.work_dir(job_id))


def sweep_files(live_tokens: set[str], live_jobs: set[str], now: float | None = None) -> int:
    """What a crash left: work folders of no live job, and videos (with
    their staging notes) whose token no live job holds and that are older
    than :data:`limits.UNSENT_TTL_SEC` (younger ones may be staged in a
    composer right now; the web process sweeps those)."""
    now = time.time() if now is None else now
    gone = 0
    if limits.WORK_DIR.is_dir():
        for d in limits.WORK_DIR.iterdir():
            if d.name not in live_jobs:
                media.remove_quietly(d)
                gone += 1
    if limits.VIDEOS_DIR.is_dir():
        for p in limits.VIDEOS_DIR.iterdir():
            if not p.is_file() or p.name[:32] in live_tokens:
                continue
            try:
                if now - p.stat().st_mtime > limits.UNSENT_TTL_SEC:
                    p.unlink(missing_ok=True)
                    gone += 1
            except OSError:
                continue
    return gone


async def sweep_at_boot() -> int:
    """:func:`sweep_files` with the live jobs read from the database."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, token FROM chat_video_jobs WHERE state IN ('queued', 'running')"))).all()
    return sweep_files({r[1] for r in rows}, {r[0] for r in rows})
