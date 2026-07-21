"""VoiceNotesHandler — quick text capture against the `voice_notes` table.

Three usage shapes:

1. "jot down: replace the air filter" — any verb in the capture set
   (jot, write, note, save) followed by an optional " down" + the note
   body (separated by ":" or just whitespace).
2. "what did I jot down today" — read back recent notes within a window
   (today, yesterday, this week, recently).
3. "what was my last note" / "read my last note" — most-recent single note.

Fully local (`requires_network="no"`). Writes go to the `voice_notes`
table (V001 schema, untouched). The note's `room_id` is the room it was
captured from — useful for later "what did I jot down in the office"
queries (future).
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import VoiceNotesRepository, utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────

# "jot down: replace the air filter" / "jot down replace the air filter"
# / "save a note: ..." / "write down ..." / "note that ..."
_CAPTURE_VERBS = r"(?:jot|write|note|save)"
_ADD_RE = re.compile(
    rf"^{_CAPTURE_VERBS}"
    r"(?: this)?(?: down)?"
    r"(?: a note)?(?: that)?"
    r"\s*[:,]?\s+(?P<body>.+)$"
)

# "what did I jot down today" / "what notes did I take this week" /
# "read my notes from today"
_READ_WINDOW_RE = re.compile(
    r"^(?:"
    r"what (?:did i (?:jot down|note|save|write down)|notes did i (?:take|jot|save))"
    r"|read (?:my |the )?notes?(?: from)?"
    r")\s*(?P<window>today|yesterday|this week|recently)?$"
)

# "what was my last note" / "read my last note" / "read me my last note"
_READ_LATEST_RE = re.compile(
    r"^(?:"
    r"what was my (?:last|most recent) note"
    r"|read (?:me )?my (?:last|most recent) note"
    r")$"
)

_WINDOW_DELTAS = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=2),
    "this week": timedelta(days=7),
    "recently": timedelta(days=30),
}


def _strip_leading_filler(s: str) -> str:
    """Drop a leading "that " or "down " that the regex's optional groups
    can't fully absorb when phrasing varies (e.g. "jot down that pizza is
    here" → after the regex captures "down" + ":" + body, body might
    start with "that ").
    """
    cleaned = s.strip()
    for prefix in ("that ", "down ", "this "):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
    return cleaned.rstrip(".,!?")


class VoiceNotesHandler(Handler):
    name = "voice_notes"
    # band rationale: after intercom (210) — see the "tell the kitchen X" note there.
    priority_band = 230
    display = HandlerDisplay(label="Voice Notes", tone="comms")
    requires_network = "no"

    tool_schema = {
        "name": "voice_notes",
        "description": (
            "Capture short notes against the local notes log, or read them "
            "back. Notes are stamped with the room they were captured "
            "from and the wall-clock time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "read_window", "read_latest"],
                },
                "body": {
                    "type": "string",
                    "description": "Note body (for add action).",
                },
                "window": {
                    "type": "string",
                    "enum": ["today", "yesterday", "this week", "recently"],
                    "description": "Time window for read_window (default: recently).",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_READ_LATEST_RE, VoiceNotesHandler._latest_from_match),
            FastPath(_READ_WINDOW_RE, VoiceNotesHandler._window_from_match),
            # _ADD_RE is greediest — keep last so the read patterns get a
            # chance to match first.
            FastPath(_ADD_RE, VoiceNotesHandler._add_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I didn't catch a note command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "add":
            body = (args.get("body") or "").strip()
            if not body:
                return Response(
                    text="What should I note down?",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return await self._add(body, ctx, session)
        if action == "read_window":
            window = args.get("window") or "recently"
            return await self._read_window(window, ctx, session)
        if action == "read_latest":
            return await self._read_latest(ctx, session)
        return Response(
            text=f"I don't know how to {action} notes.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _add_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        body = _strip_leading_filler(m.group("body"))
        if not body:
            return Response(
                text="What should I note down?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return await self._add(body, ctx, session)

    async def _window_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        window = m.groupdict().get("window") or "recently"
        return await self._read_window(window, ctx, session)

    async def _latest_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._read_latest(ctx, session)

    # ─── Core actions ─────────────────────────────────────────────────
    async def _add(
        self, body: str, ctx: Context, session: AsyncSession
    ) -> Response:
        await VoiceNotesRepository(session).add(room_id=ctx.room_id, body=body)
        return Response(
            text="Got it.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _read_window(
        self, window: str, ctx: Context, session: AsyncSession
    ) -> Response:
        delta = _WINDOW_DELTAS.get(window, _WINDOW_DELTAS["recently"])
        since = utcnow() - delta
        rows = await VoiceNotesRepository(session).added_within(since=since)
        if not rows:
            label = window if window in _WINDOW_DELTAS else "that window"
            return Response(
                text=f"No notes from {label}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # Spoken summary: count + the most recent body verbatim. Reading
        # all of them aloud gets long; the user can ask "what was my last
        # note" to hear just one.
        bodies = [r[2] for r in rows]
        if len(bodies) == 1:
            return Response(
                text=f"One note: {bodies[0]}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=(
                f"{len(bodies)} notes. The most recent: {bodies[0]}. "
                f"Before that: {bodies[1]}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _read_latest(
        self, ctx: Context, session: AsyncSession
    ) -> Response:
        latest = await VoiceNotesRepository(session).latest()
        if latest is None:
            return Response(
                text="You haven't taken any notes yet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        _id, _room, body, _at = latest
        return Response(
            text=f"Your last note: {body}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
