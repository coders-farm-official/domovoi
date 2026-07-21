"""DropInHandler — initiate / end a two-way live "drop-in" call (Feature 4).

A drop-in is a live, bidirectional 16 kHz PCM audio channel between two
satellite rooms, relayed by the core with no STT/TTS in the audio
path (see ``StreamSession._begin_dropin`` / ``_on_audio`` relay branch).

This handler owns only the SPOKEN surface — turning "drop in on the kitchen"
into a feasibility-checked request, "hang up" into an end, and the target's
"yeah"/"no" (confirm mode) into accept/decline. It sets ``Response.dropin_action``
and the streaming layer — which owns the live ``StreamSession`` pairing — acts
on it after the turn, exactly like ``IntercomHandler`` sets ``announce_to_rooms``.

Feasibility gating lives here because the handler has ``ctx.app`` (stamped by
``_process_utterance``), so it can read ``app.state.active_sessions`` /
``satellite_full_duplex`` / ``active_dropins`` and speak the right answer
up-front ("the kitchen isn't connected", "...is already in a call", "...can't
do drop-in"). The shared, import-cycle-free check is ``dropin_feasibility``.

Fully local (``requires_network="no"``) — the relay never touches the network,
so no ``fallback_offline`` (the registry test in ``tests/test_registry.py``
exempts ``"no"`` handlers).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients import mpd as _mpd
from domovoi.config import settings
from domovoi.dropin_common import OK, dropin_feasibility, pretty_room
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.intercom import _resolve_target_rooms
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────
#
# Tightly anchored on drop-in vocabulary. DropInHandler is registered BEFORE
# IntercomHandler (and well before MusicHandler/RadioHandler), so the end
# regex must NEVER match a bare "stop" / "end" — those belong to music/radio
# stop. Only call-specific phrasings ("hang up", "end the call", "stop the
# drop-in") match, and the start regex requires the explicit "drop in on" /
# "connect me to" lead-in so it can't poach "play X" / "call mom".
_START_RE = re.compile(
    r"^(?:drop[- ]?in (?:on|to)|connect me to) (?P<room>.+)$"
)
_END_RE = re.compile(
    r"^(?:hang up(?: the (?:call|phone))?"
    r"|end(?: the)? (?:call|drop[- ]?in)"
    r"|stop(?: the)? (?:call|drop[- ]?in))$"
)


class DropInHandler(Handler):
    name = "dropin"
    # band rationale: immediately before intercom (210) so "drop in on X" / "connect me
    #   to X" / "hang up" win before intercom's announce regexes. Regexes
    #   are tightly anchored (the start path needs the explicit "drop in
    #   on"/"connect me to" lead-in; the end path never matches a bare
    #   "stop") so it can't poach "play X" / "stop the music" even though
    #   it sits above the media bands.
    priority_band = 200
    display = HandlerDisplay(label="Drop-In", tone="comms")
    confirmation_kinds = ("core.dropin_invite",)
    requires_network = "no"

    tool_schema = {
        "name": "dropin",
        "description": (
            "Start or end a live two-way intercom 'drop-in' (open mic) "
            "between this room and another room's satellite. Use action "
            "'start' with a target room to open a call (e.g. 'drop in on "
            "the kitchen'); action 'end' to hang up the active call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room": {
                    "type": "string",
                    "description": (
                        "Target room_id to drop in on (e.g. 'kitchen'). "
                        "Required for action 'start'."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["start", "end"],
                    "description": (
                        "'start' opens a drop-in to `room`; 'end' hangs up "
                        "the active call in this room."
                    ),
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_START_RE, DropInHandler._start_from_match),
            FastPath(_END_RE, DropInHandler._end_from_match),
        ]

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _start_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._start((m.group("room") or "").strip() or None, ctx)

    async def _end_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._end(ctx)

    # ─── Entry points ─────────────────────────────────────────────────
    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="Who do you want to drop in on?",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = (args.get("action") or "start").strip().lower()
        if action == "end":
            return self._end(ctx)
        room = (args.get("room") or "").strip() or None
        return self._start(room, ctx)

    async def handle_confirmation(
        self,
        kind: str,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The TARGET's yes/no to a confirm-mode drop-in invite.

        Parked by ``StreamSession._prompt_target_for_dropin`` as
        ``pending_confirmation = {handler:'dropin', kind:'core.dropin_invite',
        initiator_room:..., peer_label:...}``; the router routes the target's
        "yeah"/"no" here. Yes → ``dropin_action='accept'`` so streaming pairs
        the two rooms; no → a plain decline (no action).
        """
        if kind == "core.dropin_invite":
            if not affirmative:
                return Response(
                    text="Okay, never mind.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            initiator_room = data.get("initiator_room")
            label = pretty_room(initiator_room or "")
            return Response(
                text=f"Okay, connecting you with the {label}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
                dropin_action="accept",
                dropin_room=initiator_room,
                dropin_peer_label=label,
            )
        return Response(
            text="Okay.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Response construction ────────────────────────────────────────
    def _start(self, room_phrase: str | None, ctx: Context) -> Response:
        if ctx.app is None:
            # No streaming context (e.g. a direct /v1/intent call) → there's
            # no live mic to open.
            return self._say("Drop-in only works from a satellite.", ctx)
        if not getattr(settings, "dropin_enabled", True):
            return self._say("Drop-in is turned off right now.", ctx)

        targets = _resolve_target_rooms(room_phrase)
        if targets is None:
            if not _mpd._room_ports:
                return self._say("No rooms are connected yet.", ctx)
            return self._say(
                f"I don't know which room you mean by {room_phrase!r}.", ctx
            )
        # _resolve_target_rooms returns ALL rooms for a broadcast phrase /
        # None — a drop-in needs exactly one target.
        if len(targets) != 1:
            return self._say(
                "You can only drop in on one room at a time — which one?", ctx
            )

        target_room = targets[0]
        initiator_room = ctx.room_id or ""
        label = pretty_room(target_room)
        code = dropin_feasibility(ctx.app, initiator_room, target_room)
        if code != OK:
            return self._say(self._refusal_text(code, label), ctx)

        mode = getattr(settings, "dropin_accept_mode", "auto")
        text = (
            f"Asking the {label} if you can drop in."
            if mode == "confirm"
            else f"Dropping in on the {label}."
        )
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            dropin_action="request",
            dropin_room=target_room,
            dropin_peer_label=label,
        )

    def _end(self, ctx: Context) -> Response:
        initiator_room = ctx.room_id or ""
        active = ctx.app.state.active_dropins if ctx.app is not None else {}
        if initiator_room in active:
            return Response(
                text="Hanging up.",
                session_id=ctx.session_id,
                matched_handler=self.name,
                dropin_action="end",
            )
        return self._say("You're not in a call right now.", ctx)

    @staticmethod
    def _refusal_text(code: str, label: str) -> str:
        return {
            "same_room": "You can't drop in on your own room.",
            "initiator_offline": "I can't tell which room you're in.",
            "target_offline": f"The {label} isn't connected right now.",
            "initiator_no_aec": (
                "Your speaker can't do drop-in — it needs echo cancellation."
            ),
            "target_no_aec": (
                f"The {label} can't do drop-in — its speaker isn't "
                "echo-cancelling."
            ),
            "initiator_busy": "You're already in a call.",
            "target_busy": f"The {label} is already in a call.",
        }.get(code, "I can't start a drop-in right now.")

    def _say(self, text: str, ctx: Context) -> Response:
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
