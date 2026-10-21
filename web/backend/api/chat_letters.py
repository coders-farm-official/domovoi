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

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from domovoi import ai_features, executors, image_safety
from domovoi.admin_auth import require_device
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
_MAX_BYTES = 20 * 1024 * 1024


def _require_ai() -> None:
    if ai_features.turned_off():
        raise ai_features.http_exception("chat")


SEND = [Depends(require_device), Depends(_require_ai)]


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
            transcripts.append(await transcribe(b, vision))
        yield _sse("status", {"state": "checking_letter"})
        stats: dict[str, Any] = {}
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
def _refused(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


@router.post(
    "/letters/read",
    dependencies=SEND,
    responses={
        409: {"description": "The household's AI features are off (`X-Domovoi-Refusal: ai-off`)"},
        413: {"description": '{"detail": "...", "code": "file_too_large"|"too_many_pixels"}'},
        415: {"description": '{"detail": "...", "code": "unsupported_format"}'},
        503: {"description": '{"detail": "...", "code": "vision_model_missing"|"read_failed"}'},
    },
)
async def read_letter(file: UploadFile = File(...), stop_if_handwritten: bool = Form(True)) -> Any:
    """A phone chat's letter photo, read by the home server (documents plan
    D6): ``{"blur", "kind", "text", "stopped", "seconds"}``. ``blur`` is the
    variance of the Laplacian (the phone decides the blurry card, which it
    shows before sending); ``kind`` "printed", "handwritten" or null;
    ``text`` the transcript. With ``stop_if_handwritten`` (the default) a
    handwritten photo comes back with ``stopped`` true and no text, so the
    phone can show the handwriting card first and ask again with false.

    Nothing is kept: the photo is read in memory and never written, and
    nothing about it is logged beyond its timing. The explanation is
    written on the phone."""
    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        return _refused(413, "file_too_large", "That photo is too large: more than 20 MB.")
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
    started = time.monotonic()
    b64 = base64.b64encode(jpeg).decode()
    try:
        kind, _raw = await classify(b64, vision)
        if kind == "handwritten" and stop_if_handwritten:
            return {"blur": blur, "kind": kind, "text": None, "stopped": True,
                    "seconds": round(time.monotonic() - started, 1)}
        text = await transcribe(b64, vision)
    except Exception as e:  # noqa: BLE001 — words for the phone, details for the log
        log.warning("chat letter read failed: %s: %s", type(e).__name__, str(e)[:300])
        return _refused(503, "read_failed", "The home server couldn't read that photo. Try again.")
    log.info("chat letter read on the home server in %.1f s", time.monotonic() - started)
    return {"blur": blur, "kind": kind, "text": text, "stopped": False,
            "seconds": round(time.monotonic() - started, 1)}
