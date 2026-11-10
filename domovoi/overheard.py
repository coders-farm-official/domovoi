"""Long captures that are background talk, not a request (false wakes,
2026-11-09).

A TV or a phone that says the wake word ("Jarvis" is a meme format) wakes a
room; the media then never pauses for the 1.2 s that ends a capture, so the
satellite records until its cap (``[listen] max_record_seconds``, 30 s) and
the core used to answer whatever it heard, offer "Want me to check that
online?", and reopen the mic with no wake word, which the TV answered again.

This module decides, for a long capture, what the turn is. It is pure (the
streaming layer reads the session's parked question and passes it in), so
the rule is tested on transcripts alone.

**Long.** A capture is long when the satellite ended it at its cap
(``exit_reason`` "max_record_seconds"), or it holds at least
:data:`LONG_CAPTURE_SEC` (20 s) of audio, or at least
:data:`LONG_CAPTURE_WORDS` (60) words. People asking an assistant something,
even something long, pause within 20 s: a long question is 25-40 words,
about 10-15 s at an ordinary 2.5-3 words a second, and the satellite ends
the capture on the first 1.2 s pause after it. Twenty seconds of speech with
no pause, or sixty words (20 s at 3 words a second), is a TV, a podcast or
people talking to each other. Below either line nothing here changes how a turn is handled.

**The rule, for a long wake-word or follow-up capture** (push-to-talk and
barge-in turns are never ignored):

1. Drop a leading address ("Domovoi, ...", "hey Domovoi ...") and a leading
   stock wake phrase ("hey Jarvis", which the media says itself).
2. The *head* is the first sentence of what is left. If it is at most
   :data:`HEAD_MAX_WORDS` (15) words and is a command the router's fast
   tiers take (``router.plan_route``: "play DTMF by Bad Bunny", "set a
   timer for ten minutes", a yes/no to a parked question, a reply to a
   parked choice on a follow-up), the turn is that command and nothing
   else: the head is routed, the chatter after it is dropped. A command is
   said first, right after the wake word; the TV that kept the capture open
   is what follows. When the capture was NOT cut off at its cap (the
   person stopped talking) and the whole transcript is one command (a long
   reminder said in one breath), the whole transcript is routed instead.
3. Else, if the capture is addressed to the assistant (it starts with its
   name) or the head is a clear question to it ("can you ...", "do you
   know ...", "tell me ...", "how do I ...", or a question about the world
   such as "What is the capital of France?"), it is answered as a question
   by the Q&A model only: no tool model, so nothing overheard can start an
   action. A capture cut off at its cap is answered from its first
   sentences up to :data:`ANSWER_MAX_WORDS` (40) words. A capture that
   ended on a pause (a person stopped talking: not cut off at the cap)
   whose LAST sentence is such a question ("..., can you explain it
   simply?") is answered whole too.
4. Else it is ignored: no answer, no action, no follow-up, a history row
   that says "ignored: sounded like background talk".

"Jarvis" at the start is NOT an address: the capture opens after the wake
word, so a transcript that starts with it again is the media repeating the
word, the exact case being fixed. A person's command after it still counts
(step 2 strips it).

Every long turn, whatever it becomes, opens no follow-up mic and asks no
follow-up question (streaming and router read :attr:`Verdict.long`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

#: A capture at least this long is long (see the module docstring).
LONG_CAPTURE_SEC = 20.0
#: A transcript with at least this many words is long (20 s at 3 words a
#: second: the same line as LONG_CAPTURE_SEC, for fast talk).
LONG_CAPTURE_WORDS = 60
#: The satellite's own reason for a capture it cut off at its cap.
CAPPED_EXIT_REASON = "max_record_seconds"
#: An older satellite sends no exit reason: a capture this long reached the
#: default 30 s cap (the frames are 30 ms; a little under, for the rounding).
CAPPED_SEC_WITHOUT_REASON = 29.5
#: The most words a command at the start of a long capture may have.
HEAD_MAX_WORDS = 15
#: What of a capped, addressed capture is answered.
ANSWER_MAX_WORDS = 40
#: The turns a long capture may be ignored on. Push-to-talk is deliberate;
#: a barge-in has its own echo guard and ends a reply already playing.
GUARDED_TRIGGERS = frozenset({"wake_word", "followup"})

ROUTE = "route"      # a normal turn (or a long one that may not be ignored)
COMMAND = "command"  # route `text`, the command at the start
ANSWER = "answer"    # answer `text` with the Q&A model only
IGNORE = "ignore"    # no answer at all

_WORD_RE = re.compile(r"[\w']+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_NAMES = ("domovoi", "domovoy", "domovoj", "domavoi", "domo")
_GREETING = r"(?:(?:hey|hi|hello|ok|okay|yo)[,\s]+)?"
# The stock openWakeWord phrases a satellite may listen for (satellite
# config.toml.example), which media says itself. Not "Alexa": a capture
# that starts with it is somebody talking to another assistant in the room.
_WAKE_PHRASE_RE = re.compile(
    rf"^{_GREETING}(?:jarvis|mycroft|rhasspy)\b[,.!?:;]*\s*", re.IGNORECASE,
)
_ASSISTANT_QUESTION_RE = re.compile(
    r"^(?:hey[,\s]+)?(?:"
    r"(?:can|could|would|will) you\b"
    r"|do you (?:know|have|remember)\b"
    # Not the idiom "tell me about it".
    r"|(?:please[,\s]+)?(?:tell|show|give|help) me (?:(?:a|an|the|what|how|why|who|when|where"
    r"|whether|if|more|some)\b|about (?!it\b|that\b|this\b))"
    r"|how (?:do|can|should|would) i\b"
    r"|what (?:should|can|do) i\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Verdict:
    """What a turn is. ``text``: what to route (ROUTE / COMMAND / ANSWER)
    or what was heard (IGNORE). ``long``: the capture was long — no
    follow-up mic, no follow-up question, and (COMMAND / ANSWER) no tool
    model. ``why``: for the log."""

    action: str
    text: str
    long: bool = False
    why: str = ""


def words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def wake_score(value: Any) -> float | None:
    """A satellite's ``wake_score`` (utterance_start), 0..1, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not math.isfinite(v) or v < 0 or v > 1:
        return None
    return round(v, 3)


def is_capped(exit_reason: str | None, audio_seconds: float) -> bool:
    """Whether the satellite cut the capture off at its cap: speech never
    paused long enough to end it."""
    if exit_reason is not None:
        return exit_reason == CAPPED_EXIT_REASON
    return audio_seconds >= CAPPED_SEC_WITHOUT_REASON


def is_long(transcript: str, *, exit_reason: str | None, audio_seconds: float) -> bool:
    return (
        is_capped(exit_reason, audio_seconds)
        or audio_seconds >= LONG_CAPTURE_SEC
        or words(transcript) >= LONG_CAPTURE_WORDS
    )


def _address_re(bot_name: str | None) -> re.Pattern[str]:
    names = set(_NAMES)
    if bot_name and bot_name.strip():
        names.add(re.escape(bot_name.strip().lower()))
    alt = "|".join(sorted(names, key=len, reverse=True))
    return re.compile(rf"^{_GREETING}(?:{alt})\b[,.!?:;]*\s*", re.IGNORECASE)


def strip_address(transcript: str, bot_name: str | None = None) -> tuple[bool, str]:
    """``(addressed, rest)``: whether the transcript starts with the
    assistant's name, and the transcript without it (or a stock wake
    phrase, which does not count as an address) at its start."""
    text = (transcript or "").strip()
    m = _address_re(bot_name).match(text)
    if m and text[m.end():].strip():
        return True, text[m.end():].strip()
    m = _WAKE_PHRASE_RE.match(text)
    if m and text[m.end():].strip():
        text = text[m.end():].strip()
        # "Jarvis, Domovoi, play X": the name after the phrase still counts.
        m = _address_re(bot_name).match(text)
        if m and text[m.end():].strip():
            return True, text[m.end():].strip()
    return False, text


def first_sentence(text: str) -> str:
    for part in _SENTENCE_END_RE.split((text or "").strip()):
        if part.strip():
            return part.strip()
    return ""


def last_sentence(text: str) -> str:
    """The last sentence; one ending in a comma clause ("..., can you
    explain it?") is that clause."""
    parts = [p.strip() for p in _SENTENCE_END_RE.split((text or "").strip()) if p.strip()]
    if not parts:
        return ""
    last = parts[-1]
    return last.rsplit(",", 1)[-1].strip() if "," in last else last


def first_sentences(text: str, max_words: int) -> str:
    """Whole sentences from the start up to ``max_words`` words; the first
    sentence cut to ``max_words`` when it alone is longer."""
    out: list[str] = []
    n = 0
    for part in _SENTENCE_END_RE.split((text or "").strip()):
        w = words(part)
        if out and n + w > max_words:
            break
        out.append(part.strip())
        n += w
        if n >= max_words:
            break
    joined = " ".join(p for p in out if p)
    if words(joined) > max_words:
        joined = " ".join(joined.split()[:max_words])
    return joined


def is_clear_question(head: str) -> bool:
    """A question put to the assistant: "can you ...", "tell me ...", "how
    do I ...", or a question about the world (router's own test)."""
    if _ASSISTANT_QUESTION_RE.match((head or "").strip()):
        return True
    from domovoi.router import is_question_about_the_world

    return is_question_about_the_world(head)


def _command_handler(text: str, *, pending: Any, trigger: str | None) -> str | None:
    """The handler the router's fast tiers would hand ``text`` to (its
    name), or None. A reply on a follow-up to a parked free-text choice
    ("the second one") counts too."""
    from domovoi.router import dispatchable_choice, plan_route

    plan = plan_route(text, pending=pending)
    if plan is not None:
        return plan.handler.name
    if trigger == "followup" and pending is not None:
        choice = dispatchable_choice(pending)
        if choice is not None:
            return choice[0].name
    return None


def screen(
    transcript: str,
    *,
    trigger: str | None,
    exit_reason: str | None,
    audio_seconds: float,
    pending: Any = None,
    bot_name: str | None = None,
) -> Verdict:
    """What the turn is (see the module docstring). ``pending``: the
    session's parked ``pending_confirmation`` payload, or None."""
    if not is_long(transcript, exit_reason=exit_reason, audio_seconds=audio_seconds):
        return Verdict(ROUTE, transcript)
    if trigger not in GUARDED_TRIGGERS:
        return Verdict(ROUTE, transcript, long=True, why=f"long {trigger} capture")
    capped = is_capped(exit_reason, audio_seconds)
    addressed, rest = strip_address(transcript, bot_name)
    head = first_sentence(rest)
    if head and words(head) <= HEAD_MAX_WORDS:
        handler = _command_handler(head, pending=pending, trigger=trigger)
        if handler is not None:
            text = head
            if not capped and rest != head and _command_handler(
                rest, pending=pending, trigger=trigger,
            ) == handler:
                text = rest
            return Verdict(COMMAND, text, long=True, why=f"a command for {handler} at the start")
    if not capped and rest:
        # One long command said in one breath ("remind me to ... and ...
        # and ..."): the person stopped talking, so it is all theirs.
        handler = _command_handler(rest, pending=pending, trigger=trigger)
        if handler is not None:
            return Verdict(COMMAND, rest, long=True, why=f"a long command for {handler}")
    if addressed or (head and is_clear_question(head)):
        text = first_sentences(rest, ANSWER_MAX_WORDS) if capped else rest
        return Verdict(
            ANSWER, text, long=True,
            why="addressed to the assistant" if addressed else "a question to the assistant",
        )
    if not capped and _ASSISTANT_QUESTION_RE.match(last_sentence(rest)):
        return Verdict(ANSWER, rest, long=True, why="a question to the assistant at the end")
    return Verdict(
        IGNORE, transcript, long=True,
        why=("cut off at the recording cap" if capped else "long") + ", no command or question at the start",
    )
