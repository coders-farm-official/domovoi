"""A command said in a loud room (1.5.0, noisy rooms, design §5 stage 2).

At a party the chatter never pauses long enough to end a capture, so the
satellite records to its cap (``exit_reason`` "max_record_seconds") and
``domovoi/overheard.py`` screens the turn. What this module adds (owner
answer Q7, 2026-11-22):

* **Transcribe the start.** A satellite from 1.5.0 on says where speech
  began (``utterance_end.first_voiced_frame``). For a capped wake-word or
  follow-up capture the core transcribes only from just before that frame
  up to :data:`WINDOW_SEC` later (:func:`stt_window`): a command is said
  first, right after the wake word, and the rest is the room. An older
  satellite sends no such field and its turn is transcribed whole, as
  before.
* **Say what was heard.** A command at the start of a capped capture
  (``overheard.COMMAND``) that only reads something (the time, the
  weather, what's playing) is answered after "It's loud in here. I heard:
  <command>." (:func:`heard_line`).
* **Ask before changing the house.** One that would change something
  (music, lights, lists, timers that ring, messages, anything a plugin
  does) is not run: the room asks "It's loud in here. Should I <command>?"
  (:func:`ask_line`) and parks the command (:data:`LOUD_KIND`). A yes
  within :data:`CONFIRM_TTL_SEC` runs it; a no, or nothing, doesn't.

What "changes the house" means: every fast path does, unless its handler
says it only reads (``Handler.read_only``: the clock, the calculator,
help, the calendar, news...) or the command is phrased as a question
("what's on the shopping list", "is the music playing"), which no
handler acts on (:func:`changes_house`). A plugin's handler changes the
house unless it says otherwise: asking once too often is the safe side.

Pure, like overheard.py: the router and the streaming layer call it.
"""

from __future__ import annotations

import re
import time
from typing import Any

from domovoi.endpointing import FRAME_MS

#: How much of a capped capture is transcribed, from its first voiced
#: frame: a command is a few seconds (15 words at 3 a second). Words past
#: it are the room, and a longer window only gives the TV more to say.
WINDOW_SEC = 5.0
#: Taken from before the first voiced frame too: the detector calls a
#: word's soft start silence.
LEAD_SEC = 0.3
#: 16 kHz, 16-bit mono.
_BYTES_PER_SEC = 16_000 * 2
_FRAME_BYTES = _BYTES_PER_SEC * FRAME_MS // 1000

#: The parked confirmation's kind. Owned by the router itself, not by a
#: handler: the command it parks is any handler's.
LOUD_KIND = "core.loud_command"
#: How long a parked loud command waits for its yes. Past it the yes is an
#: ordinary turn: a "yes" minutes later is not an answer to a party.
CONFIRM_TTL_SEC = 60.0

LOUD_OPENER = "It's loud in here."
NO_LINE = "Okay, I won't."
GONE_LINE = "Sorry, I can't do that one now. Try asking again."

#: A command phrased as a question reads, it does not act.
_QUESTION_RE = re.compile(
    r"^(?:what|what's|whats|when|when's|where|where's|who|who's|whose|which|how|how's|why"
    r"|is|are|was|were|do|does|did|have|has|any|anything)\b",
    re.IGNORECASE,
)
#: First words that don't make "Should I <command>?" read as English.
_NOT_A_VERB = frozenset({
    "i", "i'm", "i'd", "i'll", "we", "we're", "let's", "it", "it's", "the", "a", "an",
    "my", "our", "this", "that", "there", "you", "your", "please",
})
_PRONOUNS = {"my": "your", "me": "you", "mine": "yours", "myself": "yourself", "i": "you"}
_PRONOUN_RE = re.compile(r"\b(my|me|mine|myself|i)\b", re.IGNORECASE)


def first_voiced_frame(ctrl: dict[str, Any], frames: int) -> int | None:
    """An ``utterance_end``'s ``first_voiced_frame``, when it is about the
    ``frames`` the core holds (its ``frames`` agrees), else None."""
    first, sent = ctrl.get("first_voiced_frame"), ctrl.get("frames")
    if type(first) is int and type(sent) is int and sent == frames and 0 <= first < frames:
        return first
    return None


def stt_window(
    ctrl: dict[str, Any], *, frames: int, trigger: str | None,
) -> tuple[int, int] | None:
    """The byte range of a capped capture to transcribe, ``(start, end)``,
    or None to transcribe it whole (any other capture, an older satellite,
    counts that don't agree with the core's buffer)."""
    from domovoi import overheard

    if trigger not in overheard.GUARDED_TRIGGERS:
        return None
    if ctrl.get("exit_reason") != overheard.CAPPED_EXIT_REASON:
        return None
    first = first_voiced_frame(ctrl, frames)
    if first is None:
        return None
    total = frames * _FRAME_BYTES
    if total / _BYTES_PER_SEC < overheard.LONG_CAPTURE_SEC:
        # Not screened as capped (a household that lowered its cap): the
        # whole capture is the command, as always.
        return None
    start = max(0, first * _FRAME_BYTES - int(LEAD_SEC * _BYTES_PER_SEC) // 2 * 2)
    end = min(total, first * _FRAME_BYTES + int(WINDOW_SEC * _BYTES_PER_SEC))
    if end - start < _FRAME_BYTES:
        return None
    return start, end


def is_question(command: str) -> bool:
    return bool(_QUESTION_RE.match((command or "").strip()))


def changes_house(handler: Any, command: str) -> bool:
    """Whether running ``command`` on ``handler``'s fast path would change
    something in the house (see the module docstring)."""
    if getattr(handler, "read_only", False) is True:
        return False
    return not is_question(command)


def _said(command: str) -> str:
    return (command or "").strip().rstrip(".,!?;: ")


def heard_line(command: str) -> str:
    """"It's loud in here. I heard: what time is it." — said before the
    answer to a read-only command at the start of a capped capture."""
    return f"{LOUD_OPENER} I heard: {_said(command)}."


def _second_person(command: str) -> str:
    return _PRONOUN_RE.sub(lambda m: _PRONOUNS[m.group(1).lower()], command)


def ask_line(command: str) -> str:
    """"It's loud in here. Should I play some jazz in the kitchen?" — or,
    when the command doesn't start with a verb, "It's loud in here. I
    heard: <command>. Should I do that?"."""
    said = _said(command)
    first = said.split(" ", 1)[0].lower() if said else ""
    if not said or first in _NOT_A_VERB or is_question(said):
        return f"{heard_line(said)} Should I do that?"
    return f"{LOUD_OPENER} Should I {_second_person(said[0].lower() + said[1:])}?"


def with_heard_line(command: str, text: str) -> str:
    """The reply to a read-only loud command: what was heard, then the
    answer."""
    text = (text or "").strip()
    return f"{heard_line(command)} {text}" if text else heard_line(command)


def pending_payload(handler_name: str, command: str, *, now: float | None = None) -> dict[str, Any]:
    """The session's ``pending_confirmation`` for a parked loud command."""
    return {
        "handler": handler_name,
        "kind": LOUD_KIND,
        "command": command,
        "prompt": ask_line(command),
        "asked_at": time.time() if now is None else now,
    }


def is_loud_pending(pending: Any) -> bool:
    return isinstance(pending, dict) and pending.get("kind") == LOUD_KIND


def still_fresh(pending: dict[str, Any], *, now: float | None = None) -> bool:
    """A parked loud command a yes may still run."""
    at = pending.get("asked_at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    age = (time.time() if now is None else now) - float(at)
    return 0 <= age <= CONFIRM_TTL_SEC
