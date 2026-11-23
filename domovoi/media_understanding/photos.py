"""Photos (media understanding slice 2, design MEDIA-UNDERSTANDING §2):
the date a photo was taken, a caption and tags, and the text in it.

Two analysers on the runner (:mod:`.runner`), cheap work first:

* :class:`PhotoDateAnalyser` (``photo_date``, no model): the EXIF
  ``DateTimeOriginal`` (with ``OffsetTimeOriginal`` when the camera wrote
  one, else the household's wall clock), else the file's modified time.
  EXIF GPS is kept only while the household's "Use photo locations"
  switch is on; with it off nothing about where is saved (places
  themselves, the names, are slice 7). Reads only the first 256 KB, where
  a JPEG keeps its EXIF, through the read-rate bucket.
* :class:`PhotoCaptionAnalyser` (``photo_caption``): ONE vision call per
  photo, on the household's existing vision slot
  (``ollama_vision_model``: ``qwen3-vl:2b-instruct`` on a CPU house, a
  larger one where a GPU house picked it in Settings → Models), with
  Ollama's structured output (:data:`PHOTO_SCHEMA`), on the photo
  downscaled to 768 px on its long edge: ``caption`` (one sentence),
  ``tags`` (up to 12 plain nouns) and ``has_text``. Only when the model
  says there is text does Tesseract read it (:mod:`domovoi.scan_reader.ocr`,
  the scan reader's OS package): the exact characters, not a model's
  guess. With the household's AI features off there is no model to ask:
  Tesseract reads every photo and only a confident reading is kept
  (the Settings card says text in pictures still works), and the stage
  is skipped with ``ai-off``.

Voice first: the vision call goes through ``ctx.model_step(vision=True)``
(memory-gated, cancelled by a voice turn and asked again after it);
Tesseract runs through ``ctx.hold()`` (held still during a turn) at the
lowest priority. Progress is saved after the vision call, so a restart
doesn't ask the model again.

**Untrusted text.** A caption, a tag and the text found in a picture are
words a model or OCR made from a picture anybody could have put in the
library. They are cleaned of control and format characters and cut to
length here, stored as plain text, searched as words, and shown as text
(never HTML); a caption is shown as "might show". They are never
instructions to anything.

Core-internal.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from domovoi import ai_features, executors, image_safety
from domovoi.db.session import session_scope
from domovoi.media_understanding import search
from domovoi.media_understanding.runner import Item, Paused, Skip, StepContext, register

log = logging.getLogger(__name__)

#: The EXIF of a JPEG is in its first 64 KB (an APP1 segment); a little
#: more covers a camera that writes another segment before it.
EXIF_HEAD_BYTES = 256 * 1024
#: A photo larger than this is not read whole for its caption.
MAX_PHOTO_BYTES = 80 * 1024 * 1024
#: The long edge of the copy the vision model sees (as chat video frames).
VISION_EDGE = 768
#: The long edge of the copy Tesseract reads.
OCR_EDGE = 2400
#: What the vision call may say, at most.
VISION_NUM_PREDICT = 256
CAPTION_MAX = 300
TAGS_MAX = 12
TAG_MAX = 40
OCR_TEXT_MAX = 4000
#: With AI off, Tesseract's reading of a photo is kept only when it is at
#: least this sure (the scan reader's bar for a page).
OCR_KEEP_CONF_AI_OFF = 70.0
#: With the model's word that there is text, a reading is kept down to
#: this confidence (signs and labels read worse than a page).
OCR_KEEP_CONF = 45.0

CODE_UNREADABLE = "photo-unreadable"
CODE_TOO_LARGE = "photo-too-large"
CODE_VISION_MISSING = "vision-model-missing"

PHOTO_PROMPT = (
    "Describe this photo for a family's photo search. Answer only with JSON with these keys: "
    '"caption": one short plain sentence saying what the photo shows; '
    '"tags": up to 12 plain lowercase nouns for the things, animals, places and events in it '
    '(like "dog", "beach", "cake", "snow"); '
    '"has_text": true if the photo has readable writing in it (a sign, a page, a label, a screen, '
    "a shirt), otherwise false. "
    "Say only what you can see. Don't guess who people are or where the photo was taken."
)

#: Ollama structured output: the reply must be this object.
PHOTO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": TAGS_MAX},
        "has_text": {"type": "boolean"},
    },
    "required": ["caption", "tags", "has_text"],
}


# ─── untrusted text ───────────────────────────────────────────────────────

def clean_text(value: Any, limit: int, *, lines: bool = False) -> str:
    """Plain text from a model or OCR: control and format characters gone
    (bidi overrides, zero-width marks), spaces collapsed (newlines kept
    when ``lines``), cut to ``limit``."""
    if not isinstance(value, str):
        return ""
    out = []
    for ch in value:
        if ch == "\n" and lines:
            out.append(ch)
        elif ch in "\t\r\n":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    s = "".join(out)
    if lines:
        s = "\n".join(re.sub(r"[ ]+", " ", ln).strip() for ln in s.split("\n"))
        s = re.sub(r"\n{3,}", "\n\n", s).strip()
    else:
        s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit].rstrip()
    return s


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


class BadReply(ValueError):
    """The vision model's reply wasn't the JSON asked for."""


def parse_reply(raw: str) -> dict[str, Any]:
    """``{"caption", "tags", "has_text"}`` from the model's reply, cleaned.
    A reply with words around the JSON is read from its first object."""
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
        raise BadReply("not a JSON object")
    caption = clean_text(doc.get("caption"), CAPTION_MAX)
    has_text = doc.get("has_text")
    if isinstance(has_text, str):
        has_text = has_text.strip().lower() in ("true", "yes", "1")
    return {"caption": caption, "tags": clean_tags(doc.get("tags")), "has_text": bool(has_text)}


# ─── EXIF ─────────────────────────────────────────────────────────────────

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME = 306
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME_DIGITIZED = 36868
TAG_OFFSET_ORIGINAL = 36881
TAG_OFFSET_DIGITIZED = 36882
TAG_OFFSET = 36880


def _exif_datetime(value: Any) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if not isinstance(value, str):
        return None
    value = value.strip().rstrip("\x00").strip()
    m = re.match(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", value)
    if not m:
        return None
    try:
        dt = datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None
    if dt.year < 1900:
        return None
    return dt


def _exif_offset(value: Any) -> timezone | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*([+\-])(\d{2}):(\d{2})", value)
    if not m:
        return None
    hours, minutes = int(m.group(2)), int(m.group(3))
    if hours > 14 or minutes > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(-delta if m.group(1) == "-" else delta)


def _ratio(v: Any) -> float | None:
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if float(v[1]) else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(value: Any, ref: Any) -> float | None:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    parts = [_ratio(v) for v in value]
    if any(p is None for p in parts):
        return None
    deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0  # type: ignore[operator]
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "replace")
    if isinstance(ref, str) and ref.strip().upper()[:1] in ("S", "W"):
        deg = -deg
    return deg


def exif_facts(exif: dict[int, Any], exif_ifd: dict[int, Any], gps_ifd: dict[int, Any], *,
               places: bool, now_utc: datetime | None = None) -> dict[str, Any]:
    """``{"taken_at": aware UTC | None, "gps": {"lat", "lon"} | None}``
    from a picture's EXIF. GPS only when ``places``. Pure."""
    from domovoi import household_time

    now_utc = now_utc or household_time.utc_now()
    taken = None
    for tag, off_tag in ((TAG_DATETIME_ORIGINAL, TAG_OFFSET_ORIGINAL),
                         (TAG_DATETIME_DIGITIZED, TAG_OFFSET_DIGITIZED)):
        local = _exif_datetime(exif_ifd.get(tag))
        if local is not None:
            zone = _exif_offset(exif_ifd.get(off_tag))
            taken = local.replace(tzinfo=zone).astimezone(timezone.utc) if zone else household_time.to_utc(local)
            break
    if taken is None:
        local = _exif_datetime(exif.get(TAG_DATETIME))
        if local is not None:
            zone = _exif_offset(exif_ifd.get(TAG_OFFSET))
            taken = local.replace(tzinfo=zone).astimezone(timezone.utc) if zone else household_time.to_utc(local)
    if taken is not None and taken > now_utc + timedelta(days=2):
        taken = None      # a camera clock set in the future: the file's time is better
    gps = None
    if places and gps_ifd:
        lat = _dms(gps_ifd.get(2), gps_ifd.get(1))
        lon = _dms(gps_ifd.get(4), gps_ifd.get(3))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 \
                and not (lat == 0 and lon == 0):
            gps = {"lat": round(lat, 6), "lon": round(lon, 6)}
    return {"taken_at": taken, "gps": gps}


def read_exif(head: bytes) -> tuple[dict[int, Any], dict[int, Any], dict[int, Any]]:
    """The main, Exif and GPS directories from the start of a picture.
    Blocking (the ``pictures`` pool). Empty when there are none or the
    picture can't be opened."""
    try:
        with image_safety.open_image(io.BytesIO(head), image_safety.LIBRARY_FORMATS) as im:
            exif = im.getexif()
            return dict(exif), dict(exif.get_ifd(EXIF_IFD)), dict(exif.get_ifd(GPS_IFD))
    except Exception:  # noqa: BLE001 — no EXIF: the file's time stands
        return {}, {}, {}


# ─── the pictures the model and Tesseract see ─────────────────────────────

def _decoded(data: bytes, edge: int, mode: str) -> Any:
    from PIL import Image, ImageOps

    im = image_safety.open_image(io.BytesIO(data), image_safety.LIBRARY_FORMATS)
    with im:
        im.draft("RGB", (edge, edge))
        im.thumbnail((edge, edge))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        return im.convert(mode)


def vision_jpeg(data: bytes) -> bytes:
    """The photo upright, at most 768 px on its long edge, as JPEG.
    Blocking. Raises :class:`image_safety.PictureRefused`."""
    im = _decoded(data, VISION_EDGE, "RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def ocr_png(data: bytes) -> bytes:
    """The photo upright and grey, at most 2400 px, as PNG for Tesseract."""
    im = _decoded(data, OCR_EDGE, "L")
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


# ─── the models ───────────────────────────────────────────────────────────

Vision = Callable[[bytes], Awaitable[str]]


def vision_model() -> str:
    from domovoi.config import settings

    return (settings.ollama_vision_model or "").strip()


async def ollama_vision(jpeg: bytes) -> str:
    """The household's vision slot, asked for :data:`PHOTO_SCHEMA`."""
    from domovoi.clients import ollama
    from domovoi.config import settings

    model = vision_model()
    if not model:
        raise Skip(CODE_VISION_MISSING)
    msgs = [{"role": "user", "content": PHOTO_PROMPT, "images": [base64.b64encode(jpeg).decode("ascii")]}]
    out: list[str] = []
    try:
        async for d in ollama.chat_stream(msgs, model=model, think=False,
                                          num_ctx=settings.ollama_vision_num_ctx or None,
                                          num_predict=VISION_NUM_PREDICT, temperature=0.2,
                                          format=PHOTO_SCHEMA):
            out.append(d)
    except ollama.OllamaChatError as e:
        if e.status == 404 or "not found" in e.detail.lower():
            raise Skip(CODE_VISION_MISSING) from None
        raise
    return "".join(out)


class Ocr:
    """Tesseract (:mod:`domovoi.scan_reader.ocr`); tests swap in a fake."""

    def available(self) -> bool:
        from domovoi.scan_reader import ocr

        return ocr.find() is not None

    async def read(self, png_path: Any, hold: Any) -> tuple[str, float | None]:
        from domovoi.scan_reader import ocr

        page = await ocr.read_page(png_path, hold=hold)
        return page.text, page.confidence


# ─── writing ──────────────────────────────────────────────────────────────

async def _save_frame(item_id: int, *, caption: str, tags: list[str], has_text: bool | None,
                      ocr_text: str | None, model: str | None) -> None:
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO media_frames (item_id, idx, at_s, caption, tags, has_text, ocr_text, model) "
            "VALUES (:i, 0, NULL, :c, :t, :h, :o, :m) ON CONFLICT (item_id, idx) DO UPDATE SET "
            "at_s = NULL, caption = EXCLUDED.caption, tags = EXCLUDED.tags, has_text = EXCLUDED.has_text, "
            "ocr_text = EXCLUDED.ocr_text, model = EXCLUDED.model"),
            {"i": item_id, "c": caption, "t": tags, "h": has_text, "o": ocr_text, "m": model})
        if model:
            await s.execute(text("UPDATE media_items SET vision_model = :m WHERE id = :i"),
                            {"m": model, "i": item_id})
        await search.refresh_doc(s, item_id)


# ─── the analysers ────────────────────────────────────────────────────────

class PhotoDateAnalyser:
    """The date a photo was taken (and, places on, where): no model."""

    name = "photo_date"
    kinds = frozenset({"image"})
    order = 5
    deepens = False

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def run(self, item: Item, ctx: StepContext) -> None:
        places = bool(ctx.runner.sw.get("places"))
        async with ctx.charge():
            head = await ctx.read(0, min(item.size_bytes, EXIF_HEAD_BYTES))
            exif, exif_ifd, gps_ifd = await executors.run("pictures", read_exif, head)
        facts = exif_facts(exif, exif_ifd, gps_ifd, places=places)
        taken = facts["taken_at"] or datetime.fromtimestamp(item.mtime, tz=timezone.utc)
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE media_items SET taken_at = :t, gps = CAST(:g AS JSONB), "
                "place_name = CASE WHEN :g IS NULL THEN NULL ELSE place_name END WHERE id = :i"),
                {"t": taken, "g": json.dumps(facts["gps"]) if facts["gps"] else None, "i": item.id})
            await search.refresh_doc(s, item.id)
        await ctx.save({"from": "exif" if facts["taken_at"] else "file", "gps": bool(facts["gps"])})


class PhotoCaptionAnalyser:
    """The caption, tags and has-text from one vision call; the text from
    Tesseract when there is some."""

    name = "photo_caption"
    kinds = frozenset({"image"})
    order = 40
    deepens = False

    def __init__(self, vision: Vision | None = None, ocr: Ocr | None = None,
                 model_name: Callable[[], str] | None = None) -> None:
        self.vision = vision or ollama_vision
        self.ocr = ocr or Ocr()
        self.model_name = model_name or vision_model

    def wanted(self, item: Item, sw: dict[str, Any]) -> bool:
        return True

    async def _load(self, item: Item, ctx: StepContext) -> bytes:
        if item.size_bytes > MAX_PHOTO_BYTES:
            raise Skip(CODE_TOO_LARGE)
        async with ctx.charge():
            return await ctx.read(0, item.size_bytes)

    async def _ocr(self, data: bytes, ctx: StepContext, keep_conf: float) -> str | None:
        if not self.ocr.available():
            log.info("media understanding: tesseract isn't installed; no text read from photos")
            return None
        try:
            png = await executors.run("pictures", ocr_png, data)
        except Exception:  # noqa: BLE001 — undecodable: no text
            return None
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.work_dir / "ocr.png"
        path.write_bytes(png)
        try:
            async with ctx.charge():
                words, conf = await self.ocr.read(path, ctx.hold())
        except (Skip, Paused):
            raise
        except Exception as e:  # noqa: BLE001 — Tesseract failing loses the text, not the caption
            log.info("media understanding: tesseract failed on item %d: %s", ctx.item.id, type(e).__name__)
            return None
        finally:
            path.unlink(missing_ok=True)
        words = clean_text(words, OCR_TEXT_MAX, lines=True)
        if not words or conf is None or conf < keep_conf:
            return None
        return words

    async def run(self, item: Item, ctx: StepContext) -> None:
        data = await self._load(item, ctx)
        if ai_features.turned_off():
            # No model to ask whether there is text: Tesseract reads every
            # photo and only a confident reading is kept.
            if not ctx.progress.get("ocr_done"):
                words = await self._ocr(data, ctx, OCR_KEEP_CONF_AI_OFF)
                await _save_frame(item.id, caption="", tags=[], has_text=None, ocr_text=words, model=None)
                await ctx.save({"ocr_done": True, "ai_off": True})
            raise Skip("ai-off")
        seen = ctx.progress.get("vision")
        if not isinstance(seen, dict) or ctx.progress.get("ai_off"):
            try:
                jpeg = await executors.run("pictures", vision_jpeg, data)
            except image_safety.PictureRefused:
                raise Skip(CODE_UNREADABLE) from None
            reply = await ctx.model_step(lambda: self.vision(jpeg), vision=True)
            seen = {**parse_reply(reply), "model": self.model_name() or None}
            await _save_frame(item.id, caption=seen["caption"], tags=seen["tags"], has_text=seen["has_text"],
                              ocr_text=None, model=seen["model"])
            await ctx.save({"vision": seen})
        if seen.get("has_text") and not ctx.progress.get("ocr_done"):
            words = await self._ocr(data, ctx, OCR_KEEP_CONF)
            if words:
                await _save_frame(item.id, caption=seen["caption"], tags=seen["tags"], has_text=True,
                                  ocr_text=words, model=seen.get("model"))
            await ctx.save({"vision": seen, "ocr_done": True})


def install(vision: Vision | None = None, ocr: Ocr | None = None) -> None:
    """Register the photo analysers on the runner (the worker does at start)."""
    register(PhotoDateAnalyser())
    register(PhotoCaptionAnalyser(vision=vision, ocr=ocr))
