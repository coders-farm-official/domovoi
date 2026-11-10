"""Long captures that are background talk, not a request (false wakes,
2026-11-09).

A TV or a phone that says the wake word ("Jarvis" is a meme format) wakes a
room; the media then never pauses for the 1.2 s that ends a capture, so the
satellite records until its cap (``[listen] max_record_seconds``, 30 s) and
the core used to answer whatever it heard, offer "Want me to check that
online?", and reopen the mic with no wake word, which the TV answered again.

This module decides what such a turn is. It is pure (the streaming layer
reads the session's parked question and passes it in), so the rule is
tested on transcripts alone.

**Only wake-word and follow-up captures** are screened. Push-to-talk and
barge-in turns are routed as they always were (:data:`GUARDED_TRIGGERS`).

**Capped** — the satellite cut the capture off at its recording cap
(``exit_reason`` "max_record_seconds") AND it holds at least
:data:`LONG_CAPTURE_SEC` (20 s) of audio. Speech that never paused for 1.2 s
in 20 s is a TV, a podcast or people talking to each other, not someone
talking to the assistant. The 20 s floor keeps a household that lowered
"Max command length" to 10 s from having its ordinary 10 s requests
screened. An older satellite sends no exit reason: a capture of
:data:`CAPPED_SEC_WITHOUT_REASON` or more reached its default 30 s cap.
(``audio_seconds`` counts the lead-in and the trailing silence too.)

**Long** — capped, or at least 20 s of audio, or at least
:data:`LONG_CAPTURE_WORDS` (60) words (20 s at 3 words a second). A long
question is 25-40 words, 10-15 s.

What a turn becomes:

* **Not long**: routed as always.
* **Long, ended on a pause** (not capped): a person stopped talking, so it is
  theirs. Routed as always, tool model included; being long only means no
  follow-up mic and no follow-up question (:attr:`Verdict.long`). Never
  ignored.
* **Capped**: the tool model is never asked (:attr:`Verdict.capped`), and:

  1. A leading address ("Domovoi, ...", "hey Domo, ...") and a leading
     stock wake phrase ("hey Jarvis", which the media says itself) are set
     aside.
  2. A **command at the start** — the first sentence (at most
     :data:`HEAD_MAX_WORDS`, 15, words), else its first clause cut at the
     first comma or "and" (2 to 15 words), when the router's fast tiers
     take it (``router.plan_route``: "play some jazz in
     the kitchen", a yes/no to a parked question, a reply to a parked
     choice on a follow-up). That command is routed alone; the chatter after
     it is dropped (it stays in the log). A command is said first, right
     after the wake word; the TV that kept the capture open is what follows.
     Whisper's punctuation is not needed: "play some jazz in the kitchen
     and in other news tonight..." plays.
  3. Else, **a question**: addressed by name, or the FIRST sentence a
     clear question to the assistant ("can you ...", "tell me how ...",
     "how do I ...", or a question about the world such as "Which river is
     the longest?"). A question later in the capture doesn't count: in TV
     dialogue "Can you believe it?" and "Will you marry me?" are the TV
     talking. Answered without the tool model (nothing overheard can act
     through it; a fast path the question itself is, "what time is it?",
     still answers): the first sentences up to :data:`ANSWER_MAX_WORDS`
     (40) words.
  4. Else **ignored**: no answer, no action, no follow-up, a history row
     that says "ignored: sounded like background talk".

On a capped capture, the fast paths in :data:`NOT_OVER_THE_TV` (voice
enrolment's "I'm ...", memory's "remember ...") never count, here or in
the router: those are words a TV says all the time.

"Jarvis" at the start is NOT an address: the capture opens after the wake
word, so a transcript that starts with it again is the media repeating the
word. A person's command after it still counts (step 1 sets it aside).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

#: A capture at least this long is long, and a capped one must be this long
#: to be screened at all (see the module docstring).
LONG_CAPTURE_SEC = 20.0
#: A transcript with at least this many words is long (20 s at 3 words a
#: second: the same line as LONG_CAPTURE_SEC, for fast talk).
LONG_CAPTURE_WORDS = 60
#: The satellite's own reason for a capture it cut off at its cap.
CAPPED_EXIT_REASON = "max_record_seconds"
#: An older satellite sends no exit reason: a capture this long reached the
#: default 30 s cap (the frames are 30 ms; a little under, for the rounding).
CAPPED_SEC_WITHOUT_REASON = 29.5
#: The most words a command at the start of a capped capture may have.
HEAD_MAX_WORDS = 15
#: What of a capped capture that opens with a question is answered.
ANSWER_MAX_WORDS = 40
#: The turns that are screened. Push-to-talk is deliberate; a barge-in has
#: its own echo guard and ends a reply already playing.
GUARDED_TRIGGERS = frozenset({"wake_word", "followup"})

ROUTE = "route"      # route `text` as always (long: no follow-ups)
COMMAND = "command"  # route `text`, the command at the start of a capped capture
ANSWER = "answer"    # answer `text` with the Q&A model only
IGNORE = "ignore"    # no answer at all

_WORD_RE = re.compile(r"[\w']+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_RE = re.compile(r",|\s+and\s+", re.IGNORECASE)
_GREETING = r"(?:(?:hey|hi|hello|ok|okay|yo)[,\s]+)"
# The assistant's names as Whisper writes them. Bare "Domo" is not one (TV
# says "domo arigato"); "hey Domo" is.
_NAMES = ("domovoi", "domovoy", "domovoj", "domavoi")
# The stock openWakeWord phrases a satellite may listen for (satellite
# config.toml.example), which media says itself. Not "Alexa": a capture
# that starts with it is somebody talking to another assistant in the room.
_WAKE_PHRASE_RE = re.compile(
    rf"^{_GREETING}?(?:jarvis|mycroft|rhasspy)\b[,.!?:;]*\s*", re.IGNORECASE,
)
_ASSISTANT_QUESTION_RE = re.compile(
    r"^(?:hey[,\s]+)?(?:"
    r"(?:can|could|would|will) you\b"
    r"|do you (?:know|have|remember|think)\b"
    r"|what do you think\b"
    # Not the idiom "tell me about it".
    r"|(?:please[,\s]+)?(?:tell|show|give|help) me (?:(?:a|an|the|what|how|why|who|when|where"
    r"|whether|if|more|some)\b|about (?!it\b|that\b|this\b))"
    r"|how (?:(?:long|many|much|often|far)\s+)?(?:do|can|should|would|will) (?:i|we|you)\b"
    r"|what (?:should|can|do) i\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Verdict:
    """What a turn is. ``text``: what to route (ROUTE / COMMAND / ANSWER)
    or what was heard (IGNORE). ``long``: no follow-up mic, no follow-up
    question. ``capped``: cut off at the cap — no tool model either.
    ``why``: for the log."""

    action: str
    text: str
    long: bool = False
    capped: bool = False
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
    """Whether the satellite cut the capture off at its cap after at least
    LONG_CAPTURE_SEC: speech never paused long enough to end it."""
    if exit_reason is not None:
        return exit_reason == CAPPED_EXIT_REASON and audio_seconds >= LONG_CAPTURE_SEC
    return audio_seconds >= CAPPED_SEC_WITHOUT_REASON


def is_long(transcript: str, *, exit_reason: str | None, audio_seconds: float) -> bool:
    return (
        is_capped(exit_reason, audio_seconds)
        or audio_seconds >= LONG_CAPTURE_SEC
        or words(transcript) >= LONG_CAPTURE_WORDS
    )


def _address_re(bot_name: str | None) -> re.Pattern[str]:
    names = set(_NAMES)
    if bot_name and bot_name.strip() and bot_name.strip().lower() != "domo":
        names.add(re.escape(bot_name.strip().lower()))
    alt = "|".join(sorted(names, key=len, reverse=True))
    return re.compile(
        rf"^(?:{_GREETING}?(?:{alt})|{_GREETING}domo)\b[,.!?:;]*\s*", re.IGNORECASE,
    )


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


def sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_END_RE.split((text or "").strip()) if p.strip()]


def first_sentence(text: str) -> str:
    parts = sentences(text)
    return parts[0] if parts else ""


def first_sentences(text: str, max_words: int) -> str:
    """Whole sentences from the start up to ``max_words`` words; the first
    sentence cut to ``max_words`` when it alone is longer."""
    out: list[str] = []
    n = 0
    for part in sentences(text):
        w = words(part)
        if out and n + w > max_words:
            break
        out.append(part)
        n += w
        if n >= max_words:
            break
    joined = " ".join(out)
    if words(joined) > max_words:
        joined = " ".join(joined.split()[:max_words])
    return joined


def _tail_clause(sentence: str) -> str:
    """A sentence's last comma clause ("..., can you explain it?")."""
    return sentence.rsplit(",", 1)[-1].strip() if "," in sentence else sentence


def is_clear_question(text: str) -> bool:
    """A question put to the assistant: "can you ...", "tell me how ...",
    "how do I ...", or a question about the world (router's own test)."""
    text = (text or "").strip()
    if _ASSISTANT_QUESTION_RE.match(text) or _ASSISTANT_QUESTION_RE.match(_tail_clause(text)):
        return True
    from domovoi.router import is_question_about_the_world

    return is_question_about_the_world(text)


#: Fast paths whose words a TV says all the time, so a capped capture never
#: counts them as a command at its start: voice_profile takes "I'm ...",
#: "this is ...", "call me ..." (it would enroll the TV as a person), memory
#: takes "remember ..." (it would save what the TV said). Asked for over the
#: TV, these need the person to try again; nothing is lost but a retry.
NOT_OVER_THE_TV = frozenset({"voice_profile", "memory"})


def _command_handler(text: str, *, pending: Any, trigger: str | None) -> str | None:
    """The handler the router's fast tiers would hand ``text`` to (its
    name), or None. A reply on a follow-up to a parked free-text choice
    ("the second one") counts too. Never one of NOT_OVER_THE_TV."""
    from domovoi.router import dispatchable_choice, plan_route

    plan = plan_route(text, pending=pending)
    if plan is not None:
        if plan.path == "fast" and plan.handler.name in NOT_OVER_THE_TV:
            return None
        return plan.handler.name
    if trigger == "followup" and pending is not None:
        choice = dispatchable_choice(pending)
        if choice is not None:
            return choice[0].name
    return None


def _head_candidates(rest: str) -> list[str]:
    """What a command at the start of ``rest`` may be, in the order tried:
    the first sentence (at most HEAD_MAX_WORDS words), then its first
    clause, cut at the first comma or "and" (2 to HEAD_MAX_WORDS words).
    No shorter word prefixes: in TV dialogue they find "go back", "turn it
    down", "shuffle", "stop"."""
    head = first_sentence(rest)
    out: list[str] = []
    if head and words(head) <= HEAD_MAX_WORDS:
        out.append(head)
    clause = _CLAUSE_RE.split(head, maxsplit=1)[0].strip() if head else ""
    if clause and clause != head and 2 <= words(clause) <= HEAD_MAX_WORDS:
        out.append(clause)
    return out


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
    if trigger not in GUARDED_TRIGGERS:
        return Verdict(ROUTE, transcript)
    if not is_long(transcript, exit_reason=exit_reason, audio_seconds=audio_seconds):
        return Verdict(ROUTE, transcript)
    if not is_capped(exit_reason, audio_seconds):
        # Ended on a pause: a person. Routed as always, no follow-ups.
        return Verdict(ROUTE, transcript, long=True, why="long, ended on a pause")
    addressed, rest = strip_address(transcript, bot_name)
    for cand in _head_candidates(rest):
        handler = _command_handler(cand, pending=pending, trigger=trigger)
        if handler is not None:
            return Verdict(COMMAND, cand, long=True, capped=True,
                           why=f"a command for {handler} at the start")
    parts = sentences(rest)
    if addressed or (parts and is_clear_question(parts[0])):
        return Verdict(ANSWER, first_sentences(rest, ANSWER_MAX_WORDS), long=True, capped=True,
                       why="addressed to the assistant" if addressed
                       else "a question to the assistant")
    # Only the FIRST sentence: later in TV dialogue, "Can you believe it?",
    # "Will you marry me?" are the TV talking.
    return Verdict(IGNORE, transcript, long=True, capped=True,
                   why="cut off at the recording cap, no command or question in it")
