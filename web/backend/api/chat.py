"""Text-chat web API — the dashboard's ``/api/chat`` surface.

Claude-desktop-style threads (V007: ``chat_threads`` / ``chat_messages``)
answered directly by the local Ollama over ``clients/ollama.chat_stream``.
Deliberately independent of the voice pipeline's chat mode (Letta, per-room
sessions); ``chat_threads.letta_agent_id`` is the parked bridge for backing
a thread with a stateful agent later.

Send flow (``POST /threads/{id}/messages``): persist the user row, then
stream the assistant reply as **SSE** (``text/event-stream`` — ``delta``
events per token chunk, one final ``done`` event carrying the persisted
message). The full assistant row is written when the stream ends, so an
interrupted stream loses nothing but the tail. Vision: a message carrying
image uploads is answered by ``ollama_vision_model`` instead of the QA
model; images ride base64 in the Ollama chat payload. A message with
images whose vision model isn't installed (or isn't set) is answered at
once with a plain refusal and no Ollama call (:func:`send_message`).
Every call opens with the same system message
(``ollama.chat_system_prompt``): the model answers as Domovoi, this
household's assistant, with the thread's person's profile when the thread
has one; a call carrying pictures adds how to read them and keeps the
vision model's own Modelfile instructions, and its history is cut to the
vision context window (:func:`_fit_history`).

Failures (:func:`_failure`): whatever stopped a reply — the model missing,
Ollama unreachable or too slow, any other error it reported — reaches the
user as a plain sentence with a machine-readable code (``event: error``
``{detail, code, model}``, kept on the row as ``error`` plus
``stats.error_code``). The exception itself goes to the server log only:
its text names internal hosts, ports and library help URLs.

Uploads land in ``~/.domovoi/chat_uploads/<token><ext>`` (token = uuid hex,
server-generated — the client never names files here) and are referenced
from ``chat_messages.images`` as ``[{token, name}]``. The model gets each
picture upright and at most 1280 px on its long edge (:func:`_vision_bytes`),
from a copy kept beside the upload as ``<token>-1280.jpg|.png``; a call
that carries pictures also sends ``think: false`` and the vision context
window (``ollama_vision_num_ctx``).

Thread/message writes are DEVICE tier (REV-1): a valid ``X-Device-Token``
or an admin Bearer, with the pre-setup LAN grace kept. Thread history
contains only what the user typed here, but it is the household's
conversation all the same, and the send verb spends the box's model time.
READING it is device tier too (owner decision 2026-09-26: household
speech and personal content are for paired devices only): the thread list
(titles and the last-message snippet are message text), a thread's
messages and the images staged into them take the read half of the gate,
``require_device_read`` — the household token or an admin Bearer, the
dashboard cookie, or ``?device_token=`` for the ``<img src>`` an upload is
rendered through. The model picker stays open; it lists models, not
anything anybody said. Mutations fire
``chat_changed`` NOTIFY → the ``chat.changed`` WS event so a second open
dashboard's thread list stays fresh.

Voice turns (V025): the app's chat mic sends a recording to
``POST /threads/{id}/voice``, which this process hands to the core's
``POST /v1/phone/voice`` (domovoi/phone_voice.py) with the caller's
credentials — the core answers it like a satellite turn and writes both
rows itself, ``source = 'voice'``, the user row carrying who voice
identification heard (``speaker_person_id`` / ``speaker_confidence``).
Every row read out says its ``source``; a voice row adds the speaker's
name and whether the match was clear (``speaker_confident``).

History and titles (V034, assistant plan part 7): a thread is titled
with the first five words of its first message, then, once a reply has
finished, by the chat model in the background (:func:`_write_title`); a
name a person gave it (``PATCH`` with a title: ``title_by_user``) is never
written over. ``GET /threads?q=`` searches titles and messages;
``archived`` moves a thread to the archived list and back; ``POST
.../retry`` ("try again") answers the last question again in place of
its reply. A reply that didn't see the whole thread says where its view
began (``stats.window_from_id``), for the app's window divider.

Message details (V017): an assistant row keeps ``stats`` — Ollama's token
counts and durations for the reply, the server's own first-token and wall
times, how much of the thread was sent as context and which model setting
answered (:func:`_reply_stats`). A user row keeps the ``device_id`` of the
install that sent it — self-asserted, like queue provenance, and kept only
when it is a well-formed id (``devices.valid_device_id``) — and reads join
``devices`` for its current name.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import ai_features, chat_titles, executors, image_safety
from domovoi.admin_auth import require_device, require_device_read
from domovoi.clients import ollama as ollama_client
from domovoi.help_desk import answer as help_answer
from domovoi.help_desk import delivery as help_delivery
from domovoi.config import settings as core_settings
from domovoi.profile_context import build_profile_prefix
from domovoi.voice_identifier import speaker_is_confident
from web.backend.api.devices import valid_device_id
from web.backend.db import session_scope
from web.backend.domovoi_client import auth_forward_headers, domovoi_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]


def _require_ai() -> None:
    """A message to the model, or a picture for one, while the household's
    AI features are off (Settings → Models, domovoi/ai_features.py): the 409
    ai-off refusal, before anything is stored or Ollama is asked. Reading
    the threads already there stays open."""
    if ai_features.turned_off():
        raise ai_features.http_exception("chat")


# Sending to the model: device tier, and AI features on.
SEND = [Depends(require_device), Depends(_require_ai)]

UPLOADS_DIR = Path.home() / ".domovoi" / "chat_uploads"
_UPLOAD_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_MAX_IMAGES_PER_MESSAGE = 4
# How much history rides along to the model per turn (messages, not tokens —
# local models have modest contexts; the UI shows everything from the DB).
_HISTORY_LIMIT = 30
# The longest edge a picture reaches the model with. A CPU benchmark of
# vision models (2026-10-11/12, owner-approved 2026-10-12) found a 12 MP
# phone photo sent as it is fills a 4096-token context on its own (the
# reply stopped after ~60 tokens) and can take minutes to read on a CPU. At
# 1280 px a picture is ~1.1k tokens, and the default model still read the
# screenshots, receipts and charts in the test.
_VISION_MAX_EDGE = 1280
# What one picture at that size costs in the vision model's context: ~1.1k
# tokens measured (config.ollama_vision_num_ctx); qwen3-vl reads a 32 px
# square per token, 40 x 30 for a 1280 x 960 photo.
_PICTURE_TOKENS = 1200
# Room kept in the vision window for the reply when the thread's history is
# cut to fit (_fit_history), when the reply has no cap
# (ollama_chat_num_predict 0); with one, the cap is the room kept.
_REPLY_RESERVE_TOKENS = 1024
# What a reply stopped by ollama_chat_num_predict ends with: the text it
# had, "…", and this, so it reads as stopped rather than broken off.
CUT_OFF_NOTE = "\n\n*Stopped here: this reply reached the chat's length limit.*"
_EXIF_ORIENTATION = 0x0112

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


async def _notify_chat(s: Any, what: str) -> None:
    await s.execute(text("SELECT pg_notify('chat_changed', :w)"), {"w": what})


# ─── Threads ────────────────────────────────────────────────────────────────
class ThreadCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


class ThreadPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    archived: Optional[bool] = None


# The longest search a client may send, and the most threads it returns.
_SEARCH_MAX_CHARS = 200
_SEARCH_LIMIT = 100
# Characters of a matching message shown around the match.
_MATCH_CONTEXT = 40


def _like_pattern(q: str) -> str:
    """``q`` as an ILIKE pattern that matches it anywhere, literally: its
    own ``%``, ``_`` and backslash are escaped (with ``ESCAPE``)."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# The ILIKE escape character, as SQL (a backslash in a string literal).
_LIKE_ESCAPE = "ESCAPE '\\'"


def match_snippet(content: str | None, q: str) -> str | None:
    """The words around the first place ``q`` appears in ``content`` (case
    ignored), with "…" where it was cut; None when it isn't there."""
    if not content or not q:
        return None
    flat = " ".join(content.split())
    at = flat.lower().find(q.lower())
    if at < 0:
        return None
    start = max(0, at - _MATCH_CONTEXT)
    end = min(len(flat), at + len(q) + _MATCH_CONTEXT)
    return ("…" if start > 0 else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


@router.get("/threads", dependencies=READ)
async def list_threads(
    archived: Optional[bool] = Query(
        None,
        description="true: the archived threads; false: the others. Without a search the default is false; "
                    "a search (`q`) looks in both unless this says which.",
    ),
    q: Optional[str] = Query(
        None, max_length=_SEARCH_MAX_CHARS,
        description="Search: threads whose title or any message contains this (case ignored), newest first, "
                    "at most 100, each with `match_snippet` (the words around the first match in a message).",
    ),
) -> dict[str, Any]:
    query = (q or "").strip()
    where: list[str] = []
    params: dict[str, Any] = {}
    if archived is not None or not query:
        where.append("t.archived = :arch")
        params["arch"] = bool(archived)
    match_col = "NULL::text AS match_content"
    limit = ""
    if query:
        params["pat"] = _like_pattern(query)
        where.append(
            f"(t.title ILIKE :pat {_LIKE_ESCAPE} OR EXISTS (SELECT 1 FROM chat_messages mm "
            f"WHERE mm.thread_id = t.id AND mm.content ILIKE :pat {_LIKE_ESCAPE}))"
        )
        match_col = (
            "(SELECT content FROM chat_messages mm WHERE mm.thread_id = t.id "
            f"AND mm.content ILIKE :pat {_LIKE_ESCAPE} ORDER BY mm.id DESC LIMIT 1) AS match_content"
        )
        limit = f"LIMIT {_SEARCH_LIMIT}"
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    f"""
                    SELECT t.id, t.title, t.archived, t.title_by_user, t.created_at, t.updated_at,
                           COUNT(m.id) AS message_count,
                           (SELECT LEFT(content, 120) FROM chat_messages
                             WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_snippet,
                           {match_col}
                      FROM chat_threads t
                      LEFT JOIN chat_messages m ON m.thread_id = t.id
                     WHERE {' AND '.join(where)}
                     GROUP BY t.id
                     ORDER BY t.updated_at DESC
                     {limit}
                    """
                ),
                params,
            )
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        match = d.pop("match_content", None)
        d |= {"created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}
        if query:
            d["match_snippet"] = match_snippet(match, query)
        out.append(d)
    return {"threads": out}


@router.post("/threads", dependencies=DEVICE)
async def create_thread(body: ThreadCreate) -> dict[str, Any]:
    title = chat_titles.plain_title(body.title) or None
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO chat_threads (title, title_by_user) VALUES (:t, :by)
                    RETURNING id, title, archived, title_by_user, created_at, updated_at
                    """
                ),
                # A thread made with a name was named by whoever made it.
                {"t": title, "by": title is not None},
            )
        ).mappings().first()
        if (body.title or "").startswith(help_delivery.TITLE_PREFIX):
            # A help chat ("ask Domovoi"): its title is final (chat history's
            # title job leaves it; V034's column, when it is there).
            await help_delivery.mark_title_kept(s, row["id"])
        await _notify_chat(s, "thread-created")
        await s.commit()
    return dict(row) | {
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "message_count": 0,
        "last_snippet": None,
    }


@router.patch("/threads/{thread_id}", dependencies=DEVICE)
async def patch_thread(thread_id: int, body: ThreadPatch) -> dict[str, Any]:
    """Rename and/or archive a thread. A title set here is the person's
    (``title_by_user``): the model's title job never writes over it. A
    blank title is refused (400)."""
    sets, params = [], {"id": thread_id}
    if body.title is not None:
        title = chat_titles.plain_title(body.title)
        if not title:
            raise HTTPException(status_code=400, detail="a title can't be empty")
        sets.append("title = :t")
        sets.append("title_by_user = true")
        params["t"] = title
    if body.archived is not None:
        sets.append("archived = :a")
        params["a"] = body.archived
    if not sets:
        raise HTTPException(status_code=400, detail="nothing to change")
    async with session_scope() as s:
        r = await s.execute(
            text(f"UPDATE chat_threads SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="thread not found")
        await _notify_chat(s, "thread-patched")
        await s.commit()
    if body.title is not None:
        # A model title still being written would only be thrown away.
        cancel_title_job(thread_id)
    return {"ok": True}


@router.delete("/threads/{thread_id}", dependencies=DEVICE)
async def delete_thread(thread_id: int) -> dict[str, Any]:
    """Delete a thread and its messages (CASCADE). Upload files referenced by
    the thread are removed too when no other message references them."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text("SELECT images FROM chat_messages WHERE thread_id = :id AND images IS NOT NULL"),
                {"id": thread_id},
            )
        ).scalars().all()
        r = await s.execute(text("DELETE FROM chat_threads WHERE id = :id"), {"id": thread_id})
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="thread not found")
        still_used = set(
            (
                await s.execute(
                    text(
                        """
                        SELECT jsonb_array_elements(images)->>'token'
                          FROM chat_messages WHERE images IS NOT NULL
                        """
                    )
                )
            ).scalars().all()
        )
        await _notify_chat(s, "thread-deleted")
        await s.commit()
    for images in rows:
        for img in images or []:
            token = img.get("token")
            if token and token not in still_used and _TOKEN_RE.match(token):
                # The upload, and the copy the model was sent (_vision_bytes).
                for p in [*UPLOADS_DIR.glob(f"{token}.*"), *UPLOADS_DIR.glob(f"{token}-*")]:
                    p.unlink(missing_ok=True)
    return {"deleted": thread_id}


# ─── Messages ───────────────────────────────────────────────────────────────
_MSG_COLUMNS = (
    "m.id, m.role, m.content, m.images, m.model, m.error, m.created_at, m.stats, m.device_id, "
    "m.source, m.speaker_person_id, m.speaker_confidence"
)


def _msg_dict(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["created_at"] = d["created_at"].isoformat()
    if isinstance(d.get("stats"), str):
        d["stats"] = json.loads(d["stats"])
    if d.get("speaker_confidence") is not None:
        d["speaker_confidence"] = round(float(d["speaker_confidence"]), 4)
    # The same judgement the core made when the turn came in
    # (domovoi/phone_voice.py), so a message reads back drawn as it arrived.
    d["speaker_confident"] = d.get("speaker_person_id") is not None and speaker_is_confident(
        d.get("speaker_confidence")
    )
    # Whether "try again" may replace this reply (the chat model's own
    # reply to a typed message); always false for a person's message.
    d["retryable"] = d.get("role") == "assistant" and reply_retryable(d.get("source"), d.get("stats"))
    stats = d.get("stats") if isinstance(d.get("stats"), dict) else {}
    if d.get("role") == "assistant" and d.get("error") and not stats.get("error_code"):
        # A row from before the plain answers kept the exception's own
        # text ("Client error '404 Not Found' for url 'http://localhost:
        # 11434/api/chat' ..."). Read it out as a plain answer too; the
        # row itself is left as it was.
        role = stats.get("model_role") or (
            "vision" if d.get("model") and d.get("model") == core_settings.ollama_vision_model else "chat"
        )
        code, d["error"] = _legacy_failure(str(d["error"]), d.get("model") or "", role)
        d["stats"] = {**stats, "error_code": code}
    return d


# ─── Plain answers for what stopped a reply ─────────────────────────────────
# The codes the clients key their own wording on (web chat_details.js,
# Android ChatErrors.kt). Kept on the assistant row as ``stats.error_code``.
ERR_VISION_MODEL_MISSING = "vision_model_missing"  # pictures, and no vision model to read them
ERR_MODEL_MISSING = "model_missing"                # the model for a text turn isn't installed
ERR_VISION_UNSUPPORTED = "vision_unsupported"      # the vision model can't take pictures
ERR_MODEL_MEMORY = "model_memory"                  # not enough memory to load the model
ERR_UNREACHABLE = "model_unreachable"              # Ollama down, or the connection dropped
ERR_TIMEOUT = "model_timeout"                      # Ollama took too long to take the request
ERR_MODEL = "model_error"                          # anything else Ollama reported
ERR_EMPTY = "empty_reply"                          # the model answered with nothing

_SETTINGS_MODELS = "Settings → Models"


def vision_missing_text(model: str) -> str:
    """Why a message with pictures can't be answered here. Worded the same
    in both clients, which say it before a picture is even attached."""
    if not (model or "").strip():
        return (
            "Pictures need a vision model, and none is set on this Domovoi. "
            f"An admin can pick one in {_SETTINGS_MODELS}."
        )
    return (
        f"Pictures need the vision model {model}, which isn't installed on this "
        f"Domovoi. An admin can install it in {_SETTINGS_MODELS}, or pick another "
        "vision model there."
    )


def _missing_text(model: str, role: str) -> tuple[str, str]:
    if role == "vision":
        return ERR_VISION_MODEL_MISSING, vision_missing_text(model)
    if not (model or "").strip():
        return ERR_MODEL_MISSING, (
            f"No chat model is set on this Domovoi. An admin can pick one in {_SETTINGS_MODELS}."
        )
    return ERR_MODEL_MISSING, (
        f"The model {model} isn't installed on this Domovoi. An admin can install it "
        f"in {_SETTINGS_MODELS}, or pick another model there."
    )


def _plain(code: str, model: str, role: str) -> tuple[str, str]:
    """(code, the sentence the user reads) for a failure class."""
    if code in (ERR_MODEL_MISSING, ERR_VISION_MODEL_MISSING):
        return _missing_text(model, role)
    name = f"model {model}" if model else "model"
    return code, {
        ERR_VISION_UNSUPPORTED: (
            f"The vision {name} can't read pictures. An admin can pick one that "
            f"does in {_SETTINGS_MODELS}."
        ),
        ERR_MODEL_MEMORY: (
            f"The {name} needs more memory than this Domovoi has free right now. Try again "
            f"in a few minutes; if it keeps happening, an admin can pick a smaller model "
            f"in {_SETTINGS_MODELS}."
        ),
        ERR_UNREACHABLE: (
            "Domovoi couldn't reach its language model. It may be restarting; "
            "try again in a minute."
        ),
        ERR_TIMEOUT: (
            "The language model took too long to respond. It may still be loading; "
            "try again in a minute."
        ),
        ERR_EMPTY: "The model returned an empty answer. Try again, or ask it another way.",
    }.get(code, (
        "The language model couldn't answer this message. Try again; if it keeps "
        "happening, an admin can find the reason in the server log."
    ))


def _failure(exc: BaseException, model: str, role: str) -> tuple[str, str]:
    """(code, plain sentence) for whatever stopped a reply. Never the
    exception's text: that is for the log."""
    if isinstance(exc, ollama_client.OllamaChatError):
        said = exc.detail.lower()
        if exc.status == 404 or "not found" in said:
            code = ERR_MODEL_MISSING
        elif "image input" in said or "multimodal" in said or (
            "not support" in said and ("image" in said or "vision" in said)
        ):
            code = ERR_VISION_UNSUPPORTED
        elif "memory" in said:
            code = ERR_MODEL_MEMORY
        else:
            code = ERR_MODEL
    elif isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        code = ERR_UNREACHABLE
    elif isinstance(exc, httpx.TimeoutException):
        code = ERR_TIMEOUT
    elif isinstance(exc, (httpx.TransportError, OSError)):
        # The connection broke mid-reply (Ollama stopped, or crashed).
        code = ERR_UNREACHABLE
    else:
        code = ERR_MODEL
    return _plain(code, model, role)


def _legacy_failure(text: str, model: str, role: str) -> tuple[str, str]:
    """(code, plain sentence) for an error kept as raw text by an older
    server, read from the words alone."""
    said = text.lower()
    if "returned nothing" in said:
        code = ERR_EMPTY
    elif "404" in said or "not found" in said:
        code = ERR_MODEL_MISSING
    elif "memory" in said:
        code = ERR_MODEL_MEMORY
    elif "timed out" in said or "timeout" in said:
        code = ERR_TIMEOUT
    elif "connect" in said:
        code = ERR_UNREACHABLE
    else:
        code = ERR_MODEL
    return _plain(code, model, role)


def _ms(ns: Any) -> float | None:
    return round(ns / 1_000_000, 1) if isinstance(ns, (int, float)) else None


def _reply_stats(
    ollama: dict[str, Any],
    *,
    first_token_ms: float | None,
    wall_ms: float,
    context_sent: int,
    context_in_thread: int,
    model_role: str,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    """The ``chat_messages.stats`` object for one reply (keys: V017).
    ``ollama`` is what :func:`ollama_client.chat_stream` reported, possibly
    nothing at all if the stream failed; absent figures are left out.
    ``num_ctx`` is the context window the call asked for (default: the
    Q&A one; 0 or None: none was sent)."""
    if num_ctx is None:
        num_ctx = core_settings.ollama_num_ctx
    out: dict[str, Any] = {
        "prompt_tokens": ollama.get("prompt_eval_count"),
        "output_tokens": ollama.get("eval_count"),
        "total_ms": _ms(ollama.get("total_duration")),
        "load_ms": _ms(ollama.get("load_duration")),
        "prompt_ms": _ms(ollama.get("prompt_eval_duration")),
        "generate_ms": _ms(ollama.get("eval_duration")),
        "done_reason": ollama.get("done_reason"),
        "first_token_ms": round(first_token_ms, 1) if first_token_ms is not None else None,
        "wall_ms": round(wall_ms, 1),
        "context_sent": context_sent,
        "context_in_thread": context_in_thread,
        "context_limit": _HISTORY_LIMIT,
        "num_ctx": num_ctx or None,
        "model_role": model_role,
    }
    tokens, gen_ns = ollama.get("eval_count"), ollama.get("eval_duration")
    if isinstance(tokens, int) and isinstance(gen_ns, (int, float)) and gen_ns > 0:
        out["tokens_per_sec"] = round(tokens / (gen_ns / 1e9), 1)
    return {k: v for k, v in out.items() if v is not None}


@router.get("/threads/{thread_id}/messages", dependencies=READ)
async def list_messages(thread_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        exists = (
            await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="thread not found")
        rows = (
            await s.execute(
                text(
                    """
                    SELECT {cols}, d.name AS device_name, p.name AS speaker_name
                      FROM chat_messages m
                      LEFT JOIN devices d ON d.device_id = m.device_id
                      LEFT JOIN people p ON p.id = m.speaker_person_id
                     WHERE m.thread_id = :id ORDER BY m.id
                    """.format(cols=_MSG_COLUMNS)
                ),
                {"id": thread_id},
            )
        ).mappings().all()
    return {"messages": [_msg_dict(r) for r in rows]}


class SendBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000)
    images: list[dict[str, str]] = Field(default_factory=list, max_length=_MAX_IMAGES_PER_MESSAGE)
    model: Optional[str] = Field(None, max_length=200)
    # The sending install (the device tier's usual id), for the details view.
    device_id: Optional[str] = Field(None, max_length=64)


def _upload_path(token: str) -> Path | None:
    if not _TOKEN_RE.match(token or ""):
        return None
    for p in UPLOADS_DIR.glob(f"{token}.*"):
        if p.is_file():
            return p
    return None


def _vision_copy_path(src: Path) -> Path:
    """Where the copy of an upload the model is sent lives: beside it, as
    ``<token>-1280.jpg`` for a JPEG and ``<token>-1280.png`` for anything
    else (lossless, so the text in a screenshot stays sharp). The ``-``
    keeps it out of :func:`_upload_path`'s ``<token>.*``."""
    ext = ".jpg" if src.suffix.lower() in (".jpg", ".jpeg") else ".png"
    return src.with_name(f"{src.stem}-{_VISION_MAX_EDGE}{ext}")


def _vision_bytes(src: Path) -> bytes:
    """The picture ``src`` as the model gets it: upright (a phone photo's
    EXIF orientation applied, which Ollama ignores) and at most
    ``_VISION_MAX_EDGE`` px on its long edge, from the copy beside the
    upload, made on first use. A picture that is already both is sent as
    it was uploaded, with no copy. Sync — run it in the ``pictures`` pool.

    Only a JPEG, PNG, GIF, WebP or BMP is decoded, and only up to
    ``image_safety.MAX_IMAGE_PIXELS`` (2026-10-16 delta review S7: every
    Pillow format was tried, so an EPS named ``cat.png`` reached
    Ghostscript, and a 164 KB PNG of 169 megapixels decoded to 350 MB). A
    bigger one raises :class:`~domovoi.image_safety.PictureRefused` and is
    not sent at all (the model would only decode it instead); one of
    another kind, or one Pillow can't read, is sent as it is, as before,
    never decoded here. Raises ``OSError`` when the upload itself can't be
    read."""
    copy = _vision_copy_path(src)
    if copy.is_file():
        try:
            return copy.read_bytes()
        except OSError:
            pass
    try:
        from PIL import Image, ImageOps

        with image_safety.open_image(src, image_safety.PICTURE_FORMATS) as im:
            upright = im.getexif().get(_EXIF_ORIENTATION, 1) in (1, None)
            if upright and max(im.size) <= _VISION_MAX_EDGE:
                return src.read_bytes()
            jpeg = copy.suffix == ".jpg"
            out = im
            if jpeg and out.mode not in ("RGB", "L"):
                out = out.convert("RGB")
            elif out.mode not in ("RGB", "RGBA", "L", "LA"):
                # Palette and other modes: resampled as RGB(A), not nearest-pixel.
                alpha = "transparency" in out.info or out.mode in ("PA", "RGBa", "La")
                out = out.convert("RGBA" if alpha else "RGB")
            # Shrunk before it is turned: a JPEG then decodes at a fraction of
            # its size, and turning moves 1280 px, not the whole photo.
            out.thumbnail((_VISION_MAX_EDGE, _VISION_MAX_EDGE), Image.Resampling.LANCZOS)
            out = ImageOps.exif_transpose(out)
        buf = io.BytesIO()
        # No EXIF in the copy: it is upright now, and the rest is not the
        # model's business.
        if jpeg:
            out.save(buf, "JPEG", quality=90)
        else:
            out.save(buf, "PNG")
        data = buf.getvalue()
    except image_safety.PictureRefused as e:
        if e.too_large:
            raise
        log.warning("chat: picture %s is not a kind the chat decodes (%s); sending it as it is",
                    src.name, e)
        return src.read_bytes()
    except Exception as e:  # noqa: BLE001 — any decode failure: send it as it is
        log.warning("chat: picture %s not shrunk for the model (%s: %s); sending it as it is",
                    src.name, type(e).__name__, e)
        return src.read_bytes()
    part = copy.with_name(f"{copy.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        part.write_bytes(data)
        os.replace(part, copy)
    except OSError as e:
        log.warning("chat: could not keep the model's copy of %s (%s); it is made again next time",
                    src.name, e)
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
    return data


def _text_tokens(content: str | None) -> int:
    """A generous guess at a message's tokens: ~3 characters each (English
    runs nearer 4) plus the chat template's few per message."""
    return len(content or "") // 3 + 4


def _fit_history(
    rows: list[tuple[str, str, Any]], budget: int | None,
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """``rows`` (newest first: role, content, images) cut to what fits in
    ``budget`` tokens, oldest first. The newest message (the one being
    answered) always goes, pictures and all. Walking back from it, a
    message goes whole while it fits; one whose pictures don't fit goes
    without them (its words, and the reply about them, still do); the walk
    stops at the first message whose words don't fit. ``budget`` None: no
    cut (the text model's calls, as before)."""
    kept: list[tuple[str, str, list[dict[str, Any]]]] = []
    used = 0
    for i, (role, content, images) in enumerate(rows):
        imgs = list(images or [])
        words = _text_tokens(content)
        pics = _PICTURE_TOKENS * len(imgs)
        if budget is not None and i > 0:
            if used + words > budget:
                break
            if used + words + pics > budget:
                imgs, pics = [], 0
        used += words + pics
        kept.append((role, content, imgs))
    kept.reverse()
    return kept


async def _history_for_model(
    thread_id: int, with_images: bool = True, budget: int | None = None,
    *, exclude_id: int | None = None, window: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The last N persisted turns in Ollama chat shape, images inlined as
    base64 (vision context survives across turns within the window), each
    upright and shrunk to the model's size (:func:`_vision_bytes`).

    ``with_images=False`` leaves the pictures out — for a turn answered by
    the text model, which can't read them: Ollama refuses a request that
    hands a text-only model an image (400, "Multimodal data provided, but
    model does not support multimodal requests." on 0.34), so one picture
    early in a thread would otherwise break every text question after it.

    ``budget`` (tokens) cuts the turns to fit (:func:`_fit_history`): the
    vision call's window, less the system message and room for the reply.
    Ollama would otherwise drop the oldest part of an overlong prompt
    itself, mid-message, or a picture's tokens with it.

    ``exclude_id``: a message left out (the reply "try again" replaces).
    ``window``, when given, is told ``from_id``: the oldest message sent,
    where the model's view of the thread begins (the app's "earlier
    messages are beyond what Domovoi can keep in mind" divider)."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, role, content, images FROM chat_messages
                     WHERE thread_id = :id AND error IS NULL
                       AND id <> COALESCE(CAST(:skip AS BIGINT), -1)
                     ORDER BY id DESC LIMIT :lim
                    """
                ),
                {"id": thread_id, "lim": _HISTORY_LIMIT, "skip": exclude_id},
            )
        ).all()
    fitted = _fit_history(
        [(role, content, images if with_images else None) for _id, role, content, images in rows],
        budget,
    )
    if window is not None and fitted:
        # _fit_history keeps the newest len(fitted) rows, in order.
        window["from_id"] = rows[len(fitted) - 1][0]
    out: list[dict[str, Any]] = []
    for role, content, images in fitted:
        msg: dict[str, Any] = {"role": role, "content": content}
        b64s = []
        for img in images:
            p = _upload_path(img.get("token", ""))
            if p is not None:
                try:
                    data = await executors.run("pictures", _vision_bytes, p)
                except OSError:
                    continue
                except image_safety.PictureRefused as e:
                    log.warning("chat: picture %s left out for the model: %s", p.name, e)
                    continue
                b64s.append(base64.b64encode(data).decode())
        if b64s:
            msg["images"] = b64s
        out.append(msg)
    return out


def _reply_cap() -> int | None:
    """``ollama_chat_num_predict`` as a cap, or None for none."""
    try:
        cap = int(core_settings.ollama_chat_num_predict or 0)
    except (TypeError, ValueError):
        return None
    return cap if cap > 0 else None


def _reply_room() -> int:
    """How much of the vision window is kept for the reply."""
    return _reply_cap() or _REPLY_RESERVE_TOKENS


async def _thread_profile(person_id: int | None) -> str | None:
    """The profile blob (memories, favorites, preferences) of the person a
    thread belongs to (``chat_threads.person_id``), for the system message,
    as a voice turn gets its speaker's (router.py). A typed message doesn't
    say who typed it, so a thread with no person gets none. A failure here
    costs the personal touch, never the reply."""
    if person_id is None:
        return None
    try:
        async with session_scope() as s:
            return await build_profile_prefix(int(person_id), s) or None
    except Exception as e:  # noqa: BLE001 — answer without it
        log.warning("chat: the profile of person %s could not be read: %s", person_id, e)
        return None


# What the vision model is asked about a screenshot in a help turn, before
# the help is searched: the page and the words on it, nothing else.
SCREENSHOT_DESCRIBE_PROMPT = (
    "Describe this screenshot of the Domovoi dashboard or app in at most three short lines: "
    "which page or screen it shows (its title), and any error, warning or message text on it, "
    "copied exactly. If you can't read something, say so. Nothing else."
)


async def _describe_screenshot(history: list[dict[str, Any]], model: str) -> str:
    """The vision model's short reading of the pictures in the newest
    message ("" when there are none, or it fails: the text is searched
    alone then)."""
    last = history[-1] if history else {}
    if not last.get("images"):
        return ""
    messages = [
        {"role": "system", "content": SCREENSHOT_DESCRIBE_PROMPT},
        {"role": "user", "content": "What does this screenshot show?", "images": last["images"]},
    ]
    out: list[str] = []
    try:
        async for delta in ollama_client.chat_stream(
            messages, model=model, think=False, num_predict=160,
            num_ctx=core_settings.ollama_vision_num_ctx or None,
        ):
            out.append(delta)
    except Exception as e:  # noqa: BLE001 — the answer then reads the picture itself
        log.warning("chat help: the screenshot could not be read first: %s: %s", type(e).__name__, str(e)[:200])
        return ""
    return "".join(out)[:600]


async def _help_turn(
    content: str, *, thread_title: str | None, request: Request | None,
    history: list[dict[str, Any]], vision_call: bool, model: str,
):
    """The help turn for this message, or None. Never fails the send: a
    help that can't be read leaves the chat answering as it always has."""
    from web.backend.api import help as help_api

    help_thread = (thread_title or "").startswith(help_delivery.TITLE_PREFIX)
    try:
        if not help_thread and not help_answer.looks_like_help(content):
            return None
        extra = await _describe_screenshot(history, model) if vision_call else ""
        # The household view, even for an admin: this chat is shared.
        return await help_api.help_turn_for(
            content, help_thread=help_thread, has_screenshot=vision_call, extra_query=extra,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("chat help: the help could not be read: %s: %s", type(e).__name__, str(e)[:200])
        return None


@router.post("/threads/{thread_id}/messages", dependencies=SEND)
async def send_message(thread_id: int, body: SendBody, request: Request) -> StreamingResponse:
    """Persist the user turn, stream the assistant reply as SSE, persist the
    assistant turn at stream end. Events:

      ``event: delta``  data: {"text": "..."}
      ``event: done``   data: {<the persisted assistant message row>}
      ``event: error``  data: {"detail": "...", "code": "...", "model": "..."}
                        (also persisted on the row: ``error``, ``stats.error_code``)

    A message with pictures whose vision model isn't installed — or none
    is set — gets its answer up front: the user turn is kept, the reply is
    the refusal (``vision_model_missing``) and Ollama is never asked. When
    Ollama can't be asked whether the model is there, the send goes ahead
    and a failure is reported as it happens.

    The thread's first message titles it with its first five words
    (``domovoi/chat_titles.py``); once a reply has finished, the chat model
    writes a better title in the background (:func:`_write_title`), unless
    a person has named the thread. A title being written when the next
    message arrives is stopped, and written after that reply instead.

    Help (``web/backend/api/help.py``, part 9): in a thread titled "Help: …",
    or for a question about Domovoi whose answer its help holds, the model
    is given the help desk's instruction and the passages found instead of
    the chat persona, and the reply ends with the sections it came from
    (written here, not by the model; ``stats.help``). A help thread whose
    question the help doesn't cover is answered "The manual doesn't cover
    that." with no model asked. A screenshot in a help turn is read by the
    vision model first (which page, what error), and that reading is
    searched too.
    """
    images = [
        {"token": i.get("token", ""), "name": str(i.get("name", ""))[:200]}
        for i in body.images
        if _upload_path(i.get("token", "")) is not None
    ]
    # Well-formed or nothing, as every other route that reads a device id.
    device_id = valid_device_id(body.device_id)

    async with session_scope() as s:
        thread = (
            await s.execute(text("SELECT person_id, title FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        # The model's attention is the reply's now: every title job stops
        # and waits for it (a refused request holds nothing up).
        stop_title_jobs()
        await s.execute(
            text(
                """
                INSERT INTO chat_messages (thread_id, role, content, images, device_id)
                VALUES (:t, 'user', :c, CAST(:imgs AS JSONB), :dev)
                """
            ),
            {"t": thread_id, "c": body.content,
             "imgs": json.dumps(images) if images else None, "dev": device_id},
        )
        # The first user message titles the thread: its first five words.
        await s.execute(
            text(
                """
                UPDATE chat_threads
                   SET updated_at = now(),
                       title = COALESCE(title, :c)
                 WHERE id = :t
                """
            ),
            {"t": thread_id, "c": chat_titles.first_words_title(body.content)},
        )
        await _notify_chat(s, "message")
        await s.commit()

    return await _answer(thread_id, thread.person_id, has_images=bool(images), override=body.model)


#: Who wrote a reply that "try again" may replace: the chat model, for a
#: typed message (``stats.model_role``). A spoken turn's reply ("voice":
#: the core answered it, maybe with an action such as a timer) and any
#: other writer's (``"help"``: the help desk's delivered steps) are not
#: the chat model's to write again. A row with no ``model_role`` (from
#: before V017) counts as the chat model's when it was typed.
RETRYABLE_ROLES = frozenset({"chat", "vision", "override"})


def reply_retryable(source: str | None, stats: Any) -> bool:
    """Whether "try again" may replace an assistant row with this
    ``source`` and ``stats`` (:data:`RETRYABLE_ROLES`)."""
    if (source or "typed") != "typed":
        return False
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except ValueError:
            stats = None
    role = stats.get("model_role") if isinstance(stats, dict) else None
    if stats and isinstance(stats, dict) and stats.get("matched_handler"):
        return False
    return role is None or role in RETRYABLE_ROLES


# Threads with a retry streaming in this process: a second one waits its
# turn as a 409, not a second answer.
_retrying: set[int] = set()


def _retry_refused(code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": detail, "code": code})


@router.post(
    "/threads/{thread_id}/retry",
    dependencies=SEND,
    responses={409: {"description": '{"detail": "...", "code": "..."}: `nothing_to_retry` (the thread doesn\'t '
                                    "end with a reply to a question), `not_retryable` (a spoken turn's or another "
                                    "writer's reply, not the chat model's), `already_retried` (another try again "
                                    "on this reply is running, or has replaced it)"}},
)
async def retry_reply(thread_id: int) -> StreamingResponse:
    """"Try again": a new reply to the thread's last question, in place of
    the reply it got. Streams as ``POST .../messages`` does; the old row is
    deleted and the new one written in one transaction once the new reply
    is complete (the ``done`` event says which with ``replaces``: the old
    row's id). A retry that fails writes nothing: the thread stays as it
    was, the stream ends with the ``error`` event and a ``done`` carrying
    the reply that was kept (``kept: true``), and it can be tried again.

    Only the chat model's reply to a typed message can be tried again
    (:func:`reply_retryable`): 409 ``not_retryable`` for a spoken turn's
    reply (the core may have set a timer with it) or another writer's, 409
    ``nothing_to_retry`` when the thread doesn't end with a reply to a
    question, 409 ``already_retried`` when another try again is replacing
    it. The model doesn't see the reply being replaced."""
    async with session_scope() as s:
        thread = (
            await s.execute(text("SELECT person_id FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        last = (
            await s.execute(
                text(
                    """
                    SELECT id, role, images, source, stats FROM chat_messages
                     WHERE thread_id = :id ORDER BY id DESC LIMIT 2
                    """
                ),
                {"id": thread_id},
            )
        ).all()
    if len(last) < 2 or last[0].role != "assistant" or last[1].role != "user":
        return _retry_refused("nothing_to_retry", "there is no reply to try again")
    if not reply_retryable(last[0].source, last[0].stats):
        return _retry_refused("not_retryable", "this reply can't be tried again")
    if thread_id in _retrying:
        return _retry_refused("already_retried", "this reply is already being tried again")
    asked_images = [
        i for i in (last[1].images or []) if _upload_path((i or {}).get("token", "")) is not None
    ]
    stop_title_jobs()
    # The thread is marked as retrying by the stream itself (_answer), so a
    # client that drops before the first byte leaves no mark behind.
    return await _answer(thread_id, thread.person_id, has_images=bool(asked_images), replace_id=last[0].id)


async def _answer(
    thread_id: int,
    person_id: int | None,
    *,
    has_images: bool,
    override: str | None = None,
    replace_id: int | None = None,
) -> StreamingResponse:
    """The SSE reply to the thread's last question (:func:`send_message`,
    and :func:`retry_reply` with ``replace_id``: the reply it replaces,
    left out of what the model sees and deleted once the new one is
    written)."""
    override = (override or "").strip()
    model = override or (
        core_settings.ollama_vision_model if has_images else core_settings.ollama_model
    )
    model_role = "override" if override else ("vision" if has_images else "chat")

    refusal: tuple[str, str] | None = None
    if model_role == "vision":
        if not model.strip():
            refusal = _missing_text(model, model_role)
        else:
            tags = await ollama_client.tags_or_none()
            if tags is not None and not ollama_client.model_installed(model, tags):
                refusal = _missing_text(model, model_role)

    # The text model can't read pictures, so a text turn leaves the
    # thread's earlier ones out (see _history_for_model). A turn that may
    # carry pictures is cut to the vision window, less the system message
    # (at its longest: with the pictures paragraph) and room for the reply.
    with_images = model_role != "chat"
    history: list[dict[str, Any]] = []
    window: dict[str, Any] = {}
    profile = model_system = None
    if refusal is None:
        profile = await _thread_profile(person_id)
        if with_images:
            model_system = await ollama_client.model_system_or_none(model)
        budget = None
        ctx = (core_settings.ollama_vision_num_ctx or core_settings.ollama_num_ctx) if with_images else 0
        if ctx:
            longest = ollama_client.chat_system_prompt(profile, pictures=True, model_system=model_system)
            budget = max(0, ctx - _text_tokens(longest) - _reply_room())
        history = await _history_for_model(
            thread_id, with_images=with_images, budget=budget, exclude_id=replace_id, window=window,
        )
    # A call that carries pictures: no reasoning before the answer (only
    # the answer is streamed, so the reader would wait in silence), and the
    # vision context window, which four pictures need.
    vision_call = any(m.get("images") for m in history)
    # Who is answering (:func:`chat_system_prompt`), first in every call.
    system = {
        "role": "system",
        "content": ollama_client.chat_system_prompt(
            profile, pictures=vision_call, model_system=model_system,
        ),
    }
    # A help turn: the help desk's instruction and its material instead.
    help_turn = None
    fixed_reply: str | None = None
    if refusal is None and not override:
        help_turn = await _help_turn(
            body.content, thread_title=thread.title, request=request, history=history,
            vision_call=vision_call, model=model,
        )
        if help_turn is not None:
            system = {
                "role": "system",
                "content": help_answer.system_prompt(help_turn, bot=core_settings.bot_name, pictures=vision_call)
                + (("\n\n" + model_system.strip()) if model_system and model_system.strip() else ""),
            }
            if not help_turn.covered:
                fixed_reply = help_answer.not_covered_answer(help_turn)
    num_ctx = (core_settings.ollama_vision_num_ctx if vision_call else 0) or core_settings.ollama_num_ctx
    stream_knobs: dict[str, Any] = (
        {"think": False, "num_ctx": core_settings.ollama_vision_num_ctx} if vision_call else {}
    )
    async with session_scope() as s:
        in_thread = (
            await s.execute(
                text(
                    """
                    SELECT COUNT(*) FROM chat_messages
                     WHERE thread_id = :id AND error IS NULL
                       AND id <> COALESCE(CAST(:skip AS BIGINT), -1)
                    """
                ),
                {"id": thread_id, "skip": replace_id},
            )
        ).scalar_one()

    async def sse() -> AsyncIterator[str]:
        # A retry marks its thread for as long as it streams (set and
        # cleared here, never outside the stream); a second one that got
        # this far ends at once with already_retried, writing nothing.
        claimed = False
        if replace_id is not None:
            if thread_id in _retrying:
                payload = {"detail": "this reply is already being tried again", "code": "already_retried",
                           "model": model}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return
            _retrying.add(thread_id)
            claimed = True
        # The model is the reply's while it streams: no title job runs.
        _reply_started()
        try:
            async for event in _reply_events():
                yield event
        finally:
            if claimed:
                _retrying.discard(thread_id)
            _reply_finished()

    async def _reply_events() -> AsyncIterator[str]:
        chunks: list[str] = []
        error: str | None = None
        code: str | None = None
        ollama_stats: dict[str, Any] = {}
        started = time.monotonic()
        first_token_ms: float | None = None
        if refusal is not None:
            code, error = refusal
            log.info("chat refused up front (thread %s, model %r): %s", thread_id, model, code)
        elif fixed_reply is not None:
            # Help that found nothing: the honest line, no model asked.
            chunks.append(fixed_reply)
            yield f"event: delta\ndata: {json.dumps({'text': fixed_reply})}\n\n"
        else:
            try:
                async for delta in ollama_client.chat_stream(
                    [system, *history], model=model, stats=ollama_stats,
                    num_predict=_reply_cap(), **stream_knobs,
                ):
                    if first_token_ms is None:
                        first_token_ms = (time.monotonic() - started) * 1000
                    chunks.append(delta)
                    yield f"event: delta\ndata: {json.dumps({'text': delta})}\n\n"
                if chunks and ollama_stats.get("done_reason") == "length":
                    # The cap stopped it (a loop, or an answer that long):
                    # end it visibly, the same in the stream and the row.
                    tail = "…" + CUT_OFF_NOTE
                    chunks.append(tail)
                    yield f"event: delta\ndata: {json.dumps({'text': tail})}\n\n"
            except Exception as e:  # noqa: BLE001 — surfaced as an SSE error event
                code, error = _failure(e, model, model_role)
                # The detail is for whoever reads the log; the user gets
                # the plain sentence (it would name hosts, ports, URLs).
                log.warning(
                    "chat stream failed (thread %s, model %s, %s): %s: %s",
                    thread_id, model, code, type(e).__name__, str(e)[:500],
                )

        content = "".join(chunks)
        if help_turn is not None and fixed_reply is None and content and error is None:
            # Links no passage gave lose their target; the sources the
            # code knows it gave go under the reply. The done row carries
            # the result; the footer streams too.
            final = help_answer.finish(help_turn, content)
            if final.startswith(content) and len(final) > len(content):
                yield f"event: delta\ndata: {json.dumps({'text': final[len(content):]})}\n\n"
            content = final
        if not content and error is None:
            code, error = _plain(ERR_EMPTY, model, model_role)
        if refusal is not None:
            # No reply was attempted, so there are no reply figures.
            stats: dict[str, Any] = {"model_role": model_role}
        else:
            stats = _reply_stats(
                ollama_stats,
                first_token_ms=first_token_ms,
                wall_ms=(time.monotonic() - started) * 1000,
                context_sent=len(history),
                context_in_thread=int(in_thread),
                model_role=model_role,
                num_ctx=num_ctx,
            )
            # Where the model's view of the thread began, when it didn't
            # see all of it (the app's window divider).
            if window.get("from_id") is not None and len(history) < int(in_thread):
                stats["window_from_id"] = window["from_id"]
        if code is not None:
            stats["error_code"] = code
        if help_turn is not None:
            stats["help"] = {
                "covered": help_turn.covered,
                "sources": [src.as_dict() for src in help_turn.sources],
                "model_asked": fixed_reply is None,
            }
        if replace_id is not None:
            async for event in _finish_retry(replace_id, content, error, code, stats):
                yield event
            return
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        """
                        INSERT INTO chat_messages AS m (thread_id, role, content, model, error, stats)
                        VALUES (:t, 'assistant', :c, :m, :e, CAST(:st AS JSONB))
                        RETURNING {cols}, NULL::text AS device_name, NULL::text AS speaker_name
                        """.format(cols=_MSG_COLUMNS)
                    ),
                    {"t": thread_id, "c": content, "m": None if fixed_reply is not None else model, "e": error, "st": json.dumps(stats)},
                )
            ).mappings().first()
            await s.execute(
                text("UPDATE chat_threads SET updated_at = now() WHERE id = :t"),
                {"t": thread_id},
            )
            await _notify_chat(s, "message")
            await s.commit()
        if error is not None:
            payload = {"detail": error, "code": code, "model": model}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"
        if error is None:
            schedule_title_job(thread_id)

    async def _finish_retry(
        old_id: int, content: str, error: str | None, code: str | None, stats: dict[str, Any],
    ) -> AsyncIterator[str]:
        """The end of a retry. Failed: nothing is written; the error, then
        the kept reply. Done: the old reply is deleted first, in the same
        transaction as the new one is written; when it is already gone
        (another try again replaced it), nothing is written and the stream
        ends with the ``already_retried`` error."""
        if error is not None:
            async with session_scope() as s:
                kept = (
                    await s.execute(
                        text(
                            """
                            SELECT {cols}, NULL::text AS device_name, NULL::text AS speaker_name
                              FROM chat_messages m WHERE m.id = :id AND m.thread_id = :t
                            """.format(cols=_MSG_COLUMNS)
                        ),
                        {"id": old_id, "t": thread_id},
                    )
                ).mappings().first()
            yield f"event: error\ndata: {json.dumps({'detail': error, 'code': code, 'model': model})}\n\n"
            if kept is not None:
                yield f"event: done\ndata: {json.dumps(_msg_dict(kept) | {'kept': True})}\n\n"
            return
        async with session_scope() as s:
            gone = (
                await s.execute(
                    text(
                        """
                        DELETE FROM chat_messages
                         WHERE id = :id AND thread_id = :t AND role = 'assistant'
                        RETURNING id
                        """
                    ),
                    {"id": old_id, "t": thread_id},
                )
            ).first()
            if gone is None:
                await s.rollback()
                payload = {"detail": "this reply was already tried again", "code": "already_retried", "model": model}
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return
            row = (
                await s.execute(
                    text(
                        """
                        INSERT INTO chat_messages AS m (thread_id, role, content, model, error, stats)
                        VALUES (:t, 'assistant', :c, :m, NULL, CAST(:st AS JSONB))
                        RETURNING {cols}, NULL::text AS device_name, NULL::text AS speaker_name
                        """.format(cols=_MSG_COLUMNS)
                    ),
                    {"t": thread_id, "c": content, "m": model, "st": json.dumps(stats)},
                )
            ).mappings().first()
            await s.execute(text("UPDATE chat_threads SET updated_at = now() WHERE id = :t"), {"t": thread_id})
            await _notify_chat(s, "message")
            await s.commit()
        yield f"event: done\ndata: {json.dumps(_msg_dict(row) | {'replaces': old_id})}\n\n"
        schedule_title_job(thread_id)

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Titles written by the model ────────────────────────────────────────────
# After a reply has finished, a thread with no generated title (and no name
# a person gave it) gets one from the chat model, in the background: the
# reply is already on its way, so nothing waits for this. One job at a
# time, from one queue (oldest first): a title never runs beside another
# title. Any message, try again or voice turn to any thread stops the job
# running (it goes back to the front of the queue) and the queue waits
# until no reply is streaming here; it starts again after that reply.
# Titles stay as the first five words when the household's AI features are
# off, in stub runs (AUTO_TITLES), or when the model has failed twice for
# the thread. Threads someone else titled on purpose are skipped:
# `title_generated` already true (the help desk sets it on the "Help: …"
# threads it makes), or a title starting HELP_TITLE_PREFIX.
#
# Limit: a satellite's voice turn goes to the core, not through this
# process, so it doesn't pause the queue; a title can then share the model
# with it. It is short (at most TITLE_MAX_TOKENS tokens, after a
# TITLE_DELAY_SEC pause), so it delays such a turn by about one title.

#: None: on, except in stub runs (USE_STUBS, every test run). Tests of the
#: job set it to True and replace :func:`_model_title`.
AUTO_TITLES: bool | None = None
#: How long after a reply the queue waits before asking the model, so a
#: quick follow-up message doesn't have to stop it.
TITLE_DELAY_SEC = 1.5
#: Threads whose title is the help desk's ("Help: …"): never retitled.
HELP_TITLE_PREFIX = "Help:"
_TITLE_FAILURES_MAX = 2
_title_failures: dict[int, int] = {}
_title_queue: list[int] = []
_title_worker: asyncio.Task[None] | None = None
_title_running: int | None = None
# Replies streaming in this process (sends, retries, voice turns).
_replies_busy = 0


def _ai_turned_off() -> bool:
    """The household's AI features switch (``domovoi.ai_features``): off
    means no model is asked for anything, titles included."""
    return ai_features.turned_off()


def _auto_titles_on() -> bool:
    on = (not core_settings.use_stubs) if AUTO_TITLES is None else AUTO_TITLES
    return on and not _ai_turned_off()


def _stop_worker() -> None:
    """Cancels the queue's worker; the thread it was titling goes back to
    the front of the queue."""
    global _title_worker, _title_running
    if _title_running is not None and _title_running not in _title_queue:
        _title_queue.insert(0, _title_running)
    _title_running = None
    worker, _title_worker = _title_worker, None
    if worker is not None and not worker.done():
        worker.cancel()


def _ensure_worker() -> None:
    global _title_worker
    if not _title_queue:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    worker = _title_worker
    # (A worker of another event loop is one a finished test client left.)
    if worker is not None and not worker.done() and worker.get_loop() is loop:
        return
    _title_worker = loop.create_task(_title_loop(), name="chat-titles")


async def _title_loop() -> None:
    global _title_running
    while _title_queue:
        await asyncio.sleep(TITLE_DELAY_SEC)
        while _replies_busy > 0:
            await asyncio.sleep(0.2)
        if not _title_queue:
            return
        thread_id = _title_queue.pop(0)
        _title_running = thread_id
        try:
            await _write_title(thread_id)
        finally:
            if _title_running == thread_id:
                _title_running = None


def stop_title_jobs() -> None:
    """A message, try again or voice turn is coming: the title being
    written stops (and is written later); the queue waits for the reply."""
    _stop_worker()


def _reply_started() -> None:
    global _replies_busy
    _replies_busy += 1
    _stop_worker()


def _reply_finished() -> None:
    global _replies_busy
    _replies_busy = max(0, _replies_busy - 1)
    _ensure_worker()


def cancel_title_job(thread_id: int) -> None:
    """Drops ``thread_id`` from the title queue, and stops its title if it
    is being written (a rename: the person's name wins)."""
    if thread_id in _title_queue:
        _title_queue.remove(thread_id)
    if _title_running == thread_id:
        _stop_worker()
        if thread_id in _title_queue:
            _title_queue.remove(thread_id)
        _ensure_worker()


def schedule_title_job(thread_id: int) -> None:
    """Queues ``thread_id`` for a title (the job checks for itself whether
    the thread still needs one)."""
    if not _auto_titles_on() or _title_failures.get(thread_id, 0) >= _TITLE_FAILURES_MAX:
        return
    if thread_id not in _title_queue and thread_id != _title_running:
        _title_queue.append(thread_id)
    _ensure_worker()


async def _model_title(question: str, answer: str) -> str | None:
    """What the chat model writes as the title of a chat that began with
    ``question`` and ``answer`` (a seam: the tests answer for it)."""
    model = (core_settings.ollama_model or "").strip()
    if not model:
        return None
    messages = [
        {"role": "system", "content": chat_titles.TITLE_SYSTEM},
        {"role": "user", "content": chat_titles.title_prompt(question, answer)},
    ]
    out: list[str] = []
    async for delta in ollama_client.chat_stream(
        messages, model=model, think=False, num_predict=chat_titles.TITLE_MAX_TOKENS,
    ):
        out.append(delta)
    return "".join(out)


async def _write_title(thread_id: int) -> None:
    """The title job: the thread's first question and first good answer,
    to the model, and its title onto the thread, unless a person has named
    the thread in the meantime (checked again as the title is written), or
    the thread's title was given on purpose (``title_generated`` set by
    whoever made it, or a "Help: …" thread)."""
    async with session_scope() as s:
        flags = (
            await s.execute(
                text("SELECT title, title_by_user, title_generated FROM chat_threads WHERE id = :id"),
                {"id": thread_id},
            )
        ).first()
        if flags is None or flags.title_by_user or flags.title_generated:
            return
        if (flags.title or "").startswith(HELP_TITLE_PREFIX):
            return
        first = (
            await s.execute(
                text(
                    """
                    SELECT role, content FROM chat_messages
                     WHERE thread_id = :id AND error IS NULL
                     ORDER BY id LIMIT 12
                    """
                ),
                {"id": thread_id},
            )
        ).all()
    # The first question, and the first good answer after it.
    q_at = next((i for i, m in enumerate(first) if m.role == "user"), None)
    a_at = None if q_at is None else next(
        (i for i, m in enumerate(first) if i > q_at and m.role == "assistant"), None,
    )
    if a_at is None:
        return
    try:
        raw = await _model_title(first[q_at].content, first[a_at].content)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the first words stay
        _title_failures[thread_id] = _title_failures.get(thread_id, 0) + 1
        log.warning("chat: no title from the model for thread %s: %s: %s",
                    thread_id, type(e).__name__, str(e)[:200])
        return
    title = chat_titles.clean_model_title(raw)
    if title is None:
        _title_failures[thread_id] = _title_failures.get(thread_id, 0) + 1
        return
    async with session_scope() as s:
        r = await s.execute(
            text(
                """
                UPDATE chat_threads SET title = :t, title_generated = true
                 WHERE id = :id AND NOT title_by_user AND NOT title_generated
                   AND COALESCE(title, '') NOT LIKE 'Help:%'
                """
            ),
            {"t": title, "id": thread_id},
        )
        if r.rowcount:
            await _notify_chat(s, "thread-titled")
        await s.commit()


# ─── Voice turns (the app's chat mic) ───────────────────────────────────────
# How long the hop to the core may take: speech-to-text, the router (a cold
# language model is ~54 s on an all-CPU host) and text-to-speech.
_VOICE_TIMEOUT_SEC = 180.0
# The longest recording the core takes (phone_voice.MAX_CLIP_SEC at 16 kHz
# PCM16), plus room for a WAV header; read no further than this.
_VOICE_MAX_BYTES = 30 * 32000 + 4096


def _core_client() -> httpx.AsyncClient:
    """The client for the hop to the core (a seam: the tests hand the real
    core app in its place)."""
    return httpx.AsyncClient(timeout=_VOICE_TIMEOUT_SEC)


@router.post("/threads/{thread_id}/voice", dependencies=DEVICE)
async def send_voice(
    request: Request,
    # chat_threads.id is a BIGINT (422 past it, never a database overflow).
    thread_id: int = PathParam(..., ge=1, le=2**63 - 1),
    audio: UploadFile = File(..., description="16 kHz mono PCM16, raw or as a WAV file"),
    phone_id: str = Form(..., max_length=64),
    player: Optional[str] = Form(None, max_length=2048),
    # False: the phone speaks the reply itself, so the core renders none.
    speak: bool = Form(True),
):
    """One push-to-talk turn from the app's chat mic, answered in this
    thread: forwarded with the caller's credentials to the core's
    ``POST /v1/phone/voice`` (domovoi/phone_voice.py), which transcribes
    it, identifies the speaker, routes it like a satellite turn, speaks
    the reply and writes both rows. The core's answer (transcript, reply,
    base64 WAV, speaker, device actions, the two rows) or refusal comes
    back as it was given; 502 / 504 when the core can't be reached or took
    too long.

    The body is capped before it is parsed by the process-wide transport
    guard (domovoi/transport_guard.py: 1 MiB for a route not listed there,
    on a declared Content-Length and on a streamed overrun alike), which
    leaves room for the longest recording (30 s, 960 kB) and its form."""
    data = await audio.read(_VOICE_MAX_BYTES + 1)
    if len(data) > _VOICE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="a voice turn is at most 30 seconds")
    form = {"phone_id": phone_id, "thread_id": str(thread_id)}
    if player:
        form["player"] = player
    if not speak:
        form["speak"] = "false"
    files = {
        "audio": (
            audio.filename or "turn.pcm", data, audio.content_type or "application/octet-stream",
        ),
    }
    url = f"{domovoi_url().rstrip('/')}/v1/phone/voice"
    # The core's model is this turn's: no title job runs until it is done.
    _reply_started()
    try:
        async with _core_client() as client:
            r = await client.post(url, data=form, files=files, headers=auth_forward_headers(request))
    except httpx.TimeoutException:
        log.warning("chat voice: the core took longer than %.0f s (thread %s)", _VOICE_TIMEOUT_SEC, thread_id)
        return JSONResponse(
            status_code=504,
            content={"detail": "Domovoi took too long to answer. Try again.", "code": "core_timeout"},
        )
    except Exception as e:  # noqa: BLE001 — the core is down or restarting
        log.warning("chat voice: the core is unreachable: %s: %s", type(e).__name__, e)
        return JSONResponse(
            status_code=502,
            content={"detail": "Domovoi's voice service isn't answering.", "code": "core_unreachable"},
        )
    finally:
        _reply_finished()
    try:
        payload = r.json()
    except ValueError:
        payload = {"detail": r.text[:500]}
    if r.status_code == 200:
        # The core wrote the turn and titled a new thread with its first
        # words; the model's title follows here, as after a typed reply.
        schedule_title_job(thread_id)
    return JSONResponse(status_code=r.status_code, content=payload)


# ─── Uploads ────────────────────────────────────────────────────────────────
def _check_picture(data: bytes) -> None:
    """Raise PictureRefused unless ``data`` is a picture of an allowed kind
    and size (from its header). Blocking: the ``pictures`` pool."""
    with image_safety.open_image(io.BytesIO(data), image_safety.PICTURE_FORMATS):
        pass


# ISO base media file brands (the `ftyp` box) of the camera formats a phone
# saves: HEIC (HEVC inside HEIF), plain HEIF, AVIF.
_HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"})
_HEIF_BRANDS = frozenset({b"mif1", b"msf1"})
_AVIF_BRANDS = frozenset({b"avif", b"avis"})


def picture_format_of(head: bytes) -> str | None:
    """What kind of picture ``head`` (a file's first bytes) is, by its
    magic bytes alone (nothing is decoded and no decoder is asked), to name
    it in an upload's refusal: ``"JPEG"``, ``"PNG"``, ``"GIF"``, ``"WEBP"``,
    ``"BMP"``, or a camera format the chat doesn't take: ``"HEIC"``,
    ``"HEIF"``, ``"AVIF"``, ``"JPEG XL"``, ``"TIFF"``. None for anything
    else. The same rules as the Android app's ``sniffPicture``."""
    if head[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[:2] == b"BM" and len(head) >= 14:
        return "BMP"
    if head[4:8] == b"ftyp":
        size = int.from_bytes(head[:4], "big")
        end = min(len(head), size if 16 <= size <= 4096 else 16)
        # The major brand, then the compatible ones (minor version skipped).
        brands = [head[8:12]] + [head[i:i + 4] for i in range(16, end - 3, 4)]
        if any(b in _AVIF_BRANDS for b in brands):
            return "AVIF"
        if any(b in _HEIC_BRANDS for b in brands):
            return "HEIC"
        if any(b in _HEIF_BRANDS for b in brands):
            return "HEIF"
        return None
    if head[:2] == b"\xff\x0a" or head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return "JPEG XL"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    return None


#: The ``code`` of each refusal of a chat upload, beside its ``detail``
#: sentence (2026-10-16, chat photos from phones): the clients word the
#: refusal from it, so these never change meaning.
UPLOAD_REFUSAL_CODES = ("unsupported_format", "too_many_pixels", "file_too_large")
_FORMAT_WORDS = {"WEBP": "WebP"}


def _upload_refused(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    """A refused upload's answer: ``{detail, code, ...}`` — the plain
    sentence a client shows, and the stable code it can word itself."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _unsupported(data: bytes) -> JSONResponse:
    """415 for an upload that isn't a picture the chat reads, naming its
    kind when the bytes show one (``format``; null when they don't)."""
    fmt = picture_format_of(data[:64])
    if fmt is not None and fmt.upper() in image_safety.PICTURE_FORMATS:
        fmt = None      # a broken one, or a good one with a wrong name: not "its kind"
    what = (
        f"That picture is in {_FORMAT_WORDS.get(fmt, fmt)} format, which the chat can't read."
        if fmt else "That file isn't a picture the chat can read."
    )
    return _upload_refused(
        415, "unsupported_format", f"{what} Send a JPEG, PNG, GIF, WebP or BMP.", format=fmt,
    )


@router.post(
    "/uploads",
    dependencies=SEND,
    responses={
        413: {"description": 'Too large: {"detail": "...", "code": "too_many_pixels", "max_megapixels": 40} '
                             'or {"detail": "...", "code": "file_too_large", "max_mb": 20}'},
        415: {"description": 'Not a picture the chat reads: {"detail": "...", "code": "unsupported_format", '
                             '"format": "HEIC" | "HEIF" | "AVIF" | "JPEG XL" | "TIFF" | null}'},
    },
)
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Stage one image for a chat message. Returns the server-generated
    token the send call references; files are capped, extension-checked,
    and must really be a JPEG, PNG, GIF, WebP or BMP of at most
    ``image_safety.MAX_IMAGE_PIXELS`` (read from the header, off the loop;
    nothing is decoded and no other kind is tried: delta review S7).

    A refusal is ``{detail, code, ...}`` (:func:`_upload_refused`):
    ``415 unsupported_format`` with ``format`` (``"HEIC"``, ``"AVIF"``, …
    from the bytes, or null), ``413 too_many_pixels`` with
    ``max_megapixels``, ``413 file_too_large`` with ``max_mb``."""
    ext = Path(file.filename or "").suffix.lower()
    data = await file.read()
    if ext not in _UPLOAD_EXTS:
        return _unsupported(data)
    if len(data) > _MAX_UPLOAD_BYTES:
        return _upload_refused(
            413, "file_too_large",
            f"That picture is too large for the chat: more than {_MAX_UPLOAD_BYTES // 2**20} MB.",
            max_mb=_MAX_UPLOAD_BYTES // 2**20,
        )
    try:
        await executors.run("pictures", _check_picture, data)
    except image_safety.PictureRefused as e:
        if e.too_large:
            return _upload_refused(
                413, "too_many_pixels", f"That picture is too large for the chat: {e}.",
                max_megapixels=image_safety.MAX_IMAGE_PIXELS // 1_000_000,
            )
        return _unsupported(data)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    (UPLOADS_DIR / f"{token}{ext}").write_bytes(data)
    return {"token": token, "name": file.filename or f"image{ext}"}


@router.get("/uploads/{token}", dependencies=READ)
async def get_upload(token: str):
    """Serve a staged/persisted chat image inline (for rendering in the
    transcript). Token-addressed — the client never names paths. The token
    is unguessable, but the image is household content like the thread it
    was sent in, so it answers to the same paired-device read; an
    ``<img src>`` carries the household token as ``?device_token=``."""
    p = _upload_path(token)
    if p is None:
        raise HTTPException(status_code=404, detail="upload not found")
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(p.suffix.lower(), "application/octet-stream")
    # ``private``: this answer depends on the caller's credential, so only
    # the browser that was allowed to read it may keep a copy — never a
    # shared cache between the box and the next device to ask.
    return FileResponse(p, media_type=media, headers={"Cache-Control": "private, max-age=86400"})


# ─── Models for the picker ──────────────────────────────────────────────────
@router.get("/models")
async def chat_models() -> dict[str, Any]:
    """Installed Ollama models + the configured defaults, for the composer's
    model picker. ``vision_installed`` says whether a picture can be
    answered: false when the vision model isn't installed (or none is
    set), null when Ollama couldn't be asked — the clients explain the
    first before a picture is attached, and leave the second to the send."""
    tags = await ollama_client.tags_or_none()
    vision = core_settings.ollama_vision_model
    return {
        "installed": sorted(
            {m.get("name") or m.get("model") for m in tags or [] if (m.get("name") or m.get("model"))}
        ),
        "default_model": core_settings.ollama_model,
        "vision_model": vision,
        "vision_installed": None if tags is None else ollama_client.model_installed(vision, tags),
    }
