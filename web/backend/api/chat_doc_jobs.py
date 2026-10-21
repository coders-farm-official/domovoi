"""Long documents in Chat: map/reduce jobs (documents plan, D2).

A summary of a document longer than one pass is read in parts and then
combined (:mod:`domovoi.doc_qa.longdoc`). Reading 30 pages takes from about
ten seconds (a GPU) to several minutes (a CPU-only box), so it runs as a
**job** in this process, part by part, with progress the person can see
and stop:

* **A home-server chat's job** starts from a summary request in a thread
  with long documents (``chat.send_message``). Its row in
  ``chat_doc_jobs`` (V037) keeps each part's notes as it finishes. The
  send's SSE stream relays the progress (``event: job``, then ``event:
  progress`` per change), and the job writes the assistant message itself
  when it ends, so a person who leaves the page loses nothing: the job
  carries on, the thread's message list shows it running
  (``GET /threads/{id}/messages`` → ``jobs``), and the summary is there
  when they come back. A job this process lost (a restart) carries on from
  the next part when its chat is opened again.
* **Stop** (``POST /doc-jobs/{id}/stop``) keeps what was read: the reply is
  "Summary of pages 1-9 (stopped)" over the notes so far, written with no
  model call, and **carry on** (``POST /doc-jobs/{id}/resume``) reads the
  rest later.
* **A phone's private job** (``POST /doc-jobs``, owner decision Q6): "Use
  home server" from a phone chat sends the file and the request. The file
  is read in the capped worker and deleted as soon as it has been read;
  its text lives only in this process's memory, and goes when the job
  ends (the phone takes the summary and deletes the job, or 24 hours
  pass). Nothing is written to the database, and nothing to disk past the
  read. A web restart forgets it (the phone says so and reads on).
* **Voice turns come first.** Ollama answers one request at a time, so
  before every part a job asks the core whether a voice turn is being
  answered (``GET /v1/voice/activity``, domovoi/voice_activity.py) and waits
  while one is. A question in a room waits at most for the part already
  being read. Parts of different jobs take turns (one at a time).
* **The household's AI switch**: a job starts only with AI features on,
  and stops at the next part, keeping its notes, if they are turned off.
* **Time.** The estimate comes from measured speeds
  (:func:`measured_speed`: the token counts and durations kept for every
  reply in ``chat_messages.stats``, the jobs' own included); during a job
  the time left comes from its own parts.

Text from a file is data, never instructions: each part reaches the model
in the same marked block as a question's passages, each part's notes keep
only labels of passages in that part, and the summary keeps only labels of
real parts (:func:`domovoi.doc_qa.answer.check_reply`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from domovoi import doc_extract
from domovoi.admin_auth import require_device, require_device_read
from domovoi.clients import ollama as ollama_client
from domovoi.config import settings as core_settings
from domovoi.doc_extract import runner
from domovoi.doc_qa import answer as qa
from domovoi.doc_qa import longdoc
from web.backend.api import chat_documents as _docs
from web.backend.db import session_scope
from web.backend.domovoi_client import domovoi_url, service_auth_headers

log = logging.getLogger(__name__)

router = APIRouter()

#: Where a private job's upload sits while it is read (then it goes).
JOBS_DIR = Path.home() / ".domovoi" / "chat_uploads" / "doc-jobs"
#: A private job, and its summary, are forgotten this long after it began.
PRIVATE_TTL_SEC = 24 * 3600
#: At most this many private jobs at once (each holds a document's text).
MAX_PRIVATE_JOBS = 4
#: How often a waiting job asks whether the voice turn has ended, and the
#: longest it waits for voice before reading on regardless.
VOICE_POLL_SEC = 0.25
VOICE_WAIT_MAX_SEC = 180.0
#: How long a voice-activity question to the core may take.
VOICE_ASK_TIMEOUT_SEC = 1.5
#: A progress stream sends the time left at least this often.
TICK_SEC = 2.0

#: States: running (reading, or waiting for voice), reducing (writing the
#: summary), stopped, failed, done. Only the first two are live.
LIVE = ("running", "reducing")

DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]
SEND = [Depends(require_device), Depends(_docs._require_ai)]

_JOBS: dict[str, "Job"] = {}
#: One part at a time, across every job (Ollama answers one request at a
#: time per model; two jobs take turns part by part).
_part_lock: asyncio.Lock | None = None
_lock_loop: int | None = None
#: Speeds measured by private jobs (kept in memory only: no rows).
_private_stats: list[dict[str, Any]] = []
#: Private jobs whose upload is being read (their folder isn't swept).
_reading: set[str] = set()


def _lock() -> asyncio.Lock:
    """The part lock of the running event loop (the tests run several)."""
    global _part_lock, _lock_loop
    loop = id(asyncio.get_running_loop())
    if _part_lock is None or _lock_loop != loop:
        _part_lock, _lock_loop = asyncio.Lock(), loop
    return _part_lock


def _valid(job_id: str) -> bool:
    return len(job_id or "") == 32 and all(c in "0123456789abcdef" for c in job_id)


# ─── The voice signal ───────────────────────────────────────────────────
async def voice_busy() -> bool:
    """Whether the core is answering a voice turn now. A core that can't be
    asked counts as not busy: documents never stop for an outage."""
    try:
        async with httpx.AsyncClient(timeout=VOICE_ASK_TIMEOUT_SEC) as c:
            r = await c.get(f"{domovoi_url().rstrip('/')}/v1/voice/activity", headers=await service_auth_headers())
        return r.status_code == 200 and bool(r.json().get("busy"))
    except Exception:  # noqa: BLE001 — the core is down or restarting
        return False


# ─── Measured speed ─────────────────────────────────────────────────────
async def measured_speed(model: str | None = None) -> longdoc.Speed | None:
    """The QA model's measured speed on this server: the last 50 replies'
    token counts and durations (``chat_messages.stats``), the private
    jobs' (memory only) with them. None before anything was measured."""
    model = model or core_settings.ollama_model
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT stats FROM chat_messages WHERE role = 'assistant' AND error IS NULL "
                "AND model = :m AND stats IS NOT NULL AND stats ? 'generate_ms' "
                "ORDER BY id DESC LIMIT 50"), {"m": model})).scalars().all()
    except Exception as e:  # noqa: BLE001 — no estimate beats no job
        log.warning("chat doc jobs: speeds unreadable: %s", type(e).__name__)
        rows = []
    stats = [r if isinstance(r, dict) else json.loads(r) for r in rows]
    stats += [r for r in _private_stats if r.get("model") == model]
    return longdoc.speed_from_stats(stats)


# ─── A job ──────────────────────────────────────────────────────────────
class Job:
    """One map/reduce job: what it reads, what it has read, and who is
    watching."""

    def __init__(self, *, job_id: str, docs: list[dict[str, Any]], chunks: list[longdoc.Chunk],
                 question: str, private: bool, thread_id: int | None = None,
                 user_message_id: int | None = None, notes: list[str | None] | None = None,
                 estimate_s: float | None = None, stats: dict[str, Any] | None = None,
                 model: str | None = None) -> None:
        self.id = job_id
        self.docs = docs
        self.names = [str(d.get("name") or "the document") for d in docs]
        self.chunks = chunks
        self.question = question
        self.private = private
        self.thread_id = thread_id
        self.user_message_id = user_message_id
        self.notes: list[str | None] = list(notes) if notes is not None else [None] * len(chunks)
        self.estimate_s = estimate_s
        self.stats: dict[str, Any] = dict(stats or {})
        self.model = model or core_settings.ollama_model
        self.state = "running"
        self.waiting = False
        self.current = self.read
        self.part_started: float | None = None
        self.started = time.monotonic()
        self.created = time.time()
        self.done_times: list[tuple[int, float]] = []
        self.fallback: longdoc.Speed | None = None
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.listeners: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.stop_requested = False
        self.ended = asyncio.Event()
        #: What the parts read covered, kept when a private job's text goes.
        self.covers: str | None = None

    # The parts read so far (their notes are kept, empty or not).
    @property
    def read(self) -> int:
        return sum(1 for n in self.notes if n is not None)

    @property
    def total(self) -> int:
        return len(self.chunks)

    def job_speed(self) -> longdoc.Speed | None:
        return longdoc.speed_from_stats([self.stats]) if self.stats.get("prompt_tokens") else None

    def eta(self) -> float | None:
        if self.state not in LIVE:
            return None
        remaining = [c.chars for c, n in zip(self.chunks, self.notes) if n is None]
        left = longdoc.eta_seconds(self.done_times, remaining, self.job_speed(), self.fallback)
        if left is None:
            return None
        if self.part_started is not None and not self.waiting:
            left -= time.monotonic() - self.part_started
        floor = 1.0 if self.state == "reducing" or remaining else 0.0
        return max(floor, left)

    def line(self) -> str:
        state = "waiting" if self.waiting else ("reducing" if self.state == "reducing" else "reading")
        if self.state not in LIVE:
            return ""
        return longdoc.progress_line(state, min(self.current + 1, self.total), self.total, self.eta())

    def view(self) -> dict[str, Any]:
        eta = self.eta()
        out: dict[str, Any] = {
            "id": self.id,
            "state": self.state,
            "waiting_for_voice": self.waiting,
            "part": min(self.current + 1, self.total) if self.state in LIVE else self.read,
            "parts": self.total,
            "read": self.read,
            "eta_s": round(eta, 1) if eta is not None else None,
            "estimate_s": round(self.estimate_s, 1) if self.estimate_s is not None else None,
            "line": self.line(),
            "private": self.private,
            "thread_id": self.thread_id,
            "question": self.question,
            "covers": self.covers if self.covers is not None else longdoc.read_phrase(self.chunks, self.docs, self.read),
            "elapsed_s": round(time.monotonic() - self.started, 1),
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        return out

    def publish(self) -> None:
        v = self.view()
        for q in list(self.listeners):
            try:
                q.put_nowait(v)
            except asyncio.QueueFull:  # a reader that stopped reading
                pass

    def record(self, chars: int, seconds: float, ollama: dict[str, Any], prompt_chars: int) -> None:
        self.done_times.append((chars, seconds))
        s = self.stats
        for key, src in (("prompt_tokens", "prompt_eval_count"), ("output_tokens", "eval_count")):
            if isinstance(ollama.get(src), int):
                s[key] = int(s.get(key) or 0) + ollama[src]
        for key, src in (("prompt_ms", "prompt_eval_duration"), ("generate_ms", "eval_duration")):
            if isinstance(ollama.get(src), (int, float)):
                s[key] = round(float(s.get(key) or 0) + ollama[src] / 1e6, 1)
        if isinstance(ollama.get("prompt_eval_count"), int):
            s["prompt_chars"] = int(s.get("prompt_chars") or 0) + prompt_chars
        s["steps"] = int(s.get("steps") or 0) + 1


# ─── Persistence (home-server chats only) ───────────────────────────────
async def _save(job: Job) -> bool:
    """The thread job's notes and state, saved; False when its row is gone
    (the chat was deleted)."""
    if job.private:
        return True
    async with session_scope() as s:
        r = await s.execute(text(
            "UPDATE chat_doc_jobs SET notes = CAST(:n AS JSONB), stats = CAST(:st AS JSONB), state = :state, "
            "updated_at = now() WHERE id = :id"),
            {"id": job.id, "n": json.dumps(job.notes), "st": json.dumps(job.stats),
             "state": job.state if job.state in ("running", "stopped", "failed") else "running"})
        await s.commit()
        return (r.rowcount or 0) > 0


async def _notify(s: Any) -> None:
    await s.execute(text("SELECT pg_notify('chat_changed', 'message')"))


async def _write_reply(job: Job, content: str, doc: dict[str, Any] | None, *, error: str | None = None,
                       code: str | None = None) -> dict[str, Any] | None:
    """The thread's assistant message for an ended job; the job's row goes
    when it is done, and stays (stopped, failed) so it can carry on."""
    from web.backend.api import chat as chat_api

    stats = {**job.stats, "model_role": "documents", "wall_ms": round((time.monotonic() - job.started) * 1000, 1)}
    if job.estimate_s is not None:
        stats["estimate_s"] = round(job.estimate_s, 1)
    if code:
        stats["error_code"] = code
    async with session_scope() as s:
        exists = (await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :t"), {"t": job.thread_id})).first()
        if exists is None:
            return None
        row = (await s.execute(text(
            """
            INSERT INTO chat_messages AS m (thread_id, role, content, model, error, stats, doc)
            VALUES (:t, 'assistant', :c, :m, :e, CAST(:st AS JSONB), CAST(:doc AS JSONB))
            RETURNING {cols}, NULL::text AS device_name, NULL::text AS speaker_name
            """.format(cols=chat_api._MSG_COLUMNS)),
            {"t": job.thread_id, "c": content, "m": job.model, "e": error, "st": json.dumps(stats),
             "doc": json.dumps(doc) if doc is not None else None})).mappings().first()
        if job.state == "done":
            await s.execute(text("DELETE FROM chat_doc_jobs WHERE id = :id"), {"id": job.id})
        else:
            await s.execute(text(
                "UPDATE chat_doc_jobs SET state = :st, notes = CAST(:n AS JSONB), stats = CAST(:x AS JSONB), "
                "updated_at = now() WHERE id = :id"),
                {"id": job.id, "st": job.state, "n": json.dumps(job.notes), "x": json.dumps(job.stats)})
        await s.execute(text("UPDATE chat_threads SET updated_at = now() WHERE id = :t"), {"t": job.thread_id})
        await _notify(s)
        await s.commit()
    return chat_api._msg_dict(row)


# ─── Running ────────────────────────────────────────────────────────────
async def _yield_to_voice(job: Job) -> None:
    """Wait while a voice turn is being answered (at most
    :data:`VOICE_WAIT_MAX_SEC`)."""
    t0 = time.monotonic()
    while await voice_busy():
        if not job.waiting:
            job.waiting = True
            job.stats["voice_waits"] = int(job.stats.get("voice_waits") or 0) + 1
            job.publish()
        if time.monotonic() - t0 > VOICE_WAIT_MAX_SEC:
            break
        await asyncio.sleep(VOICE_POLL_SEC)
    if job.waiting:
        job.waiting = False
        job.stats["voice_wait_ms"] = round(float(job.stats.get("voice_wait_ms") or 0)
                                           + (time.monotonic() - t0) * 1000, 1)


class _AiOff(Exception):
    pass


async def _ask(job: Job, rules: str, user: str, cap: int) -> tuple[str, dict[str, Any], int]:
    """One model call for the job (one part, or one reduce step): first in
    line among the jobs, after any voice turn. (text, Ollama's figures, the
    characters sent)."""
    system = ollama_client.chat_system_prompt(None) + "\n\n" + rules
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    stats: dict[str, Any] = {}
    async with _lock():
        await _yield_to_voice(job)
        if _docs.ai_turned_off():
            raise _AiOff()
        job.part_started = time.monotonic()
        job.publish()
        out: list[str] = []
        async for delta in ollama_client.chat_stream(messages, model=job.model, stats=stats, num_predict=cap):
            out.append(delta)
    return "".join(out), stats, len(system) + len(user)


def _summary_doc(job: Job, checked: qa.Checked | None, notes: list[dict[str, str]]) -> dict[str, Any]:
    """The reply's ``doc``: the cited parts as its sources (never every
    page of a long document), the notes, and the job."""
    sources = []
    if checked is not None:
        for src in checked.sources:
            if not src["cited"]:
                continue
            d = job.docs[src["doc"]]
            parts = d.get("parts") or []
            part = parts[src["part"]] if 0 <= src["part"] < len(parts) else {}
            sources.append({"token": d.get("token") or "", "name": d.get("name"), "label": src["label"],
                            "page": part.get("page"), "text": src["text"], "match": src["match"], "cited": True})
    return {"mode": "summary", "found": checked.found if checked is not None else bool(job.read),
            "sources": sources, "notes": (checked.notes if checked is not None else []) + notes,
            "job": {"id": job.id, "state": job.state, "parts": job.total, "read": job.read,
                    "estimate_s": round(job.estimate_s, 1) if job.estimate_s is not None else None,
                    "wall_s": round(time.monotonic() - job.started, 1)}}


async def _finish(job: Job, content: str, checked: qa.Checked | None, notes: list[dict[str, str]],
                  *, error: str | None = None, code: str | None = None) -> None:
    doc = _summary_doc(job, checked, notes)
    result: dict[str, Any] = {"content": content, "doc": doc}
    if job.private:
        if job.stats.get("prompt_tokens"):
            _private_stats.append({**job.stats, "model": job.model})
            del _private_stats[:-20]
        # The job ended: its document's text goes now (owner decision Q6);
        # only the summary waits for the phone.
        job.covers = longdoc.read_phrase(job.chunks, job.docs, job.read)
        job.docs = [{"name": d.get("name"), "token": "", "parts": []} for d in job.docs]
    else:
        msg = await _write_reply(job, content, doc, error=error, code=code)
        if msg is not None:
            result["message"] = msg
    job.result = result
    if error is not None:
        job.error = {"detail": error, "code": code}


async def _finish_stopped(job: Job, *, ai_off: bool = False) -> None:
    """What was read, as the reply: no model call."""
    job.state = "stopped"
    phrase = longdoc.read_phrase(job.chunks, job.docs, job.read)
    content = longdoc.stopped_text(phrase, [n or "" for n in job.notes])
    checked = qa.check_reply(content, longdoc.all_given(job.docs)) if job.read else None
    if checked is not None and checked.found:
        content = checked.content
    notes = [longdoc.ai_off_note(job.read, job.total) if ai_off else longdoc.stopped_note(job.read, job.total, phrase)]
    if checked is not None:
        checked.notes = [n for n in checked.notes if n.get("code") != "not_found"]
    await _finish(job, content, checked, notes)


async def _reduce(job: Job) -> str:
    """The summary from the notes: in groups first when they don't fit one
    pass, then all at once."""
    notes = [n or "" for n in job.notes]
    while True:
        groups = longdoc.reduce_groups(notes)
        if len(groups) <= 1:
            break
        merged = []
        for g in groups:
            p = longdoc.build_reduce_prompt([notes[i] for i in g], job.names, job.question)
            reply, st, sent = await _ask(job, p["rules"], p["user"], longdoc.REDUCE_NUM_PREDICT)
            job.record(0, 0.0, st, sent)
            merged.append(longdoc.clean_note(reply, longdoc.all_given(job.docs)))
        notes = merged
    p = longdoc.build_reduce_prompt(notes, job.names, job.question)
    reply, st, sent = await _ask(job, p["rules"], p["user"], longdoc.REDUCE_NUM_PREDICT)
    job.record(0, 0.0, st, sent)
    return reply


async def _run(job: Job) -> None:
    """Reads the parts not read yet, then writes the summary; saves each
    part's notes as it goes. Stopping (the task cancelled with
    ``stop_requested``) keeps what was read."""
    try:
        if job.total == 1 and job.notes[0] is None:
            # One part: the summary straight from it (no notes stage).
            given = longdoc.chunk_given(job.chunks[0], job.docs)
            prompt = qa.build_prompt(job.question or "Summarize it.", given, job.names, mode="summary")
            job.state = "reducing"
            reply, st, sent = await _ask(job, prompt["rules"], prompt["user"], longdoc.REDUCE_NUM_PREDICT)
            job.record(job.chunks[0].chars, time.monotonic() - (job.part_started or job.started), st, sent)
            job.notes[0] = ""
        else:
            for i, chunk in enumerate(job.chunks):
                if job.notes[i] is not None:
                    continue
                job.current = i
                job.state = "running"
                job.publish()
                p = longdoc.build_map_prompt(chunk, job.docs, i, job.total, job.question)
                reply, st, sent = await _ask(job, p["rules"], p["user"], longdoc.MAP_NUM_PREDICT)
                job.notes[i] = longdoc.clean_note(reply, p["given"])
                job.record(chunk.chars, time.monotonic() - (job.part_started or job.started), st, sent)
                job.part_started = None
                if not await _save(job):
                    log.info("chat doc job %s: its chat is gone; stopping", job.id)
                    job.state = "failed"
                    return
                job.publish()
            job.state = "reducing"
            job.current = job.total - 1
            job.publish()
            reply = await _reduce(job)
        given = longdoc.all_given(job.docs)
        checked = qa.check_reply(longdoc.drop_invented_lines(reply, given), given, job.question, job.names)
        job.state = "done"
        notes = [longdoc.parts_note(job.total)] if job.total > 1 else []
        await _finish(job, checked.content, checked, notes)
    except asyncio.CancelledError:
        if job.stop_requested:
            await asyncio.shield(_finish_stopped(job))
        else:
            # Shutting down: the row says running, so the chat carries on
            # from the next part when it is opened again.
            raise
    except _AiOff:
        await _finish_stopped(job, ai_off=True)
    except Exception as e:  # noqa: BLE001 — a failed job says so, its notes kept
        from web.backend.api import chat as chat_api

        code, sentence = chat_api._failure(e, job.model, "documents")
        log.warning("chat doc job %s failed (%s): %s", job.id, code, type(e).__name__)
        job.state = "failed"
        phrase = longdoc.read_phrase(job.chunks, job.docs, job.read)
        content = longdoc.stopped_text(phrase, [n or "" for n in job.notes]) if job.read else ""
        await _finish(job, content, None, [], error=sentence, code=code)
    finally:
        job.part_started = None
        job.waiting = False
        job.ended.set()
        job.publish()
        if not job.private and job.state not in LIVE:
            # The row (or the reply) has it now; a re-opened chat reads
            # it back from there.
            asyncio.get_running_loop().call_later(FORGET_AFTER_SEC, _forget, job)


#: An ended home-server chat's job stays in memory this long (a client
#: polling it still sees how it ended), then is read from its row.
FORGET_AFTER_SEC = 300.0


def _forget(job: Job) -> None:
    if _JOBS.get(job.id) is job and job.state not in LIVE:
        _JOBS.pop(job.id, None)


def _start(job: Job) -> Job:
    _JOBS[job.id] = job
    job.ended = asyncio.Event()
    job.task = asyncio.create_task(_run(job), name=f"chat-doc-job-{job.id[:8]}")
    return job


async def stop(job: Job, wait: float = 10.0) -> None:
    """Stops a job: the part being read is abandoned, what was read kept."""
    if job.task is None or job.task.done() or job.state not in LIVE or job.result is not None:
        return
    job.stop_requested = True
    job.task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(job.ended.wait()), timeout=wait)
    except asyncio.TimeoutError:
        pass


# ─── Starting a home-server chat's job ──────────────────────────────────
async def start_thread_job(thread_id: int, user_message_id: int | None, question: str,
                           docs: list[dict[str, Any]], model: str | None = None) -> Job:
    """A summary job for a thread's long documents (``docs``: loaded, with
    ``token``, ``name`` and ``parts``), answered by ``model`` (default: the
    QA model)."""
    chunks = longdoc.plan_chunks(docs)
    speed = await measured_speed(model)
    estimate = longdoc.estimate_seconds([c.chars for c in chunks], speed)
    longdoc.heads_up("long_summary", docs, question,
                     {"parts": len(chunks), "estimate_s": estimate, "chars": sum(c.chars for c in chunks)})
    job = Job(job_id=uuid.uuid4().hex, docs=docs, chunks=chunks, question=question, private=False,
              thread_id=thread_id, user_message_id=user_message_id, estimate_s=estimate, model=model)
    job.fallback = speed
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO chat_doc_jobs (id, thread_id, user_message_id, question, state, documents, chunks, notes, "
            "estimate_s) VALUES (:id, :t, :u, :q, 'running', CAST(:d AS JSONB), CAST(:c AS JSONB), "
            "CAST(:n AS JSONB), :e)"),
            {"id": job.id, "t": thread_id, "u": user_message_id, "q": question,
             "d": json.dumps([d.get("token") for d in docs]), "c": json.dumps([c.to_json() for c in chunks]),
             "n": json.dumps(job.notes), "e": estimate})
        await s.commit()
    return _start(job)


async def _load_thread_job(row: Any) -> Job | None:
    """A job row as a Job, its documents' parts read back from the cache;
    None when a document is gone."""
    tokens = row["documents"] if isinstance(row["documents"], list) else json.loads(row["documents"] or "[]")
    metas = {d.get("token"): d for d in await _docs.thread_documents(int(row["thread_id"]))}
    docs = []
    for t in tokens:
        cache = _docs.load_cache(str(t or ""))
        if cache is None:
            return None
        meta = metas.get(t) or cache.get("meta") or {}
        docs.append({"token": t, "name": meta.get("name") or "the document", "parts": cache["parts"]})
    chunks_raw = row["chunks"] if isinstance(row["chunks"], list) else json.loads(row["chunks"] or "[]")
    notes = row["notes"] if isinstance(row["notes"], list) else json.loads(row["notes"] or "[]")
    stats = row["stats"] if isinstance(row["stats"], dict) else json.loads(row["stats"] or "{}")
    chunks = [longdoc.Chunk.from_json(c) for c in chunks_raw]
    if len(notes) != len(chunks):
        notes = (list(notes) + [None] * len(chunks))[:len(chunks)]
    job = Job(job_id=row["id"], docs=docs, chunks=chunks, question=row["question"] or "", private=False,
              thread_id=int(row["thread_id"]), user_message_id=row["user_message_id"], notes=notes,
              estimate_s=row["estimate_s"], stats=stats)
    job.state = row["state"]
    job.current = job.read
    return job


async def thread_jobs(thread_id: int, *, carry_on: bool = True) -> list[dict[str, Any]]:
    """The thread's jobs as the message list shows them (running, stopped,
    failed). A running job this process isn't running (it restarted)
    carries on from the next part, with AI features on."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, thread_id, user_message_id, question, state, documents, chunks, notes, estimate_s, stats "
            "FROM chat_doc_jobs WHERE thread_id = :t ORDER BY created_at"), {"t": thread_id})).mappings().all()
    out = []
    for row in rows:
        live = _JOBS.get(row["id"])
        if live is not None and ((live.task is not None and not live.task.done()) or live.state not in LIVE):
            out.append(live.view())
            continue
        job = await _load_thread_job(row)
        if job is None:
            continue
        out.append((await _carry_on_if_lost(job) if carry_on else job).view())
    return out


async def _carry_on_if_lost(job: Job) -> Job:
    """A running job's row with no runner here (a restart): it carries on
    from its next part, AI features on. One runner per job: a reader that
    got here first while this one was loading has started it already."""
    if job.state != "running" or _docs.ai_turned_off():
        return job
    live = _JOBS.get(job.id)
    if live is not None and live.task is not None and not live.task.done():
        return live
    job.fallback = await measured_speed()
    live = _JOBS.get(job.id)
    if live is not None and live.task is not None and not live.task.done():
        return live
    return _start(job)


async def cancel_thread(thread_id: int) -> None:
    """The chat is being deleted: its jobs stop, keeping nothing."""
    for job in [j for j in _JOBS.values() if j.thread_id == thread_id]:
        if job.task is not None and not job.task.done():
            job.task.cancel()
        _JOBS.pop(job.id, None)


async def _job(job_id: str) -> Job:
    if not _valid(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    job = _JOBS.get(job_id)
    if job is not None:
        return job
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT id, thread_id, user_message_id, question, state, documents, chunks, notes, estimate_s, stats "
            "FROM chat_doc_jobs WHERE id = :id"), {"id": job_id})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    loaded = await _load_thread_job(row)
    if loaded is None:
        raise HTTPException(status_code=404, detail="the job's document is no longer on this Domovoi")
    if loaded.state == "running":
        return await _carry_on_if_lost(loaded)
    if loaded.id not in _JOBS:
        _JOBS[loaded.id] = loaded
    return _JOBS[loaded.id]


# ─── Streams ────────────────────────────────────────────────────────────
async def follow(job: Job) -> AsyncIterator[dict[str, Any]]:
    """The job's views as they change, the current one first, until it
    ends (the last view carries the result)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    job.listeners.add(q)
    try:
        yield job.view()
        while not job.ended.is_set():
            try:
                v = await asyncio.wait_for(q.get(), timeout=TICK_SEC)
            except asyncio.TimeoutError:
                v = job.view()      # the time left, ticking down
            yield v
    finally:
        job.listeners.discard(q)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _progress_frames(job: Job) -> AsyncIterator[str]:
    """``event: job`` now, then ``event: progress`` whenever what it shows
    changes, until the job ends. A reader who goes closes this, and the
    job's listener goes with it (aclosing: a generator inside another isn't
    closed with it otherwise)."""
    last = None
    async with contextlib.aclosing(follow(job)) as gen:
        first = True
        async for v in gen:
            if first:
                yield _sse("job", v)
                first = False
                continue
            if v.get("state") in LIVE:
                key = (v["state"], v["part"], v["waiting_for_voice"], v["eta_s"])
                if key != last:
                    yield _sse("progress", v)
                    last = key


async def job_events(job: Job) -> AsyncIterator[str]:
    """``event: job`` (the start), ``event: progress`` per change, then
    ``event: done`` with the result (or ``event: error`` first)."""
    # aclosing: a reader who goes closes this generator, and that must
    # close the one inside it too (it holds the job's listener).
    async with contextlib.aclosing(_progress_frames(job)) as frames:
        async for frame in frames:
            yield frame
    v = job.view()
    if v.get("error"):
        yield _sse("error", {**v["error"], "model": job.model})
    yield _sse("done", v)


async def thread_job_events(job: Job) -> AsyncIterator[str]:
    """A chat message's stream while its job runs: ``event: job``, ``event:
    progress`` per change, then the reply as ``chat.send_message`` ends one
    (``delta`` with the checked text, ``error`` if it failed, ``done`` with
    the persisted row). A reader who leaves stops only the stream."""
    # aclosing: a reader who goes closes this generator, and that must
    # close the one inside it too (it holds the job's listener).
    async with contextlib.aclosing(_progress_frames(job)) as frames:
        async for frame in frames:
            yield frame
    res = job.result or {}
    if res.get("content"):
        yield _sse("delta", {"text": res["content"]})
    if job.error:
        yield _sse("error", {**job.error, "model": job.model})
    if res.get("message"):
        yield _sse("done", res["message"])


def stream_thread_job(job: Job) -> StreamingResponse:
    return _stream(thread_job_events(job))


def _stream(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─── Private jobs (a phone's "Use home server") ─────────────────────────
def sweep(now: float | None = None) -> int:
    """Forgets private jobs past :data:`PRIVATE_TTL_SEC` (their text with
    them) and removes any upload folder no live job owns (a restart left
    it). Returns how many went."""
    now = time.time() if now is None else now
    gone = 0
    for job in [j for j in _JOBS.values() if j.private and now - j.created > PRIVATE_TTL_SEC]:
        if job.task is not None and not job.task.done():
            job.task.cancel()
        _JOBS.pop(job.id, None)
        gone += 1
    if JOBS_DIR.is_dir():
        for p in JOBS_DIR.iterdir():
            if p.name in _reading:
                continue
            if p.name not in _JOBS or not _JOBS[p.name].private:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                gone += 1
    return gone


def boot() -> None:
    """The web process starts: a private job's leftover upload (the
    process stopped while reading one) is removed. Never fatal."""
    try:
        sweep()
    except Exception as e:  # noqa: BLE001
        log.warning("chat doc jobs: boot sweep failed: %s", type(e).__name__)


async def shutdown() -> None:
    """The web process stops: running jobs end where they are (a thread's
    job carries on from its next part after the restart)."""
    for job in list(_JOBS.values()):
        if job.task is not None and not job.task.done():
            job.task.cancel()
    _JOBS.clear()


@router.get("/doc-jobs/speed", dependencies=READ)
async def doc_speed(chars: int = Query(0, ge=0, le=doc_extract.MAX_TEXT_CHARS * 2)) -> dict[str, Any]:
    """This server's measured speed for documents, and (with ``chars``) how
    long a summary of a document that long would take here: what a phone
    compares with its own before it offers "Use home server". ``ai``
    false: the household turned AI features off (no offer)."""
    speed = await measured_speed()
    sizes = longdoc.even_chunks(chars) if chars else []
    est = longdoc.estimate_seconds(sizes, speed) if sizes else None
    return {"ai": not _docs.ai_turned_off(), "model": core_settings.ollama_model,
            "speed": speed.to_json() if speed else None, "chunk_tokens": longdoc.MAP_CHUNK_TOKENS,
            "parts": len(sizes) or None, "estimate_s": round(est, 1) if est is not None else None}


@router.post("/doc-jobs", dependencies=SEND, responses=_docs._REFUSALS)
async def start_private_job(file: UploadFile = File(...), question: str = Form("", max_length=2000)) -> Any:
    """"Use home server" from a phone chat: summarize ``file`` here and send
    the progress and the summary back over SSE (``event: job``, ``event:
    progress``, ``event: done``). Nothing is kept: the file is deleted as
    soon as it has been read, its text lives in this process's memory
    only, and the job is forgotten when the phone deletes it
    (``DELETE /doc-jobs/{id}``), after 24 hours, or at a restart."""
    sweep()
    if sum(1 for j in _JOBS.values() if j.private) >= MAX_PRIVATE_JOBS:
        return _docs._refused(429, "busy", "The home server is already reading other phones' documents. "
                              "Try again in a few minutes.")
    name = _docs._clean_name(file.filename, "")
    kind, refusal = _docs._kind_or_refusal(name)
    if refusal is not None:
        return refusal
    job_id = uuid.uuid4().hex
    work = JOBS_DIR / job_id
    _reading.add(job_id)
    work.mkdir(parents=True, exist_ok=True)
    try:
        dest = work / f"input{Path(name).suffix.lower()}"
        try:
            await _docs._copy_upload(file, dest)
        except _docs._TooLarge:
            return _docs._too_large()
        try:
            result = await runner.extract(dest, kind or "")
        except runner.ExtractFailed as e:
            return _docs._refused(422, "unreadable", str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _reading.discard(job_id)
    docs = [{"token": "", "name": name, "parts": result.get("parts") or []}]
    chunks = longdoc.plan_chunks(docs)
    if not chunks:
        return _docs._refused(422, "unreadable", _docs.qa_notes.NO_TEXT)
    speed = await measured_speed()
    estimate = longdoc.estimate_seconds([c.chars for c in chunks], speed)
    job = Job(job_id=job_id, docs=docs, chunks=chunks, question=question.strip(), private=True, estimate_s=estimate)
    job.fallback = speed
    _start(job)
    return _stream(job_events(job))


@router.get("/doc-jobs/{job_id}", dependencies=READ)
async def get_job(job_id: str) -> dict[str, Any]:
    """A job's progress, or its result once it ended (a phone coming back
    to a private job, a dashboard re-opening a chat)."""
    return (await _job(job_id)).view()


@router.get("/doc-jobs/{job_id}/events", dependencies=READ)
async def job_stream(job_id: str) -> StreamingResponse:
    """The job's progress as SSE from now on (the same events as the start)."""
    return _stream(job_events(await _job(job_id)))


@router.post("/doc-jobs/{job_id}/stop", dependencies=DEVICE)
async def stop_job(job_id: str) -> dict[str, Any]:
    """Stops the job: the part being read is abandoned, the notes kept, and
    the reply is the summary of what was read ("(stopped)")."""
    job = await _job(job_id)
    await stop(job)
    return job.view()


@router.post("/doc-jobs/{job_id}/resume", dependencies=SEND)
async def resume_job(job_id: str) -> Any:
    """Carry on: a stopped (or failed) home-server chat's job reads the parts
    it hasn't, then writes the whole summary as a new reply."""
    job = await _job(job_id)
    if job.private:
        return _docs._refused(409, "private", "A phone's job can't carry on: its document was deleted when it ended.")
    if job.task is not None and not job.task.done():
        return job.view()
    if job.state not in ("stopped", "failed"):
        return _docs._refused(409, "not_stopped", "That job isn't stopped.")
    job.state, job.stop_requested, job.result, job.error = "running", False, None, None
    job.current = job.read
    job.started = time.monotonic()
    job.done_times = []
    job.fallback = await measured_speed()
    remaining = [c.chars for c, n in zip(job.chunks, job.notes) if n is None]
    job.estimate_s = longdoc.eta_seconds([], remaining, None, job.fallback)
    if not await _save(job):
        raise HTTPException(status_code=404, detail="job not found")
    _start(job)
    return job.view()


@router.delete("/doc-jobs/{job_id}", dependencies=DEVICE)
async def delete_job(job_id: str) -> dict[str, Any]:
    """A phone took its private job's summary: the job goes, its text with
    it. (A home-server chat's job goes with its chat.)"""
    job = _JOBS.get(job_id) if _valid(job_id) else None
    if job is None or not job.private:
        raise HTTPException(status_code=404, detail="job not found")
    if job.task is not None and not job.task.done():
        job.task.cancel()
    _JOBS.pop(job_id, None)
    job.docs = []
    return {"deleted": job_id}
