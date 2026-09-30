"""ReminderHandler — set reminders that *speak* a message when they fire.

A reminder is just a row in `timers` with `message IS NOT NULL` (V001
schema is already shaped for this). The TimerWatcher fires reminders
the same way it fires plain timers, through the originating room's
`StreamSession.announce`, but when `message` is populated it speaks
"Reminder: <message>" instead of the plain "Your timer is done." line.

Three usage shapes:

1. Create. "remind me to <thing> in <duration>" is the most common; also
   "remind me in <duration> (to <thing>)", "set a reminder for <duration>
   (to <thing>)", "reminder in <duration> <thing>", "set a reminder to
   <thing> in <duration>" and "set a <duration> reminder (to <thing>)".
2. "what (are my) reminders" / "list reminders" — list pending reminders
   in the current room.
3. "cancel (my|the) reminder (about|for|to) <thing>" — cancel by label
   match, or all reminders in the room when no label given.

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

Absolute-time reminders ("remind me at 5pm") are deferred — they need a
real natural-language datetime parser, which isn't worth bolting on
yet. Until then they reach the tool model, which cannot see the clock and
guesses a ``duration_sec`` (qwen3:8b, 2026-09-30: "remind me to call mom
at five" → 5 hours; "remind me at 6 pm to feed the dog" → 10 hours or a
week, depending on the prompt).
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import TimerRepository, notify_timers_changed, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.number_words import DURATION_PATTERN, parse_duration_seconds
from domovoi.handlers.timer import _format_duration, duration_adjective
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

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
    rf"^remind me to (?P<message>.+?) in (?P<duration>{DURATION_PATTERN})$"
)

# The other create shapes. None of them keeps the words in front of the
# task: whatever verb Whisper heard ("set", "make", "new" — or "better",
# "said", "sit" for a mumbled "set a") is dropped with the article, and the
# task is only what follows the duration or a "to"/"about"/"that".
#
# Words that make "… reminder for 10 minutes" something other than a new
# reminder: cancelling, changing or asking about one. The LLM tool router
# still gets those; the create paths never do.
_NOT_A_CREATE_WORD = (
    r"(?:cancel|cancelled|stop|delete|remove|clear|end|kill|dismiss|silence|"
    r"mute|skip|pause|snooze|forget|undo|change|move|edit|update|reset|extend|"
    r"push|postpone|delay|turn|switch|list|show|read|tell|check|find|what|"
    r"what's|whats|which|when|where|where's|who|why|how|is|are|was|were|do|"
    r"does|did|any|no|not|don't|dont|never|without)"
)
# Up to two words ahead of "reminder" (the verb, heard or misheard).
_LEAD = rf"(?:(?!{_NOT_A_CREATE_WORD}\b)[a-z']+ ){{0,2}}"
_ARTICLE = r"(?:(?:a|an|the|my|another|one|me a|us a) )?"
_FROM_NOW = r"(?: from now)?"
# "a reminder for me in 10 minutes": whom it is for, not what.
_FOR_ME = r"(?: for (?:me|us))?"
# After the duration: an optional "to" / "about" / "for" / "that", then the
# task. "reminder in 5 minutes take the laundry out" has no connector.
_TASK_AFTER = r"(?:,? (?:(?P<connector>to|about|for|that) )?(?P<message>.+))?"

# "remind me in 10 minutes" / "remind me in an hour to call mom" /
# "ping me in ten minutes"
_CREATE_IN_RE = re.compile(
    rf"^(?:remind|ping|nudge) me in (?P<duration>{DURATION_PATTERN})"
    rf"{_FROM_NOW}{_TASK_AFTER}$"
)

# "set a reminder for 10 minutes" / "better reminder for 10 minutes" /
# "set a reminder for 10 minutes to call mom" / "reminder in 5 minutes take
# the laundry out" / "a reminder for ten minutes"
_CREATE_NOUN_RE = re.compile(
    rf"^{_LEAD}{_ARTICLE}reminder{_FOR_ME} (?:(?:for|in) )?"
    rf"(?P<duration>{DURATION_PATTERN}){_FROM_NOW}{_TASK_AFTER}$"
)

# "set a reminder to call mom in 10 minutes" / "a reminder about the
# pasta in half an hour" — the task first, the duration last.
_CREATE_NOUN_TASK_FIRST_RE = re.compile(
    rf"^{_LEAD}{_ARTICLE}reminder{_FOR_ME} (?P<connector>to|about|for|that) "
    rf"(?P<message>.+?) in (?P<duration>{DURATION_PATTERN}){_FROM_NOW}$"
)

# "set a 10 minute reminder" / "a five minute reminder to check the oven"
_CREATE_DURATION_FIRST_RE = re.compile(
    rf"^{_LEAD}{_ARTICLE}(?P<duration>{DURATION_PATTERN}) reminder{_TASK_AFTER}$"
)

# Every create grammar, in the order the fast paths try them.
_CREATE_PATTERNS = (
    _CREATE_RE, _CREATE_IN_RE, _CREATE_NOUN_RE, _CREATE_NOUN_TASK_FIRST_RE,
    _CREATE_DURATION_FIRST_RE,
)

# A tool-call ``message`` that is only the command, not a task: "Reminder
# set for 10 minutes." (the tool model writing its own reply), "reminder",
# "10 minute reminder", "10 minutes". Checked after the create grammars.
_COMMAND_ECHO_RE = re.compile(
    rf"^(?:{_LEAD}{_ARTICLE}(?:(?:{DURATION_PATTERN}) )?"
    rf"(?:reminder|remind me|ping me|ping|nudge me|nudge)"
    rf"(?: (?:is |was )?set)?(?: (?:for|in) (?:{DURATION_PATTERN}))?{_FROM_NOW}"
    rf"|(?:{DURATION_PATTERN}){_FROM_NOW})$"
)

# A spoken unit's plural, for the cancel match (see _cancel).
_UNIT_PLURAL_RE = re.compile(r"\b(second|minute|hour)s\b")

# Politeness after the task is not part of it: "… to call mom please".
_POLITE_TAIL_RE = re.compile(r"(?:^|,?\s+)(?:please|thanks|thank you)$")


def _clean_task(raw: str | None) -> str | None:
    """The task as it should be stored and spoken, or None for no task."""
    if raw is None:
        return None
    task = raw.strip().strip(",").strip()
    while True:
        trimmed = _POLITE_TAIL_RE.sub("", task).strip().strip(",").strip()
        if trimmed == task:
            break
        task = trimmed
    # "… reminder for me in 10 minutes" is whom it is for, not a task.
    if task.lower() in ("me", "us", "myself", "ourselves"):
        return None
    return task or None


def _task_from_tool_message(raw: object) -> tuple[str | None, str]:
    """The task in a tool-call ``message``, and the word the reply puts in
    front of it ("to" / "about" / "that").

    The tool model is told to pass only the task, but with no task spoken
    it copies the transcript ("Better reminder for 10 minutes") or writes
    its own reply ("Reminder set for 10 minutes.") instead. Either one is
    parsed with the fast-path grammars, so a command's words never become
    what a reminder says when it fires."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "to"
    text_ = raw.strip()
    norm = text_.lower().rstrip(".,!?").strip()
    for pattern in _CREATE_PATTERNS:
        m = pattern.match(norm)
        if m:
            return (
                _clean_task(m.group("message")),
                m.groupdict().get("connector") or "to",
            )
    if _COMMAND_ECHO_RE.match(norm):
        return None, "to"
    return _clean_task(text_.rstrip(".!?")), "to"


def _connector_for_reply(connector: str | None) -> str:
    """"for the pasta" reads back as "about the pasta"; a bare task as "to"."""
    if connector in ("about", "for"):
        return "about"
    if connector == "that":
        return "that"
    return "to"


# "what reminders do I have" / "list my reminders" / "what are my reminders"
_LIST_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is| are)? (?:my )?reminders?(?: do i have)?"
    r"|list (?:my |the )?reminders?"
    r")$"
)

# "cancel my reminder to call mom" / "cancel the reminder for taking the trash"
_CANCEL_RE = re.compile(
    r"^cancel (?:my |the |that )?reminder(?:s)?"
    r"(?: (?:to|about|for|named|called) (?P<label>.+))?$"
)


class ReminderHandler(Handler):
    name = "reminder"
    # band rationale: "remind me to ..." before timer (160) so the reminder regex wins
    #   over any future timer regex that might brush against "remind".
    priority_band = 140
    display = HandlerDisplay(label="Reminders", tone="info")
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
            FastPath(_CREATE_IN_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_NOUN_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_NOUN_TASK_FIRST_RE, ReminderHandler._create_from_match),
            FastPath(_CREATE_DURATION_FIRST_RE, ReminderHandler._create_from_match),
        ]

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
            return await self._cancel(args.get("message"), ctx, session)
        return Response(
            text=f"I don't know how to {action} a reminder.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _create_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        message = _clean_task(m.group("message"))
        connector = m.groupdict().get("connector")
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
        label_phrase = m.groupdict().get("label")
        if label_phrase:
            label_phrase = label_phrase.strip()
        return await self._cancel(label_phrase, ctx, session)

    # ─── Core actions ─────────────────────────────────────────────────
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
            reply = f"I'll remind you {_connector_for_reply(connector)} {task} in {spoken}."
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
            room_id=ctx.room_id,
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
            {"room_id": ctx.room_id},
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
            remaining = int((expires - utcnow()).total_seconds())
            spoken = _format_duration(max(1, remaining))
            return Response(
                text=f"In {spoken}: {msg}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # 2+ reminders: report the first two by spoken time-to-fire and
        # the count. Reading every one aloud gets long fast.
        first_msg, first_exp = rows[0]
        second_msg, _ = rows[1]
        first_spoken = _format_duration(max(1, int((first_exp - utcnow()).total_seconds())))
        return Response(
            text=(
                f"You have {len(rows)} reminders. The next one, in "
                f"{first_spoken}: {first_msg}. After that: {second_msg}."
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
        # 2. no label → DELETE all reminders in this room.
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
                    "room_id": ctx.room_id,
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
                {"room_id": ctx.room_id},
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
