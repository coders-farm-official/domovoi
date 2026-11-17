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
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from domovoi import ai_features
from domovoi.config import settings
from domovoi.scan_reader import store

log = logging.getLogger(__name__)

#: The most words of the text said when there's no summary.
START_WORDS = 60
SUMMARY_TOKENS = 500

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
                  keep: bool = True) -> dict[str, Any]:
    """``{"ai", "content", "said", "notes"}`` for a finished read:
    ``content`` the summary in markdown (None with AI off), ``said`` the
    spoken version."""
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
    from domovoi.doc_qa import letter

    question = "What does this letter say?"
    prompt = letter.build_prompt(question, [body])
    try:
        reply = await (writer or default_writer())(prompt["rules"], prompt["user"])
    except Exception as e:  # noqa: BLE001 — the model failed: the text still stands
        log.warning("scan read %s: the summary failed: %s", read["id"][:8], type(e).__name__)
        return {"ai": False, "content": None, "notes": notes, "failed": True,
                "said": "I couldn't sum it up just now. Here's how it starts: " + start_of(body)}
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
