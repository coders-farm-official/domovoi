"""ChatModeHandler — enter the open-mic conversational chat mode (Feature 8).

Domovoi has two modes per session:

  * **command mode** (the default) — every turn runs the fast-path router
    (this whole ``handlers/`` package), wake-word-gated on the satellite.
    Latency-critical; untouched by this feature.
  * **conversational chat mode** — a wake-word-triggered open mic where each
    turn is dispatched to a self-hosted **Letta** agent on the LOCAL Ollama
    (see ``domovoi/clients/letta.py``) instead of the router. Only
    conversational turns hit Letta; command latency stays exactly as it was.

This handler owns only the SPOKEN entry surface — turning "let's have a chat"
into a mode flip + a short ack. The actual per-turn Letta dispatch (and the
EXIT-phrase teardown) live in the streaming layer, because that layer owns the
live ``StreamSession`` and the per-sentence TTS pipeline. The pattern mirrors
``DropInHandler`` / ``IntercomHandler``: a handler flips a bit, the streaming
layer acts on it after the turn.

How the mode + the open-mic frame are wired (NO new ``Response`` field — the
``models.py`` matched_path Literal is owned by another agent, and a fresh
``Response`` field would be too):

  * The handler writes two keys into ``sessions.context`` (the same JSONB that
    already carries ``pending_confirmation``):
      - ``conversational_mode = True``   — the per-session mode flag. The
        streaming layer reads it BEFORE calling ``route()`` on the next turn;
        once set, conversational turns bypass the router entirely and go to
        Letta. The bound Letta ``agent_id`` also rides the session context
        (``letta_agent_id``), set lazily by the streaming layer on the first
        conversational turn.
      - ``chat_start_pending = True``     — a one-shot marker the streaming
        layer checks AFTER persisting THIS turn; when set, it sends a
        ``chat_start`` frame to the room (so the Pi opens its mic) and clears
        the marker. This keeps the frame-send out of the handler (which has no
        socket) while still being driven by the handler's decision.

Fully local (``requires_network="no"``): entering chat mode touches nothing on
the network — Letta runs against the LOCAL Ollama, and a tool a conversational
turn later invokes degrades per that tool's own ``requires_network`` contract.
Because it's ``"no"``, no ``fallback_offline`` is needed (the registry test in
``tests/test_registry.py`` exempts ``"no"`` handlers).

The whole feature is gated OFF by default (``settings.chat_mode_enabled``); when
off, the fast paths still ack but the streaming layer never actually dispatches
to Letta (``get_letta_client`` returns the stub) — so a stray "let's chat" on a
deployment without Letta running just speaks a canned reply and never wedges.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regex ──────────────────────────────────────────────────────
#
# Tightly anchored on "let's converse" vocabulary. ChatModeHandler is
# registered in the device-control cluster, ahead of the greedy
# greedy media catch-alls (MusicHandler's ``^play (.+)$``, a media-provider
# plugin's ``find X``), so these MUST NOT brush a media phrase.
# Only explicit conversation-opener phrasings match — never a bare "talk"
# (which could be "talk to the kitchen") or "chat" without the lead-in.
# The router lower-cases + strips trailing punctuation before dispatch, so
# these match a normalized lower-case transcript.
_ENTER_RE = re.compile(
    r"^(?:"
    r"let'?s (?:have a chat|chat|talk|have a conversation|converse)"
    r"|can we (?:chat|talk|have a chat|have a conversation)"
    r"|i(?:'d like| would like| want) to (?:chat|talk|have a conversation)"
    r"|(?:let'?s )?have a conversation"
    r"|(?:enter|start) (?:chat|conversation) mode"
    r")$"
)


class ChatModeHandler(Handler):
    """Enters the conversational chat mode for the current session.

    Only the ENTER surface lives here. EXIT phrases ("that's all", "stop",
    "never mind", "thanks goodbye") are handled by the streaming layer's
    conversational pre-check — by the time the user is in chat mode, the
    router is bypassed entirely, so an exit phrase never reaches a handler.
    """

    name = "chat_mode"
    # band rationale: clusters after dropin/intercom, well before media. Its enter regex
    #   is anchored on conversation-opener phrasing ("let's have a chat",
    #   "can we talk") with no bare "talk"/"chat", so it can't poach
    #   "play X" / "talk to the kitchen".
    priority_band = 220
    display = HandlerDisplay(label="Chat Mode", tone="comms")
    requires_network = "no"

    tool_schema = {
        "name": "chat_mode",
        "description": (
            "Enter an open-ended conversational chat mode where the user can "
            "talk back and forth with Domovoi without saying the wake word "
            "each turn. Use when the user explicitly asks to chat, talk, or "
            "have a conversation (e.g. 'let's have a chat', 'can we talk'). "
            "Do NOT use for one-off questions, music, or commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_ENTER_RE, ChatModeHandler._enter_from_match),
        ]

    # ─── Fast-path adapter ────────────────────────────────────────────
    async def _enter_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._enter(ctx, session)

    # ─── Entry points ─────────────────────────────────────────────────
    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._enter(ctx, session)

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._enter(ctx, session)

    # ─── Mode flip ────────────────────────────────────────────────────
    async def _enter(self, ctx: Context, session: AsyncSession) -> Response:
        """Flip the session into conversational mode and ask the streaming
        layer (via a context marker) to open the Pi's open mic.

        Writes ``conversational_mode`` + ``chat_start_pending`` into
        ``sessions.context``. Done here — not via a new ``Response`` field —
        because ``models.py`` (the matched_path Literal + Response fields) is
        owned by another agent for this feature, and the session-context JSONB
        already carries cross-turn flags (``pending_confirmation``).

        Honest about the off switch: if chat mode is disabled
        (``settings.chat_mode_enabled = False``, the default) we STILL flip the
        flag and send the ack/open-mic — the streaming layer's Letta dispatch
        falls back to the stub client when disabled, so the conversation simply
        runs against deterministic canned replies rather than wedging. That
        keeps the entry surface honest in tests (where stubs are forced) and on
        a deployment that hasn't brought Letta up yet.
        """
        if ctx.session_id is None:
            # No session (e.g. a direct /v1/intent call with no session) →
            # there's no live mic to put into open-mic mode, and nowhere to
            # persist the per-session flag for the next turn.
            return Response(
                text="Chat mode only works from a satellite.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # Conversational open-mic needs on-chip AEC or Domovoi's reply leaks
        # into the next capture. If we KNOW this room is a half-duplex board (it
        # reported supports_full_duplex=False in its hello frame), decline at
        # entry rather than open a mic the Pi will only refuse — and avoid the
        # stuck-mode case where a refusal desyncs the flag. Unknown rooms are
        # allowed (the Pi's own AEC gate + the inbound chat_end teardown are the
        # backstop).
        app = ctx.app
        if app is not None and ctx.room_id is not None:
            full_duplex = getattr(app.state, "satellite_full_duplex", {}) or {}
            if ctx.room_id in full_duplex and not full_duplex.get(ctx.room_id):
                return Response(
                    text=(
                        "Chat mode needs a satellite with echo cancellation, and "
                        "this room's mic can't do that yet."
                    ),
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )

        repo = SessionRepository(session)
        try:
            await repo.set_context_key(ctx.session_id, "conversational_mode", True)
            await repo.set_context_key(ctx.session_id, "chat_start_pending", True)
        except Exception as e:
            # If we can't park the mode flag, don't promise a chat the next
            # turn can't honor — speak a soft failure and stay in command mode.
            log.warning("couldn't enter chat mode: %s", e)
            return Response(
                text="I couldn't switch into chat mode just now.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        return Response(
            text="Sure, let's chat. What's on your mind?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            # The user just opened a conversation and the satellite is about to
            # enter open-mic — count this as a turn the Pi captures the reply to
            # without a fresh wake word. (Belt-and-suspenders: the chat_start
            # frame also opens the mic; expect_followup keeps the very first
            # reply window open even on clients that gate on it.)
            expect_followup=True,
        )
