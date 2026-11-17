""""What does it say?" for a read scan: a short plain summary, and the
full text on request.

The summary is the letter reader's (:mod:`domovoi.doc_qa.letter`, the
one that reads photos sent to chat), given the scan's text instead of a
photo's transcript: the household's chat model explains it in a fixed
shape and the app shows dates and amounts only as the letter's own
sentences, quoted. It is made once, on the first ask, and kept with the
read. Spoken, it is the sender, what the letter is about, what to do, and
the letter's own sentence for the date and the amount that matter.

With AI features off there is no summary (no model is asked): the answer
is the start of the text, said as the start, and the full text.

Asked from the dashboard or an app (``gate=True``, the route's way), the
summary is made **voice first** like the rest of the reader
(:class:`domovoi.voice_first.VoiceFirst`: it waits for a quiet house and a
voice turn cancels it and asks again after), **one at a time in the
house**, **once per read** however many ask at the same moment (they all
get that one), and a failed one isn't tried again for
:data:`FAILED_BACKOFF_SEC` (the start of the text meanwhile): security
audit round 6, DOC-11. By voice (the person is waiting in the room) it is
made at once, inside the turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from domovoi import ai_features
from domovoi.config import settings
from domovoi.scan_reader import store
from domovoi.voice_first import VoiceFirst

log = logging.getLogger(__name__)

#: The most words of the text said when there's no summary.
START_WORDS = 60
SUMMARY_TOKENS = 500
#: A summary that failed isn't asked for again for this long.
FAILED_BACKOFF_SEC = 300.0


class _AiOff(Exception):
    """AI features were turned off while the summary waited its turn."""


class _Gate(VoiceFirst):
    """The voice-first rules for a summary asked from a screen."""

    label = "scan summary"
    passthrough = (_AiOff,)

    def _before_model_step(self) -> None:
        if ai_features.turned_off():
            raise _AiOff()


_GATE = _Gate()
#: The summary being made for each read (one per read at a time).
_making: dict[str, "asyncio.Future[dict[str, Any]]"] = {}
#: When each read's summary last failed (monotonic seconds).
_failed: dict[str, float] = {}
_lock: tuple[Any, asyncio.Lock] | None = None


def _house_lock() -> asyncio.Lock:
    """One summary at a time in the house (one lock per event loop)."""
    global _lock
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    return _lock[1]


def _reset_for_tests() -> None:
    global _lock
    _making.clear()
    _failed.clear()
    _lock = None

Writer = Callable[[str, str], Awaitable[str]]


async def _real_writer(system: str, user: str) -> str:
    from domovoi.clients import ollama

    out: list[str] = []
    async for d in ollama.chat_stream([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                      model=settings.ollama_model, num_predict=SUMMARY_TOKENS, temperature=0.2):
        out.append(d)
    return "".join(out)


async def _stub_writer(system: str, user: str) -> str:
    return ("FROM: the stub company\nABOUT: A stub letter about a bill.\nTODO: Pay the bill by the due date.\n"
            "UNCLEAR: nothing")


def default_writer() -> Writer:
    return _stub_writer if settings.use_stubs else _real_writer


def ocr_text(page_rows: list[dict[str, Any]]) -> str:
    """The text the summary is made from: every page with text, a guessed
    page introduced as a guess."""
    return "\n\n".join(store.page_text(p) for p in page_rows if str(p.get("text") or "").strip())


def start_of(text: str, words: int = START_WORDS) -> str:
    ws = " ".join(line for line in text.split("\n") if not line.startswith("(")).split()
    out = " ".join(ws[:words])
    return out + ("…" if len(ws) > words else "")


async def summary(read: dict[str, Any], *, writer: Writer | None = None, refresh: bool = False,
                  keep: bool = True, gate: bool = False) -> dict[str, Any]:
    """``{"ai", "content", "said", "notes"}`` for a finished read:
    ``content`` the summary in markdown (None with AI off), ``said`` the
    spoken version. ``gate``: asked from a screen, so made voice first, one
    at a time, once per read (the module's docstring); without it (voice)
    it is made now."""
    page_rows = await store.pages(read["id"])
    body = ocr_text(page_rows)
    guessed = [p["page"] for p in page_rows if p.get("method") == "vision"]
    unread = [p["page"] for p in page_rows if p.get("method") == "none"]
    notes = page_notes(guessed, unread)
    if not body.strip():
        return {"ai": False, "content": None, "said": "I couldn't read any words on this scan.", "notes": notes}
    if ai_features.turned_off():
        return {"ai": False, "content": None, "notes": notes,
                "said": "Here's how it starts: " + start_of(body)}
    cached = read.get("summary")
    if isinstance(cached, dict) and cached.get("ai") and not refresh:
        return cached
    if not gate:
        return await _make(read, body, notes, writer, keep, gate=False)
    rid = read["id"]
    failed_at = _failed.get(rid)
    if failed_at is not None and time.monotonic() - failed_at < FAILED_BACKOFF_SEC:
        return _fallback(body, notes)
    making = _making.get(rid)
    if making is None or making.done():
        making = asyncio.ensure_future(_one_at_a_time(read, body, notes, writer, keep))
        _making[rid] = making

        def forget(f: "asyncio.Future[dict[str, Any]]", rid: str = rid) -> None:
            if _making.get(rid) is f:
                _making.pop(rid, None)

        making.add_done_callback(forget)
    # A caller that goes away (a closed sheet) doesn't stop the one being made.
    return await asyncio.shield(making)


def _fallback(body: str, notes: list[dict[str, str]]) -> dict[str, Any]:
    return {"ai": False, "content": None, "notes": notes, "failed": True,
            "said": "I couldn't sum it up just now. Here's how it starts: " + start_of(body)}


async def _one_at_a_time(read: dict[str, Any], body: str, notes: list[dict[str, str]], writer: Writer | None,
                         keep: bool) -> dict[str, Any]:
    async with _house_lock():
        # Made by an earlier ask while this one waited its turn: that one.
        fresh = await store.get(read["id"])
        kept = (fresh or {}).get("summary")
        if isinstance(kept, dict) and kept.get("ai"):
            return kept
        return await _make(read, body, notes, writer, keep, gate=True)


async def _make(read: dict[str, Any], body: str, notes: list[dict[str, str]], writer: Writer | None,
                keep: bool, *, gate: bool) -> dict[str, Any]:
    from domovoi.doc_qa import letter

    question = "What does this letter say?"
    prompt = letter.build_prompt(question, [body])
    write = writer or default_writer()
    try:
        if gate:
            job = SimpleNamespace(id=read["id"], waiting=False)
            reply = await _GATE.model_step(job, lambda: write(prompt["rules"], prompt["user"]))
        else:
            reply = await write(prompt["rules"], prompt["user"])
    except Exception as e:  # noqa: BLE001 — the model failed: the text still stands
        log.warning("scan read %s: the summary failed: %s", read["id"][:8], type(e).__name__)
        _failed[read["id"]] = time.monotonic()
        return _fallback(body, notes)
    _failed.pop(read["id"], None)
    explained = letter.finish(reply, [body], prompt["nonce"])
    content = explained.content.replace("as Domovoi read it from the photo", "as Domovoi read it from the scan")
    out = {"ai": True, "content": content, "said": spoken(reply, explained, prompt["nonce"]),
           "notes": [{"code": n["code"], "text": n["text"].replace("from the photo", "from the scan")}
                     for n in explained.notes] + notes}
    if keep:
        await store.save_summary(read["id"], out)
    return out


def spoken(reply: str, explained: Any, nonce: str | None) -> str:
    """The summary as a room says it: who it's from, what it's about, what
    to do, and the letter's own sentence for the date and the amount the
    model picked (quoted, never the model's figures)."""
    from domovoi.doc_qa import letter

    p = letter.parse(reply, nonce)
    sender, _ = letter._prose(p.sender)
    about, _ = letter._prose(p.about)
    todo, _ = letter._prose(p.todo)
    bits = []
    if sender:
        bits.append(f"It's from {sender.rstrip('.')}.")
    if about:
        bits.append(about)
    if todo:
        bits.append(f"What to do: {todo}")
    for shown in (explained.dates[:1] + explained.amounts[:1]):
        if shown.label:
            bits.append(f"It says: {shown.fact.sentence}")
    return " ".join(bits) or letter.NEUTRAL.replace(", quoted below", "")


def page_notes(guessed: list[int], unread: list[int]) -> list[dict[str, str]]:
    from domovoi.doc_qa.notes import pages_phrase

    out = []
    if guessed:
        one = len(guessed) == 1
        out.append({"code": "scan_pages_guessed",
                    "text": f"{pages_phrase(guessed)} {'was' if one else 'were'} hard to read, so "
                            f"{'its' if one else 'their'} text is what the picture model thinks "
                            f"{'it' if one else 'they'} might say. Check it against the paper."})
    if unread:
        one = len(unread) == 1
        out.append({"code": "scan_pages_unread",
                    "text": f"{pages_phrase(unread)} couldn't be read"
                            + (" (AI features are off, so the picture model wasn't asked)."
                               if ai_features.turned_off() else ".")})
    return out


async def full(read: dict[str, Any]) -> dict[str, Any]:
    page_rows = await store.pages(read["id"])
    return {"pages": [{"page": p["page"], "method": p["method"], "confidence": p["confidence"],
                       "text": store.page_text(p)} for p in page_rows],
            "text": store.full_text(page_rows)}
