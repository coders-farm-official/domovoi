"""Voice handler — list / sample / switch the satellite's TTS voice.

The core keeps a voice registry (``voices`` table): Piper
local models and optional Edge cloud voices, each a spoken ``name``. Every
satellite speaks in one of them (reported via ``voice_status``; see
``streaming``). This handler is the voice-command surface for it:

1. **list** ("what voices are there", "list voices") — speak the
   registered names.
2. **current** ("what voice are you using") — the room's current voice
   (``ctx.voice``), or the registry default.
3. **sample** ("let me hear Ryan", "what does Aria sound like") — reply
   with ``voice_override=<name>`` so the streaming layer synthesizes the
   sample line in THAT voice on the fly. No need for the Pi to hold other
   voices' clips.
4. **switch** ("switch to Ryan", "use the Aria voice") — reply with
   ``voice_override=<name>`` (so the confirmation is spoken in the NEW
   voice immediately) plus ``pi_action="set_voice"`` + ``pi_action_arg``
   so the satellite persists the selection, re-reports it, and re-syncs
   that voice's clips.

``requires_network="no"`` — routing is fully local. Sampling/switching an
*Edge* voice does need network, so those paths check ``ctx.online`` and
degrade gracefully ("that's a cloud voice and I'm offline") rather than
producing silence.
"""

from __future__ import annotations

import difflib
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import VoicesRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────
# Router lower-cases + strips trailing punctuation before dispatching.

_LIST_RE = re.compile(
    r"^(?:"
    r"(?:what|which) voices (?:are (?:there|available)|do you have|can you (?:use|do|speak))"
    r"|list (?:the |your |all (?:the )?)?voices"
    r"|what (?:other )?voices"
    r"|tell me (?:the |your )?voices"
    r")$"
)

_CURRENT_RE = re.compile(
    r"^(?:"
    r"(?:what|which) voice are you (?:using|speaking (?:in|with)|in)"
    r"|what(?:'s| is) your (?:current )?voice"
    r"|who(?:'s| is) (?:talking|speaking)(?: right now)?"
    r")$"
)

# Capture the requested voice name. Trailing "voice"/"'s voice" and filler
# are stripped by _clean_name. The "hear" form requires the word "voice" or
# "sounds like" so it doesn't poach unrelated "let me hear the news".
_SAMPLE_RE = re.compile(
    r"^(?:"
    r"(?:can i |let me |let's |i want to |i wanna )?hear (?:the )?(.+?)(?:'s)? voice"
    r"|(?:can i |let me )?hear (?:what )?(.+?) sounds? like"
    r"|sample (?:the )?(.+?)(?: voice)?"
    r"|what does (.+?) sound like"
    r"|how does (.+?) sound"
    r"|preview (?:the )?(.+?)(?: voice)?"
    r")$"
)

# The "use" form requires the word "voice"; bare "switch to X" / "sound
# like X" / "talk like X" are voice-specific enough in this context.
_SWITCH_RE = re.compile(
    r"^(?:"
    r"(?:please )?(?:switch|change|set) (?:your |the )?voice to (.+)"
    r"|(?:please )?switch to (.+)"
    r"|use (?:the )?(.+?)(?:'s)? voice"
    r"|(?:talk|speak) (?:like|in|as) (.+)"
    r"|sound like (.+)"
    r")$"
)


def _clean_name(raw: str) -> str:
    """Normalize a captured voice name: drop filler and a trailing
    'voice'/'’s voice'/punctuation so "ryan's voice" / "the aria voice"
    all resolve to the registered name."""
    s = raw.strip().strip(".?!,").strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = re.sub(r"(?:'s|’s)?\s+voice$", "", s)
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    return s.strip().strip("'’").strip()


def _and_join(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


class VoiceHandler(Handler):
    name = "voice"
    # band rationale: device-control cluster near the top. Regexes are anchored on voice
    #   vocabulary ("what voices...", "switch to X", "let me hear X's voice")
    #   so they won't poach unrelated phrasing, but sitting ahead of the
    #   media bands keeps "switch to X" / "use the X voice" clear of their
    #   greedy catch-alls.
    priority_band = 130
    display = HandlerDisplay(label="Voice", tone="device")
    requires_network = "no"

    tool_schema = {
        "name": "voice",
        "description": (
            "List the available TTS voices, report which voice is in use, "
            "play a sample of a voice, or switch the satellite to a different "
            "voice. Use 'list' for 'what voices are there', 'current' for "
            "'what voice are you using', 'sample' for 'let me hear X' / 'what "
            "does X sound like', and 'switch' for 'switch to X' / 'use the X "
            "voice'. The 'voice' parameter is the voice name for sample/switch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "current", "sample", "switch"],
                },
                "voice": {
                    "type": "string",
                    "description": "Voice name, required for sample and switch.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_LIST_RE, VoiceHandler._list_from_match),
            FastPath(_CURRENT_RE, VoiceHandler._current_from_match),
            FastPath(_SAMPLE_RE, VoiceHandler._sample_from_match),
            FastPath(_SWITCH_RE, VoiceHandler._switch_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._list_response(ctx, session)

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "list":
            return await self._list_response(ctx, session)
        if action == "current":
            return await self._current_response(ctx, session)
        if action == "sample":
            return await self._sample_response(_clean_name(args.get("voice") or ""), ctx, session)
        if action == "switch":
            return await self._switch_response(_clean_name(args.get("voice") or ""), ctx, session)
        return await self._list_response(ctx, session)

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _list_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._list_response(ctx, session)

    async def _current_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._current_response(ctx, session)

    async def _sample_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        name = _clean_name(next((g for g in m.groups() if g), ""))
        return await self._sample_response(name, ctx, session)

    async def _switch_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        name = _clean_name(next((g for g in m.groups() if g), ""))
        return await self._switch_response(name, ctx, session)

    # ─── Core responses ───────────────────────────────────────────────
    def _r(self, text: str, ctx: Context, **kw) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name, **kw)

    async def _list_response(self, ctx: Context, session: AsyncSession) -> Response:
        voices = await VoicesRepository(session).all()
        if not voices:
            return self._r("I don't have any voices set up yet.", ctx)
        names = [v["name"] for v in voices]
        return self._r(f"I've got these voices: {_and_join(names)}.", ctx)

    async def _current_response(self, ctx: Context, session: AsyncSession) -> Response:
        repo = VoicesRepository(session)
        current = None
        if ctx.voice:
            current = await repo.get_by_name(ctx.voice)
        if current is None:
            current = await repo.get_default()
        if current is None:
            return self._r("I don't have a voice set up yet.", ctx)
        return self._r(f"I'm using {current['name']} right now.", ctx)

    async def _sample_response(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        voice = await self._lookup(name, session)
        if voice is None:
            return await self._unknown_voice(name, ctx, session)
        if voice["engine"] == "edge" and not ctx.online:
            return self._r(
                f"{voice['name']} is a cloud voice and I'm offline right now, "
                f"so I can't play it.",
                ctx,
            )
        # voice_override makes the streaming layer synthesize THIS reply in
        # the sampled voice — the user hears it directly, no clip needed.
        return self._r(
            f"Here's how {voice['name']} sounds.",
            ctx,
            voice_override=voice["name"],
        )

    async def _switch_response(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        voice = await self._lookup(name, session)
        if voice is None:
            return await self._unknown_voice(name, ctx, session)
        if voice["engine"] == "edge" and not ctx.online:
            return self._r(
                f"{voice['name']} is a cloud voice and I'm offline right now, "
                f"so I can't switch to it. Try a local voice.",
                ctx,
            )
        # Confirmation is spoken in the NEW voice (voice_override) so the
        # user hears the change immediately; pi_action persists it Pi-side
        # and re-reports it for subsequent turns.
        return self._r(
            f"Okay, I'm {voice['name']} now.",
            ctx,
            voice_override=voice["name"],
            pi_action="set_voice",
            pi_action_arg=voice["name"],
        )

    async def _unknown_voice(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        voices = await VoicesRepository(session).all()
        names = [v["name"] for v in voices]
        suffix = f" I have {_and_join(names)}." if names else ""
        spoken = name or "that"
        return self._r(f"I don't have a voice called {spoken}.{suffix}", ctx)

    @staticmethod
    async def _lookup(name: str, session: AsyncSession):
        """Resolve a spoken voice name to a registry row. Exact
        (case-insensitive) match first, then a fuzzy fallback — voice names
        are proper nouns Whisper mangles into near-homophones ("Aria" →
        "area", "Ryan" → "Ron"), and an exact-only match would reject those
        even though the user clearly meant a registered voice."""
        if not name:
            return None
        repo = VoicesRepository(session)
        exact = await repo.get_by_name(name)
        if exact is not None:
            return exact
        voices = await repo.all()
        if not voices:
            return None
        by_lower = {v["name"].lower(): v for v in voices}
        close = difflib.get_close_matches(name.lower(), list(by_lower), n=1, cutoff=0.6)
        if close:
            log.info("voice lookup: fuzzy-matched %r → %r", name, by_lower[close[0]]["name"])
            return by_lower[close[0]]
        return None
