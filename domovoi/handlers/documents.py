"""DocumentsHandler — the household's Documents by voice (documents plan D7).

What it owns, all anchored:

* **"read me the lease"**, "read my cover letter", "read the letter from the
  bank to me", "read me X from Documents": a household document read aloud
  (:mod:`domovoi.voice_docs.reading`). The turn is claimed only when it
  will be answered (the router tries no later fast path after a decline):
  a document's exact name; a strong document word (document, file, PDF,
  lease, contract, …) — then names that fit, "Did you mean …?" for a close
  one, or "I couldn't find …"; or "letter" when a letter in Documents fits.
  "report", "recipe", "guide" and the like count only in an exact name.
  The decision is made while matching, from the in-memory index of
  Documents' names (:func:`domovoi.voice_docs.reading.index`). Notes, the
  news (and its "the latest", "the oldest"), books, podcasts, the calendar,
  reminders, timers, lists, stories and poems are never taken ("read my last note", "read me the headlines" and
  "read the book Dune" keep their handlers). Two documents that fit → "I
  found two: … Which one?". Over about a page →
  "It's 2 pages, about 3 minutes. The whole thing, or just the start?".
  The whole thing is read a page or so at a time, "Shall I go on?" between
  parts; "stop", "pause", "start again" and "read that again" answer it,
  and "continue reading" picks up later. Anything said over a part (the
  wake word, then "stop") cuts it off like any reply.
* **"read it back"** / "read that again": the last document read or saved.
* **"take a letter (to the landlord)"**, "write / draft a letter to …":
  dictation, written up by the QA model as a D4 draft and saved in
  Documents → From Chat, never sent (:mod:`domovoi.voice_docs.letters`).
  The author is the speaker voice ID recognised; otherwise the room asks
  "Who's it from?".
* "write my resume" by voice: pointed at Chat (a resume is a form, not a
  dictation), instead of being saved as a voice note "my resume".

Who may (owner answers Q11, Q12): any room. A satellite turn is a
household device, the tier that may already open everything in Documents
(``require_device_read``); a guest (no household token) has no satellite
and no Documents. Reading changes nothing. A letter WRITES into Documents:
the per-device block (Settings → Devices, ``files_device_blocks``) names
devices that send their id, and a voice turn names none — the same as
"scan this to Documents" by voice (``domovoi/printing/routes.py``). A
phone's chat mic is pointed at its own chat instead (the phone is a
device, not a room).

AI features off: "read me …" needs no model and works; "take a letter"
needs the QA model and says so plainly, before any dictation.

Nothing is said anywhere but the room that asked: a document is read only
to the room it was asked for in, and the letter's "saved" line names the
title, never its contents. ``requires_network = "no"``.
"""

from __future__ import annotations

import asyncio
import time
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation, take_parked_choice, PENDING_CONFIRMATION_KEY
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.speech_sanitize import sanitize_for_speech
from domovoi.voice_docs import letters, reading

log = logging.getLogger(__name__)

DOC_PICK = "core.doc_pick"
DOC_LENGTH = "core.doc_length"
DOC_MORE = "core.doc_more"
DOC_PAUSED = "core.doc_paused"
LETTER_AUTHOR = "core.letter_author"
LETTER_DICTATION = "core.letter_dictation"

#: Session-context keys: where a reading is, the letter being taken, and
#: the last document read or saved ("read it back").
READING_KEY = "doc_reading"
LETTER_KEY = "doc_letter"
LAST_KEY = "doc_last"

# ─── Fast-path patterns ──────────────────────────────────────────────────
#
# The router takes the FIRST fast path that matches and never tries a later
# one after a decline (router.route), so the read path claims a turn only
# when it will answer it (review round 1): its pattern (_ClaimingPattern)
# looks at the cached title index of Documents (reading.index: memory only,
# refreshed off the event loop) while matching.

#: Words that say a thing is a document, on their own.
_STRONG = (r"(?:cover letters?|documents?|docs?|files?|pdfs?|contracts?|lease|leases|agreements?|resumes?|cvs?"
           r"|invoices?|receipts?|certificates?|warrant(?:y|ies)|polic(?:y|ies)|statements?|memos?|essays?"
           r"|manuals?|handbooks?|minutes|proposals?|drafts?)")
#: "letter": a document only when a letter in Documents fits ("read me a
#: letter from santa" with none from Santa is the Q&A model's).
_LETTER = r"letters?"
#: Never ours, whatever follows "read": notes (voice_notes), the news and
#: its follow-ups ("read me the latest", "the oldest", "the latest on …"),
#: books and podcasts (spoken_audio), the calendar, reminders, timers and
#: lists, a story or a poem (the Q&A model's), and "it"/"that" (read back).
_NOT_OURS = (r"(?!(?:it|that|this|them)\b)"
             r"(?!(?:the |my )?(?:latest|newest|oldest|most recent)(?: (?:on|about|in|from|for|news|stories|story"
             r"|headlines?|ones?|episodes?|podcasts?)\b|$))"
             r"(?!.*\b(?:news|headlines?|stories|story|poems?|jokes?|notes?|book|books|audio ?books?|podcasts?"
             r"|episodes?|chapters?|calendar|schedule|agenda|lyrics|reminders?|timers?|alarms?|lists?|mind|room"
             r"|lips|palm)\b)")
_READ_LEAD = r"^read(?: (?:me|us))?(?: (?:out|aloud))?(?: (?:to me|for me))?"
_READ_TAIL = r"(?: (?:to me|for me|aloud|out loud|please))*"
_READ_SRC = _READ_LEAD + r" " + _NOT_OURS + r"(?P<what>.+?)" + _READ_TAIL + r"$"
_STRONG_RE = re.compile(r"\b" + _STRONG + r"\b|\b(?:from|in) (?:my |the |our )?documents$")
_LETTER_RE = re.compile(r"\b" + _LETTER + r"\b")
_FROM_DOCS = re.compile(r" (?:from|in) (?:my |the |our )?documents$")


def read_claim(what: str) -> tuple[str, list] | None:
    """What "read me ``what``" is, from the title index alone (no I/O when
    the index is warm): ("read", docs) — an exact name, or a strong document
    word and names that fit; ("near", docs) — a strong word or a letter, and
    names close to it ("Did you mean …?"); ("missing", []) — a strong word
    and nothing like it; None — not a document turn (not claimed)."""
    strong = bool(_STRONG_RE.search(what))
    name = _FROM_DOCS.sub("", what).strip()
    exact = reading.exact(name)
    if exact:
        return "read", exact
    if not strong and not _LETTER_RE.search(name):
        # "report", "recipe", "guide", "bills", "forms", a title's first
        # words ("harry potter" for "Harry Potter book report"): only an
        # exact name is claimed without a strong document word.
        return None
    found = reading.find(name)
    if found:
        return "read", found
    near = reading.find_near(name)
    if near:
        return "near", near
    return ("missing", []) if strong else None


class _ClaimingPattern:
    """A fast-path pattern (``match`` and ``pattern`` like ``re.Pattern``)
    that matches a "read …" turn only when :func:`read_claim` claims it."""

    def __init__(self, src: str) -> None:
        self._re = re.compile(src)
        self.pattern = src
        self.flags = self._re.flags
        self.groupindex = self._re.groupindex

    def match(self, text: str, *args):
        m = self._re.match(text, *args)
        if m is None:
            return None
        try:
            claim = read_claim((m.group("what") or "").strip())
        except Exception:  # noqa: BLE001 — never break routing over a document lookup
            log.exception("documents: the read claim failed")
            return None
        return m if claim is not None else None

    def search(self, text: str, *args):
        return self._re.search(text, *args)

    def __repr__(self) -> str:
        return f"_ClaimingPattern({self.pattern!r})"


_READ_RE = _ClaimingPattern(_READ_SRC)
_READ_BACK_RE = re.compile(
    r"^(?:read (?:it|that|this) back(?: to me)?|read (?:it|that|this)(?: (?:out|aloud))*(?: to me)?"
    r"|read back (?:the |my |that )?(?:letter|document|it)(?: to me)?)$"
)
_CONTINUE_RE = re.compile(
    r"^(?:(?:continue|resume|carry on|go on|keep going)(?: with)?(?: the)? reading"
    r"(?: (?:the |my |that )?(?:document|letter|it))?"
    r"|(?:continue|resume|carry on) (?:reading )?(?:the |my |that )?(?:document|letter)"
    r"|read on|read the (?:rest|next part|next bit)(?: of it)?)$"
)
_STOP_RE = re.compile(r"^(?:stop|pause) reading(?: (?:the |my |that )?(?:document|letter|it))?$")

# ─── Replies to the questions it asks ────────────────────────────────────

_WHOLE = re.compile(r"\b(?:whole|all|everything|entire|lot|through|yes|yeah|yep|sure|go ahead|please do|read it)\b")
_START = re.compile(r"\b(?:start|beginning|first (?:bit|part|page)|a bit|a little|top|opening|just)\b")
_MORE = re.compile(r"^(?:ok(?:ay)?,? )?(?:yes|yeah|yep|yup|sure|please|go on|go ahead|continue|keep going|keep reading"
                   r"|carry on|read on|more|next|next part|the next part|on|go|ok|okay|please do|yes please)\b")
_AGAIN = re.compile(r"\b(?:again|repeat|say that|one more time)\b")
_FROM_TOP = re.compile(r"\b(?:start (?:again|over)|from the (?:start|beginning|top)|start from)\b")
_PAUSE = re.compile(r"^(?:ok(?:ay)?,? )?(?:pause|wait|hold on|hang on|not now|later|not yet|no|nope|no thanks"
                    r"|one (?:sec|second|moment)|just a (?:sec|second|moment)|pause reading)\b")
_STOP = re.compile(r"^(?:ok(?:ay)?,? )?(?:stop|enough|that'?s enough|that'?ll do|that will do|that'?s all|done"
                   r"|cancel|never ?mind|forget it|stop reading|quiet|be quiet|shut up)\b")
_NEITHER = re.compile(r"^(?:neither|none|no|nope|never ?mind|forget it|cancel|nothing)\b")


def _said(text: str) -> str:
    return sanitize_for_speech(text)


def _title(name: str) -> str:
    return _said(name)[:120]


#: How long a parked question's answer is taken (music's choices: 60 s; a
#: spoken follow-up opens for 8 s after its reply).
CHOICE_TTL_SEC = 30.0


def _play_sec(words: int) -> float:
    """About how long ``words`` take to say."""
    return words * 60.0 / reading.WORDS_PER_MINUTE


def _minutes(words: int) -> str:
    m = max(1, round(words / reading.WORDS_PER_MINUTE))
    return "a minute" if m == 1 else "%d minutes" % m


# Strong references to the background work (a task nobody holds can be
# garbage-collected mid-flight), and the idle timer per session.
_TASKS: set[asyncio.Task] = set()
_IDLE: dict[str, asyncio.Task] = {}


def _spawn(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _live_session(ctx: Context) -> Any:
    """The room's live satellite session, or None (a phone, /v1/intent)."""
    if ctx.from_phone or ctx.app is None or ctx.satellite_room_id is None:
        return None
    sessions = getattr(getattr(ctx.app, "state", None), "active_sessions", None) or {}
    return sessions.get(ctx.satellite_room_id)


class DocumentsHandler(Handler):
    name = "documents"
    # band rationale: after chat_mode (220) and printing (222, whose
    # "print …" and "scan this" it never says), before voice_notes (230),
    # whose "^(jot|write|note|save) …" capture took "write a letter to the
    # landlord" as a note reading "a letter to the landlord" — the
    # collision the Documents plan found. Its read paths need a document
    # word and leave out notes, news, books and the calendar (175, ahead
    # anyway), so voice_notes' "read my notes", news' "read me the
    # headlines" (260) and spoken_audio's "read the book X" (270) keep
    # theirs.
    priority_band = 225
    display = HandlerDisplay(label="Documents", tone="info")
    requires_network = "no"
    confirmation_kinds = (DOC_PICK, DOC_LENGTH, DOC_MORE, DOC_PAUSED, LETTER_AUTHOR, LETTER_DICTATION)
    choice_kinds = (DOC_PICK, DOC_LENGTH, DOC_MORE, DOC_PAUSED, LETTER_AUTHOR, LETTER_DICTATION)
    @property
    def example_phrases(self) -> tuple[str, ...]:  # type: ignore[override]
        # The manual, Home and "what can you do" never suggest a letter
        # while AI features are off: it would only say they're off.
        from domovoi import ai_features

        if ai_features.enabled():
            return ("read me the lease", "take a letter to the landlord")
        return ("read me the lease",)

    tool_schema = {
        "name": "documents",
        "description": (
            "Read one of the household's saved documents aloud by its name, or take a letter by dictation "
            "and save it in Documents. Only when the user asks to read a document or a file, or to write, "
            "take or dictate a letter."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "letter"]},
                "document": {"type": "string", "description": "The document's name, for action=read."},
                "to": {"type": "string", "description": "Who the letter is to, for action=letter."},
            },
            "required": ["action"],
        },
    }

    # A request for this tool says so: writing or taking a letter, dictation,
    # or reading a named kind of document. "read" alone is everyday speech,
    # "what letter comes after b" is the alphabet, and the tool declines a
    # document it can't find.
    _CUE = re.compile(
        r"\b(?:take|write|draft|compose|dictate|type)\b.*\bletter\b|\bdictat\w*"
        r"|\bread\b.*\b" + _STRONG + r"\b"
    )

    def offers_tool(self, transcript: str) -> bool:
        return bool(self._CUE.search(transcript))

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_STOP_RE, DocumentsHandler._stop_from_match),
            FastPath(_CONTINUE_RE, DocumentsHandler._continue_from_match),
            FastPath(_READ_BACK_RE, DocumentsHandler._read_back_from_match),
            FastPath(letters.LETTER_RE, DocumentsHandler._letter_from_match),
            FastPath(_READ_RE, DocumentsHandler._read_from_match),
        ]

    # ─── plumbing ────────────────────────────────────────────────────────

    def _reply(self, ctx: Context, text: str, *, ask: bool = False) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name, expect_followup=ask)

    async def _park(self, ctx: Context, session: AsyncSession, kind: str, prompt: str, *,
                    ttl: float | None = None) -> None:
        """Park a question; its answer is taken for ``ttl`` seconds (review
        round 1: "ok" or "next" much later must never resume a reading)."""
        await request_confirmation(session, ctx.session_id, kind=kind, handler=self.name, prompt=prompt[:200],
                                   data={"expires_at": time.time() + (CHOICE_TTL_SEC if ttl is None else ttl)})

    @staticmethod
    async def _get(session: AsyncSession, ctx: Context, key: str) -> Any:
        if ctx.session_id is None:
            return None
        return (await SessionRepository(session).get_context(ctx.session_id) or {}).get(key)

    @staticmethod
    async def _set(session: AsyncSession, ctx: Context, key: str, value: Any) -> None:
        if ctx.session_id is not None:
            await SessionRepository(session).set_context_key(ctx.session_id, key, value)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._reply(ctx, "Say read me and a document's name, or take a letter.")

    async def execute_from_tool(self, args: dict, ctx: Context, session: AsyncSession) -> Response | None:
        action = args.get("action")
        if action == "read":
            what = str(args.get("document") or "").strip()
            if not what or not reading.find(what):
                return None  # nothing by that name: the Q&A model answers
            return await self._read_named(what, ctx, session)
        if action == "letter":
            to = " ".join(str(args.get("to") or "").split())[:60]
            ask = letters.parse_request("take a letter" + (" to " + to.lower() if to else ""))
            return await self._start_letter(ask, ctx, session) if ask else None
        return None

    # ─── reading ─────────────────────────────────────────────────────────

    async def _read_from_match(self, m, ctx, session):
        what = (m.group("what") or "").strip()
        claim = read_claim(what)
        if claim is None:
            return None  # the index moved since the pattern matched
        kind, docs = claim
        name = _FROM_DOCS.sub("", what).strip()
        if kind == "read":
            return await self._read_named(name, ctx, session, docs, [])
        return await self._read_named(name, ctx, session, [], docs if kind == "near" else [])

    async def _read_named(self, what: str, ctx: Context, session: AsyncSession, found=None,
                          near=None) -> Response:
        found = reading.find(what) if found is None else found
        if not found:
            near = reading.find_near(what) if near is None else near
            if near:
                # Heard "the leaves", Documents has "Lease": asked, never read
                # on a guess.
                from domovoi.handlers.printing import _list_words

                names = [_title(d.title) for d in near]
                listed = " or ".join(names) if len(names) < 3 else _list_words(names).replace(", and ", ", or ")
                text = "Did you mean %s?" % listed
                if ctx.session_id is None or not ctx.answerable:
                    return self._reply(ctx, "I couldn't find that in Documents. " + text.replace(
                        "Did you mean", "Say read me and its name:").rstrip("?") + ".")
                await self._set(session, ctx, READING_KEY, {"pick": [{"rel": d.rel, "title": d.title} for d in near]})
                await self._park(ctx, session, DOC_PICK, text)
                return self._reply(ctx, text, ask=True)
            if not [w for w in re.findall(r"[a-z0-9]+", what.lower())
                    if w not in ("a", "an", "the", "my", "our", "some", "any", "document", "documents", "file",
                                 "files", "doc", "docs", "pdf", "letter", "letters")]:
                return self._reply(ctx, "Which document? Say read me and its name, like read me the lease.")
            return self._reply(ctx, _said(
                "I couldn't find a document called %s in Documents. I can only read documents that are in "
                "Documents." % what[:80]))
        if len(found) > 1:
            from domovoi.handlers.printing import _list_words, _number_word

            names = [_title(d.title) for d in found]
            text = "I found %s: %s. Which one?" % (_number_word(len(names)), _list_words(names))
            if ctx.session_id is None or not ctx.answerable:
                return self._reply(ctx, text.replace(" Which one?", " Say the whole name."))
            await self._set(session, ctx, READING_KEY, {"pick": [{"rel": d.rel, "title": d.title} for d in found]})
            await self._park(ctx, session, DOC_PICK, text)
            return self._reply(ctx, text, ask=True)
        return await self._open(found[0].rel, ctx, session)

    async def _open(self, rel: str, ctx: Context, session: AsyncSession) -> Response:
        try:
            sp = await reading.load(rel)
        except reading.ReadRefused as e:
            return self._reply(ctx, str(e))
        await self._set(session, ctx, LAST_KEY, {"rel": sp.rel, "title": sp.title})
        if sp.is_short():
            await self._set(session, ctx, READING_KEY, None)
            return self._reply(ctx, " ".join(sp.sentences))
        if ctx.session_id is None or not ctx.answerable:
            # Nobody can answer a question here (the dashboard's play box):
            # the start, said as the start.
            return await self._read_from(sp, 0, reading.START_WORDS, "start", ctx, session)
        line = "%s. %s The whole thing, or just the start?" % (_title(sp.title), reading.length_line(sp))
        await self._set(session, ctx, READING_KEY, {"rel": sp.rel, "title": sp.title, "mtime": sp.mtime,
                                                    "pos": 0, "last": 0, "size": reading.PART_WORDS})
        await self._park(ctx, session, DOC_LENGTH, line)
        return self._reply(ctx, line, ask=True)

    async def _read_from(self, sp: reading.Spoken, pos: int, size: int, mode: str, ctx: Context,
                         session: AsyncSession) -> Response:
        """One part from sentence ``pos``: up to ``size`` words, cut between
        sentences, then what comes next."""
        sentences = list(sp.sentences)
        pos = max(0, min(pos, len(sentences)))
        end, words = pos, 0
        while end < len(sentences):
            w = len(sentences[end].split())
            if end > pos and words + w > size:
                break
            words += w
            end += 1
        text = " ".join(sentences[pos:end])
        left = sum(len(s.split()) for s in sentences[end:])
        if not left:
            await self._set(session, ctx, READING_KEY, None)
            return self._reply(ctx, text + " That's the end." if pos else text)
        state = {"rel": sp.rel, "title": sp.title, "mtime": sp.mtime, "pos": end, "last": pos,
                 "size": reading.PART_WORDS}
        await self._set(session, ctx, READING_KEY, state)
        if mode == "start" or ctx.session_id is None or not ctx.answerable:
            tail = " That's the start. There's about %s more: say continue reading to hear it." % _minutes(left)
            if ctx.session_id is not None and ctx.answerable:
                await self._park(ctx, session, DOC_PAUSED, tail, ttl=_play_sec(words) + CHOICE_TTL_SEC)
            return self._reply(ctx, text + tail)
        tail = " There's about %s more. Shall I go on?" % _minutes(left)
        # The question comes at the end of the part: its window opens once
        # the part has played.
        await self._park(ctx, session, DOC_MORE, tail, ttl=_play_sec(words) + CHOICE_TTL_SEC)
        return self._reply(ctx, text + tail, ask=True)

    async def _resume(self, state: dict, ctx: Context, session: AsyncSession, *, at: int | None = None) -> Response:
        try:
            sp = await reading.load(state["rel"])
        except reading.ReadRefused as e:
            await self._set(session, ctx, READING_KEY, None)
            return self._reply(ctx, str(e))
        pos = int(state.get("pos") or 0) if at is None else at
        prefix = ""
        if state.get("mtime") and sp.mtime and abs(float(state["mtime"]) - sp.mtime) > 1e-3:
            pos, prefix = 0, "That document has changed since, so from the start. "
        r = await self._read_from(sp, pos, int(state.get("size") or reading.PART_WORDS), "whole", ctx, session)
        r.text = prefix + r.text
        return r

    async def _continue_from_match(self, m, ctx, session):
        state = await self._get(session, ctx, READING_KEY)
        if not isinstance(state, dict) or not state.get("rel"):
            # Not ours to answer: no document is being read in this room.
            return None
        return await self._resume(state, ctx, session)

    async def _stop_from_match(self, m, ctx, session):
        state = await self._get(session, ctx, READING_KEY)
        if not isinstance(state, dict) or not state.get("rel"):
            return None
        if m.group(0).startswith("pause"):
            if ctx.trigger == "barge_in":
                state["pos"] = state.get("last", 0)
                await self._set(session, ctx, READING_KEY, state)
            return self._reply(ctx, "Paused. Say continue reading when you want the rest.")
        await self._set(session, ctx, READING_KEY, None)
        return self._reply(ctx, "Okay, I've stopped reading.")

    async def _read_back_from_match(self, m, ctx, session):
        pending = await self._get(session, ctx, PENDING_CONFIRMATION_KEY)
        if isinstance(pending, dict) and pending.get("kind") == "core.scan_read":
            # "Want me to tell you what it says?" after a scan, answered
            # "read it": that is a yes to the scan's own question.
            from domovoi.handlers import HANDLER_BY_NAME

            printing = HANDLER_BY_NAME.get(str(pending.get("handler") or ""))
            if printing is not None:
                await self._set(session, ctx, PENDING_CONFIRMATION_KEY, None)
                return await printing.handle_confirmation("core.scan_read", pending, True, ctx, session)
        letter = await self._get(session, ctx, LETTER_KEY)
        if isinstance(letter, dict) and letter.get("stage") == "writing":
            # Being written up: never an older document instead (review r1).
            return self._reply(ctx, "I'm still writing the letter up. I'll say when it's saved.")
        last = await self._get(session, ctx, LAST_KEY)
        if not isinstance(last, dict) or not last.get("rel"):
            return self._reply(ctx, "There's nothing to read back yet. Say read me and a document's name.")
        return await self._open(str(last["rel"]), ctx, session)

    # ─── a parked question's answer ──────────────────────────────────────

    async def handle_confirmation(self, kind, data, affirmative, ctx, session) -> Response:
        # Every kind here is a choice; a yes/no reaches handle_choice_reply
        # through the router. This is the restart-time fallback only.
        r = await self.handle_choice_reply(kind, data, "yes" if affirmative else "no", ctx, session)
        return r or self._reply(ctx, "Okay.")

    async def handle_choice_reply(self, kind, data, transcript, ctx, session) -> Response | None:
        from domovoi.router import strip_leading_filler

        said = strip_leading_filler(transcript.strip()) or transcript.strip()
        expired = time.time() > float((data or {}).get("expires_at") or float("inf"))
        if kind != LETTER_DICTATION and (expired or ctx.trigger == "wake_word"):
            # Asked too long ago, or a fresh wake word: a new request, routed
            # as one. (A barge-in over a part still answers "Shall I go on?";
            # dictation has its own rules, in _letter_reply.)
            if kind == LETTER_AUTHOR:
                await self._set(session, ctx, LETTER_KEY, None)
            elif kind == DOC_PICK:
                await self._set(session, ctx, READING_KEY, None)
            return None
        if kind in (LETTER_AUTHOR, LETTER_DICTATION):
            return await self._letter_reply(kind, transcript, said, ctx, session)
        state = await self._get(session, ctx, READING_KEY)
        if kind == DOC_PICK:
            picks = (state or {}).get("pick") if isinstance(state, dict) else None
            if not picks:
                return None
            if _NEITHER.match(said):
                await self._set(session, ctx, READING_KEY, None)
                return self._reply(ctx, "Okay.")
            from domovoi.handlers.printing import _pick_from
            from domovoi.router import _parse_yes_no

            i = _pick_from(said, [p["title"] for p in picks])
            if i is None and len(picks) == 1 and _parse_yes_no(said) is True:
                i = 0  # "Did you mean Lease?" "Yes."
            if i is None:
                return None
            return await self._open(picks[i]["rel"], ctx, session)
        if not isinstance(state, dict) or not state.get("rel"):
            return None
        if kind == DOC_LENGTH:
            if _STOP.match(said) or _NEITHER.match(said):
                await self._set(session, ctx, READING_KEY, None)
                return self._reply(ctx, "Okay.")
            whole, start = bool(_WHOLE.search(said)), bool(_START.search(said))
            if not whole and not start:
                return None
            try:
                sp = await reading.load(state["rel"])
            except reading.ReadRefused as e:
                await self._set(session, ctx, READING_KEY, None)
                return self._reply(ctx, str(e))
            if start and not re.search(r"\b(?:whole|all|everything|entire)\b", said):
                return await self._read_from(sp, 0, reading.START_WORDS, "start", ctx, session)
            return await self._read_from(sp, 0, reading.PART_WORDS, "whole", ctx, session)
        # DOC_MORE ("Shall I go on?") and DOC_PAUSED (after "just the start"
        # or "pause"). A reply that talked over the part means the part was
        # cut off: picking up again starts it over.
        interrupted = ctx.trigger == "barge_in"
        if _FROM_TOP.search(said):
            return await self._resume(state, ctx, session, at=0)
        if _STOP.match(said):
            await self._set(session, ctx, READING_KEY, None)
            return self._reply(ctx, "Okay, I've stopped reading.")
        if _PAUSE.match(said):
            if interrupted:
                state["pos"] = state.get("last", 0)
                await self._set(session, ctx, READING_KEY, state)
            await self._park(ctx, session, DOC_PAUSED, "paused")
            return self._reply(ctx, "Okay. Say continue reading when you want the rest.")
        if _AGAIN.search(said) and not _MORE.match(said):
            return await self._resume(state, ctx, session, at=int(state.get("last") or 0))
        if _MORE.match(said) or _CONTINUE_RE.match(said):
            return await self._resume(state, ctx, session, at=int(state.get("last") or 0) if interrupted else None)
        if interrupted:
            # Something else said over the part: it is answered as usual,
            # and "continue reading" later starts the cut-off part over.
            state["pos"] = state.get("last", 0)
            await self._set(session, ctx, READING_KEY, state)
        return None

    # ─── taking a letter ─────────────────────────────────────────────────

    async def _letter_from_match(self, m, ctx, session):
        ask = letters.parse_request(m.group(0))
        if ask is None:
            return None
        return await self._start_letter(ask, ctx, session)

    async def _start_letter(self, ask: letters.LetterAsk, ctx: Context, session: AsyncSession) -> Response:
        from domovoi import ai_features

        if ask.kind == "resume":
            return self._reply(ctx, "I can take a letter by voice. A resume needs a few questions answered, so "
                                    "start it in Chat, on the dashboard or in the app.")
        if ctx.from_phone:
            return self._reply(ctx, "On the phone, type it in a chat instead, like write a letter to the "
                                    "landlord, and the draft appears there.")
        if ctx.session_id is None or not ctx.answerable:
            return self._reply(ctx, "Taking a letter needs a room that can listen. Say it in a room, or "
                                    "type it in Chat.")
        if ai_features.turned_off():
            # Plainly, before any dictation: nothing would write it up.
            return self._reply(ctx, "Writing a letter needs the AI features, and they're turned off. Reading "
                                    "documents aloud still works.")
        author, author_id = await self._speaker(ctx, session)
        state = letters.new_state(ask, author=author, author_id=author_id, room=ctx.satellite_room_id)
        await self._set(session, ctx, LETTER_KEY, state)
        if author is None:
            line = "Who's it from?"
            await self._park(ctx, session, LETTER_AUTHOR, line)
            return self._reply(ctx, line, ask=True)
        return await self._dictate(state, ctx, session, first=True)

    @staticmethod
    async def _speaker(ctx: Context, session: AsyncSession) -> tuple[str | None, int | None]:
        """The speaker voice ID recognised, by name; (None, None) otherwise."""
        if ctx.person_id is None:
            return None, None
        from domovoi.db.repositories import PeopleRepository

        try:
            row = await PeopleRepository(session).get(int(ctx.person_id))
        except Exception as e:  # noqa: BLE001 — ask then
            log.warning("letter by voice: the speaker couldn't be looked up: %s", type(e).__name__)
            return None, None
        if row is None or not (row[1] or "").strip():
            return None, None
        return str(row[1]).strip()[:60], int(row[0])

    async def _dictate(self, state: dict, ctx: Context, session: AsyncSession, *, first: bool = False,
                       line: str | None = None) -> Response:
        """Keep listening: the letter's state saved, the dictation parked,
        the quiet-room timer re-armed."""
        state["stage"] = "dictating"
        state["seq"] = int(state.get("seq") or 0) + 1
        await self._set(session, ctx, LETTER_KEY, state)
        if line is None:
            if first and state.get("parts"):
                line = "Got that. Go on, or say end of letter when you're done."
            elif first and state.get("author_id") and state.get("author"):
                # Voice ID named the speaker: said, so a wrong guess is heard.
                line = "Go ahead, %s. Say end of letter when you're done." % _said(str(state["author"]))
            elif first:
                line = "Go ahead. Say end of letter when you're done."
            else:
                line = "Go on."
        await self._park(ctx, session, LETTER_DICTATION, "dictating")
        self._arm_idle(ctx, int(state["seq"]))
        return self._reply(ctx, line, ask=True)

    async def _letter_reply(self, kind: str, transcript: str, said: str, ctx: Context,
                            session: AsyncSession) -> Response | None:
        state = await self._get(session, ctx, LETTER_KEY)
        if not isinstance(state, dict) or state.get("stage") not in ("author", "dictating"):
            return None
        if letters.CANCEL_RE.match(said):
            await self._set(session, ctx, LETTER_KEY, None)
            self._disarm_idle(ctx)
            return self._reply(ctx, "Okay, I've thrown that letter away. Nothing was saved.")
        if kind == LETTER_AUTHOR:
            from domovoi.router import first_fast_path

            name, skipped = letters.author_from_reply(said)
            if (name is None and not skipped) or first_fast_path(said) is not None:
                # Not a name ("what time is it"): no letter, and the turn is
                # routed as the request it is.
                await self._set(session, ctx, LETTER_KEY, None)
                return None
            state["author"] = name
            line = None
            if name:
                line = "Thanks, %s. %s" % (_said(name), "Go on, or say end of letter when you're done."
                                           if state.get("parts") else "Go ahead. Say end of letter when you're done.")
            return await self._dictate(state, ctx, session, first=True, line=line)
        # LETTER_DICTATION
        if ctx.trigger == "wake_word":
            # A fresh wake word after the room stopped listening: a command
            # ("what time is it", "read me the lease") ends the letter and is
            # answered; anything else is more of it. A note or another
            # letter said here is dictation.
            from domovoi.router import first_fast_path

            hit = first_fast_path(said)
            if (hit is not None and hit[0].name not in ("voice_notes", "dismiss")
                    and hit[1].pattern is not letters.LETTER_RE):
                await self._finish(state, ctx, session, background_only=True)
                return None
        if letters.UNDO_RE.match(said):
            parts = list(state.get("parts") or [])
            if not parts:
                return await self._dictate(state, ctx, session, line="There's nothing to take out yet. Go on.")
            parts.pop()
            state["parts"] = parts
            return await self._dictate(state, ctx, session, line="Took that out. Go on.")
        if letters.END_RE.match(said):
            return await self._finish(state, ctx, session)
        tail = letters.END_TAIL_RE.match(transcript.strip())
        words = letters.clean_dictation(tail.group("body") if tail else transcript)
        if words:
            state.setdefault("parts", []).append(words)
        if tail:
            return await self._finish(state, ctx, session)
        if len(state["parts"]) >= letters.MAX_PARTS or len(letters.dictation_text(state)) >= letters.MAX_CHARS:
            return await self._finish(state, ctx, session, line="That's as long as a letter by voice can be. ")
        return await self._dictate(state, ctx, session)

    async def _finish(self, state: dict, ctx: Context, session: AsyncSession, *, line: str = "",
                      background_only: bool = False) -> Response | None:
        self._disarm_idle(ctx)
        if not letters.dictation_text(state):
            await self._set(session, ctx, LETTER_KEY, None)
            return None if background_only else self._reply(
                ctx, "There's nothing in the letter yet, so nothing was saved.")
        state["stage"] = "writing"
        await self._set(session, ctx, LETTER_KEY, state)
        live = _live_session(ctx)
        if live is not None or background_only:
            # The model can take a while on a small box: the room hears that
            # it's being written, then where it is once it's saved.
            _spawn(_write_up_later(state, ctx.session_id, ctx.satellite_room_id), "letter-write-up")
            if background_only:
                return None
            return self._reply(ctx, line + "Writing it up now. I'll tell you when it's saved.")
        try:
            saved = await letters.write_and_save(state)
        except letters.LetterFailed as e:
            await self._set(session, ctx, LETTER_KEY, None)
            return self._reply(ctx, _said(line + str(e)[:1].upper() + str(e)[1:] + "."))
        await self._set(session, ctx, LETTER_KEY, None)
        await self._set(session, ctx, LAST_KEY, {"rel": saved.rel_path, "title": saved.title})
        return self._reply(ctx, _said(line + letters.saved_line(saved)))

    # ─── the quiet room ──────────────────────────────────────────────────

    def _arm_idle(self, ctx: Context, seq: int) -> None:
        live = _live_session(ctx)
        if live is None or ctx.session_id is None:
            return  # nothing to time: "end of letter" ends it
        self._disarm_idle(ctx)
        sid = str(ctx.session_id)
        _IDLE[sid] = _spawn(_idle_end(ctx.session_id, ctx.satellite_room_id, seq, ctx.app), "letter-idle")

    @staticmethod
    def _disarm_idle(ctx: Context) -> None:
        task = _IDLE.pop(str(ctx.session_id), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()


async def _idle_end(session_id: Any, room_id: str | None, seq: int, app: Any) -> None:
    """End the letter when the room has been quiet since its last "Go on."
    (the follow-up window closed with nothing said): it is written up and
    the room hears where it is saved."""
    from domovoi.db.session import session_scope

    try:
        await asyncio.sleep(letters.IDLE_SEC)
        give_up = time.monotonic() + 600.0
        while time.monotonic() < give_up:
            # A reply going out or playing, someone speaking, or the
            # follow-up window still open: not quiet yet. A satellite that
            # went away ends the letter too (its next session is a new one):
            # the words are written up, never dropped.
            sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
            live = sessions.get(room_id)
            if live is None or not _awaiting_reply(live):
                break
            await asyncio.sleep(letters.QUIET_POLL_SEC)
        async with session_scope() as s:
            repo = SessionRepository(s)
            context = await repo.get_context(session_id) or {}
            state = context.get(LETTER_KEY)
            if not isinstance(state, dict) or state.get("stage") != "dictating" or int(state.get("seq") or 0) != seq:
                return
            pending = context.get(PENDING_CONFIRMATION_KEY)
            if isinstance(pending, dict) and pending.get("kind") == LETTER_DICTATION:
                await repo.set_context_key(session_id, PENDING_CONFIRMATION_KEY, None)
            take_parked_choice(session_id)
            if not letters.dictation_text(state):
                # Nothing was dictated: nothing to write up, nothing saved.
                await repo.set_context_key(session_id, LETTER_KEY, None)
                empty = True
            else:
                empty = False
            if not empty:
                state["stage"] = "writing"
                await repo.set_context_key(session_id, LETTER_KEY, state)
    except asyncio.CancelledError:
        return
    finally:
        if _IDLE.get(str(session_id)) is asyncio.current_task():
            _IDLE.pop(str(session_id), None)
    if empty:
        if room_id:
            try:
                await _say_reply(room_id, "I didn't hear a letter, so nothing was saved.")
            except Exception as e:  # noqa: BLE001
                log.info("letter by voice: the announcement failed: %s", type(e).__name__)
        return
    await _write_up_later(state, session_id, room_id)


async def _say_reply(room_id: str, text_: str) -> list[str]:
    """Say the end of a dictation in its room: the answer to someone who
    just dictated a letter there and is waiting for it, so a reply
    (``kind="user"``), never held back by quiet hours
    (domovoi/quiet_hours.py). Core only: the SDK's ``speech.announce``
    can only send notices. The rooms reached ([] when it isn't connected)."""
    from domovoi.sdk import speech

    provider = speech._active_sessions_provider
    sess = (provider() or {}).get(room_id) if provider is not None else None
    if sess is None:
        return []
    try:
        await sess.announce(text_, kind="user")
    except Exception as e:  # noqa: BLE001 — best-effort, like any announcement
        log.info("letter by voice: %s didn't take the line: %s", room_id, type(e).__name__)
        return []
    return [room_id]


def _awaiting_reply(live: Any) -> bool:
    """The room may still answer: StreamSession.awaiting_reply, or (a
    session without it) a capture open or a reply still being made."""
    check = getattr(live, "awaiting_reply", None)
    if callable(check):
        return bool(check())
    task = getattr(live, "_response_task", None)
    return bool(getattr(live, "utterance_active", False)) or (task is not None and not task.done())


async def _write_up_later(state: dict, session_id: Any, room_id: str | None) -> None:
    """Write the letter up and say where it is (or why not) in the room."""
    from domovoi.db.session import session_scope

    try:
        saved = await letters.write_and_save(state)
        line = letters.saved_line(saved)
        last = {"rel": saved.rel_path, "title": saved.title}
    except letters.LetterFailed as e:
        line = str(e)[:1].upper() + str(e)[1:] + "."
        last = None
    except Exception:  # noqa: BLE001 — a background task: logged, said
        log.exception("letter by voice: writing it up failed")
        line, last = "I couldn't save the letter, sorry.", None
    try:
        async with session_scope() as s:
            repo = SessionRepository(s)
            await repo.set_context_key(session_id, LETTER_KEY, None)
            if last is not None:
                await repo.set_context_key(session_id, LAST_KEY, last)
    except Exception as e:  # noqa: BLE001
        log.warning("letter by voice: the session couldn't be updated: %s", type(e).__name__)
    if room_id:
        try:
            reached = await _say_reply(room_id, _said(line))
            if not reached:
                log.info("letter by voice: %s wasn't reachable to hear where the letter is", room_id)
        except Exception as e:  # noqa: BLE001
            log.info("letter by voice: the announcement failed: %s", type(e).__name__)
