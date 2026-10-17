"""Voice handler — list / sample / switch the satellite's TTS voice.

The core keeps a voice registry (``voices`` table): Piper
local models, each a spoken ``name``. Every
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
   that voice's clips. A room whose device keeps no voice of its own (a
   phone in room mode) has the choice kept on the server instead
   (``domovoi/room_voice.py``): same reply, no ``pi_action``.

5. **not downloaded yet** — the three voices Domovoi offers
   (``SHIPPED_VOICES``) are all in Settings → Voices, but a server that
   answered the internet question downloads only its own voice. Asked for
   one that isn't here ("let me hear Linda's voice"), the handler never
   says there is no such voice: it says she isn't downloaded yet and
   offers to download her when the internet answer allows it (a yes/no,
   ``core.voice_download``; "download Linda's voice" does it at once), or
   says why it can't and where she can be added. A shipped voice that is
   registered but not on disk, with no way to fetch it, gets the same
   answer instead of a sample "in her voice" spoken by the fallback.

``requires_network="no"`` — routing and every voice are local (the
Microsoft Edge cloud voices were dropped on 2026-10-12); only a download
needs the internet, and it is asked for, never assumed.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import egress
from domovoi.clients.tts import SHIPPED_VOICES, ShippedVoice, piper_voice_on_disk
from domovoi.confirmations import request_confirmation
from domovoi.db.repositories import VoicesRepository
from domovoi.handlers.base import (
    FastPath,
    Handler,
    HandlerDisplay,
    satellite_setting_refusal,
)
from domovoi import room_voice
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


# "download Linda's voice" / "get the Linda voice": a voice Domovoi offers
# that isn't on this server yet. "voice" must close it: "download the latest
# episode" is spoken audio's.
_DOWNLOAD_RE = re.compile(
    r"^(?:download|get|fetch|install|add) (?:the )?(.+?)(?:'s)? voice$"
)

_DOWNLOAD_KIND = "core.voice_download"
# Voice downloads this core started, by model_ref.
_DOWNLOADS: dict[str, asyncio.Task] = {}


def _shipped_by_name(name: str, *, fuzzy: bool) -> ShippedVoice | None:
    """The voice Domovoi offers that ``name`` names: exactly (any case),
    or, with ``fuzzy``, the closest one Whisper could have meant."""
    if not name:
        return None
    by_lower = {v.name.lower(): v for v in SHIPPED_VOICES}
    hit = by_lower.get(name.lower())
    if hit is not None or not fuzzy:
        return hit
    close = difflib.get_close_matches(name.lower(), list(by_lower), n=1, cutoff=0.6)
    return by_lower[close[0]] if close else None


def _pronouns(v: ShippedVoice) -> tuple[str, str]:
    return ("he", "him") if v.sex == "male" else ("she", "her")


def _megabytes(v: ShippedVoice) -> int:
    return max(1, round(v.onnx_bytes / 1_000_000))


def _downloading(model_ref: str) -> bool:
    task = _DOWNLOADS.get(model_ref)
    return task is not None and not task.done()


def _clean_name(raw: str) -> str:
    """Normalize a captured voice name: drop filler and a trailing
    'voice'/'’s voice'/punctuation so "ryan's voice" / "the aria voice"
    all resolve to the registered name."""
    s = raw.strip().strip(".?!,").strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = re.sub(r"(?:'s|’s)?\s+voice$", "", s)
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    return s.strip().strip("'’").strip()


def _capitalized(s: str) -> str:
    return s[:1].upper() + s[1:]


async def _download_voice(voice: ShippedVoice, app: Any, room_id: str | None) -> None:
    """Fetch ``voice`` (the TTS client's own checked download, in its
    downloads pool), register it, and say so in the room that asked."""
    from domovoi.clients import tts
    from domovoi.db.session import session_scope

    try:
        await tts.ensure_piper_voice(voice.model_ref)
        async with session_scope() as s:
            repo = VoicesRepository(s)
            rows = await repo.all()
            if not any(
                r["model_ref"] == voice.model_ref or r["name"].lower() == voice.name.lower()
                for r in rows
            ):
                await repo.create(name=voice.name, engine="piper", model_ref=voice.model_ref)
        said = f"{voice.name}'s voice is ready."
        log.info("voice download: %s is on this server", voice.model_ref)
    except Exception as e:  # noqa: BLE001 - said in the room, never raised
        log.warning("voice download: %s failed: %s", voice.model_ref, e)
        said = f"I couldn't download {voice.name}'s voice."
    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    target = sessions.get(room_id) if room_id else None
    if target is not None:
        try:
            await target.announce(said)
        except Exception as e:  # noqa: BLE001
            log.warning("voice download: couldn't tell room %s: %s", room_id, e)


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
    example_phrases = (
        "what voices are there",
        "let me hear Norman's voice",
    )
    requires_network = "no"
    confirmation_kinds = (_DOWNLOAD_KIND,)

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
            FastPath(_LIST_RE, VoiceHandler._list_from_match, early_commit="A"),
            FastPath(_CURRENT_RE, VoiceHandler._current_from_match, early_commit="A"),
            FastPath(_DOWNLOAD_RE, VoiceHandler._download_from_match),
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

    async def _download_from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        return await self._download_response(_clean_name(m.group(1)), ctx, session)

    # ─── Core responses ───────────────────────────────────────────────
    def _r(self, text: str, ctx: Context, **kw) -> Response:
        return Response(text=text, session_id=ctx.session_id, matched_handler=self.name, **kw)

    async def _list_response(self, ctx: Context, session: AsyncSession) -> Response:
        voices = await VoicesRepository(session).all()
        if not voices:
            return self._r("I don't have any voices set up yet.", ctx)
        names = [v["name"] for v in voices]
        text_ = f"I've got these voices: {_and_join(names)}."
        missing = self._not_here(voices)
        if missing:
            verb = "isn't" if len(missing) == 1 else "aren't"
            text_ += f" {_and_join([v.name for v in missing])} {verb} downloaded yet."
        return self._r(text_, ctx)

    @staticmethod
    def _not_here(voices: list[dict]) -> list[ShippedVoice]:
        """The voices Domovoi offers that this server's registry lacks."""
        refs = {v["model_ref"] for v in voices}
        names = {v["name"].lower() for v in voices}
        return [
            v for v in SHIPPED_VOICES
            if v.model_ref not in refs and v.name.lower() not in names
        ]

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
        voice, shipped = await self._resolve(name, session)
        if voice is None and shipped is not None:
            voice = await self._register_if_on_disk(shipped, session)
            if voice is None:
                return await self._not_downloaded(shipped, "sample", ctx, session)
        if voice is None:
            return await self._unknown_voice(name, ctx, session)
        stuck = self._stuck_off_disk(voice, ctx)
        if stuck is not None:
            return await self._not_downloaded(stuck, "sample", ctx, session)
        # voice_override makes the streaming layer synthesize THIS reply in
        # the sampled voice — the user hears it directly, no clip needed.
        return self._r(
            f"Here's how {voice['name']} sounds.",
            ctx,
            voice_override=voice["name"],
        )

    async def _switch_response(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        if ctx.from_phone:
            # The voice a satellite speaks in is kept on that satellite
            # (pi_action set_voice); a phone has none to switch. Listing,
            # "what voice" and a sample still work from a phone.
            return satellite_setting_refusal(self.name, ctx)
        voice, shipped = await self._resolve(name, session)
        if voice is None and shipped is not None:
            voice = await self._register_if_on_disk(shipped, session)
            if voice is None:
                return await self._not_downloaded(shipped, "switch", ctx, session)
        if voice is None:
            return await self._unknown_voice(name, ctx, session)
        stuck = self._stuck_off_disk(voice, ctx)
        if stuck is not None:
            return await self._not_downloaded(stuck, "switch", ctx, session)
        if room_voice.kept_by_server(ctx.app, ctx.room_id):
            # Its hello declared what it can do (`supports`) without
            # "voice" (a phone in room mode): it keeps no voice choice, so
            # the server keeps this room's and renders its speech in it
            # (domovoi/room_voice.py). Nothing for the device to do.
            await room_voice.choose(session, ctx.app, ctx.room_id, voice["name"])
            return self._r(
                f"Okay, I'm {voice['name']} now.",
                ctx,
                voice_override=voice["name"],
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

    # ─── Voices that aren't on this server yet ─────────────────────────
    @staticmethod
    def _can_download(ctx: Context) -> bool:
        return egress.internet_allowed() and ctx.online

    @staticmethod
    def _stuck_off_disk(voice: dict, ctx: Context) -> ShippedVoice | None:
        """A registered voice Domovoi offers whose files aren't here and
        can't be fetched now: speaking "in" it would be the fallback voice
        pretending. None when it can speak (on disk, or a download the
        reply can wait for)."""
        if voice.get("engine") != "piper":
            return None
        shipped = next((v for v in SHIPPED_VOICES if v.model_ref == voice.get("model_ref")), None)
        if shipped is None or piper_voice_on_disk(shipped.model_ref):
            return None
        return None if VoiceHandler._can_download(ctx) else shipped

    @staticmethod
    async def _register_if_on_disk(voice: ShippedVoice, session: AsyncSession) -> dict | None:
        """A voice whose files are here but whose registry row isn't (put
        there by hand, or by the installer before the registry knew it):
        register it now and speak in it. None when it isn't on disk."""
        if not piper_voice_on_disk(voice.model_ref):
            return None
        repo = VoicesRepository(session)
        await repo.create(name=voice.name, engine="piper", model_ref=voice.model_ref)
        return await repo.get_by_name(voice.name)

    async def _not_downloaded(
        self, voice: ShippedVoice, then: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """One of the voices Domovoi offers, not downloaded on this server:
        say so, and offer to get it when the internet answer allows."""
        she, her = _pronouns(voice)
        head = f"{voice.name} isn't downloaded on this server yet."
        if _downloading(voice.model_ref):
            return self._r(f"I'm still downloading {voice.name}. Ask again in a minute.", ctx)
        if egress.internet_turned_off():
            return self._r(
                f"{head} I'm set to stay off the internet, so I can't get {her}. "
                f"An admin can upload {her} voice files in Settings, Voices, on the dashboard.",
                ctx,
            )
        if not ctx.online:
            return self._r(
                f"{head} I can't reach the internet right now to get {her}. "
                "Ask me again when we're back online.",
                ctx,
            )
        try:
            await request_confirmation(
                session,
                ctx.session_id,
                kind=_DOWNLOAD_KIND,
                handler=self.name,
                data={"model_ref": voice.model_ref, "name": voice.name, "then": then},
            )
        except Exception as e:  # noqa: BLE001 - no session to park in
            log.warning("couldn't park the voice download question: %s", e)
            return self._r(
                f"{head} Say \"download {voice.name}'s voice\" to get {her}.", ctx
            )
        return self._r(
            f"{head} {_capitalized(she)}'s about {_megabytes(voice)} megabytes. "
            f"Want me to download {her}?",
            ctx,
            expect_followup=True,
        )

    async def _download_response(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        """"download Linda's voice": start it now (it was asked for)."""
        shipped = _shipped_by_name(name, fuzzy=True)
        if shipped is None:
            offered = _and_join([v.name for v in SHIPPED_VOICES])
            return self._r(f"I can download {offered}.", ctx)
        if piper_voice_on_disk(shipped.model_ref):
            return self._r(f"{shipped.name} is already on this server.", ctx)
        if not self._can_download(ctx) or _downloading(shipped.model_ref):
            return await self._not_downloaded(shipped, "sample", ctx, session)
        return self._start_download(shipped, "sample", ctx)

    async def handle_confirmation(
        self, kind: str, data: dict[str, Any], affirmative: bool, ctx: Context,
        session: AsyncSession,
    ) -> Response:
        shipped = next((v for v in SHIPPED_VOICES if v.model_ref == data.get("model_ref")), None)
        if not affirmative or shipped is None:
            return self._r("Okay.", ctx)
        then = str(data.get("then") or "sample")
        if piper_voice_on_disk(shipped.model_ref):
            return self._r(f"{shipped.name} is already on this server.", ctx)
        if not self._can_download(ctx) or _downloading(shipped.model_ref):
            return await self._not_downloaded(shipped, then, ctx, session)
        return self._start_download(shipped, then, ctx)

    def _start_download(self, voice: ShippedVoice, then: str, ctx: Context) -> Response:
        _DOWNLOADS[voice.model_ref] = asyncio.create_task(
            _download_voice(voice, ctx.app, ctx.room_id),
            name=f"voice-download-{voice.model_ref}",
        )
        ask = (
            f"switch to {voice.name}" if then == "switch"
            else f"let me hear {voice.name}'s voice"
        )
        return self._r(
            f"Okay, downloading {voice.name}. It takes a minute or two. Then say \"{ask}\".",
            ctx,
        )

    async def _unknown_voice(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        voices = await VoicesRepository(session).all()
        names = [v["name"] for v in voices]
        suffix = f" I have {_and_join(names)}." if names else ""
        spoken = name or "that"
        return self._r(f"I don't have a voice called {spoken}.{suffix}", ctx)

    @staticmethod
    async def _resolve(name: str, session: AsyncSession) -> tuple[dict | None, ShippedVoice | None]:
        """The registry row ``name`` means, or - when the registry hasn't
        got it - the voice Domovoi offers that it names (with that voice's
        row, if it is registered under another name). An exact registry
        name first, then an exact offered name, then the fuzzy matches in
        the same order: "Linda" is never taken for a registered "Lindsay"."""
        if not name:
            return None, None
        repo = VoicesRepository(session)
        exact = await repo.get_by_name(name)
        if exact is not None:
            return exact, None
        voices = await repo.all()
        by_ref = {v["model_ref"]: v for v in voices}
        shipped = _shipped_by_name(name, fuzzy=False)
        if shipped is not None:
            return by_ref.get(shipped.model_ref), shipped
        row = await VoiceHandler._lookup(name, session)
        if row is not None:
            return row, None
        shipped = _shipped_by_name(name, fuzzy=True)
        if shipped is not None:
            return by_ref.get(shipped.model_ref), shipped
        return None, None

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
