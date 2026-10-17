"""ReminderHandler — set reminders that *speak* a message when they fire.

A reminder is just a row in `timers` with `message IS NOT NULL` (V001
schema is already shaped for this). The TimerWatcher fires reminders
the same way it fires plain timers, through the originating room's
`StreamSession.announce`, but when `message` is populated it speaks
"Reminder: <message>" instead of the plain "Your timer is done." line.

Three usage shapes:

1. Create. "remind me to <task> in <duration>" is the most common; also
   "remind me in <duration> (to <task>)", "set a reminder for <duration>
   (to <task>)", "reminder in <duration> <task>", "set a reminder to
   <task> in <duration>", "set a <duration> reminder (to <task>)", and a
   task named in front of the word: "laundry reminder in 5 minutes",
   "set an oven reminder for 10 minutes", "a 5 minute pizza reminder".
2. "what (are my) reminders" / "list reminders" — list pending reminders
   in the current room.
3. "cancel (my|the) reminder (about|for|to) <thing>", "cancel the laundry
   reminder", "cancel the 10 minute reminder" — cancel by label match, or
   all reminders in the room when no label given.

A reminder with NO task ("set a reminder for 10 minutes") is still a
reminder: it is stored with an EMPTY message (``message = ''``, so
``message IS NOT NULL`` still says reminder everywhere it is read), the
label "10 minute reminder", and fires as "Here's your 10 minute
reminder." — the same shape as an unlabelled timer ("Your 10 minute timer
is done."). It is never stored with the command words as its message:
before this, "set a reminder for 10 minutes" had no fast path, the tool
model copied the whole transcript into ``message``, and a reminder fired
as "Reminder: Better reminder for 10 minutes" (a live garage row,
2026-09-30, Whisper having heard "set a" as "better").

What the create grammar keeps and drops:

* The words in front of "reminder" are a verb only when they are one of a
  closed list of verbs, heard or misheard ("set", "make", "give me",
  "i want" — and "better", "said", "sit" for a mumbled "set a"). Any other
  word there is the task: "laundry reminder in 5 minutes" is a reminder
  about laundry, not a reminder with the task thrown away.
* A statement about a reminder is not a new one. "my / the / that / your
  reminder ..." creates only after a verb ("set my reminder for 10
  minutes"), a task never starts with an auxiliary or a negation ("... for
  10 minutes didn't go off", "... was useless"), and the tool model is
  offered neither this handler's tool nor the timer's for those shapes
  (``offers_tool``, ``shared/tool_gate.TIMER_STATEMENT_RE``), so "you have
  a reminder in 10 minutes" cannot become a reminder or a timer through it.
* Politeness is never part of the task, wherever it was said ("remind me
  in 10 minutes, please, to call mom"), and a task that is only a
  connector ("set a reminder for 10 minutes to..." cut off at a pause) is
  no task.

Clock times (2026-10-17). "remind me at 6 pm to take the bins out",
"remind me to call mom tomorrow at 7", "remind me tomorrow morning",
"wake me up at 7" and "set an alarm for 6:30" are fast paths. The time is
read by ``handlers/shared/clock_time`` and placed on the household's wall
clock (``domovoi/household_time``: the server's own zone, the one "what
time is it" answers from): the next time it comes, so "at 6 pm" said at
23:20 is tomorrow's, and a time with no AM/PM is whichever comes first
("wake me up at 5" at 15:00 is 5 PM, at 23:00 5 AM). The reply says the clock
time ("I'll remind you tomorrow at 6 PM to take the bins out."). Before
this they reached the tool model, which cannot see the clock and guessed
a ``duration_sec`` (qwen3:8b: "remind me at 6 pm" at 23:20 → "in 10
hours"; "wake me up at 7" → "in 7 hours"; "remind me tomorrow morning" →
"in 24 hours"). A tool call whose ``message`` still holds the spoken time
is read the same way (``_absolute_from_tool_message``).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import household_time
from domovoi.db.repositories import TimerRepository, notify_timers_changed, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.clock_time import (
    ALARM_WHEN_PATTERN,
    TIME_PATTERN,
    WHEN_FOR_PATTERN,
    WHEN_PATTERN,
    WhenProblem,
    candidate_hours,
    parse_when,
    resolve as resolve_when,
    spoken_day,
    spoken_clock,
    spoken_time_and_day,
    spoken_when,
)
from domovoi.handlers.shared.number_words import (
    DURATION_PATTERN,
    NUMBER_WORDS,
    parse_duration_seconds,
)
from domovoi.handlers.shared.tool_gate import STATEMENT_VERBS, is_timer_statement
from domovoi.handlers.timer import _format_duration, duration_adjective
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


def _words(*groups: str) -> str:
    """An alternation of whole words / phrases, longest first so a phrase
    is never cut short by its own first word."""
    items = sorted({w for g in groups for w in g.split("|") if w}, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(w) for w in items) + ")"


# A whole token: not followed by more of a word ("that" but not "that's").
_END = r"(?![a-z'-])"

# ─── The words around "reminder" ─────────────────────────────────────────

# Politeness, wherever it lands between the parts: "set a reminder please
# for 10 minutes", "remind me in 10 minutes, please, to call mom".
_POLITE_WORD = r"(?:please|thanks|thank you)"
_POLITE = rf"(?:,? {_POLITE_WORD}{_END},?)?"

# The verbs that can front "reminder", heard or misheard. A CLOSED list:
# a word that is not on it is the task ("laundry reminder"), not a verb to
# drop. "better", "said", "sit", "sat", "sent", "bet" are what Whisper
# makes of a mumbled "set a" (the live row was "Better reminder for 10
# minutes").
_SET_VERBS = (
    "set|set up|setup|sets|setting|sit|sat|said|say|sent|bet|better|"
    "get|give|make|add|create|schedule|start|put|"
    "i need|i want|i'd like|i would like|we need|we want|"
    "let's|let's set|let's have|let's make|lets|lets set|"
    "can i get|can i have|could i get|could i have|may i have"
)
_SET_VERB = _words(_SET_VERBS)
# Articles that can front "reminder" on their own ("a reminder for 10
# minutes", "another reminder in 5 minutes").
_BARE_DETS = "a|an|another|one more|one"
# ... and the ones that need a verb in front: "set my reminder for 10
# minutes" is a command, "my reminder for 10 minutes didn't go off" is not.
_VERB_DETS = _BARE_DETS + "|the|my|our|that|this|me a|us a|me an|us an"
_LEAD = (
    rf"(?:(?:just|now|go ahead and) )?"
    rf"(?:{_SET_VERB} (?:(?:me|us) )?(?:{_words(_VERB_DETS)} )?"
    rf"|(?:{_words(_BARE_DETS)} )?)"
)
# Adjectives that say nothing about what to remind: "a quick reminder".
_MODIFIERS = "quick|little|short|new|simple|brief|small|friendly|gentle|single"
_MODIFIER = rf"(?:{_words(_MODIFIERS)} )?"

# Words that are never (the start of) a task named in front of "reminder":
# the verbs and articles above, pronouns, auxiliaries and negations (the
# statement shapes), cancelling / asking / changing, prepositions, numbers
# and units (the duration paths), and the reminder words themselves.
_NOT_A_TOPIC_WORDS = (
    _SET_VERBS + "|" + _VERB_DETS + "|" + _MODIFIERS + "|"
    "your|his|her|their|its|these|those|you|i|we|he|she|they|it|me|us|them|"
    "there|here|i'm|i've|i'd|you've|you're|we've|it's|that's|there's|what's|"
    "whats|where's|who's|"
    "am|is|isn't|isnt|are|aren't|was|wasn't|wasnt|were|weren't|be|been|being|"
    "do|does|doesn't|doesnt|did|didn't|didnt|don't|dont|has|hasn't|have|"
    "haven't|had|hadn't|will|won't|wont|would|wouldn't|shall|should|"
    "shouldn't|can|can't|cannot|could|couldn't|may|might|must|never|not|no|"
    "any|some|all|every|each|both|next|last|first|other|"
    "cancel|cancelled|stop|delete|remove|clear|end|kill|dismiss|silence|mute|"
    "skip|pause|snooze|forget|undo|change|move|edit|update|reset|extend|push|"
    "postpone|delay|turn|switch|list|show|read|tell|check|find|what|which|"
    "when|where|who|why|how|without|"
    # an existing reminder, or doing it again: "next reminder in 10
    # minutes", "repeat reminder in 10 minutes"
    "repeat|again|resend|reschedule|restart|renew|redo|previous|earlier|"
    "later|old|older|recent|upcoming|pending|current|existing|active|same|"
    "missed|original|"
    "for|in|to|about|at|on|of|and|or|but|so|with|from|by|if|then|than|as|"
    "after|before|until|like|"
    "reminder|reminders|remind|timer|timers|alarm|alarms|ping|nudge|"
    "please|thanks|thank|hey|okay|ok|yeah|yes|um|uh|er|"
    "today|tonight|tomorrow|half|second|seconds|minute|minutes|hour|hours|"
    "day|days|week|weeks|" + "|".join(NUMBER_WORDS)
)
_TOPIC_WORD = rf"(?!{_words(_NOT_A_TOPIC_WORDS)}{_END})[a-z][a-z'-]*"
# One to three words named in front of "reminder": "laundry", "call mom",
# "mom's birthday".
_TOPIC = rf"(?P<topic>{_TOPIC_WORD}(?: {_TOPIC_WORD}){{0,2}})"

_FROM_NOW = r"(?: from now)?"
# "a reminder for me in 10 minutes": whom it is for, not what.
_FOR_ME = r"(?: for (?:me|us))?"
# Between "reminder" and its duration: "reminder for 10 minutes",
# "reminder in 10 minutes", "reminder, 10 minutes".
_BEFORE_DURATION = r",? (?:(?:for|in) )?"

# A task with no connector in front never starts like a statement ("...
# for 10 minutes didn't go off", "... that was useless"), nor with the
# "and" of a longer duration ("10 minutes and 30 seconds" is all duration).
_NOT_A_TASK_START = _words(
    STATEMENT_VERBS,
    "that|that's|it|it's|which|and|but|or|because|nevermind|ago|later|earlier|back",
)
# After the duration: an optional "to" / "about" / "for" / "that", then the
# task. "reminder in 5 minutes take the laundry out" has no connector, and
# Whisper may end a sentence before it ("remind me in 10 minutes. call mom").
_TASK_AFTER = (
    rf"(?:[,.…]* (?:(?P<connector>to|about|for|that) )?"
    rf"(?!{_NOT_A_TASK_START}{_END})(?P<message>.+))?"
)

# ─── The create grammars ─────────────────────────────────────────────────

# "remind me to call mom in 10 minutes"
# "remind me to take the trash out in an hour"
# "remind me to check the oven in an hour and a half"
# The duration grammar is the shared one in handlers/shared/number_words
# (digits, spelled numbers, articles, halves, two joined clauses) — the
# same closed vocabulary the timer handler embeds, so "in ten minutes" no
# longer falls through to the LLM tool router. Lazy `.+?` for the message
# keeps the duration capture anchored to the END of the utterance, so
# "remind me to check in on grandma in ten minutes" still yields the
# message "check in on grandma".
_CREATE_RE = re.compile(
    rf"^remind me{_POLITE} to (?P<message>.+?) in (?P<duration>{DURATION_PATTERN})$"
)

# "remind me about the laundry in 5 minutes" / "remind me that the oven is
# on in 10 minutes"
_CREATE_ABOUT_RE = re.compile(
    rf"^remind me{_POLITE} (?P<connector>about|that) (?P<message>.+?)"
    rf" in (?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_POLITE}$"
)

# "remind me in 10 minutes" / "remind me in an hour to call mom" /
# "ping me in ten minutes"
_CREATE_IN_RE = re.compile(
    rf"^(?:remind|ping|nudge) me{_POLITE} in (?P<duration>{DURATION_PATTERN})"
    rf"{_FROM_NOW}{_POLITE}{_TASK_AFTER}$"
)

# "set a reminder for 10 minutes" / "better reminder for 10 minutes" /
# "set a reminder for 10 minutes to call mom" / "reminder in 5 minutes take
# the laundry out" / "a reminder for ten minutes"
_CREATE_NOUN_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}reminder{_FOR_ME}{_POLITE}{_BEFORE_DURATION}"
    rf"(?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_POLITE}{_TASK_AFTER}$"
)

# "laundry reminder in 5 minutes" / "set an oven reminder for 10 minutes" /
# "pizza reminder, 10 minutes" — the task named in front of "reminder".
# Nothing may follow the duration but politeness: "laundry reminder in 5
# minutes to move it to the dryer" is one task said in two halves, which
# the tool model joins better than a regex would, so it goes there.
_CREATE_TOPIC_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}{_TOPIC} reminder{_FOR_ME}{_POLITE}{_BEFORE_DURATION}"
    rf"(?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_POLITE}$"
)

# "set a reminder to call mom in 10 minutes" / "a reminder about the
# pasta in half an hour" — the task first, the duration last.
_CREATE_NOUN_TASK_FIRST_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}reminder{_FOR_ME}{_POLITE} (?P<connector>to|about|for|that) "
    rf"(?P<message>.+?) in (?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_POLITE}$"
)

# "set a 10 minute reminder" / "a five minute reminder to check the oven"
_CREATE_DURATION_FIRST_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}(?P<duration>{DURATION_PATTERN}) reminder{_FOR_ME}"
    rf"{_POLITE}{_TASK_AFTER}$"
)

# "set a 10 minute laundry reminder" / "a five minute pizza reminder"
_CREATE_DURATION_TOPIC_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}(?P<duration>{DURATION_PATTERN}) {_TOPIC} reminder"
    rf"{_FOR_ME}{_POLITE}$"
)

# Every create grammar, in the order the fast paths try them.
_CREATE_PATTERNS = (
    _CREATE_RE, _CREATE_ABOUT_RE, _CREATE_IN_RE, _CREATE_NOUN_RE,
    _CREATE_TOPIC_RE, _CREATE_NOUN_TASK_FIRST_RE, _CREATE_DURATION_FIRST_RE,
    _CREATE_DURATION_TOPIC_RE,
)

# ─── The clock-time grammars ─────────────────────────────────────────────
#
# Tried after every duration grammar, so "in 10 minutes" is never read as
# a time. The time words are clock_time's closed vocabulary; what follows
# them is the task, as in the duration grammars.

_WHEN = rf"(?P<when>{WHEN_PATTERN})"

# "remind me at 6 pm to take the bins out" / "remind me tomorrow morning to
# call the plumber" / "remind me on friday about the dentist" / "remind me
# at 6 pm" (no task)
_CREATE_AT_RE = re.compile(rf"^remind me{_POLITE},? {_WHEN}{_POLITE}{_TASK_AFTER}$")

# "remind me to take the bins out at 6 pm" / "remind me to call mom
# tomorrow" / "remind me about the dentist on friday at 9". Not "that":
# "remind me that the party is at 7" holds its time in the task.
_CREATE_TASK_AT_RE = re.compile(
    rf"^remind me{_POLITE} (?P<connector>to|about) (?P<message>.+?),? {_WHEN}{_POLITE}$"
)

# "set a reminder for 6 pm" / "a reminder at noon to eat" / "set a reminder
# for tomorrow at 9 to call the bank". After "for", only a time that can't
# be a count ("for 7" might be minutes; "for 7 pm" can't).
_CREATE_NOUN_AT_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}reminder{_FOR_ME}{_POLITE},? (?:for |on )?"
    rf"(?P<when>{WHEN_FOR_PATTERN}){_POLITE}{_TASK_AFTER}$"
)

# "set a laundry reminder for 6 pm" / "dentist reminder tomorrow at 9"
_CREATE_TOPIC_AT_RE = re.compile(
    rf"^{_LEAD}{_MODIFIER}{_TOPIC} reminder{_FOR_ME}{_POLITE},? (?:for |on )?"
    rf"(?P<when>{WHEN_FOR_PATTERN}){_POLITE}$"
)

# "wake me up at 7" (the next 7) / "wake me at 6:30 tomorrow" / "wake me up
# tomorrow at 7"
_WAKE_VERB = r"(?:wake (?:me|us)(?: up)?|get (?:me|us) up)"
_WAKE_AT_RE = re.compile(rf"^{_WAKE_VERB}{_POLITE},? {_WHEN}{_POLITE}$")
# "wake me up in 20 minutes"
_WAKE_IN_RE = re.compile(
    rf"^{_WAKE_VERB}{_POLITE} in (?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_POLITE}$"
)

# "set an alarm for 7" / "set my alarm for 6:30 tomorrow" / "alarm at 7 am".
# Any clock time after "alarm for", bare numbers too: nothing may follow it,
# so "set an alarm for 10 minutes" doesn't fit and goes where it went.
_CREATE_ALARM_RE = re.compile(
    rf"^(?:(?:just |now )?{_SET_VERB} (?:me |us )?)?(?:an |a |my |the |another |one )?"
    rf"alarm{_FOR_ME}{_POLITE},? (?:for |at |on )?(?P<when>{ALARM_WHEN_PATTERN}){_POLITE}$"
)

# "cancel my alarm" / "cancel the 7 am alarm" / "delete my alarms"
_CANCEL_ALARM_RE = re.compile(
    r"^(?:cancel|delete|remove|clear) (?:my |the |that |all (?:my |the )?)?"
    rf"(?:(?P<clock>{TIME_PATTERN}) )?alarms?$"
)

# What "wake me up" says when it goes off ("Reminder: time to wake up").
_WAKE_MESSAGE = "time to wake up"
# The labels alarms get (lower-cased, a Postgres regex): "7 am alarm",
# "6:30 am alarm", "noon alarm", "20 minute alarm".
_ALARM_LABEL_SQL_RE = (
    r"^(?:[0-9]{1,2}(?::[0-9]{2})? (?:am|pm)|noon|midnight"
    r"|[0-9]+ (?:second|minute|hour)(?: and [0-9]+ minute)?) alarm$"
)

# ─── The tool-call ``message`` ───────────────────────────────────────────

# A tool-call ``message`` that is only the command, not a task: "Reminder
# set for 10 minutes." (the tool model writing its own reply), "reminder",
# "10 minute reminder", "10 minutes". Checked after the create grammars.
_COMMAND_ECHO_RE = re.compile(
    rf"^(?:{_LEAD}{_MODIFIER}(?:(?:{DURATION_PATTERN}) )?"
    rf"(?:reminder|remind me|ping me|ping|nudge me|nudge)"
    rf"(?: (?:is |was |has been )?set)?{_FOR_ME}"
    rf"(?:{_BEFORE_DURATION}(?:{DURATION_PATTERN}))?{_FROM_NOW}"
    rf"|(?:{DURATION_PATTERN}){_FROM_NOW})$"
)
# "Medication reminder", "set the oven reminder", "Call mom reminder": the
# task named in front, as on the fast path.
_TOOL_TOPIC_RE = re.compile(rf"^{_LEAD}{_MODIFIER}{_TOPIC} reminder{_FOR_ME}$")
# "Reminder to call mom", "remind me to take my pills": the task after.
_TOOL_TASK_RE = re.compile(
    rf"^(?:{_LEAD}{_MODIFIER}reminder{_FOR_ME}|remind me)"
    rf" (?P<connector>to|about|for|that) (?P<message>.+)$"
)
# "I set a reminder for 10 minutes" / "I'll set a reminder ..." — a
# subject in front of a copied command.
_TOOL_SUBJECT_RE = re.compile(r"^(?:i|we|you)(?:'ve|'ll|'d| have| will| just)* (?=[a-z])")
# "Reminder: call mom" — the model's own heading on the task.
_TOOL_HEADING_RE = re.compile(r"^reminder\s*[:\-–—]\s*", re.IGNORECASE)

# ─── Cleaning a task ─────────────────────────────────────────────────────

_LEAD_POLITE_RE = re.compile(
    r"^(?:please|kindly|um|uh|er|erm|hmm)(?:\s*[,.…]+\s*|\s+)", re.IGNORECASE
)
_TAIL_POLITE_RE = re.compile(r"(?:^|,?\s+)(?:please|thanks|thank you)$", re.IGNORECASE)
_MID_POLITE_RE = re.compile(r"\s*,\s*(?:please|thanks|thank you)\s*,\s*", re.IGNORECASE)
_LEAD_CONNECTOR_RE = re.compile(
    r"^(to|about|that|for)(?:\s*[,.…]+\s*|\s+)", re.IGNORECASE
)
# A task that is only these is no task: "set a reminder for 10 minutes
# to..." cut off at a pause, or "remind me about it in 10 minutes", which
# would fire as "Reminder: it".
_DANGLING = frozenset({
    "to", "about", "that", "for", "and", "so", "then", "the", "a", "an",
    "um", "uh", "er", "erm", "hmm", "it", "this", "them", "something",
})
# The connector of a task named in front of "reminder": "laundry reminder
# in 5 minutes" → "Your laundry reminder is set for 5 minutes." ("I'll
# remind you about call mom" is what "about" would make of "call mom
# reminder").
_TOPIC_CONNECTOR = "topic"
# "... a reminder for me in 10 minutes" is whom it is for, not a task.
_WHOM = frozenset({"me", "us", "myself", "ourselves"})
_END_PUNCT = ".,;:!?… "

# A spoken unit's plural, for the cancel match (see _cancel).
_UNIT_PLURAL_RE = re.compile(r"\b(second|minute|hour)s\b")


def _clean_task(
    raw: str | None, connector: str | None = None
) -> tuple[str | None, str | None]:
    """The task as it should be stored and spoken (None for no task), and
    the connector that introduced it ("to" / "about" / "for" / "that")."""
    if raw is None:
        return None, connector
    task = raw.strip()
    while True:
        before = task
        task = task.strip(_END_PUNCT).lstrip(",;: ")
        task = _MID_POLITE_RE.sub(", ", task)
        task = _LEAD_POLITE_RE.sub("", task)
        task = _TAIL_POLITE_RE.sub("", task)
        if connector is None:
            # "please, to call mom" once the politeness is gone
            m = _LEAD_CONNECTOR_RE.match(task)
            if m and task[m.end():].strip(_END_PUNCT):
                connector = m.group(1).lower()
                task = task[m.end():]
        if task == before:
            break
    low = task.lower()
    if not task or low in _DANGLING or low in _WHOM:
        return None, connector
    return task, connector


def _task_of(m: re.Match[str], source: str | None = None) -> tuple[str | None, str | None]:
    """The task and its connector from a create-grammar match. A task
    named in front of "reminder" ("laundry reminder") has the connector
    ``"topic"``: it reads back as "Your laundry reminder ...". ``source``
    is the text the match was made on before lower-casing, so a tool
    call's "Medication" keeps its capital."""
    gd = m.groupdict()

    def span(name: str) -> str | None:
        value = gd.get(name)
        if value is None or source is None or len(source) < m.end(name):
            return value
        return source[m.start(name):m.end(name)]

    if gd.get("topic"):
        task, _ = _clean_task(span("topic"), _TOPIC_CONNECTOR)
        return task, _TOPIC_CONNECTOR
    return _clean_task(span("message"), gd.get("connector"))


def _task_from_tool_message(raw: object) -> tuple[str | None, str]:
    """The task in a tool-call ``message``, and the connector the reply
    reads it with ("to" / "about" / "for" / "that", or "topic" for a task
    named in front of "reminder").

    The tool model is told to pass only the task, but with no task spoken
    it copies the transcript ("Better reminder for 10 minutes") or writes
    its own reply ("Reminder set for 10 minutes.") instead. Either one is
    parsed with the fast-path grammars, so a command's words never become
    what a reminder says when it fires — and a task named in front of the
    word ("Medication reminder") is kept as the task, not dropped with
    the command."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "to"
    text_ = _TOOL_HEADING_RE.sub("", raw.strip()).strip()
    source = text_.rstrip(_END_PUNCT)
    norm = source.lower()
    if len(norm) != len(source):
        source = norm  # a case fold that changed length: spans would drift
    # The transcript copied whole, subject and all: "I set a reminder for
    # ten minutes" (qwen3:8b, 2026-09-30) is the command with no task, not
    # a task. Tried after the text as given, so "I want a reminder ..."
    # still reads with its own verb.
    unsubjected = _TOOL_SUBJECT_RE.sub("", norm)
    candidates = [(norm, source)]
    if unsubjected != norm:
        candidates.append((unsubjected, source[len(source) - len(unsubjected):]))
    for text_norm, text_source in candidates:
        for pattern in (*_CREATE_PATTERNS, _TOOL_TOPIC_RE, _TOOL_TASK_RE):
            m = pattern.match(text_norm)
            if m:
                task, connector = _task_of(m, text_source)
                return task, connector or "to"
        if _COMMAND_ECHO_RE.match(text_norm):
            return None, "to"
    task, connector = _clean_task(text_)
    return task, connector or "to"


def _cancel_phrase_from_tool(raw: object) -> str | None:
    """The label to cancel by from a tool call's ``message``: "Laundry
    reminder" cancels the reminder labelled "laundry"; nothing cancels
    every reminder in the room, as before."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    source = raw.strip().rstrip(_END_PUNCT)
    m = _TOOL_TOPIC_RE.match(source.lower())
    if m and len(source) == len(source.lower()):
        return source[m.start("topic"):m.end("topic")]
    return source


# "feed the dog at 6 pm" / "call mom tomorrow": a tool call's task with its
# time still on the end.
_TOOL_TASK_AT_RE = re.compile(rf"^(?P<message>.+?),? {_WHEN}$")


def _absolute_from_tool_message(raw: object) -> tuple[str | None, str | None, str] | None:
    """``(task, connector, spoken time)`` when a tool call's ``message``
    still holds a clock time, else None. The tool model can't see the
    clock: given "remind me at 6 pm to feed the dog" it guesses a
    ``duration_sec`` (10 hours at 20:00) but usually copies the time into
    ``message`` - the transcript whole, or the task with its time ("feed
    the dog at 6 pm"). Reading the time from there sets the reminder for
    6 PM, not for whatever the guess was."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    source = _TOOL_HEADING_RE.sub("", raw.strip()).strip().rstrip(_END_PUNCT)
    norm = source.lower()
    if len(norm) != len(source):
        source = norm  # a case fold that changed length: spans would drift
    for pattern in (
        _CREATE_AT_RE, _CREATE_TASK_AT_RE, _CREATE_NOUN_AT_RE, _CREATE_TOPIC_AT_RE,
        _TOOL_TASK_AT_RE,
    ):
        m = pattern.match(norm)
        if m:
            task, connector = _task_of(m, source)
            return task, connector, m.group("when")
    return None


def _connector_for_reply(connector: str | None) -> str:
    """"for the pasta" reads back as "about the pasta"; a bare task as "to"."""
    if connector in ("about", "for"):
        return "about"
    if connector == "that":
        return "that"
    return "to"


def _spoken_due(expires_at: datetime) -> str:
    """When a reminder goes off, as the list says it: "in 8 minutes" within
    the hour, else its clock time ("at 6 PM", "tomorrow at 7 AM")."""
    remaining = int((expires_at - utcnow()).total_seconds())
    if remaining < 3600:
        return f"in {_format_duration(max(1, remaining))}"
    return spoken_when(household_time.to_local(expires_at), household_time.now())


def _when_problem_text(problem: WhenProblem, kind: str, now: datetime) -> str:
    """The reply when a spoken time can't be set: a named day's time has
    gone ("9 PM has already gone tonight"), or a day needs a time. Nothing
    hears an answer to a question here, so it says what to say instead."""
    example = {
        "wake": "wake me up at 7",
        "alarm": "set an alarm for 7 AM",
    }.get(kind, "remind me at 3 PM to call mom")
    if problem.kind == "past" and problem.target is not None:
        day = spoken_day(problem.target.date(), now.date())
        if problem.target.hour >= 18 and day == "today":
            day = "tonight"
        return (
            f"{spoken_clock(problem.target)} has already gone {day}. "
            f"Say a time that's still to come, like \"{example}\"."
        )
    return f"What time? Say it with the time, like \"{example}\"."


# "what reminders do I have" / "list my reminders" / "what are my reminders"
_LIST_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is| are)? (?:my )?reminders?(?: do i have)?"
    r"|list (?:my |the )?reminders?"
    r")$"
)

# "cancel my reminder to call mom" / "cancel the reminder for taking the trash"
# / "cancel the laundry reminder" / "cancel the 10 minute reminder" — the
# last two are how the create paths name what they set, and without them
# the tool model cancelled every reminder in the room (qwen3:8b,
# 2026-09-30: "cancel the laundry reminder" → cancel with no message).
_CANCEL_RE = re.compile(
    r"^cancel (?:my |the |that )?"
    rf"(?:(?P<duration>{DURATION_PATTERN}) |{_TOPIC} )?reminder(?:s)?"
    r"(?: (?:to|about|for|named|called) (?P<label>.+))?$"
)


class ReminderHandler(Handler):
    name = "reminder"
    # band rationale: "remind me to ..." before timer (160) so the reminder regex wins
    #   over any future timer regex that might brush against "remind".
    priority_band = 140
    display = HandlerDisplay(label="Reminders", tone="info")
    example_phrases = (
        "remind me to call Mom in 20 minutes",
        "remind me in 2 hours to take the bins out",
        "remind me at 6 pm to take the bins out",
        "wake me up at 7",
        "what reminders do I have",
    )
    requires_network = "no"

    tool_schema = {
        "name": "reminder",
        "description": (
            "Schedule a spoken reminder for the current room. Reminders "
            "are timers with a message that gets read aloud when they "
            "fire. Use 'list' to read pending reminders, 'cancel' to "
            "remove them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "cancel"],
                },
                "message": {
                    "type": "string",
                    "description": (
                        "The task to remind about, e.g. 'call mom' (create "
                        "action). Empty when no task was said — never the "
                        "command itself ('set a reminder for 10 minutes')."
                    ),
                },
                "duration_sec": {
                    "type": "integer",
                    "description": "How long until the reminder fires.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_CREATE_RE, ReminderHandler._create_from_match, early_commit="B"),
            FastPath(_LIST_RE, ReminderHandler._list_from_match, early_commit="A"),
            FastPath(_CANCEL_RE, ReminderHandler._cancel_from_match),
            # No early commit on these: each one is whole without a task
            # ("set a reminder for 10 minutes") and a pause can come before
            # "… to call mom". Committing there would lose the task, which
            # is the one thing a reminder is for.
            FastPath(_CREATE_ABOUT_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_IN_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_NOUN_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_TOPIC_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_NOUN_TASK_FIRST_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_DURATION_FIRST_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_DURATION_TOPIC_RE, ReminderHandler._create_from_match),
            # Clock times, after every duration grammar. No early commit:
            # a time can go on ("at 6 … tomorrow") and a task can follow.
            FastPath(_CREATE_AT_RE, ReminderHandler._create_at_from_match),
            FastPath(_CREATE_TASK_AT_RE, ReminderHandler._create_at_from_match),
            FastPath(_CREATE_NOUN_AT_RE, ReminderHandler._create_at_from_match),
            FastPath(_CREATE_TOPIC_AT_RE, ReminderHandler._create_at_from_match),
            FastPath(_WAKE_AT_RE, ReminderHandler._wake_at_from_match),
            FastPath(_WAKE_IN_RE, ReminderHandler._wake_in_from_match),
            FastPath(_CREATE_ALARM_RE, ReminderHandler._alarm_from_match),
            FastPath(_CANCEL_ALARM_RE, ReminderHandler._cancel_alarm_from_match),
        ]

    def offers_tool(self, transcript: str) -> bool:
        # A statement about a reminder ("my reminder for 10 minutes didn't
        # go off", "you have a reminder in 10 minutes") asks for no
        # reminder action, and qwen3:8b made new reminders out of some of
        # them when shown this tool (see TIMER_STATEMENT_RE; the timer
        # withholds its tool on the same shapes). Every command keeps it.
        return not is_timer_statement(transcript)

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I didn't catch a reminder command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "create":
            absolute = _absolute_from_tool_message(args.get("message"))
            if absolute is not None:
                task, connector, when_text = absolute
                response = await self._create_at(
                    when_text, task, ctx, session, connector=connector
                )
                if response is not None:
                    return response
            task, connector = _task_from_tool_message(args.get("message"))
            duration = int(args.get("duration_sec") or 0)
            if duration <= 0:
                return Response(
                    text="When should I remind you?",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            # No task is a reminder with no task, as on the fast paths —
            # not a question: nothing would hear the answer (a parked
            # confirmation only resumes on yes/no), so asking "what
            # should I remind you about?" dropped the reminder instead.
            return await self._create(
                task, duration, ctx, session, connector=connector
            )
        if action == "list":
            return await self._list(ctx, session)
        if action == "cancel":
            return await self._cancel(
                _cancel_phrase_from_tool(args.get("message")), ctx, session
            )
        return Response(
            text=f"I don't know how to {action} a reminder.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _create_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        message, connector = _task_of(m)
        duration_sec = parse_duration_seconds(m.group("duration"))
        if not duration_sec:
            # "in 0 minutes" fits the grammar but means nothing — same
            # refusal as the tool-call path rather than a reminder that
            # fires immediately.
            return Response(
                text="When should I remind you?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return await self._create(
            message, duration_sec, ctx, session, connector=connector
        )

    async def _list_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._list(ctx, session)

    async def _cancel_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        gd = m.groupdict()
        label_phrase = gd.get("label") or gd.get("topic")
        if not label_phrase and gd.get("duration"):
            # "the ten minute reminder" is the one labelled "10 minute
            # reminder" (a reminder set with no task).
            seconds = parse_duration_seconds(gd["duration"])
            label_phrase = (
                f"{duration_adjective(seconds)} reminder" if seconds else gd["duration"]
            )
        if label_phrase:
            label_phrase = label_phrase.strip()
        return await self._cancel(label_phrase, ctx, session)

    async def _create_at_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        task, connector = _task_of(m)
        return await self._create_at(m.group("when"), task, ctx, session, connector=connector)

    async def _wake_at_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        return await self._create_at(m.group("when"), None, ctx, session, kind="wake")

    async def _wake_in_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        duration_sec = parse_duration_seconds(m.group("duration"))
        if not duration_sec:
            return self._reply(ctx, "When should I wake you?")
        now = utcnow()
        await TimerRepository(session).create(
            expires_at=now + timedelta(seconds=duration_sec),
            created_at=now,
            label=f"{duration_adjective(duration_sec)} alarm",
            message=_WAKE_MESSAGE,
            room_id=ctx.satellite_room_id,
        )
        return self._reply(ctx, f"I'll wake you in {_format_duration(duration_sec)}.")

    async def _alarm_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        return await self._create_at(m.group("when"), None, ctx, session, kind="alarm")

    async def _cancel_alarm_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response | None:
        """Cancel this room's alarms ("7 AM alarm" labels: "wake me up",
        "set an alarm"), or the one at a time. Declines when there is no
        alarm to cancel: "cancel the alarm" for a timer that is going off
        then goes where it always went (the tool model)."""
        clock: list[str] | None = None
        if m.group("clock"):
            when = parse_when(m.group("clock"))
            if when is None or when.hour is None:
                return None
            # The labels the time can have: "the 7 alarm" is the 7 AM one
            # or the 7 PM one, as "set an alarm for 7" would have set it.
            clock = sorted({
                spoken_clock(datetime(2000, 1, 1, hour, when.minute))
                for hour, _extra in candidate_hours(when)
            })
        if clock is None and ctx.satellite_room_id is not None:
            from domovoi.timer_delivery import ACK_WITHIN_SEC, ack_recent_fire

            if await ack_recent_fire(
                session, ctx.satellite_room_id, within_sec=ACK_WITHIN_SEC, kind="reminder",
            ) is not None:
                return self._reply(ctx, "Okay.")
        if clock:
            where, params = "lower(label) = ANY(:labels)", {
                "labels": [f"{c.lower()} alarm" for c in clock]
            }
        else:
            # Only the labels this handler gives an alarm ("7 AM alarm",
            # "20 minute alarm"), never a task that ends in the word ("check
            # the smoke alarm").
            where, params = "lower(label) ~ :alarm_label", {"alarm_label": _ALARM_LABEL_SQL_RE}
        result = await session.execute(
            text(
                f"""
                DELETE FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND message IS NOT NULL
                  AND {where}
                """
            ),
            {"room_id": ctx.satellite_room_id, **params},
        )
        deleted = result.rowcount or 0
        if not deleted:
            return None
        await notify_timers_changed(session, "cancelled")
        if deleted == 1:
            return self._reply(ctx, "Cancelled the alarm.")
        return self._reply(ctx, f"Cancelled {deleted} alarms.")

    # ─── Core actions ─────────────────────────────────────────────────
    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name)

    async def _create_at(
        self,
        when_text: str,
        task: str | None,
        ctx: Context,
        session: AsyncSession,
        *,
        connector: str | None = None,
        kind: str = "reminder",
    ) -> Response | None:
        """A reminder (``kind`` "reminder"), a wake-up ("wake") or an alarm
        ("alarm") at a spoken clock time, on the household's wall clock.
        None when the words hold no usable time (declined: the turn routes
        on as before)."""
        when = parse_when(when_text)
        if when is None:
            return None
        now_utc = household_time.utc_now()
        now_local = household_time.to_local(now_utc)
        target = resolve_when(when, now_local)
        if isinstance(target, WhenProblem):
            return self._reply(ctx, _when_problem_text(target, kind, now_local))
        expires_at = household_time.to_utc(target)
        if expires_at <= now_utc:
            # Only a wall-clock time the zone skips can land here (the
            # spring-forward hour, read as the hour after): say it plainly.
            return self._reply(ctx, _when_problem_text(WhenProblem("past", target), kind, now_local))
        clock = spoken_clock(target)
        said = spoken_when(target, now_local)
        task = (task or "").strip()
        if kind == "wake":
            message, label = _WAKE_MESSAGE, f"{clock} alarm"
            reply = f"I'll wake you {said}."
        elif kind == "alarm":
            message, label = "", f"{clock} alarm"
            reply = f"Alarm set for {spoken_time_and_day(target, now_local)}."
        elif task:
            message, label = task, task[:100]
            if connector == _TOPIC_CONNECTOR:
                reply = f"Your {task} reminder is set for {spoken_time_and_day(target, now_local)}."
            else:
                reply = f"I'll remind you {said} {_connector_for_reply(connector)} {task}."
        else:
            message, label = "", f"{clock} reminder"
            reply = f"I'll remind you {said}."
        await TimerRepository(session).create(
            expires_at=expires_at,
            created_at=now_utc,
            label=label,
            message=message,
            room_id=ctx.satellite_room_id,
        )
        return self._reply(ctx, reply)
    async def _create(
        self,
        message: str | None,
        duration_sec: int,
        ctx: Context,
        session: AsyncSession,
        *,
        connector: str | None = None,
    ) -> Response:
        repo = TimerRepository(session)
        # One instant for both ends, as the timer does: the TimerWatcher
        # recovers the duration as expires_at - created_at to say "Here's
        # your 10 minute reminder.", and NOW() (the transaction start) is
        # seconds early on a tool-routed turn.
        now = utcnow()
        task = (message or "").strip()
        spoken = _format_duration(duration_sec)
        if task:
            # Use the message as the label too so "cancel the reminder for X"
            # can match without a separate column. Trimmed at 100 chars to
            # keep labels searchable; the full message stays in `message`.
            label = task[:100]
            if connector == _TOPIC_CONNECTOR:
                reply = f"Your {task} reminder is set for {spoken}."
            else:
                reply = (
                    f"I'll remind you {_connector_for_reply(connector)} {task} in {spoken}."
                )
        else:
            # No task: an empty message (still a reminder — message IS NOT
            # NULL) and a label that says what it is on every screen that
            # lists it ("10 minute reminder"), where the command's own
            # words used to show.
            label = f"{duration_adjective(duration_sec)} reminder"
            reply = f"I'll remind you in {spoken}."
        await repo.create(
            expires_at=now + timedelta(seconds=duration_sec),
            created_at=now,
            label=label,
            message=task,
            room_id=ctx.satellite_room_id,
        )
        return Response(
            text=reply,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _list(self, ctx: Context, session: AsyncSession) -> Response:
        # Reminders are timers WHERE message IS NOT NULL. Plain timers
        # (no message) are the TimerHandler's territory.
        result = await session.execute(
            text(
                """
                SELECT message, expires_at, label
                FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND message IS NOT NULL
                ORDER BY expires_at ASC
                """
            ),
            {"room_id": ctx.satellite_room_id},
        )
        # A reminder with no task reads as its label: "your 10 minute
        # reminder", not an empty "In 8 minutes: ."
        rows = [
            (msg if msg.strip() else f"your {label or 'reminder'}", expires)
            for msg, expires, label in result.all()
        ]
        if not rows:
            return Response(
                text="You have no reminders set.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if len(rows) == 1:
            msg, expires = rows[0]
            due = _spoken_due(expires)
            return Response(
                text=f"{due[:1].upper()}{due[1:]}: {msg}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # 2+ reminders: report the first two by spoken time-to-fire and
        # the count. Reading every one aloud gets long fast.
        first_msg, first_exp = rows[0]
        second_msg, _ = rows[1]
        return Response(
            text=(
                f"You have {len(rows)} reminders. The next one, "
                f"{_spoken_due(first_exp)}: {first_msg}. After that: {second_msg}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _cancel(
        self, label_phrase: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        # Two modes:
        # 1. label given → DELETE WHERE label LIKE :phrase% AND room_id = X
        #    AND message IS NOT NULL. Substring match because Whisper's
        #    transcript rarely matches the original label exactly.
        # 2. no label → DELETE all reminders in this room — unless a
        #    reminder just went off here: every room announces every room's
        #    reminders now, and "cancel that reminder" right after hearing
        #    "Reminder from the garage: call mom" means "I heard it", not
        #    "delete this room's own reminders". Acknowledge that fire (the
        #    timer handler does the same for "stop the timer") and delete
        #    nothing.
        if not label_phrase and ctx.satellite_room_id is not None:
            from domovoi.timer_delivery import ACK_WITHIN_SEC, ack_recent_fire

            if await ack_recent_fire(
                session, ctx.satellite_room_id, within_sec=ACK_WITHIN_SEC, kind="reminder",
            ) is not None:
                return Response(
                    text="Okay.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
        if label_phrase:
            result = await session.execute(
                text(
                    """
                    DELETE FROM timers
                    WHERE room_id IS NOT DISTINCT FROM :room_id
                      AND message IS NOT NULL
                      AND lower(label) LIKE :pattern
                    """
                ),
                {
                    "room_id": ctx.satellite_room_id,
                    # "10 minutes" → "10 minute", so "cancel the reminder
                    # for 10 minutes" finds a no-task "10 minute reminder".
                    # Only ever widens the substring match: the singular
                    # is inside the plural.
                    "pattern": "%" + _UNIT_PLURAL_RE.sub(r"\1", label_phrase.lower()) + "%",
                },
            )
        else:
            result = await session.execute(
                text(
                    """
                    DELETE FROM timers
                    WHERE room_id IS NOT DISTINCT FROM :room_id
                      AND message IS NOT NULL
                    """
                ),
                {"room_id": ctx.satellite_room_id},
            )
        deleted = result.rowcount or 0
        if deleted:
            # Same transaction as the DELETE, so the dashboard hears about
            # it on commit and never about a rolled-back cancel.
            await notify_timers_changed(session, "cancelled")
        if deleted == 0:
            return Response(
                text="I couldn't find a matching reminder.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if deleted == 1:
            return Response(
                text="Cancelled the reminder.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"Cancelled {deleted} reminders.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
