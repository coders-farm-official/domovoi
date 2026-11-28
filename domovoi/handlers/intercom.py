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
import time

from sqlalchemy.ext.asyncio import AsyncSession

# Imported as a module rather than `from ... import _room_ports` so tests
# (and any later swap) can rebind the attribute on the mpd module without
# leaving a stale reference here. See test_intercom_handler for the
# pattern.
from domovoi.clients import mpd as _mpd
from domovoi.dropin_common import room_may_reach_other_rooms
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.house_facts import GENERIC_OBJECT
from domovoi.models import Context, Intent, Response, is_phone_room

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
        "rooms",
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
# "(can you) announce stuff", "tell everyone things": a question about the
# ability, never a broadcast of the word "things" (G1; house_facts answers it).
_NOT_GENERIC = rf"(?!(?:{GENERIC_OBJECT})(?:,? (?:please|for me|for us))?$)"
_ANNOUNCE_TO_RE = re.compile(
    r"^(?:announce|broadcast) (?:to|in) (?P<room>.+?)\s*[:,]\s*(?P<message>.+)$"
)
_ANNOUNCE_BARE_RE = re.compile(
    rf"^(?:announce|broadcast)(?:\s*[:,]\s*|\s+){_NOT_GENERIC}(?P<message>.+)$"
)

# "tell the house dinner's ready" / "tell everyone the package is here"
# Tightly restricted to broadcast phrases — "tell me a story" / "tell me
# about Jupiter" must fall through to QA, not get hijacked by intercom.
# Per-room "tell the kitchen X" intentionally isn't here: users can say
# "announce in the kitchen X" instead, which is unambiguously intercom
# and avoids the "tell the time" / "tell the truth" overlap.
_TELL_RE = re.compile(
    r"^tell (?P<room>everyone|everybody|everywhere|the house|the whole house|all rooms|all the rooms)"
    rf"(?:\s*[:,]\s*|\s+){_NOT_GENERIC}(?P<message>.+)$"
)


# ─── What to announce ─────────────────────────────────────────────────────
#
# The fast paths read the normalized transcript (lowercase, no closing
# punctuation, polite opener taken off). What is announced is cleaned and
# put back in the words as said (N20, 1.5.0):
#
# * "announce that dinner's ready": the "that" before a clause is not said;
# * "announce dinner's ready to everyone please": the routing words at the
#   end are not said either (the target is the house anyway);
# * "Please announce that Alex, Sam and Jo need another shot": the names
#   keep the casing they were said (or typed) with (``Context.said``);
# * "announce that", "announce it to the rooms please": a POINTER at
#   something said before, never announced as the words "that" or "it to
#   the rooms please". It repeats the last announcement this session made,
#   or, in a chat, the person's own earlier announce request in the thread
#   (the typed message, never an assistant reply: an old reply that only
#   pretended to announce must not be read out), within the last few
#   minutes; the session's own announcement first. With neither, Domovoi
#   asks "What should I announce?" and the next turn's words are the
#   announcement.

# "that" introducing a clause (two words or more after it), so "announce
# that" alone is a pointer, never the word "that".
_THAT_CLAUSE_RE = re.compile(r"^that\s+(?P<clause>\S+\s+\S.*)$")
# Who an announcement is for, said as a routing phrase.
_EVERYONE = (
    r"(?:the\s+)?(?:rooms|house|whole\s+house|everyone|everybody|every\s+room|all(?:\s+the)?\s+rooms)"
)
# A routing phrase straight after the verb ("announce to everyone dinner's
# ready"), and at the very end only with "to" ("..., to the rooms please"):
# "announce the cat is in the house" keeps its words.
_LEADING_ROUTE_RE = re.compile(r"^(?:to|in)\s+" + _EVERYONE + r"\s*[,:]?\s+(?=\S)")
_TRAILING_ROUTE_RE = re.compile(r"(?:,?\s+(?:please|to\s+" + _EVERYONE + r"))+\s*$")
_POINTER_RE = re.compile(
    r"^(?:that|it|this)(?:\s+(?:to|in)\s+(?P<room>.+?))?"
    r"(?:,?\s+(?:please|again|now|for me|for us|too|as well))*$"
)
#: A reply to "What should I announce?" that takes it back.
_NEVER_MIND_RE = re.compile(
    r"^(?:(?:no|nope),? )?(?:never ?mind|nothing|cancel|forget it|no|nope|don't|stop)"
    r"(?:,? (?:thanks|thank you|then))?$"
)
#: Where the session keeps its last announcement ({"text", "room", "at"}).
LAST_ANNOUNCEMENT_KEY = "intercom_last_announcement"
#: How long "announce that" may reach back for what to announce again.
ANNOUNCE_AGAIN_WINDOW_SEC = 300.0
#: The parked question (a choice: its reply is the announcement).
WHAT_KIND = "core.intercom_what"
#: Typed chat reads the reply back first ("Announce "X" to every room?").
CONFIRM_KIND = "core.intercom_confirm"
#: How long "What should I announce?" (and its read-back) stays answerable.
WHAT_TTL_SEC = 60.0
#: A reply this long, or shaped as a question, is not an announcement.
WHAT_MAX_CHARS = 200
_QUESTION_START_RE = re.compile(r"^(?:who|whom|whose|what|why|where|how|when|which)\b", re.I)
#: Asked for a pointer with nothing to point at.
WHAT_TO_ANNOUNCE_TEXT = "What should I announce?"
NOTHING_ANNOUNCED_TEXT = "Okay, I won't announce anything."


def _recase(message: str, said: str | None) -> str:
    """``message`` (lowercase, from the normalized transcript) in the
    casing it had in ``said``, the turn's own words; as it is when it can't
    be found there."""
    if not said or not message:
        return message
    low = said.lower()
    if len(low) != len(said):
        return message
    at = low.rfind(message.lower())
    return said[at:at + len(message)] if at >= 0 else message


def _clean_message(message: str, said: str | None = None) -> str:
    """The words to announce: no "that" before a clause, no routing words or
    "please" at the end, in the casing they were said."""
    msg = message.strip()
    m = _THAT_CLAUSE_RE.match(msg)
    if m:
        msg = m.group("clause").strip()
    msg = _LEADING_ROUTE_RE.sub("", msg)
    msg = _TRAILING_ROUTE_RE.sub("", msg).strip()
    return _recase(msg, said)


def _pointer(message: str) -> re.Match[str] | None:
    """The match when ``message`` only points at something said before."""
    return _POINTER_RE.match(message.strip().lower())


def _announce_request(raw: str) -> tuple[str | None, str] | None:
    """``(room phrase, message)`` of an announce request said as ``raw``,
    or None (not one, or only a pointer)."""
    from domovoi.router import normalize_transcript, strip_leading_filler

    said = strip_leading_filler(normalize_transcript(raw or ""))
    for rx in (_ANNOUNCE_TO_RE, _ANNOUNCE_BARE_RE, _TELL_RE):
        m = rx.match(said)
        if m is None:
            continue
        message = m.group("message").strip()
        if _pointer(message) is not None:
            return None
        room = (m.groupdict().get("room") or "").strip() or None
        cleaned = _clean_message(message, raw)
        return (room, cleaned) if cleaned else None
    return None


async def _session_announcement(ctx: Context, session: AsyncSession | None) -> dict | None:
    """The last announcement this session made, within the window."""
    if session is None or ctx.session_id is None:
        return None
    from domovoi.db.repositories import SessionRepository

    data = (await SessionRepository(session).get_context(ctx.session_id)).get(LAST_ANNOUNCEMENT_KEY)
    if not (isinstance(data, dict) and isinstance(data.get("text"), str) and data["text"].strip()):
        return None
    at = float(data.get("at") or 0)
    if time.time() - at > ANNOUNCE_AGAIN_WINDOW_SEC:
        return None
    return {"text": data["text"], "room": data.get("room"), "at": at}


async def _thread_request(ctx: Context, session: AsyncSession | None) -> dict | None:
    """In a chat (the chat mic or typed chat: ``ctx.chat_thread_id``), the
    person's own latest announce request in the thread, within the window:
    the phone's own user rows (domovoi/phone_voice.py ``recent_requests``),
    never an assistant reply."""
    if session is None or ctx.chat_thread_id is None or not is_phone_room(ctx.room_id):
        return None
    from domovoi.phone_voice import recent_requests

    for content, at in await recent_requests(session, ctx, ANNOUNCE_AGAIN_WINDOW_SEC):
        found = _announce_request(content or "")
        if found is not None:
            return {"room": found[0], "text": found[1], "at": float(at or 0)}
    return None


async def _what_to_repeat(ctx: Context, session: AsyncSession | None) -> dict | None:
    """This session's last announcement, else the thread's last request
    (above), else None. A source that can't be read is only nothing found:
    Domovoi then asks."""
    for source in (_session_announcement, _thread_request):
        try:
            hit = await source(ctx, session)
        except Exception as e:  # noqa: BLE001
            log.warning("intercom: couldn't read what to announce again: %s", type(e).__name__)
            hit = None
        if hit:
            return hit
    return None


async def _remember_announcement(
    response: Response, room_phrase: str | None, ctx: Context, session: AsyncSession | None,
) -> None:
    if session is None or ctx.session_id is None or not response.announce_text:
        return
    from domovoi.db.repositories import SessionRepository

    try:
        await SessionRepository(session).set_context_key(
            ctx.session_id, LAST_ANNOUNCEMENT_KEY,
            {"text": response.announce_text, "room": room_phrase, "at": time.time()},
        )
    except Exception as e:  # noqa: BLE001 — the announcement still goes out
        log.warning("intercom: couldn't remember the announcement: %s", e)


# ─── Handler ──────────────────────────────────────────────────────────────


class IntercomHandler(Handler):
    name = "intercom"
    # band rationale: before voice_notes (230) so "tell the kitchen X" can't be
    #   mis-routed into the notes capture verbs.
    priority_band = 210
    display = HandlerDisplay(label="Intercom", tone="comms")
    example_phrases = (
        "tell everyone dinner's ready",
        "announce to the kitchen, dinner's ready",
    )
    requires_network = "no"
    # "announce that" with nothing to point at: "What should I announce?",
    # and the next turn's words are the announcement.
    confirmation_kinds = (WHAT_KIND, CONFIRM_KIND)
    choice_kinds = (WHAT_KIND,)

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
        if _pointer(message) is not None:
            return await self._announce(room, message, ctx, session)
        if room and _resolve_target_rooms(room) is None:
            # "tell the Smiths dinner's at six": no room by that name, but
            # perhaps a friend house (domovoi/handlers/friend_houses.py),
            # which reads it back and asks before sending.
            from domovoi.handlers.friend_houses import offer_to_friend

            handed = await offer_to_friend(room, message, ctx, session)
            if handed is not None:
                return handed
        return await self._announce(room, message, ctx, session)

    # ─── Fast-path adapter ────────────────────────────────────────────
    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        room_phrase = (m.groupdict().get("room") or "").strip() or None
        message = m.group("message").strip()
        return await self._announce(room_phrase, message, ctx, session)

    async def _announce(
        self, room_phrase: str | None, message: str, ctx: Context, session: AsyncSession | None,
    ) -> Response:
        """Announce ``message`` (cleaned: see "What to announce"), or, when
        it only points at something said before ("announce that", "announce
        it to the rooms"), that again: never the pointer's words."""
        pointer = _pointer(message)
        if pointer is not None:
            last = await _what_to_repeat(ctx, session)
            if last is None:
                return await self._ask_what(room_phrase or pointer.group("room"), ctx, session)
            message = last["text"]
            room_phrase = (pointer.group("room") or "").strip() or room_phrase or last.get("room")
        else:
            message = _clean_message(message, ctx.said)
        response = self._build_response(room_phrase, message, ctx)
        await _remember_announcement(response, room_phrase, ctx, session)
        return response

    async def _ask_what(self, room_phrase: str | None, ctx: Context, session: AsyncSession | None) -> Response:
        """ "What should I announce?", its reply parked as a choice."""
        asked = False
        if session is not None and ctx.session_id is not None and ctx.answerable:
            from domovoi.confirmations import request_confirmation

            await request_confirmation(
                session, ctx.session_id, kind=WHAT_KIND, handler=self.name,
                data={"room": (room_phrase or "").strip() or None,
                      "expires_at": time.time() + WHAT_TTL_SEC},
                prompt=WHAT_TO_ANNOUNCE_TEXT,
            )
            asked = True
        return Response(
            text=WHAT_TO_ANNOUNCE_TEXT, session_id=ctx.session_id, matched_handler=self.name,
            expect_followup=asked,
        )

    async def handle_confirmation(self, kind, data, affirmative, ctx, session) -> Response:
        """A yes or no: to the read-back ("Announce "X" to every room?"),
        yes announces it; to "What should I announce?" (the router hands a
        choice's reply to :meth:`handle_choice_reply` first), yes asks again,
        since a yes is not what to announce. No, or a question gone stale,
        is nothing."""
        room = data.get("room") if isinstance(data.get("room"), str) else None
        fresh = float(data.get("expires_at") or 0) >= time.time()
        if not affirmative or not fresh:
            return Response(text=NOTHING_ANNOUNCED_TEXT, session_id=ctx.session_id, matched_handler=self.name)
        if kind == CONFIRM_KIND:
            message = str(data.get("text") or "").strip()
            if not message:
                return Response(text=NOTHING_ANNOUNCED_TEXT, session_id=ctx.session_id, matched_handler=self.name)
            response = self._build_response(room, message, ctx)
            await _remember_announcement(response, room, ctx, session)
            return response
        return await self._ask_what(room, ctx, session)

    async def _read_back(self, room: str | None, message: str, ctx: Context, session: AsyncSession) -> Response:
        """Typed chat: "Announce "X" to every room?", parked for a yes, before
        what was typed after "What should I announce?" is broadcast."""
        from domovoi.confirmations import request_confirmation

        where = "to every room" if room is None or _normalize_room_phrase(room) in _BROADCAST_TARGETS \
            else f"in the {_normalize_room_phrase(room)}"
        await request_confirmation(
            session, ctx.session_id, kind=CONFIRM_KIND, handler=self.name,
            data={"room": room, "text": message, "expires_at": time.time() + WHAT_TTL_SEC},
            prompt="announce read-back",
        )
        return Response(
            text=f"Announce \u201c{message}\u201d {where}?", session_id=ctx.session_id,
            matched_handler=self.name, expect_followup=True,
        )

    async def handle_choice_reply(self, kind, data, transcript, ctx, session) -> Response | None:
        """The reply to "What should I announce?": its words are the
        announcement ("never mind" takes it back). Another pointer, a
        command, or a question gone stale is an ordinary turn."""
        if kind != WHAT_KIND or float(data.get("expires_at") or 0) < time.time():
            return None
        said = (transcript or "").strip()
        if not said or _NEVER_MIND_RE.match(said):
            return Response(text=NOTHING_ANNOUNCED_TEXT, session_id=ctx.session_id, matched_handler=self.name)
        if _pointer(said) is not None:
            return None
        raw = (ctx.said or said).strip()
        if raw.endswith("?") or _QUESTION_START_RE.match(said) or len(raw) > WHAT_MAX_CHARS:
            # A question, or a long message, is not what to announce: it is
            # an ordinary turn (the question is dropped).
            return None
        from domovoi.router import first_fast_path, strip_leading_filler

        request = strip_leading_filler(said)
        if first_fast_path(request) is not None:
            # A command after all ("stop the music", "announce dinner is
            # ready"): routed as one, never read out.
            return None
        message = _clean_message(request, ctx.said)
        if not message:
            return None
        room = data.get("room") if isinstance(data.get("room"), str) else None
        if ctx.trigger == "typed" and session is not None and ctx.session_id is not None:
            return await self._read_back(room, message, ctx, session)
        response = self._build_response(room, message, ctx)
        await _remember_announcement(response, room, ctx, session)
        return response

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
        if not room_may_reach_other_rooms(ctx.app, ctx.room_id):
            # CORE-11: a satellite accepted WITHOUT a pairing token (strict
            # pairing off) is whatever LAN device named itself this room;
            # it does not get to speak in the others.
            log.warning(
                "intercom: refused for room=%s — connected without a pairing "
                "token", ctx.room_id,
            )
            return Response(
                text=(
                    "This speaker isn't paired with the house, so it can't "
                    "make announcements in other rooms."
                ),
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
