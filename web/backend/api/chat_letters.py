"""Letters in Chat: "what does this letter say?" (documents plan 6.4, D6).

A home-server chat message with photos that asks what a letter says or
what to do about it (:func:`domovoi.doc_qa.letter.letter_request`) is
answered here instead of by one vision call:

1. a blurry photo gets its heads-up card before any model is asked
   (chat.py's ``_picture_heads_up``, D3);
2. the vision model says whether the writing is printed or handwritten
   (one word); handwriting gets its card (``headsup`` kind
   ``handwriting``), once a chat, unless silenced;
3. the vision model transcribes each photo on its own;
4. the chat model explains the transcript in a fixed shape, and the app
   checks every date, amount and reference number against it
   (:func:`domovoi.doc_qa.letter.finish`) before anything is shown.

The reply's ``doc`` is ``{"mode": "letter", "sources": [the transcript of
each photo, "photo 1"...], "notes": [...], "letter": {...}}``: the
transcript is kept with the thread (the household's chat, which already
keeps the photo); nothing else is.

``POST /api/chat/letters/read`` is the phone's way in (a phone chat on a
phone whose GPU can't run the photo model): one photo, read here and
**kept nowhere** (in memory, never written), its blur, kind and
transcript sent back, and the explanation written on the phone by its own
model. Device tier, and only while the household's AI features are on.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import time
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from domovoi import ai_features, executors, image_safety
from domovoi.admin_auth import require_device, require_device_read
from domovoi.clients import ollama as ollama_client
from domovoi.config import settings as core_settings
from domovoi.doc_qa import headsup as _headsup
from domovoi.doc_qa import letter as _letter

log = logging.getLogger(__name__)

router = APIRouter()

#: The picture is measured for blur at this size (longest side, grey):
#: the same as the eval that set the threshold (chat-weak-spots.json).
BLUR_EDGE = 384
#: The long edge a photo reaches the vision model with (as chat.py's
#: ``_VISION_MAX_EDGE``).
VISION_EDGE = 1280


def _require_ai() -> None:
    if ai_features.turned_off():
        raise ai_features.http_exception("chat")


SEND = [Depends(require_device), Depends(_require_ai)]
READ = [Depends(require_device_read)]


# ─── Pictures ───────────────────────────────────────────────────────────
def picture_blur(src: Path | bytes) -> float | None:
    """The variance of the Laplacian of a picture, grey, at
    :data:`BLUR_EDGE` (low is blurry); None for one too small to say.
    ``src`` passed the upload's checks. Blocking: the ``pictures`` pool."""
    from PIL import Image

    with Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src) as im:
        im.draft("L", (BLUR_EDGE * 2, BLUR_EDGE * 2))
        grey = im.convert("L")
        grey.thumbnail((BLUR_EDGE, BLUR_EDGE), Image.Resampling.BILINEAR)
        w, h = grey.size
        px = list(grey.getdata())
    rows = [px[y * w:(y + 1) * w] for y in range(h)]
    v = _headsup.blur_variance(rows)
    return None if v == float("inf") else v


def shrunk_jpeg(data: bytes) -> bytes:
    """``data`` (a checked picture) upright, at most :data:`VISION_EDGE` on
    its long edge, as a JPEG, in memory. Raises
    :class:`~domovoi.image_safety.PictureRefused` for one that isn't a
    picture the chat reads. Blocking: the ``pictures`` pool."""
    from PIL import Image, ImageOps

    with image_safety.open_image(io.BytesIO(data), image_safety.PICTURE_FORMATS) as im:
        out = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        out.thumbnail((VISION_EDGE, VISION_EDGE), Image.Resampling.LANCZOS)
        out = ImageOps.exif_transpose(out)
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ─── The model steps ─────────────────────────────────────────────────────
def _vision_model() -> str:
    return (core_settings.ollama_vision_model or "").strip()


def _chat_model() -> str:
    return (core_settings.ollama_model or "").strip()


async def _ask(messages: list[dict[str, Any]], model: str, *, num_predict: int, vision: bool,
               stats: dict[str, Any] | None = None) -> str:
    out: list[str] = []
    knobs: dict[str, Any] = {"think": False, "num_ctx": core_settings.ollama_vision_num_ctx or None} if vision else {}
    async for delta in ollama_client.chat_stream(messages, model=model, stats=stats, num_predict=num_predict,
                                                 temperature=0, **knobs):
        out.append(delta)
    return "".join(out)


async def classify(b64: str, model: str | None = None) -> tuple[str | None, str]:
    """"printed", "handwritten" or None for one photo, and the model's word."""
    raw = await _ask([{"role": "system", "content": _letter.KIND_SYSTEM},
                      {"role": "user", "content": _letter.KIND_QUESTION, "images": [b64]}],
                     model or _vision_model(), num_predict=_letter.KIND_TOKENS, vision=True)
    return _letter.parse_kind(raw), raw


async def transcribe(b64: str, model: str | None = None, stats: dict[str, Any] | None = None) -> str:
    """One photo's text, as the vision model reads it, cleaned."""
    raw = await _ask([{"role": "system", "content": _letter.TRANSCRIBE_SYSTEM},
                      {"role": "user", "content": _letter.TRANSCRIBE_QUESTION, "images": [b64]}],
                     model or _vision_model(), num_predict=_letter.TRANSCRIBE_TOKENS, vision=True, stats=stats)
    return _letter.clean_transcript(raw)


async def explain(question: str, transcripts: Sequence[str], model: str | None = None,
                  stats: dict[str, Any] | None = None) -> tuple[_letter.Explained, str]:
    """The checked explanation of ``transcripts``, and the model's own words."""
    if not any(t.strip() for t in transcripts):
        return _letter.finish("", transcripts), ""
    prompt = _letter.build_prompt(question, transcripts)
    raw = await _ask([{"role": "system", "content": prompt["rules"]}, {"role": "user", "content": prompt["user"]}],
                     model or _chat_model(), num_predict=700, vision=False, stats=stats)
    return _letter.finish(raw, transcripts, prompt["nonce"]), raw


# ─── In a home-server chat ───────────────────────────────────────────────
def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


#: The note under a letter read past its blurry-photo card.
NOTE_BLURRY = "Read from a blurry photo: some words may be guessed. Check them on the letter."


async def letter_events(
    *, thread_id: int, question: str, pictures: Sequence[Path], prefs: Any, shown: set[str],
    write: Callable[..., Awaitable[Any]], fail: Callable[[BaseException, str, str], tuple[str, str]],
    msg: Callable[[Any], dict[str, Any]],
) -> AsyncIterator[str]:
    """The SSE events of a letter turn (module docstring). ``write`` stores
    the assistant row (content, model, error, stats, doc) and returns it,
    ``fail`` words a model failure, ``msg`` turns a row into its JSON."""
    chosen = getattr(prefs, "chosen", None)
    skip = list(getattr(prefs, "skip", []) or [])
    off = bool(getattr(prefs, "off", False))
    vision = _vision_model()
    started = time.monotonic()
    yield _sse("status", {"state": "reading_letter", "photo": 1, "photos": len(pictures)})
    try:
        images = [base64.b64encode(await executors.run("pictures", _read_shrunk, p)).decode() for p in pictures]
    except (OSError, image_safety.PictureRefused) as e:
        log.warning("chat letter: a photo couldn't be read: %s", type(e).__name__)
        images = []
    if not images:
        row = await write(content=_letter.NOTHING_READ, model=None, error=None,
                          stats={"model_role": "letter"},
                          doc={"mode": "letter", "found": False, "sources": [], "notes": []})
        yield _sse("delta", {"text": _letter.NOTHING_READ})
        yield _sse("done", msg(row))
        return

    handwritten = chosen is not None and chosen[0] == "handwriting"
    try:
        if not handwritten:
            # One word per photo: is any of them handwriting?
            for b in images:
                await _yield_to_voice()
                kind, _raw = await classify(b, vision)
                if kind == "handwritten":
                    handwritten = True
                    break
            if handwritten:
                found = _headsup.findings(_headsup.Request(
                    mode="picture", question=question, where="server", picture={"handwritten": True},
                    ai_on=not ai_features.turned_off()))
                decision = _headsup.decide(found, shown=shown, skip=skip, off=off, chosen=chosen)
                if decision.card is not None:
                    card = decision.card
                    doc = {"mode": "heads_up", "found": True, "sources": [], "notes": [],
                           "heads_up": card.to_json() | {"chosen": None}}
                    row = await write(content=card.text, model=None, error=None,
                                      stats={"model_role": "heads_up"}, doc=doc)
                    yield _sse("delta", {"text": card.text})
                    yield _sse("done", msg(row))
                    return
        transcripts: list[str] = []
        for i, b in enumerate(images):
            if i:
                yield _sse("status", {"state": "reading_letter", "photo": i + 1, "photos": len(images)})
            await _yield_to_voice()
            transcripts.append(await transcribe(b, vision))
        yield _sse("status", {"state": "checking_letter"})
        stats: dict[str, Any] = {}
        await _yield_to_voice()
        explained, _raw = await explain(question, transcripts, stats=stats)
    except Exception as e:  # noqa: BLE001 — surfaced as the reply's error
        code, error = fail(e, vision, "letter")
        log.warning("chat letter failed (thread %s): %s: %s", thread_id, type(e).__name__, str(e)[:300])
        row = await write(content="", model=vision, error=error, stats={"model_role": "letter", "error_code": code},
                          doc=None)
        yield _sse("error", {"detail": error, "code": code, "model": vision})
        yield _sse("done", msg(row))
        return
    notes = list(explained.notes)
    if handwritten:
        notes.append({"code": "letter_handwriting", "text": _letter.NOTE_HANDWRITING})
    if chosen is not None and chosen[0] == "blurry":
        notes.append({"code": "letter_blurry", "text": NOTE_BLURRY})
    doc = {"mode": "letter", "found": bool(explained.deadlines or explained.amounts or explained.shaped),
           "sources": _letter.sources(transcripts), "notes": notes,
           "letter": explained.facts_json() | {"kind": "handwritten" if handwritten else "printed",
                                               "photos": len(images)}}
    if chosen is not None:
        doc["heads_up_chosen"] = {"kind": chosen[0], "option": chosen[1]}
    row_stats = {"model_role": "letter", "wall_ms": round((time.monotonic() - started) * 1000),
                 "vision_model": vision}
    if stats.get("eval_count") is not None:
        row_stats["completion_tokens"] = stats.get("eval_count")
    row = await write(content=explained.content, model=_chat_model(), error=None, stats=row_stats, doc=doc)
    yield _sse("delta", {"text": explained.content})
    yield _sse("done", msg(row))


def _read_shrunk(p: Path) -> bytes:
    return shrunk_jpeg(p.read_bytes())


# ─── The phone's way in: read one photo, keep nothing ───────────────────
def _refused(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _too_big() -> JSONResponse:
    return _refused(413, "file_too_large", "That photo is too large: the home server reads photos of up to 8 MB.",
                    max_mb=MAX_PHOTO_BYTES // (1024 * 1024))


#: The biggest letter photo the phone may send, bytes (a phone sends a
#: 1,024-px JPEG of about 0.2-0.5 MB; the transport guard's budget for the
#: route is this plus its form, middleware.BODY_LIMITS).
MAX_PHOTO_BYTES = 8 * 1024 * 1024
#: One read per phone at a time, and this many in the house at once (as
#: long documents: chat_doc_jobs.MAX_RUNNING).
MAX_READING = 2
#: How long a read waits for a voice turn to finish before each model step.
VOICE_WAIT_SEC = 30.0

_reading_by: dict[str, int] = {}
_reading = 0

BUSY_PHONE = "This phone is already having a letter read. Wait for that one to finish, then try again."
BUSY_HOUSE = "The home server is already reading two letters. Try again in a minute."


def _caller(request: Request) -> str:
    """Who is asking, for one-at-a-time: the household token (hashed: it
    is never kept) and the address it came from."""
    token = request.headers.get("x-device-token") or request.headers.get("authorization") or ""
    host = request.client.host if request.client else ""
    return hashlib.sha256(f"{token}|{host}".encode()).hexdigest()[:16]


_pause = asyncio.sleep


async def _yield_to_voice() -> None:
    """A voice turn in a room goes first: wait while the core answers one
    (at most :data:`VOICE_WAIT_SEC`)."""
    from web.backend.api import chat_doc_jobs as _jobs

    waited = 0.0
    while waited < VOICE_WAIT_SEC and await _jobs.voice_busy():
        await _pause(1.0)
        waited += 1.0


async def _read_body(request: Request, cap: int) -> bytes | None:
    """The request body, in memory, or None once it passes ``cap`` (it is
    never spooled to disk: no form parser touches it)."""
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


def _form_fields(content_type: str, body: bytes) -> dict[str, bytes]:
    """The parts of a multipart/form-data ``body``, by name, in memory."""
    msg = BytesParser(policy=email_policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out: dict[str, bytes] = {}
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name and name not in out:
            out[str(name)] = part.get_payload(decode=True) or b""
    return out


_FORM_SCHEMA = {
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary",
                                "description": "One letter photo: JPEG, PNG, GIF, WebP or BMP, at most 8 MB."},
                       "stop_if_handwritten": {"type": "boolean", "default": True}}}}}},
}


@router.get("/letters/ready", dependencies=READ)
async def letters_ready() -> dict[str, Any]:
    """Whether this home server can read a phone's letter photo now: AI
    features on and its picture model installed (``vision``; null when
    Ollama couldn't be asked). The phone offers "a letter for the home
    server" only when both are true."""
    ai = not ai_features.turned_off()
    vision = _vision_model()
    installed: bool | None = False
    if vision:
        tags = await ollama_client.tags_or_none()
        installed = None if tags is None else ollama_client.model_installed(vision, tags)
    return {"ai": ai, "vision": installed, "max_mb": MAX_PHOTO_BYTES // (1024 * 1024)}


@router.post(
    "/letters/read",
    dependencies=SEND,
    openapi_extra=_FORM_SCHEMA,
    responses={
        409: {"description": "The household's AI features are off (`X-Domovoi-Refusal: ai-off`)"},
        413: {"description": '{"detail": "...", "code": "file_too_large", "max_mb": 8} or {"code": "too_many_pixels"}'},
        415: {"description": '{"detail": "...", "code": "unsupported_format"}'},
        429: {"description": '{"detail": "...", "code": "busy"}: this phone has a read under way, or the house two'},
        503: {"description": '{"detail": "...", "code": "vision_model_missing"|"read_failed"}'},
    },
)
async def read_letter(request: Request) -> Any:
    """A phone chat's letter photo, read by the home server (documents plan
    D6): ``{"blur", "kind", "text", "stopped", "seconds"}``. ``blur`` is the
    variance of the Laplacian (the phone decides the blurry card, which it
    shows before sending); ``kind`` "printed", "handwritten" or null;
    ``text`` the transcript. With ``stop_if_handwritten`` (the default) a
    handwritten photo comes back with ``stopped`` true and no text, so the
    phone can show the handwriting card first and ask again with false.

    The caller is checked before a byte of the body is read; the body is
    read into memory and refused past :data:`MAX_PHOTO_BYTES` (never
    spooled to disk). One read per phone at a time and :data:`MAX_READING`
    in the house (429 ``busy``); a voice turn in a room goes first. Nothing
    is kept: the photo is never written, and nothing about it is logged
    beyond its timing. The explanation is written on the phone."""
    global _reading
    who = _caller(request)
    if _reading_by.get(who):
        return _refused(429, "busy", BUSY_PHONE)
    if _reading >= MAX_READING:
        return _refused(429, "busy", BUSY_HOUSE)
    # Held from here (nothing awaits between the checks and these lines).
    _reading_by[who] = 1
    _reading += 1
    try:
        body = await _read_body(request, MAX_PHOTO_BYTES + 64 * 1024)
        if body is None:
            return _too_big()
        fields = _form_fields(request.headers.get("content-type", ""), body)
        body = b""
        data = fields.get("file") or b""
        if not data:
            return _refused(415, "unsupported_format", "That request has no photo in it.")
        if len(data) > MAX_PHOTO_BYTES:
            return _too_big()
        stop_if_handwritten = (fields.get("stop_if_handwritten") or b"true").strip().lower() not in (b"false", b"0")
        try:
            jpeg = await executors.run("pictures", shrunk_jpeg, data)
            blur = await executors.run("pictures", picture_blur, data)
        except image_safety.PictureRefused as e:
            if e.too_large:
                return _refused(413, "too_many_pixels", f"That photo is too large: {e}.")
            return _refused(415, "unsupported_format", "That file isn't a photo the home server can read.")
        except Exception:  # noqa: BLE001 — a broken file
            return _refused(415, "unsupported_format", "That file isn't a photo the home server can read.")
        vision = _vision_model()
        if not vision:
            return _refused(503, "vision_model_missing", "The home server has no picture model set up.")
        tags = await ollama_client.tags_or_none()
        if tags is not None and not ollama_client.model_installed(vision, tags):
            return _refused(503, "vision_model_missing", "The home server's picture model isn't installed.")
        from web.backend.api import chat as _chat

        started = time.monotonic()
        b64 = base64.b64encode(jpeg).decode()
        _chat._reply_started()
        try:
            await _yield_to_voice()
            kind, _raw = await classify(b64, vision)
            if kind == "handwritten" and stop_if_handwritten:
                return {"blur": blur, "kind": kind, "text": None, "stopped": True,
                        "seconds": round(time.monotonic() - started, 1)}
            await _yield_to_voice()
            text = await transcribe(b64, vision)
        except Exception as e:  # noqa: BLE001 — words for the phone, details for the log
            log.warning("chat letter read failed: %s", type(e).__name__)
            return _refused(503, "read_failed", "The home server couldn't read that photo. Try again.")
        finally:
            _chat._reply_finished()
        log.info("chat letter read on the home server in %.1f s", time.monotonic() - started)
        return {"blur": blur, "kind": kind, "text": text, "stopped": False,
                "seconds": round(time.monotonic() - started, 1)}
    finally:
        _reading = max(0, _reading - 1)
        _reading_by.pop(who, None)
