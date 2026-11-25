"""Memory commands in typed chat: said by the code that did them, never by
the model (owner priority: trust).

A typed chat message never goes through the core's router, so until this
step "remember that …", "forget that", "forget that <X>" and "what do you
remember about me?" reached the chat model, which could answer "I'll
remember that" or "forgotten" with nothing saved or deleted. Voice and the
phone's chat mic were always right: they go through ``route()`` and the
memory handler (``domovoi/handlers/memory.py``).

:func:`reply` runs ahead of the model in ``chat.py`` (a send, and a "try
again" of a model reply to one of these). It recognises the same commands
with the memory handler's own regexes and does them with the handler's own
methods, so the words are the voice ones ("Got it — I'll remember that.",
"OK, forgotten: …", "Here's what I remember: …"), and only the code path
that actually saved, deleted or read says them. The reply is written like
any other deterministic chat reply (``stats.model_role = "memory"``, no
model asked, not "try again"-able), with ``stats.memory`` saying what it
did.

**Whose memory** (:func:`device_person`): memories are per person, and a
typed message names no voice. The person is the one the asking phone or
browser belongs to (``devices.owner_person_id``, "this is me" on a
person's People page; the device is the id the caller names itself by, as
for listening history and the music mix). Never a guess:

* a device linked to nobody (or that didn't name itself, or is unknown):
  the "isn't linked" line, nothing saved, read or deleted;
* a SHARED SCREEN (``devices.shared_screen``): treated as linked to nobody
  even if it once was (marking it shared drops its person anyway,
  web/backend/api/devices.py): it is the whole household's, so nobody's
  personal memories are saved, read or forgotten from it;
* the thread's own person (``chat_threads.person_id``) is NOT used: a
  household thread is typed in from many devices.

**A bare "forget that"** forgets the memory saved by the reply just before
it in this thread (the row right before the message is this step's save
confirmation, for the same person), else asks "Forget what?" (a brush-off
"forget it" with nothing to forget is acknowledged, as by voice). The
next message after "Forget what?" is read as the thing to forget (the
handler's own reply reader); anything that matches none of the person's
memories goes to the model as usual. This keys on the thread's previous
messages and the person only, never on the thread's visibility.

There is no switch that turns explicit memories off (``memory_extractor_*``
only governs the implicit offers), so there is no "Memory is off" answer.
While the household's AI features are off the whole typed chat is refused
before anything is stored (chat.py ``SEND``), memory commands included:
that refusal says AI is off, and nothing claims a memory.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from domovoi import house_facts
from domovoi.db.repositories import MemoriesRepository
from domovoi.handlers import memory as _mem
from domovoi.handlers.dismiss import _ack
from domovoi.models import Context

log = logging.getLogger(__name__)

#: ``stats.model_role`` of a reply written here.
MODEL_ROLE = "memory"

#: Where a phone or browser says whose it is: the People section, under
#: Settings → Household (web/static/settings.jsx SETTINGS_GROUPS), on the
#: person's own entry ("this is me", web/static/people.jsx).
LINK_PLACE = "Settings → Household → People"

_UNLINKED = (
    "I can't {what} from this device yet: it isn't linked to a person. "
    "Open your name in " + LINK_PLACE + " and press \"this is me\" to link it."
)
SHARED_SCREEN_TEXT = (
    "This is a shared screen, so it's the whole household's, not one person's: "
    "I don't save, read or forget anyone's memories from it. Use your own phone or browser."
)
NOT_YOURS_TEXT = "I can only forget that for the person who asked me to remember it."

# The router's "can you …" lead (router._LEADING_FILLER_RE), which
# house_facts.normalize keeps for its own questions: "can you remember
# that my locker is 12" is the command, as it is by voice. "Can you remember
# things about me?" is not: the memory regexes refuse a generic object (G1),
# so it goes on to house_facts.chat_reply, which answers the question.
_ASK_LEAD_RE = re.compile(r"^(?:(?:can|could|would|will) you(?:[,\s]+please)?[,\s]+)+")


def unlinked_text(what: str) -> str:
    """The "isn't linked" line, ``what`` being "save that", "read your
    memories" or "forget that"."""
    return _UNLINKED.format(what=what)


def _normal(raw: str) -> str:
    return _ASK_LEAD_RE.sub("", house_facts.normalize(raw)).strip()


def _cased(raw: str) -> str:
    """``raw`` as :func:`_normal` reads it, with its letters' case kept:
    a typed memory is saved as it was typed."""
    t = " ".join((raw or "").replace("’", "'").replace("‘", "'").split())
    t = t.strip().rstrip(".,!?").strip()
    t = re.compile(house_facts._FILLER_RE.pattern, re.I).sub("", t).strip()
    return re.compile(_ASK_LEAD_RE.pattern, re.I).sub("", t).strip()


def _cased_group(pattern: re.Pattern[str], raw: str, group: str, fallback: str) -> str:
    m = re.compile(pattern.pattern, re.I).match(_cased(raw))
    return m.group(group) if m else fallback


@dataclass(frozen=True)
class Command:
    """What a message asks: ``kind`` is ``remember`` / ``forget`` (with
    ``arg``: the fact or fragment), ``forget_last`` (``brush_off``) or
    ``list``."""
    kind: str
    arg: str = ""
    brush_off: bool = False


def command(raw: str) -> Command | None:
    """The memory command ``raw`` (a typed message) is, or None. The
    memory handler's own fast paths, in its order (bare forget first)."""
    t = _normal(raw)
    if not t:
        return None
    m = _mem._REMEMBER_RE.match(t)
    if m:
        return Command("remember", _cased_group(_mem._REMEMBER_RE, raw, "body", m.group("body")))
    m = _mem._FORGET_LAST_RE.match(t)
    if m:
        return Command("forget_last", brush_off=bool(m.group("lead")) or m.group("what") in _mem._BRUSH_OFF_WHAT)
    m = _mem._FORGET_MEMORY_RE.match(t)
    if m:
        return Command("forget", _cased_group(_mem._FORGET_MEMORY_RE, raw, "body", m.group("body")))
    if _mem._LIST_MEMORIES_RE.match(t):
        return Command("list")
    return None


# ─── Whose memory ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Asker:
    """Who a device is, for memories: ``state`` is ``linked`` (with
    ``person_id``), ``unlinked`` or ``shared``."""
    state: str
    person_id: int | None = None


async def device_person(s: Any, device_id: str | None) -> Asker:
    """The person ``device_id`` belongs to, for memories. Linked only when
    the device row names a person and is not a shared screen."""
    if not device_id:
        return Asker("unlinked")
    row = (await s.execute(
        text("SELECT owner_person_id, shared_screen FROM devices WHERE device_id = :d"),
        {"d": device_id},
    )).first()
    if row is None:
        return Asker("unlinked")
    if row[1]:
        return Asker("shared")
    if row[0] is None:
        return Asker("unlinked")
    return Asker("linked", int(row[0]))


# ─── The step ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reply:
    """The text to send and the row's ``stats``."""
    text: str
    stats: dict[str, Any]


def _reply(text_: str, action: str, person_id: int | None = None, **extra: Any) -> Reply:
    memory: dict[str, Any] = {"action": action}
    if person_id is not None:
        memory["person_id"] = person_id
    memory.update(extra)
    return Reply(text_, {"model_role": MODEL_ROLE, "memory": memory})


async def _previous(s: Any, thread_id: int, before_id: int) -> dict[str, Any] | None:
    """The thread's row right before ``before_id`` (the message being
    answered): its role and ``stats.memory``, or None."""
    row = (await s.execute(
        text("SELECT role, stats FROM chat_messages WHERE thread_id = :t AND id < :u "
             "ORDER BY id DESC LIMIT 1"),
        {"t": thread_id, "u": before_id},
    )).first()
    if row is None or row[0] != "assistant":
        return None
    stats = row[1]
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except ValueError:
            stats = None
    if not isinstance(stats, dict):
        return None
    if stats.get("model_role") != MODEL_ROLE or not isinstance(stats.get("memory"), dict):
        return None
    return stats["memory"]


async def reply(
    s: Any, *, thread_id: int, content: str, before_id: int, device_id: str | None,
) -> Reply | None:
    """The memory step for ``content`` (the message whose row is
    ``before_id`` in ``thread_id``, typed on ``device_id``), done in ``s``
    (the caller commits), or None: not a memory command, and the model
    answers it as before."""
    cmd = command(content)
    if cmd is None:
        return await _forget_what_answer(s, thread_id=thread_id, content=content,
                                         before_id=before_id, device_id=device_id)
    asker = await device_person(s, device_id)
    what = {"remember": "save that", "list": "read your memories"}.get(cmd.kind, "forget that")
    if asker.state == "shared":
        return _reply(SHARED_SCREEN_TEXT, "refused_shared_screen")
    if asker.state != "linked":
        return _reply(unlinked_text(what), "refused_unlinked")
    pid = int(asker.person_id)  # type: ignore[arg-type]
    handler = _mem.MemoryHandler()
    # No session: the voice turn's "last saved" mark lives in the core's
    # session context; here the thread's rows are the record.
    ctx = Context(person_id=pid)

    if cmd.kind == "remember":
        r = await handler._add_memory(cmd.arg, ctx, s)
        saved = (r.data or {}).get("memory_id")
        if saved is None:
            return _reply(r.text, "asked", pid)
        return _reply(r.text, "saved", pid, memory_id=int(saved))
    if cmd.kind == "forget":
        r = await handler._forget_memory(cmd.arg, ctx, s)
        gone = (r.data or {}).get("deleted_memory_id")
        if gone is None:
            return _reply(r.text, "not_forgotten", pid)
        return _reply(r.text, "forgotten", pid, memory_id=int(gone))
    if cmd.kind == "list":
        r = await handler._list_memories(ctx, s)
        return _reply(r.text, "listed", pid)
    return await _forget_last(s, thread_id=thread_id, before_id=before_id, pid=pid, brush_off=cmd.brush_off)


async def _forget_last(s: Any, *, thread_id: int, before_id: int, pid: int, brush_off: bool) -> Reply:
    """A bare "forget that": the memory the reply just before it saved
    (this step's own confirmation), when it is the asker's; else "Forget
    what?" (or, for "forget it", an acknowledgement)."""
    prev = await _previous(s, thread_id, before_id)
    if prev is not None and prev.get("action") == "saved" and isinstance(prev.get("memory_id"), int):
        owner = prev.get("person_id")
        memory_id = int(prev["memory_id"])
        if owner != pid:
            return _reply(NOT_YOURS_TEXT, "not_yours", pid)
        memories = MemoriesRepository(s)
        body = next((b for i, b, _t, _s in await memories.list_active(pid) if i == memory_id), None)
        if body is not None and await memories.delete(memory_id, pid):
            return _reply(_mem.forgotten_text(body), "forgotten", pid, memory_id=memory_id)
    if brush_off:
        return _reply(_ack(), "dismissed", pid)
    return _reply(_mem._FORGET_WHAT_TEXT, "forget_what", pid)


async def _forget_what_answer(
    s: Any, *, thread_id: int, content: str, before_id: int, device_id: str | None,
) -> Reply | None:
    """The message after "Forget what?" in this thread, read by the
    handler's own reply reader (a "never mind", or one of the asker's
    memories); None for anything else."""
    prev = await _previous(s, thread_id, before_id)
    if prev is None or prev.get("action") != "forget_what" or not isinstance(prev.get("person_id"), int):
        return None
    asker = await device_person(s, device_id)
    if asker.state != "linked" or asker.person_id != prev["person_id"]:
        return None
    pid = int(asker.person_id)  # type: ignore[arg-type]
    handler = _mem.MemoryHandler()
    r = await handler.handle_choice_reply(
        _mem.FORGET_WHAT_KIND, {"person_id": pid, "expires_at": time.time() + 60},
        _normal(content), Context(person_id=pid), s,
    )
    if r is None:
        return None
    gone = (r.data or {}).get("deleted_memory_id")
    if gone is not None:
        return _reply(r.text, "forgotten", pid, memory_id=int(gone))
    return _reply(r.text, "not_forgotten" if (r.data or {}).get("candidates") else "dismissed", pid)


__all__ = [
    "LINK_PLACE", "MODEL_ROLE", "NOT_YOURS_TEXT", "SHARED_SCREEN_TEXT",
    "Asker", "Command", "Reply", "command", "device_person", "reply", "unlinked_text",
]
