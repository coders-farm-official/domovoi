"""IntercomHandler — fan-out announcements to one or many satellite rooms.

Three usage shapes:

1. Broadcast: "announce to the house: dinner's ready"  (or "to everyone",
   "everywhere", "in every room", or no recipient at all).
2. Targeted: "announce in the kitchen: someone's at the door"
   ("in <room>" or "to <room>" both supported).
3. Bare "tell" alias: "tell the house dinner's ready" / "tell the kitchen
   the package arrived". Same routing rules as `announce`.

The handler resolves the target list to room_ids that match currently-
provisioned MPD rooms (via `clients.mpd._room_ports`) and writes them
to ``Response.announce_to_rooms``. The streaming layer fans out the
audio after the response is sent to the requesting Pi — see
``StreamSession.announce`` for the per-target delivery.

Fully local (``requires_network="no"``); no external state.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

# Imported as a module rather than `from ... import _room_ports` so tests
# (and any later swap) can rebind the attribute on the mpd module without
# leaving a stale reference here. See test_intercom_handler for the
# pattern.
from domovoi.clients import mpd as _mpd
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Spoken-room normalization ────────────────────────────────────────────

# Phrases that mean "every active room." Anything that round-trips to one
# of these (after stripping articles/pluralization) gets the full broadcast.
_BROADCAST_TARGETS = frozenset(
    {
        "house",
        "the house",
        "everyone",
        "everywhere",
        "every room",
        "all rooms",
        "all the rooms",
        "everybody",
    }
)


def _normalize_room_phrase(phrase: str) -> str:
    """Lowercase, drop the leading "the ", strip trailing whitespace.

    Doesn't try to be clever about pluralization — "kitchens" won't match
    a "kitchen" room_id. That's fine: room IDs are user-controlled and
    rarely plural.
    """
    cleaned = phrase.strip().lower()
    if cleaned.startswith("the "):
        cleaned = cleaned[4:]
    return cleaned


def _resolve_target_rooms(room_phrase: str | None) -> list[str] | None:
    """Resolve a spoken room phrase to a list of room_ids.

    Returns:
      * The full list of provisioned rooms when ``room_phrase`` is None or
        a broadcast phrase ("the house", "everyone", ...).
      * A single-room list when the phrase matches a known room_id (after
        normalizing case + spaces vs underscores).
      * None when no rooms are provisioned, OR when the phrase doesn't
        match anything — caller should respond with "I don't know that
        room" or similar.
    """
    rooms = list(_mpd._room_ports.keys())
    if not rooms:
        return None

    if room_phrase is None:
        return rooms

    normalized = _normalize_room_phrase(room_phrase)
    if normalized in _BROADCAST_TARGETS:
        return rooms

    # Loose match against known rooms. "living room" should match
    # room_id "living_room" or "livingroom" — we collapse spaces /
    # underscores on both sides before comparing.
    target_compact = normalized.replace(" ", "").replace("_", "").replace("-", "")
    for room in rooms:
        room_compact = room.lower().replace(" ", "").replace("_", "").replace("-", "")
        if room_compact == target_compact:
            return [room]

    return None


# ─── Fast-path regexes ────────────────────────────────────────────────────

# Two shapes for "announce", split apart so the regex engine can tell
# where the recipient ends:
#
# 1. With explicit recipient — requires a colon (or comma) between
#    recipient and message. Lazy `.+?` plus a non-space-only separator
#    couldn't reliably distinguish "announce to the kitchen dinner's
#    ready" (room="the kitchen") from "announce to the kitchen the door
#    is open" (room="the" + msg="kitchen ..." under lazy matching). The
#    explicit colon dodges the ambiguity entirely.
# 2. Bare broadcast — no recipient, no colon required. Just "announce
#    pizza is here" or "broadcast: dinner's ready".
_ANNOUNCE_TO_RE = re.compile(
    r"^(?:announce|broadcast) (?:to|in) (?P<room>.+?)\s*[:,]\s*(?P<message>.+)$"
)
_ANNOUNCE_BARE_RE = re.compile(
    r"^(?:announce|broadcast)(?:\s*[:,]\s*|\s+)(?P<message>.+)$"
)

# "tell the house dinner's ready" / "tell everyone the package is here"
# Tightly restricted to broadcast phrases — "tell me a story" / "tell me
# about Jupiter" must fall through to QA, not get hijacked by intercom.
# Per-room "tell the kitchen X" intentionally isn't here: users can say
# "announce in the kitchen X" instead, which is unambiguously intercom
# and avoids the "tell the time" / "tell the truth" overlap.
_TELL_RE = re.compile(
    r"^tell (?P<room>everyone|everybody|everywhere|the house|the whole house|all rooms|all the rooms)"
    r"(?:\s*[:,]\s*|\s+)(?P<message>.+)$"
)


# ─── Handler ──────────────────────────────────────────────────────────────


class IntercomHandler(Handler):
    name = "intercom"
    # band rationale: before voice_notes (230) so "tell the kitchen X" can't be
    #   mis-routed into the notes capture verbs.
    priority_band = 210
    display = HandlerDisplay(label="Intercom", tone="comms")
    requires_network = "no"

    tool_schema = {
        "name": "intercom",
        "description": (
            "Broadcast a spoken announcement to one or more rooms' Pi "
            "satellites. Use 'all' as the room to fan out to every "
            "active satellite (house-wide). Use a specific room_id "
            "(e.g. 'kitchen') to target one room."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "description": (
                        "Target room_id, or 'all' for house-wide. "
                        "Defaults to 'all' when unspecified."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "What to announce.",
                },
            },
            "required": ["message"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            # Try the explicit-recipient form first so its capture wins
            # when both could match (e.g. "announce in the kitchen: ...").
            FastPath(_ANNOUNCE_TO_RE, IntercomHandler._from_match),
            FastPath(_ANNOUNCE_BARE_RE, IntercomHandler._from_match),
            FastPath(_TELL_RE, IntercomHandler._from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="What should I announce, and to where?",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        message = (args.get("message") or "").strip()
        if not message:
            return Response(
                text="What should I announce?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        room = args.get("room")
        # Tool-call shorthand: 'all' / 'house' / 'everyone' all mean
        # broadcast. Anything else is treated as a literal room_id.
        if room and room.lower() in {"all", "house", "everyone", "everywhere"}:
            room = None
        return self._build_response(room, message, ctx)

    # ─── Fast-path adapter ────────────────────────────────────────────
    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        room_phrase = (m.groupdict().get("room") or "").strip() or None
        message = m.group("message").strip()
        return self._build_response(room_phrase, message, ctx)

    # ─── Response construction ────────────────────────────────────────
    def _build_response(
        self, room_phrase: str | None, message: str, ctx: Context
    ) -> Response:
        if not message:
            return Response(
                text="I didn't catch what to announce.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        targets = _resolve_target_rooms(room_phrase)
        if targets is None:
            if not _mpd._room_ports:
                return Response(
                    text="No rooms are connected yet, so there's nobody to tell.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return Response(
                text=f"I don't know which room you mean by {room_phrase!r}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # Pretty confirmation text for the requesting Pi. We exclude the
        # requester from the fan-out list (streaming layer handles that)
        # so the requester only hears the confirmation, not the announcement
        # back to itself. Counting recipients in the confirmation text uses
        # the raw target list — "broadcasting to the house" still says
        # "house" even if the requester is one of the rooms.
        if room_phrase is None or len(targets) == len(_mpd._room_ports):
            confirmation = "Broadcasting to the house."
        elif len(targets) == 1:
            confirmation = f"Announcing in the {targets[0]}."
        else:
            confirmation = f"Announcing in {len(targets)} rooms."

        return Response(
            text=confirmation,
            session_id=ctx.session_id,
            matched_handler=self.name,
            announce_to_rooms=targets,
            announce_text=message,
        )
