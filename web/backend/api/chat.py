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

Message details (V017): an assistant row keeps ``stats`` — Ollama's token
counts and durations for the reply, the server's own first-token and wall
times, how much of the thread was sent as context and which model setting
answered (:func:`_reply_stats`). A user row keeps the ``device_id`` of the
install that sent it — self-asserted, like queue provenance, and kept only
when it is a well-formed id (``devices.valid_device_id``) — and reads join
``devices`` for its current name.
"""

from __future__ import annotations

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
from typing import Any, AsyncIterator, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import ai_features, egress, executors, image_safety, live_env, lookup_answer
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
    # The header chip: "Allow in this chat" on or off (V037).
    internet_allowed: Optional[bool] = None


@router.get("/threads", dependencies=READ)
async def list_threads(archived: bool = False) -> dict[str, Any]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT t.id, t.title, t.archived, t.created_at, t.updated_at,
                           t.internet_allowed, t.used_internet,
                           COUNT(m.id) AS message_count,
                           (SELECT LEFT(content, 120) FROM chat_messages
                             WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_snippet
                      FROM chat_threads t
                      LEFT JOIN chat_messages m ON m.thread_id = t.id
                     WHERE t.archived = :arch
                     GROUP BY t.id
                     ORDER BY t.updated_at DESC
                    """
                ),
                {"arch": archived},
            )
        ).mappings().all()
    return {"threads": [dict(r) | {
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    } for r in rows]}


@router.post("/threads", dependencies=DEVICE)
async def create_thread(body: ThreadCreate) -> dict[str, Any]:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO chat_threads (title) VALUES (:t)
                    RETURNING id, title, archived, created_at, updated_at, internet_allowed, used_internet
                    """
                ),
                {"t": body.title},
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
    sets, params = [], {"id": thread_id}
    if body.title is not None:
        sets.append("title = :t")
        params["t"] = body.title
    if body.archived is not None:
        sets.append("archived = :a")
        params["a"] = body.archived
    if body.internet_allowed is not None:
        sets.append("internet_allowed = :ia")
        params["ia"] = body.internet_allowed
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
    itself, mid-message, or a picture's tokens with it."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT role, content, images FROM chat_messages
                     WHERE thread_id = :id AND error IS NULL
                       AND NOT (role = 'assistant' AND COALESCE(stats ? 'lookup_card', false))
                     ORDER BY id DESC LIMIT :lim
                    """
                ),
                {"id": thread_id, "lim": _HISTORY_LIMIT},
            )
        ).all()
    fitted = _fit_history(
        [(role, content, images if with_images else None) for role, content, images in rows],
        budget,
    )
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
    has_images = bool(images)
    override = (body.model or "").strip()
    model = override or (
        core_settings.ollama_vision_model if has_images else core_settings.ollama_model
    )
    model_role = "override" if override else ("vision" if has_images else "chat")
    # Well-formed or nothing, as every other route that reads a device id.
    device_id = valid_device_id(body.device_id)

    async with session_scope() as s:
        thread = (
            await s.execute(text("SELECT person_id, title, internet_allowed FROM chat_threads WHERE id = :id"),
                            {"id": thread_id})
        ).first()
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
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
        # First user message titles the thread (cheap heuristic, editable).
        await s.execute(
            text(
                """
                UPDATE chat_threads
                   SET updated_at = now(),
                       title = COALESCE(title, LEFT(:c, 60))
                 WHERE id = :t
                """
            ),
            {"t": thread_id, "c": body.content.strip().splitlines()[0]},
        )
        await _notify_chat(s, "message")
        await s.commit()

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
    profile = model_system = None
    if refusal is None:
        profile = await _thread_profile(thread.person_id)
        if with_images:
            model_system = await ollama_client.model_system_or_none(model)
        budget = None
        window = (core_settings.ollama_vision_num_ctx or core_settings.ollama_num_ctx) if with_images else 0
        if window:
            longest = ollama_client.chat_system_prompt(profile, pictures=True, model_system=model_system)
            budget = max(0, window - _text_tokens(longest) - _reply_room())
        history = await _history_for_model(thread_id, with_images=with_images, budget=budget)
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
    # Lookups (part 5): the model is told about LOOK_UP / READ_PAGE on a
    # plain text turn, and a "look up …" or "what does this page say: …"
    # needs no model to say so. A help turn and a picture turn don't.
    lookups_offered = refusal is None and help_turn is None and not vision_call
    proposal: lookup_answer.Proposal | None = None
    if lookups_offered:
        proposal = lookup_answer.fast_path(body.content)
        system = {
            "role": "system",
            "content": system["content"] + "\n\n"
            + lookup_answer.tool_prompt(internet=not egress.internet_turned_off()),
        }
    num_ctx = (core_settings.ollama_vision_num_ctx if vision_call else 0) or core_settings.ollama_num_ctx
    stream_knobs: dict[str, Any] = (
        {"think": False, "num_ctx": core_settings.ollama_vision_num_ctx} if vision_call else {}
    )
    async with session_scope() as s:
        in_thread = (
            await s.execute(
                text("SELECT COUNT(*) FROM chat_messages WHERE thread_id = :id AND error IS NULL"),
                {"id": thread_id},
            )
        ).scalar_one()
    thread_allowed = bool(getattr(thread, "internet_allowed", False))

    async def sse() -> AsyncIterator[str]:
        nonlocal proposal
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
        elif proposal is None:
            # While a reply might still be a lookup proposal ("LOOK_UP: …"),
            # its first characters are held back, so a proposal never
            # reaches the screen as text.
            held = ""
            holding = lookups_offered
            proposing = False
            try:
                async with contextlib.aclosing(ollama_client.chat_stream(
                    [system, *history], model=model, stats=ollama_stats,
                    num_predict=_reply_cap(), **stream_knobs,
                )) as stream:
                    async for delta in stream:
                        if first_token_ms is None:
                            first_token_ms = (time.monotonic() - started) * 1000
                        if holding:
                            held += delta
                            head = held.lstrip()
                            if not proposing and len(head) < 12 and lookup_answer.may_be_proposal(head):
                                continue
                            if proposing or _PROPOSAL_START.match(head):
                                proposing = True
                                body_line = head.split("\n", 1)
                                if (len(body_line) > 1 and body_line[0].strip()) or len(head) > 400:
                                    break       # the proposal's line is complete
                                continue
                            holding = False
                            delta, held = held, ""
                        chunks.append(delta)
                        yield f"event: delta\ndata: {json.dumps({'text': delta})}\n\n"
                if held:
                    proposal = lookup_answer.parse_proposal(held) if proposing else None
                    if proposal is None:
                        chunks.append(held)
                        yield f"event: delta\ndata: {json.dumps({'text': held})}\n\n"
                if chunks and proposal is None and ollama_stats.get("done_reason") == "length":
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

        if proposal is not None and error is None:
            # A lookup: local sources answer at once; anything that would
            # leave the house waits for the person's allow (the card).
            async for event in _lookup_turn(
                request, thread_id=thread_id, question=body.content, proposal=proposal,
                model=model, history=history, thread_allowed=thread_allowed,
            ):
                yield event
            return

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
        if code is not None:
            stats["error_code"] = code
        if help_turn is not None:
            stats["help"] = {
                "covered": help_turn.covered,
                "sources": [src.as_dict() for src in help_turn.sources],
                "model_asked": fixed_reply is None,
            }
        row = await _insert_assistant_row(
            thread_id, content=content, model=None if fixed_reply is not None else model,
            error=error, stats=stats,
        )
        if error is not None:
            payload = {"detail": error, "code": code, "model": model}
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"
        yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _insert_assistant_row(
    thread_id: int, *, content: str, model: str | None, error: str | None, stats: dict[str, Any],
    used_internet: bool = False,
) -> Any:
    """Write one assistant row (and touch the thread), returning it as the
    message reads (``_MSG_COLUMNS``)."""
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
                {"t": thread_id, "c": content, "m": model, "e": error, "st": json.dumps(stats)},
            )
        ).mappings().first()
        await s.execute(
            text(
                "UPDATE chat_threads SET updated_at = now()"
                + (", used_internet = true" if used_internet else "")
                + " WHERE id = :t"
            ),
            {"t": thread_id},
        )
        await _notify_chat(s, "message")
        await s.commit()
    return row


# ─── Lookups (assistant plan part 5, W1) ────────────────────────────────────
# A reply that starts like a proposal: held back until its line is complete.
_PROPOSAL_START = re.compile(r"^\W*(?:look[_ ]?up|read[_ ]?page)\s*[:(]", re.I)
# How long the hop to the core's lookup may take (several sources, each
# capped at 8 s, and a page of up to 2 MB).
_LOOKUP_TIMEOUT_SEC = 45.0


def _lookup_client() -> httpx.AsyncClient:
    """The client for the hop to the core's ``POST /v1/phone/lookup`` (a
    seam: the tests hand the real core app, or a fake, in its place)."""
    return httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_SEC)


def always_allow() -> bool:
    """The household's "Always allow internet" (Settings → Chat & AI →
    Search), read live: a save is seen on the next message."""
    return live_env.flag("lookup_always_allow", bool(getattr(core_settings, "lookup_always_allow", False)))


class _LookupFailed(Exception):
    def __init__(self, status: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


async def _core_lookup(request: Request | None, proposal: lookup_answer.Proposal, *, internet: bool) -> dict[str, Any]:
    """The core's lookup with the caller's own credentials: the payload of
    ``POST /v1/phone/lookup``. Raises :class:`_LookupFailed`."""
    body: dict[str, Any] = (
        {"url": proposal.query} if proposal.kind == "page" else {"query": proposal.query, "internet": internet}
    )
    url = f"{domovoi_url().rstrip('/')}/v1/phone/lookup"
    headers = auth_forward_headers(request) if request is not None else {}
    try:
        async with _lookup_client() as client:
            r = await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise _LookupFailed(504, "The lookup took too long.", "lookup_timeout")
    except Exception as e:  # noqa: BLE001 — the core is down or restarting
        log.warning("chat lookup: the core is unreachable: %s", type(e).__name__)
        raise _LookupFailed(502, "Domovoi's lookup service isn't answering.", "core_unreachable")
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        code = payload.get("code") if isinstance(payload, dict) else None
        if r.headers.get(egress.REFUSAL_HEADER) == egress.REFUSAL_VALUE:
            code = "internet_off"
        raise _LookupFailed(r.status_code, str(detail or "The lookup didn't work."), code)
    return payload if isinstance(payload, dict) else {}


class _Result:
    """A result from the core's JSON, shaped as lookup_answer reads it."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.title = str(d.get("title") or "")[:200]
        self.url = d.get("url") if isinstance(d.get("url"), str) else None
        self.site = str(d.get("site") or "")[:200]
        self.snippet = str(d.get("snippet") or "")[:600]
        self.extract = str(d.get("extract") or "")[:4000]
        self.source_name = str(d.get("source_name") or "")[:120]
        self.source_class = str(d.get("source_class") or "web")
        self.licence = d.get("licence") if isinstance(d.get("licence"), str) else None

    @property
    def text(self) -> str:
        return self.extract or self.snippet


def _card_text(proposal: lookup_answer.Proposal, destination: str) -> str:
    if proposal.kind == "page":
        return f"May I read this page? {proposal.query} → {destination}"
    return f"May I look this up online? “{proposal.query}” → {destination}"


def _destination(proposal: lookup_answer.Proposal, sources_payload: dict[str, Any] | None) -> str:
    if proposal.kind == "page":
        from urllib.parse import urlsplit

        try:
            return urlsplit(proposal.query).hostname or "that site"
        except ValueError:
            return "that site"
    if sources_payload and isinstance(sources_payload.get("destination"), str):
        return sources_payload["destination"]
    return "your home server's search: Wikipedia and Wikidata first, then the web"


async def _lookup_turn(
    request: Request | None, *, thread_id: int, question: str, proposal: lookup_answer.Proposal,
    model: str, history: list[dict[str, Any]], thread_allowed: bool, decided: bool = False,
) -> AsyncIterator[str]:
    """What a proposal becomes: an answer from this server's own sources
    (no question asked), the permission card, or, with the person's allow
    (``decided``, the chat's own, or the household's), an answer from what
    the internet sources found. Under internet "never" nothing leaves."""
    internet_on = not egress.internet_turned_off()
    allowed = decided or thread_allowed or always_allow()
    if proposal.kind == "search" and not (allowed and internet_on):
        try:
            local = await _core_lookup(request, proposal, internet=False)
        except _LookupFailed as e:
            local = {}
            log.info("chat lookup (local): %s %s", e.status, e.code)
        if local.get("results"):
            async for ev in _answer_from(thread_id, question, proposal, local, model, history):
                yield ev
            return
    if not internet_on:
        text_ = (lookup_answer.NEVER_REPLY if proposal.kind == "search" else
                 "This Domovoi is set to stay off the internet (Settings → Internet), so I can't read that page.")
        async for ev in _fixed(thread_id, text_, proposal, {"internet_used": False}, reason="internet_off"):
            yield ev
        return
    if not allowed:
        sources = None
        if proposal.kind == "search":
            try:
                sources = await _core_sources(request)
            except Exception:  # noqa: BLE001 — the card says it plainly anyway
                sources = None
        dest = _destination(proposal, sources)
        card = {
            "id": uuid.uuid4().hex, "kind": proposal.kind, "query": proposal.query, "destination": dest,
            "state": "pending",
        }
        row = await _insert_assistant_row(
            thread_id, content=_card_text(proposal, dest), model=None, error=None,
            stats={"model_role": "lookup", "lookup_card": card},
        )
        yield f"event: lookup_permission\ndata: {json.dumps({'message_id': row['id'], **card})}\n\n"
        yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"
        return
    try:
        found = await _core_lookup(request, proposal, internet=True)
    except _LookupFailed as e:
        if e.code == "internet_off":
            async for ev in _fixed(thread_id, lookup_answer.NEVER_REPLY, proposal, {"internet_used": False},
                                   reason="internet_off"):
                yield ev
            return
        text_ = (e.detail if proposal.kind == "page" and e.status == 422 else
                 "I couldn't look that up just now: " + e.detail[:1].lower() + e.detail[1:])
        async for ev in _fixed(thread_id, text_, proposal, {"internet_used": proposal.kind == "page"},
                               reason=e.code or "lookup_failed"):
            yield ev
        return
    async for ev in _answer_from(thread_id, question, proposal, found, model, history):
        yield ev


async def _core_sources(request: Request | None) -> dict[str, Any]:
    url = f"{domovoi_url().rstrip('/')}/v1/lookup/sources"
    headers = auth_forward_headers(request) if request is not None else {}
    async with _lookup_client() as client:
        r = await client.get(url, headers=headers)
    return r.json() if r.status_code < 400 else {}


def _lookup_stats(proposal: lookup_answer.Proposal, payload: dict[str, Any], lines: list[Any], *,
                  covered: bool, model_asked: bool, reason: str | None = None) -> dict[str, Any]:
    out = {
        "kind": proposal.kind, "query": proposal.query,
        "internet_used": bool(payload.get("internet_used")),
        "sources": [ln.as_dict() for ln in lines],
        "notice": payload.get("notice") if isinstance(payload.get("notice"), str) else None,
        "covered": covered, "model_asked": model_asked,
    }
    if reason:
        out["reason"] = reason
    return out


async def _fixed(thread_id: int, text_: str, proposal: lookup_answer.Proposal, payload: dict[str, Any], *,
                 reason: str, lines: list[Any] | None = None) -> AsyncIterator[str]:
    lines = lines or []
    content = lookup_answer.fixed_reply(text_, lines, notice=payload.get("notice"))
    yield f"event: delta\ndata: {json.dumps({'text': content})}\n\n"
    stats = {"model_role": "lookup",
             "lookup": _lookup_stats(proposal, payload, lines, covered=False, model_asked=False, reason=reason)}
    row = await _insert_assistant_row(thread_id, content=content, model=None, error=None, stats=stats,
                                      used_internet=bool(payload.get("internet_used")))
    yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"


async def _answer_from(
    thread_id: int, question: str, proposal: lookup_answer.Proposal, payload: dict[str, Any], model: str,
    history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """Answer ``question`` from what the lookup found: a few ranked
    passages, fenced, no tools; the sources line written here."""
    results = [_Result(d) for d in (payload.get("results") or []) if isinstance(d, dict)][:3]
    lines = lookup_answer.source_lines(results)
    if not results:
        text_ = lookup_answer.NOTHING_FOUND if payload.get("internet_used") else lookup_answer.NOTHING_FOUND_LOCAL
        async for ev in _fixed(thread_id, text_, proposal, payload, reason="nothing_found"):
            yield ev
        return
    ask = proposal.query if proposal.kind == "search" else question
    passages = lookup_answer.rank_passages(ask, results)
    if proposal.kind == "search" and not lookup_answer.covers(ask, passages):
        async for ev in _fixed(thread_id, lookup_answer.COULDNT_FIND, proposal, payload, reason="not_covered",
                               lines=lines):
            yield ev
        return
    system = {"role": "system", "content": lookup_answer.system_prompt(question, passages, bot=core_settings.bot_name)}
    turns = [m for m in history if m.get("role") in ("user", "assistant")]
    if not turns or turns[-1].get("role") != "user":
        turns.append({"role": "user", "content": question})
    chunks: list[str] = []
    ollama_stats: dict[str, Any] = {}
    started = time.monotonic()
    first_token_ms: float | None = None
    error = code = None
    try:
        async with contextlib.aclosing(ollama_client.chat_stream(
            [system, *[{"role": t["role"], "content": t["content"]} for t in turns]], model=model,
            stats=ollama_stats, num_predict=_reply_cap(),
        )) as stream:
            async for delta in stream:
                if first_token_ms is None:
                    first_token_ms = (time.monotonic() - started) * 1000
                chunks.append(delta)
                yield f"event: delta\ndata: {json.dumps({'text': delta})}\n\n"
    except Exception as e:  # noqa: BLE001
        code, error = _failure(e, model, "chat")
        log.warning("chat lookup answer failed (thread %s, %s): %s", thread_id, code, type(e).__name__)
    reply = "".join(chunks)
    content = lookup_answer.finish(reply, lines, notice=payload.get("notice")) if reply else ""
    if reply and content.startswith(reply) and len(content) > len(reply):
        yield f"event: delta\ndata: {json.dumps({'text': content[len(reply):]})}\n\n"
    elif reply and content != reply:
        # The honest line replaced a reply that wandered: say it as sent.
        yield f"event: replace\ndata: {json.dumps({'text': content})}\n\n"
    if not content and error is None:
        code, error = _plain(ERR_EMPTY, model, "chat")
    stats = _reply_stats(ollama_stats, first_token_ms=first_token_ms, wall_ms=(time.monotonic() - started) * 1000,
                         context_sent=len(turns), context_in_thread=len(turns), model_role="lookup")
    stats["lookup"] = _lookup_stats(proposal, payload, lines, covered=not lookup_answer.couldnt_find(reply),
                                    model_asked=True)
    if code is not None:
        stats["error_code"] = code
    row = await _insert_assistant_row(thread_id, content=content, model=model, error=error, stats=stats,
                                      used_internet=bool(payload.get("internet_used")))
    if error is not None:
        yield f"event: error\ndata: {json.dumps({'detail': error, 'code': code, 'model': model})}\n\n"
    yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"


class LookupDecision(BaseModel):
    # once: this lookup only. chat: this lookup and every later one in this
    # chat (the chip shows it). no: not now; answered without looking.
    decision: Literal["once", "chat", "no"]
    device_id: Optional[str] = Field(None, max_length=64)


@router.get("/internet", dependencies=READ)
async def chat_internet() -> dict[str, Any]:
    """What the conversation header's chip reads: the household's internet
    answer (``never`` turns lookups off) and its "Always allow internet"."""
    policy = egress.policy() or ""
    return {"internet": policy, "always_allow": always_allow() and policy != "never",
            "lookups": True}


@router.post("/threads/{thread_id}/lookups/{message_id}", dependencies=SEND)
async def decide_lookup(
    request: Request,
    body: LookupDecision,
    thread_id: int = PathParam(..., ge=1, le=2**63 - 1),
    message_id: int = PathParam(..., ge=1, le=2**63 - 1),
) -> StreamingResponse:
    """The person's answer to a permission card (``stats.lookup_card`` on
    assistant row ``message_id``): ``once`` / ``chat`` look it up and
    answer from what was found (``chat`` also allows this chat's later
    lookups); ``no`` answers without looking. Streamed like a send
    (``delta`` / ``replace`` / ``done`` / ``error``). A card is answered
    once: 409 for a card already answered, 404 for no such card."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT m.stats, t.internet_allowed
                      FROM chat_messages m JOIN chat_threads t ON t.id = m.thread_id
                     WHERE m.id = :m AND m.thread_id = :t AND m.role = 'assistant'
                       FOR UPDATE OF m
                    """
                ),
                {"m": message_id, "t": thread_id},
            )
        ).first()
        stats = row[0] if row is not None else None
        if isinstance(stats, str):
            stats = json.loads(stats)
        card = (stats or {}).get("lookup_card") if isinstance(stats, dict) else None
        if not isinstance(card, dict):
            raise HTTPException(status_code=404, detail="no such lookup card")
        if card.get("state") != "pending":
            raise HTTPException(status_code=409, detail="that lookup card was already answered")
        state = {"once": "allowed_once", "chat": "allowed_chat", "no": "declined"}[body.decision]
        card = {**card, "state": state}
        await s.execute(
            text("UPDATE chat_messages SET stats = CAST(:st AS JSONB) WHERE id = :m"),
            {"st": json.dumps({**stats, "lookup_card": card}), "m": message_id},
        )
        if body.decision == "chat":
            await s.execute(text("UPDATE chat_threads SET internet_allowed = true WHERE id = :t"), {"t": thread_id})
        question = (
            await s.execute(
                text(
                    "SELECT content FROM chat_messages WHERE thread_id = :t AND id < :m AND role = 'user' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"t": thread_id, "m": message_id},
            )
        ).scalar_one_or_none() or str(card.get("query") or "")
        await _notify_chat(s, "lookup-decided")
        await s.commit()
    kind = card.get("kind") if card.get("kind") in ("search", "page") else "search"
    proposal = lookup_answer.Proposal(kind, str(card.get("query") or "")[:2000])
    model = core_settings.ollama_model
    history = await _history_for_model(thread_id, with_images=False)

    async def sse() -> AsyncIterator[str]:
        if body.decision == "no":
            async for ev in _declined_answer(thread_id, history, model):
                yield ev
            return
        async for ev in _lookup_turn(
            request, thread_id=thread_id, question=question, proposal=proposal, model=model,
            history=history, thread_allowed=True, decided=True,
        ):
            yield ev

    return StreamingResponse(
        sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _declined_answer(thread_id: int, history: list[dict[str, Any]], model: str) -> AsyncIterator[str]:
    """"Not now": the model answers from what it knows, told it didn't
    look, with no tools."""
    system = {"role": "system", "content": ollama_client.chat_system_prompt(None) + "\n\n" + lookup_answer.DECLINED_NOTE}
    chunks: list[str] = []
    ollama_stats: dict[str, Any] = {}
    started = time.monotonic()
    error = code = None
    try:
        async with contextlib.aclosing(ollama_client.chat_stream(
            [system, *history], model=model, stats=ollama_stats, num_predict=_reply_cap(),
        )) as stream:
            async for delta in stream:
                chunks.append(delta)
                yield f"event: delta\ndata: {json.dumps({'text': delta})}\n\n"
    except Exception as e:  # noqa: BLE001
        code, error = _failure(e, model, "chat")
    content = "".join(chunks)
    if not content and error is None:
        code, error = _plain(ERR_EMPTY, model, "chat")
    stats = _reply_stats(ollama_stats, first_token_ms=None, wall_ms=(time.monotonic() - started) * 1000,
                         context_sent=len(history), context_in_thread=len(history), model_role="chat")
    stats["lookup"] = {"declined": True, "internet_used": False, "sources": []}
    if code is not None:
        stats["error_code"] = code
    row = await _insert_assistant_row(thread_id, content=content, model=model, error=error, stats=stats)
    if error is not None:
        yield f"event: error\ndata: {json.dumps({'detail': error, 'code': code, 'model': model})}\n\n"
    yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"


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
    try:
        payload = r.json()
    except ValueError:
        payload = {"detail": r.text[:500]}
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
