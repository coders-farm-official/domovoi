"""Videos in home-server chats: attach, watch, ask (1.4.0).

The routes (under ``/api/chat``, on :mod:`web.backend.api.chat`'s router),
on the same tiers as the chat itself:

* ``POST /videos?name=birthday.mp4`` — stage a video for a message (device
  tier, AI features on). The request body IS the file (no multipart), so a
  1 GB phone video goes to disk a chunk at a time and is never spooled
  twice. Refused at once past :data:`limits.MAX_BYTES`, then checked with
  ffprobe before it is accepted: at most 3 minutes, a phone format
  (MP4/MOV/WebM, H.264/HEVC/VP9/AV1). Stored under
  ``~/.domovoi/chat_uploads/videos/<token><ext>`` with a staging note
  ``<token>.json``; a video no message took is removed after an hour (a
  sweep every ten minutes, chat.py's, and at each upload). The folder has a
  ceiling (:data:`limits.MAX_STORED_BYTES`, :data:`limits.MAX_UNSENT`
  staged and not sent) and the disk's free space is checked again as the
  body is written, chunked or not (security audit round 6, DOC-13).
* ``POST /threads/{id}/messages`` (chat.py) carries ``videos: [{token}]``:
  the message queues a job (V064) that the core runs
  (domovoi/chat_video/runner.py), and the reply comes at once ("I'm
  watching birthday.mp4 (2:31) now. It should take about 6 minutes …").
  The person can keep chatting; when the job ends the core writes the
  summary (and the answer to a question sent with the video) into the chat.
* ``GET /threads/{id}/messages`` lists the thread's ``videos`` (each job's
  view, below). ``GET /video-jobs/{id}`` is one job's view (read tier);
  poll it while ``state`` is ``queued`` or ``running``.
* ``GET /video-jobs/{id}/results`` — what was kept: the summary, the
  transcript with times, and each frame's description worded "might show".
* ``POST /video-jobs/{id}/cancel`` — stop (device tier): a queued job
  ends at once, a running one at its next check (within a second); the
  video, the frames and anything understood so far are deleted.

The video file and every frame grab are deleted when the job ends,
whatever the ending; only the text stays, with the chat, and goes when the
chat is deleted (CASCADE; :func:`thread_tokens` for the files of a job
that hadn't ended).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from domovoi import ai_features
from domovoi.admin_auth import require_device, require_device_read
from domovoi.chat_video import context, limits, media, plan
from domovoi.chat_video.runner import delete_files
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter()


def _require_ai() -> None:
    """A video for a chat while AI features are off: refused before
    anything is stored (409 ai-off), as a picture or a document is."""
    if ai_features.turned_off():
        raise ai_features.http_exception("chat")


DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]
SEND = [Depends(require_device), Depends(_require_ai)]

_CHUNK = 1024 * 1024
#: Room kept free on the disk beside a video being uploaded.
DISK_HEADROOM = 512 * 1024 * 1024
#: How often (bytes written) an upload looks again at the disk and the folder.
RECHECK_EVERY = 64 * 1024 * 1024

LIVE = ("queued", "running")


def _refused(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _too_large() -> JSONResponse:
    return _refused(413, "video_too_large", limits.TOO_LARGE, max_mb=limits.MAX_BYTES // 2 ** 20)


def _clean_name(name: str | None) -> str:
    base = Path((name or "").replace("\\", "/")).name.strip()
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f")[:200]
    return base or "video.mp4"


def availability() -> dict[str, Any]:
    """Whether the composer offers videos, and its limits (``GET
    /api/chat/models`` → ``video``). Hidden (``available`` false) with AI
    features off or with no ffmpeg on the server: no control that can't do
    what it says."""
    reason = None
    if ai_features.turned_off():
        reason = "ai-off"
    elif not media.tools_present():
        reason = "no-ffmpeg"
    return {"available": reason is None, "reason": reason, "max_seconds": int(limits.MAX_DURATION_SEC),
            "max_mb": limits.MAX_BYTES // 2 ** 20, "accept": limits.ACCEPT,
            "extensions": list(limits.EXTENSIONS)}


# ─── Staging ────────────────────────────────────────────────────────────
def _meta(token: str) -> dict[str, Any] | None:
    if not limits.valid_token(token):
        return None
    try:
        data = json.loads(limits.meta_path(token).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def _live_tokens(strict: bool = False) -> set[str]:
    """The tokens of videos sent and waiting to be watched. When the
    database can't say: ``strict`` raises (the sweep deletes nothing), else
    none (every file then counts as unsent, the stricter way for a quota)."""
    try:
        async with session_scope() as s:
            return set((await s.execute(text(
                "SELECT token FROM chat_video_jobs WHERE state IN ('queued', 'running')"))).scalars().all())
    except Exception as e:  # noqa: BLE001
        if strict:
            raise
        log.warning("chat videos: the live jobs are unreadable: %s", type(e).__name__)
        return set()


async def sweep_unsent(now: float | None = None) -> int:
    """Remove staged videos older than :data:`limits.UNSENT_TTL_SEC` that no
    job took (picked in the composer, never sent), and a file an upload left
    without its staging note (the process stopped mid-upload) once it is as
    old. Run every ten minutes (chat.py's sweeper) and at each upload."""
    if not limits.VIDEOS_DIR.is_dir():
        return 0
    now = time.time() if now is None else now
    newest: dict[str, float] = {}
    for p in limits.VIDEOS_DIR.iterdir():
        token = p.name[:32]
        if not limits.valid_token(token):
            continue
        try:
            if not p.is_file():
                continue
            newest[token] = max(newest.get(token, 0.0), p.stat().st_mtime)
        except OSError:
            continue
    old = {t for t, m in newest.items() if now - m > limits.UNSENT_TTL_SEC}
    if not old:
        return 0
    gone = old - await _live_tokens(strict=True)
    for t in gone:
        delete_files(t)
    return len(gone)


def store_use(live: set[str]) -> tuple[int, int]:
    """(bytes of every video in the folder, how many staged videos no job has
    taken yet), uploads under way included."""
    total = 0
    unsent: set[str] = set()
    if not limits.VIDEOS_DIR.is_dir():
        return 0, 0
    for p in limits.VIDEOS_DIR.iterdir():
        if p.suffix.lower() not in limits.EXTENSIONS:
            continue
        try:
            if not p.is_file():
                continue
            total += p.stat().st_size
        except OSError:
            continue
        if p.name[:32] not in live:
            unsent.add(p.name[:32])
    return total, len(unsent)


def _room_left(live: set[str], adding: int = 0) -> JSONResponse | None:
    """The 507 when the disk or the videos' folder is full (``adding``: bytes
    about to come), else None."""
    free = media.disk_free(limits.VIDEOS_DIR)
    if free is not None and free < adding + DISK_HEADROOM:
        return _refused(507, "video_no_room", limits.NO_ROOM)
    total, _unsent = store_use(live)
    if total + adding > limits.MAX_STORED_BYTES:
        return _refused(507, "video_no_room", limits.STORE_FULL)
    return None


_REFUSALS = {
    409: {"description": "AI features are off (X-Domovoi-Refusal: ai-off)"},
    413: {"description": 'Too large: {"detail", "code": "video_too_large", "max_mb": 2048}'},
    415: {"description": 'Not a video chat takes: {"detail", "code": "video_unsupported"}'},
    422: {"description": 'Too long or unreadable: {"detail", "code": "video_too_long" | "video_unreadable", '
                         '"duration_s"?}'},
    503: {"description": 'The server has no ffmpeg: {"detail", "code": "video_no_tools"}'},
    507: {"description": 'Not enough disk space: {"detail", "code": "video_no_room"}'},
}


@router.post("/videos", dependencies=SEND, responses=_REFUSALS)
async def upload_video(request: Request, name: str = Query(..., min_length=1, max_length=255)) -> Any:
    """Stage one video for a chat message. The body is the file. The answer
    is ``{token, name, duration_s, size, width, height, has_audio,
    estimate_s, estimate_text}``; ``token`` goes in the send's ``videos``."""
    try:
        await sweep_unsent()
    except Exception as e:  # noqa: BLE001 — tidying never stops an upload
        log.warning("chat videos: the unsent sweep failed: %s", type(e).__name__)
    clean = _clean_name(name)
    if not limits.extension_ok(clean):
        return _refused(415, "video_unsupported", limits.UNSUPPORTED)
    declared = request.headers.get("content-length")
    size_hint = int(declared) if declared and declared.isdigit() else None
    if size_hint is not None and size_hint > limits.MAX_BYTES:
        return _too_large()
    live = await _live_tokens()
    full = _room_left(live, size_hint or 0)
    if full is not None:
        return full
    if store_use(live)[1] >= limits.MAX_UNSENT:
        return _refused(507, "video_no_room", limits.STORE_FULL)
    if not media.tools_present():
        return _refused(503, "video_no_tools", limits.NO_TOOLS)
    token = uuid.uuid4().hex
    limits.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    dest = limits.VIDEOS_DIR / f"{token}{Path(clean).suffix.lower()}"
    size = 0
    next_check = RECHECK_EVERY
    try:
        with dest.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > limits.MAX_BYTES:
                    raise _TooLarge()
                out.write(chunk)
                if size >= next_check:
                    # A chunked body declares no size: the disk and the
                    # folder are looked at again as it comes (this file's
                    # bytes so far are in the folder's total already).
                    next_check = size + RECHECK_EVERY
                    out.flush()
                    full = _room_left(live)
                    if full is not None:
                        raise _NoRoom(full)
    except _TooLarge:
        dest.unlink(missing_ok=True)
        return _too_large()
    except _NoRoom as e:
        dest.unlink(missing_ok=True)
        return e.response
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        return _refused(422, "video_unreadable", limits.UNREADABLE)
    try:
        probed = await media.probe(dest)
    except media.ProbeFailed as e:
        dest.unlink(missing_ok=True)
        if e.missing_tools:
            return _refused(503, "video_no_tools", limits.NO_TOOLS)
        return _refused(422, "video_unreadable", limits.UNREADABLE)
    why = media.refusal(probed)
    if why is not None:
        dest.unlink(missing_ok=True)
        code, sentence = why
        status = 415 if code == limits.CODE_UNSUPPORTED else 422
        return _refused(status, code, sentence, duration_s=round(probed.duration, 1),
                        max_seconds=int(limits.MAX_DURATION_SEC))
    est = plan.estimate(plan.Counts(duration=probed.duration), await _server_speeds())
    meta = {"token": token, "name": clean, "duration_s": round(probed.duration, 2), "size": size,
            "width": probed.width, "height": probed.height, "has_audio": probed.has_audio,
            "probe": probed.to_json(), "estimate_s": est, "estimate_text": plan.about_minutes(est)}
    part = limits.meta_path(token).with_suffix(".part")
    part.write_text(json.dumps(meta), encoding="utf-8")
    part.replace(limits.meta_path(token))
    return {k: v for k, v in meta.items() if k != "probe"}


class _TooLarge(Exception):
    pass


class _NoRoom(Exception):
    def __init__(self, response: JSONResponse) -> None:
        super().__init__("no room")
        self.response = response


async def _server_speeds() -> dict[str, float]:
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT stats FROM chat_video_jobs WHERE state = 'done' AND stats ? 'speeds' "
                "ORDER BY finished_at DESC NULLS LAST LIMIT 10"))).scalars().all()
    except Exception:  # noqa: BLE001
        return {}
    return plan.speeds_from_stats([r if isinstance(r, dict) else json.loads(r) for r in rows])


def resolve(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The staged videos a message names (unknown tokens dropped, at most
    :data:`limits.MAX_VIDEOS_PER_MESSAGE`)."""
    out: list[dict[str, Any]] = []
    for item in items:
        token = str((item or {}).get("token") or "")
        meta = _meta(token)
        if meta and limits.video_path(token) is not None and all(m["token"] != token for m in out):
            out.append(meta)
        if len(out) >= limits.MAX_VIDEOS_PER_MESSAGE:
            break
    return out


async def queue_jobs(s: Any, thread_id: int, message_id: int, videos: list[dict[str, Any]],
                     question: str) -> list[dict[str, Any]]:
    """The jobs for a sent message's videos, in the send's transaction."""
    out = []
    for v in videos:
        # One staged file, one job: a second send of the same token would
        # find its video deleted when the first job ends.
        if (await s.execute(text("SELECT 1 FROM chat_video_jobs WHERE token = :tok"), {"tok": v["token"]})).first():
            raise HTTPException(status_code=409, detail="that video was already sent; attach it again to send it again")
        job_id = uuid.uuid4().hex
        await s.execute(text(
            "INSERT INTO chat_video_jobs (id, thread_id, message_id, token, name, question, duration_s, size_bytes, "
            "probe, estimate_s, eta_s) VALUES (:id, :t, :m, :tok, :n, :q, :d, :sz, CAST(:p AS JSONB), :e, :e)"),
            {"id": job_id, "t": thread_id, "m": message_id, "tok": v["token"], "n": v["name"], "q": question,
             "d": float(v["duration_s"]), "sz": v.get("size"), "p": json.dumps(v.get("probe") or {}),
             "e": v.get("estimate_s")})
        out.append({**v, "job_id": job_id})
    await s.execute(text("SELECT pg_notify('chat_video_changed', 'queued')"))
    return out


async def attach_reply(jobs: list[dict[str, Any]], question: str) -> str:
    """The chat's answer to a message that sent a video."""
    v = jobs[0]
    async with session_scope() as s:
        ahead = int((await s.execute(text(
            "SELECT COUNT(*) FROM chat_video_jobs WHERE state IN ('queued', 'running') AND id <> :id "
            "AND created_at <= (SELECT created_at FROM chat_video_jobs WHERE id = :id)"),
            {"id": v["job_id"]})).scalar_one())
    return context.attach_reply(v["name"], float(v["duration_s"]), v.get("estimate_s"), question,
                                queued_behind=ahead)


# ─── Views ──────────────────────────────────────────────────────────────
_VIEW_COLS = ("id, thread_id, message_id, name, question, duration_s, size_bytes, state, stage, progress, "
              "waiting_for_voice, estimate_s, eta_s, summary, notes, error, error_code, cancel_requested, "
              "created_at, started_at, finished_at")


def _js(v: Any, default: Any) -> Any:
    if v is None:
        return default
    return json.loads(v) if isinstance(v, str) else v


def view(r: Any, queued_behind: int = 0) -> dict[str, Any]:
    state = r["state"]
    if state == "running" and r["cancel_requested"]:
        line = "Stopping…"
    elif state in LIVE:
        line = plan.progress_line(state, r["stage"], r["eta_s"], waiting_for_voice=bool(r["waiting_for_voice"]),
                                  queued_behind=queued_behind)
    elif state == "done":
        line = "Watched"
    elif state == "cancelled":
        line = "Stopped"
    else:
        line = "Couldn't watch it"
    progress = _js(r["progress"], {})
    return {
        "id": r["id"],
        "thread_id": r["thread_id"],
        "message_id": r["message_id"],
        "name": r["name"],
        "question": r["question"] or "",
        "duration_s": round(float(r["duration_s"]), 1),
        "size": r["size_bytes"],
        "state": state,
        "stage": r["stage"],
        "line": line,
        "waiting_for_voice": bool(r["waiting_for_voice"]) and state == "running",
        "estimate_s": r["estimate_s"],
        "eta_s": r["eta_s"] if state in LIVE else None,
        "progress": {
            "pieces_done": int(progress.get("pieces_done") or 0),
            "pieces": int(progress.get("pieces") or 0),
            "frames_done": int(progress.get("frames_done") or 0),
            "frames": int(progress.get("frames") or 0),
        },
        "summary": r["summary"] if state == "done" else None,
        "notes": _js(r["notes"], []),
        "error": r["error"],
        "error_code": r["error_code"],
        "cancel_requested": bool(r["cancel_requested"]),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


async def _ahead(s: Any) -> dict[str, int]:
    """For each queued job: how many live jobs are ahead of it."""
    rows = (await s.execute(text(
        "SELECT id FROM chat_video_jobs WHERE state IN ('queued', 'running') ORDER BY "
        "CASE state WHEN 'running' THEN 0 ELSE 1 END, created_at, id"))).scalars().all()
    return {jid: i for i, jid in enumerate(rows)}


async def thread_videos(thread_id: int) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            f"SELECT {_VIEW_COLS} FROM chat_video_jobs WHERE thread_id = :t ORDER BY created_at, id"),
            {"t": thread_id})).mappings().all()
        ahead = await _ahead(s) if any(r["state"] == "queued" for r in rows) else {}
    return [view(r, ahead.get(r["id"], 0)) for r in rows]


async def _row(job_id: str) -> Any:
    if not limits.valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="video job not found")
    async with session_scope() as s:
        row = (await s.execute(text(f"SELECT {_VIEW_COLS} FROM chat_video_jobs WHERE id = :id"),
                               {"id": job_id})).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="video job not found")
        ahead = await _ahead(s) if row["state"] == "queued" else {}
    return row, ahead.get(row["id"], 0)


@router.get("/video-jobs/{job_id}", dependencies=READ)
async def get_video_job(job_id: str) -> dict[str, Any]:
    """One video's progress: ``state`` queued | running | done | failed |
    cancelled, ``line`` ("Watching your video… about 4 minutes left"),
    ``waiting_for_voice``, ``eta_s``, ``progress``, and once ended the
    summary or the error (``error_code`` has a help-desk entry)."""
    row, ahead = await _row(job_id)
    return view(row, ahead)


@router.get("/video-jobs/{job_id}/results", dependencies=READ)
async def get_video_results(job_id: str) -> dict[str, Any]:
    """What was kept from a watched video: the summary, the transcript
    pieces with their times, and the frame descriptions, each worded
    ``might_show`` (a small model's guess, never a fact)."""
    row, ahead = await _row(job_id)
    async with session_scope() as s:
        segs = (await s.execute(text(
            "SELECT start_s, end_s, text FROM chat_video_segments WHERE job_id = :id ORDER BY idx"),
            {"id": job_id})).mappings().all()
        frames = (await s.execute(text(
            "SELECT at_s, description FROM chat_video_frames WHERE job_id = :id ORDER BY idx"),
            {"id": job_id})).mappings().all()
    return {
        "job": view(row, ahead),
        "summary": row["summary"] if row["state"] == "done" else None,
        "transcript": [{"start_s": round(float(r["start_s"]), 2), "end_s": round(float(r["end_s"]), 2),
                        "text": r["text"]} for r in segs if (r["text"] or "").strip()],
        "frames": [{"at_s": round(float(r["at_s"]), 2), "might_show": r["description"]} for r in frames
                   if (r["description"] or "").strip()],
    }


@router.post("/video-jobs/{job_id}/cancel", dependencies=DEVICE)
async def cancel_video_job(job_id: str) -> dict[str, Any]:
    """Stop watching: a queued job ends now, a running one within about a
    second. The video, its frames and what was understood so far go."""
    if not limits.valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="video job not found")
    token = None
    async with session_scope() as s:
        # Queued: ended here, in one step the core's claim can't race
        # (it takes only rows still queued or running).
        r = (await s.execute(text(
            "UPDATE chat_video_jobs SET state = 'cancelled', cancel_requested = true, finished_at = now(), "
            "updated_at = now(), files_deleted_at = now(), eta_s = NULL WHERE id = :id AND state = 'queued' "
            "RETURNING token"), {"id": job_id})).first()
        if r is not None:
            token = r[0]
        else:
            await s.execute(text(
                "UPDATE chat_video_jobs SET cancel_requested = true, updated_at = now() "
                "WHERE id = :id AND state = 'running'"), {"id": job_id})
        await s.execute(text("SELECT pg_notify('chat_video_changed', :id)"), {"id": job_id})
        await s.commit()
    if token is not None:
        delete_files(token, job_id)
    row, ahead = await _row(job_id)
    return view(row, ahead)


# ─── The chat's side ────────────────────────────────────────────────────
async def thread_tokens(s: Any, thread_id: int) -> list[tuple[str, str]]:
    """(token, job id) of a thread's videos, read before it is deleted:
    their files go with it."""
    rows = (await s.execute(text("SELECT token, id FROM chat_video_jobs WHERE thread_id = :t"),
                            {"t": thread_id})).all()
    return [(r[0], r[1]) for r in rows]


def remove_files(pairs: list[tuple[str, str]]) -> None:
    for token, job_id in pairs:
        delete_files(token, job_id)


async def chat_rules(thread_id: int) -> str | None:
    """The system message's addition for a question in a chat with videos:
    each watched video's saved text (worded "might show" for the frames),
    and the names of any still being watched. None: no videos."""
    async with session_scope() as s:
        jobs = (await s.execute(text(
            "SELECT id, name, duration_s, state, summary, notes FROM chat_video_jobs WHERE thread_id = :t "
            "AND state IN ('queued', 'running', 'done') ORDER BY created_at, id"), {"t": thread_id})).mappings().all()
        if not jobs:
            return None
        code = context._fence()
        blocks: list[str] = []
        pending: list[str] = []
        for j in jobs:
            if j["state"] != "done":
                pending.append(j["name"])
                continue
            segs = (await s.execute(text(
                "SELECT start_s, end_s, text FROM chat_video_segments WHERE job_id = :id ORDER BY idx"),
                {"id": j["id"]})).mappings().all()
            frames = (await s.execute(text(
                "SELECT at_s, description FROM chat_video_frames WHERE job_id = :id ORDER BY idx"),
                {"id": j["id"]})).mappings().all()
            blocks.append(context.video_block(j["name"], float(j["duration_s"]), [dict(x) for x in segs],
                                              [dict(x) for x in frames], j["summary"], code,
                                              notes=_js(j["notes"], [])))
    return context.chat_rules(blocks, code, pending)
