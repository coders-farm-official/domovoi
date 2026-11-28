"""Which typed chat messages are the house's to do (1.5.0): the rules the
web's typed send (web/backend/api/chat.py, its router hop) and the core's
text turn (domovoi/phone_voice.py ``run_phone_text_turn``) share, so the
two can't disagree.

Typed chat reaches the router through its FAST tiers only: a question the
house parked for this phone and thread (a confirmation or a choice), or a
fast path, core or plugin. The tool model is never asked from typed chat,
and nothing that matches neither costs a model call: the chat's own model
answers it, as before. No I/O here (``ANSWERED_SQL`` is a statement both
writers run).
"""

from __future__ import annotations

import re
from typing import Any

#: A house command is short and one line: a longer or pasted message is
#: never handed to the router (words in a pasted email are not commands).
COMMAND_MAX_CHARS = 400

#: Fast paths typed chat never runs through the router: the chat answers
#: these itself (its help, lookups, memory and house facts), or they only
#: make sense spoken (voice enrolment and "forget me", "say that again",
#: "never mind", open-mic chat mode). Excluded in both processes.
CHAT_OWN_HANDLERS = frozenset({
    "help", "help_desk", "house_facts", "memory", "double_check",
    "voice_profile", "repeat", "dismiss", "chat_mode",
})

#: The chat's own handlers' fast paths typed chat DOES take, as
#: ``(handler, fast path method)``: "remember to call mom" (no time) is
#: stored by the memory handler, as by voice, never left to a model.
TYPED_OWN_FAST_PATHS = frozenset({("memory", "_remember_to_from_match")})

#: Handlers whose fast paths also take ordinary prose, so a message to them
#: that ends in "?" (a question about saying it, not a command) stays the
#: chat's: "tell everyone I'm proud of them is what I'd say, right?". A
#: polite request asked as a question ("Can you announce that dinner is
#: ready?") is a request, as by voice: see :func:`prose_question`.
_PROSE_PRONE = {
    "intercom": None,
    "music": re.compile(r"^(?:play|shuffle)\b"),
    "lists": re.compile(r"^add\b"),
}
#: "please" anywhere makes it a request, not a question.
_PLEASE_RE = re.compile(r"\b(?:please|pls|plz)\b", re.I)

#: SQL: whether user row ``:u`` of thread ``:t`` already has its reply, an
#: assistant row after it and before the thread's next user row. The one
#: test both writers of a typed turn's reply use (the core's text turn and
#: the web's own fixed line), so a row never gets two.
ANSWERED_SQL = (
    "EXISTS (SELECT 1 FROM chat_messages a WHERE a.thread_id = :t AND a.role = 'assistant' "
    "AND a.id > :u AND NOT EXISTS (SELECT 1 FROM chat_messages n WHERE n.thread_id = :t "
    "AND n.role = 'user' AND n.id > :u AND n.id < a.id))"
)

#: Media handlers: with no player of the client's own (the dashboard), a
#: play there can't happen, and their other answers are about that player.
MEDIA_HANDLERS = frozenset({"music", "playlist", "music_alias", "spoken_audio"})


def eligible(text: str) -> bool:
    """Whether ``text`` may be a house command at all: some words, one line,
    at most :data:`COMMAND_MAX_CHARS`."""
    q = (text or "").strip()
    return bool(q) and len(q) <= COMMAND_MAX_CHARS and "\n" not in q and any(ch.isalnum() for ch in q)


def prose_question(handler_name: str, text: str) -> bool:
    """A message a prose-prone handler's fast path took that ends in "?"
    and is no polite request. Read as voice reads it
    (``router.normalize_transcript`` and ``strip_leading_filler``, the
    polite opener voice drops: "can you", "could you", "would you", "will
    you", "can u", "please"): a message that loses an opener there, or says
    "please" anywhere, is a request ("Can you announce that dinner is
    ready?") and goes where voice sends it."""
    raw = (text or "").strip()
    if handler_name not in _PROSE_PRONE or not raw.endswith("?"):
        return False
    from domovoi.router import normalize_transcript, strip_leading_filler

    said = normalize_transcript(raw)
    request = strip_leading_filler(said)
    if request != said or _PLEASE_RE.search(raw):
        return False
    rx = _PROSE_PRONE[handler_name]
    return rx is None or bool(rx.match(request))


def _typed_own(plan: Any) -> bool:
    """``plan`` is one of the chat's own handlers' fast paths typed chat
    takes (:data:`TYPED_OWN_FAST_PATHS`)."""
    name = getattr(getattr(plan, "handler", None), "name", None)
    method = getattr(getattr(getattr(plan, "fast_path", None), "method", None), "__name__", None)
    return (name, method) in TYPED_OWN_FAST_PATHS


def skip_handlers(plan: Any, text: str) -> tuple[str, ...]:
    """The handlers a typed turn must not run for ``text``: the chat's own,
    and the one ``plan`` (``router.plan_route``) names when the message is
    a question to a prose-prone handler."""
    skip = set(CHAT_OWN_HANDLERS)
    name = getattr(getattr(plan, "handler", None), "name", None)
    if getattr(plan, "path", None) == "fast" and name:
        if _typed_own(plan):
            skip.discard(name)
        elif prose_question(name, text):
            skip.add(name)
    return tuple(sorted(skip))


def hands_to_router(plan: Any, text: str) -> bool:
    """Whether ``plan`` (``router.plan_route`` on ``text``) is a fast path
    typed chat hands to the house (not the chat's own, not a question)."""
    if plan is None or getattr(plan, "path", None) != "fast":
        return False
    return plan.handler.name not in skip_handlers(plan, text)
