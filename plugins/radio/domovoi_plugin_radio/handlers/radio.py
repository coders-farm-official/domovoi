"""RadioHandler — the plugin's voice surface.

Fast paths (all tightly anchored to avoid poaching MusicHandler's greedy
``^play (.+)$`` at band 300):

  * ``stream X``            — "stream NPR", "stream the river"
  * ``play <freq> fm|am``    — "play 97.5 FM"
  * ``tune to X``            — "tune to KEXP"
  * ``stop streaming``       — terminate playback in the active room

# band rationale: 280 — the anchored-media range (270-349), between
# spoken_audio (270) and playlist (290), so "play 97.5 fm" is claimed
# before MusicHandler's catch-all (design §4.2, dossier §4 coupling 3).

``requires_network = "degraded"`` with per-path ``offline_ok`` (design
§4.3, audit M4 fix): internet-station paths (`stream`, `tune`) declare
``offline_ok=False`` so the router auto-falls-back to
:meth:`fallback_offline` while offline — which tries FM-via-SDR, the
half of the feature that genuinely works without internet. The
frequency and stop paths are ``offline_ok=True``.

Streaming flow:
  1. Resolve the spoken station name/freq to a ``radio_stations`` row.
  2. Online stations: hand ``stream_url`` to ``sdk.playback.play_url``
     (which stamps station-name-as-title on MPD — the convention the
     favorites matcher's FM reverse-matching depends on).
  3. FM stations (``source='fm'``): tune the SDR, then play its HTTP
     URL the same way.

Stop ordering (dossier §7 invariant 9): the SDR tuner stops BEFORE MPD.
If MPD stops first, the satellite disconnects from ffmpeg, which ffmpeg
sees as WSAECONNABORTED — its exit-watcher then logs "exited
unexpectedly" because it raced the explicit teardown. Tuner-first puts
the SDR pipeline through the clean cancel-bridge → terminate path, and
the subsequent MPD stop just confirms state after its source is gone.

Disambiguation: when "stream X" matches multiple candidates, park a
mediated pending confirmation (namespaced kind
``radio.station_choice``) and arm ``expect_followup`` so the user can
clarify without re-waking.

DB access: the router hands handlers a session whose search_path is the
core default, so every plugin-table reference below is schema-qualified
(``plugin_radio.radio_stations``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.sdk import (
    Context,
    FastPath,
    Handler,
    HandlerDisplay,
    Intent,
    PluginSDK,
    Response,
)

from domovoi_plugin_radio import SCHEMA

log = logging.getLogger(__name__)


# Tightly anchored. Every pattern leads with a distinctive verb — never
# a bare "play X" (MusicHandler's territory). The optional literal
# articles double as the second anchor word the §4.2 greedy-catch-all
# rule requires below band 900.
_STREAM_RE = re.compile(r"^stream (?:the )?(.+?)$")
_STOP_STREAM_RE = re.compile(
    r"^(?:stop|quit|pause|kill) (?:the )?(?:radio|stream|streaming)$"
)
# 2-4 digits to accommodate AM phrasings (530-1700). The handler refuses
# AM with a message, but the regex must match so the user hears that
# message instead of falling through to MusicHandler's `play X`.
_PLAY_FREQUENCY_RE = re.compile(r"^play (\d{2,4}(?:\.\d)?)\s?(fm|am)$")
_TUNE_RE = re.compile(r"^tune (?:to |in to |in )?(.+)$")

_STATION_CHOICE_KIND = "radio.station_choice"


class RadioHandler(Handler):
    name = "radio"
    priority_band = 280
    chat_exposed = True          # organic media tool in chat mode
    # Online streams need the network; FM-via-SDR doesn't — per-path
    # offline_ok below carries the distinction.
    requires_network = "degraded"
    display = HandlerDisplay(
        label="Radio", tone="media", icon="web/static/icon.svg"
    )
    confirmation_kinds = (_STATION_CHOICE_KIND,)

    tool_schema = {
        "name": "radio",
        "description": (
            "Stream an internet radio station to the current room, or "
            "tune an FM frequency via the SDR receiver. Use this when "
            "the user asks for a station name (e.g. 'NPR', 'KEXP') or "
            "a frequency (e.g. '97.5 FM'). Different from music — radio "
            "is live continuous audio, not on-demand tracks. "
            "Example: 'stream the jazz station'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["stream", "stop", "tune_frequency"],
                },
                "query": {
                    "type": "string",
                    "description": "Station name when action=stream/tune_frequency.",
                },
                "frequency_mhz": {
                    "type": "number",
                    "description": "FM frequency when action=tune_frequency.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self, sdk: PluginSDK) -> None:
        self.sdk = sdk
        self.fast_paths = [
            # Stop works offline (SDR + MPD are both local).
            FastPath(_STOP_STREAM_RE, RadioHandler._stop_from_match,
                     offline_ok=True),
            # Frequency = FM-via-SDR = fully local.
            FastPath(_PLAY_FREQUENCY_RE, RadioHandler._frequency_from_match,
                     offline_ok=True),
            # Name resolution targets internet stations first — offline
            # these auto-fallback to the FM-only resolver.
            FastPath(_STREAM_RE, RadioHandler._stream_from_match,
                     offline_ok=False),
            FastPath(_TUNE_RE, RadioHandler._tune_from_match,
                     offline_ok=False),
        ]

    @property
    def _config(self) -> Any:
        return self.sdk.config

    def _tuner(self) -> Any | None:
        """The live SdrTuner (or None) — shared via the plugin's
        namespaced state slice, set by core.register()."""
        return self.sdk.state.get("sdr_tuner")

    # ─── Fast-path adapters ─────────────────────────────────────────────

    async def _stream_from_match(self, m, ctx, session):
        return await self._stream_by_name(m.group(1).strip(), ctx, session)

    async def _tune_from_match(self, m, ctx, session):
        return await self._stream_by_name(m.group(1).strip(), ctx, session)

    async def _frequency_from_match(self, m, ctx, session):
        try:
            freq = float(m.group(1))
        except ValueError:
            return self._reply(ctx, "I didn't catch that frequency.")
        band = m.group(2).lower()
        if band == "am":
            return self._reply(ctx, "I can't tune AM yet — only FM.")
        return await self._stream_by_frequency(freq, ctx, session)

    async def _stop_from_match(self, m, ctx, session):
        return await self._stop_stream(ctx)

    # ─── Public entry points (execute_from_tool shares these) ──────────

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        # Routed here without a fast-path match (tool call / QA hint) —
        # best-effort treat the whole transcript as a station query.
        return await self._stream_by_name(intent.transcript.strip(), ctx, session)

    async def execute_from_tool(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "stop":
            return await self._stop_stream(ctx)
        if action == "tune_frequency":
            freq = args.get("frequency_mhz")
            if not isinstance(freq, (int, float)):
                return self._reply(ctx, "I need a frequency in MHz.")
            return await self._stream_by_frequency(float(freq), ctx, session)
        query = (args.get("query") or "").strip()
        if not query:
            return self._reply(ctx, "Which station?")
        return await self._stream_by_name(query, ctx, session)

    async def fallback_offline(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        """Offline path — genuinely reachable now that the internet
        fast paths declare ``offline_ok=False`` (audit M4 fix). We may
        still be able to tune FM via the SDR: try the frequency parse,
        then FM-only name resolution."""
        transcript = intent.transcript.strip().lower()
        m = _PLAY_FREQUENCY_RE.match(transcript)
        if m and m.group(2).lower() == "fm":
            try:
                freq = float(m.group(1))
                return await self._stream_by_frequency(freq, ctx, session)
            except ValueError:
                pass
        return await self._stream_by_name(
            transcript.replace("stream", "").replace("tune to", "").strip()
            or transcript,
            ctx,
            session,
            fm_only=True,
        )

    # ─── handle_confirmation (multi-turn disambig) ──────────────────────

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Radio disambig isn't yes/no — it's "which one?". When the
        user answers with a station fragment ("the KEXP one"), the
        router's yes/no pre-empt doesn't fire and the transcript
        re-routes through our fast paths naturally. When they DO answer
        with a clean yes token, treat it as "the first candidate,
        please" — the intent "yes" usually means here."""
        if kind != _STATION_CHOICE_KIND:
            return self._reply(ctx, "I'm not sure what you're confirming.")
        candidates = data.get("candidates") or []
        if not candidates:
            return self._reply(ctx, "I lost track of those stations — say it again?")
        if affirmative:
            return await self._stream_station_row(candidates[0], ctx, session)
        return self._reply(ctx, "OK, never mind.")

    # ─── Internal resolution ────────────────────────────────────────────

    async def _stream_by_name(
        self,
        query: str,
        ctx: Context,
        session: AsyncSession,
        *,
        fm_only: bool = False,
    ) -> Response:
        """Look up favorited stations matching ``query``. One hit →
        stream. Multiple → park a station_choice confirmation. Zero →
        polite pointer at the dashboard."""
        if not query:
            return self._reply(ctx, "Which station?")

        like = f"%{query.lower()}%"
        source_clause = "AND source = 'fm'" if fm_only else ""
        result = await session.execute(
            text(
                f"""
                SELECT id, name, source, stream_url, frequency_mhz, call_sign
                FROM {SCHEMA}.radio_stations
                WHERE favorited
                  {source_clause}
                  AND (LOWER(name) LIKE :like
                       OR LOWER(COALESCE(call_sign, '')) LIKE :like)
                ORDER BY (LOWER(name) = LOWER(:exact)) DESC, name
                LIMIT 5
                """
            ),
            {"like": like, "exact": query.lower()},
        )
        candidates = [
            {
                "id": int(r[0]),
                "name": r[1],
                "source": r[2],
                "stream_url": r[3],
                "frequency_mhz": float(r[4]) if r[4] is not None else None,
                "call_sign": r[5],
            }
            for r in result.all()
        ]

        if not candidates:
            return self._reply(
                ctx,
                f"I don't have a favorited station called {query}. "
                "Favorite one in the dashboard first.",
            )

        if len(candidates) == 1:
            return await self._stream_station_row(candidates[0], ctx, session)

        # Multi-candidate: park the namespaced confirmation, ask.
        names = " or ".join(c["name"] for c in candidates[:2])
        prompt = f"I have {len(candidates)} matches — {names}?"
        try:
            await self.sdk.sessions.request_confirmation(
                session,
                ctx.session_id or ctx.room_id,
                kind=_STATION_CHOICE_KIND,
                handler=self.name,
                data={"candidates": candidates, "room_id": ctx.room_id},
                prompt=prompt,
            )
        except Exception as e:
            log.warning("radio: couldn't park pending confirmation: %s", e)
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _stream_by_frequency(
        self, frequency_mhz: float, ctx: Context, session: AsyncSession
    ) -> Response:
        """Resolve an FM frequency through the configured market.

        State is a hard filter (a local dongle can't tune out-of-state
        signals); city is a *preference* — an in-state match still
        resolves when the configured city has no station on the
        frequency. City comparison is case-insensitive (FCC stores
        upper-case, users configure title case).
        """
        market_clause = ""
        city_preference_order = ""
        params: dict[str, Any] = {"freq": frequency_mhz}
        if self._config.market_state:
            market_clause = "AND market_state = :ms"
            params["ms"] = self._config.market_state.upper()
        if self._config.market_city:
            city_preference_order = (
                "CASE WHEN UPPER(market_city) = UPPER(:mc) THEN 0 ELSE 1 END,"
            )
            params["mc"] = self._config.market_city

        # `:freq` binds as a Python float; asyncpg refuses to compare it
        # directly with the NUMERIC(5,1) column. Cast to numeric so 90.3
        # (float) compares as 90.3 (numeric) — WITHOUT this cast every
        # FM frequency lookup silently misses.
        result = await session.execute(
            text(
                f"""
                SELECT id, name, source, stream_url, frequency_mhz, call_sign
                FROM {SCHEMA}.radio_stations
                WHERE source = 'fm'
                  AND frequency_mhz = (:freq)::numeric(5,1)
                  {market_clause}
                ORDER BY {city_preference_order} favorited DESC, id
                LIMIT 1
                """
            ),
            params,
        )
        row = result.first()
        if row is None:
            return self._reply(
                ctx,
                f"I don't know {frequency_mhz} FM in your market. "
                "Run the FCC import in the dashboard to load local stations.",
            )
        station = {
            "id": int(row[0]),
            "name": row[1],
            "source": row[2],
            "stream_url": row[3],
            "frequency_mhz": float(row[4]) if row[4] is not None else None,
            "call_sign": row[5],
        }
        return await self._stream_station_row(station, ctx, session)

    async def _stream_station_row(
        self, station: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        """Hand the resolved station's URL to the playback SDK. Online
        stations use the persisted stream_url; FM routes through the SDR
        tuner first."""
        source = station.get("source")
        url_to_play: str | None = None

        if source == "online":
            url_to_play = station.get("stream_url")
            if not url_to_play:
                return self._reply(
                    ctx, f"{station['name']} doesn't have a stream URL on file."
                )

        elif source == "fm":
            tuner = self._tuner()
            if tuner is None:
                return self._reply(
                    ctx,
                    f"{station['name']} is FM, but the FM tuner isn't enabled "
                    "right now. Plug in the SDR dongle and set "
                    "RADIO_SDR_ENABLED=true, then try again.",
                )
            freq = station.get("frequency_mhz")
            if not isinstance(freq, (int, float)):
                return self._reply(
                    ctx, f"{station['name']} has no frequency on file."
                )
            try:
                url_to_play = await tuner.tune(float(freq))
            except Exception as e:
                log.warning("sdr tune to %s failed: %s", freq, e)
                return self._reply(
                    ctx, f"The FM tuner couldn't tune {freq} MHz right now."
                )
        else:
            return self._reply(
                ctx, f"I don't know how to play a {source!r} source."
            )

        # One-call playback (design §9.4): prepare_url + MPD
        # title/artist stamping (station-name-as-title — favorites FM
        # reverse-matching depends on it) + the now-playing stamp +
        # media_plays history + the hardened music_start handshake.
        response = await self.sdk.playback.play_url(
            ctx.room_id,
            url_to_play,
            title=station.get("name") or "radio",
            artist=station.get("call_sign"),
            source="radio",
            now_playing_data={
                "station_id": station.get("id"),
                "station_name": station.get("name"),
                "stream_url": url_to_play,
            },
        )
        if response.music_action != "start":
            # MPD refused the stream — play_url already phrased a
            # fallback; keep the canonical wording.
            return self._reply(
                ctx,
                f"I found {station['name']} but the music player wouldn't take it.",
            )
        response.text = f"Streaming {station['name']}."
        response.session_id = ctx.session_id
        response.matched_handler = self.name
        response.data = {"station": station, **response.data}
        return response

    async def _stop_stream(self, ctx: Context) -> Response:
        """Tear down playback in the originating room, SDR tuner FIRST
        (see module docstring — invariant 9), then MPD via the playback
        SDK. Idempotent."""
        tuner = self._tuner()
        if tuner is not None:
            try:
                await tuner.stop()
            except Exception as e:
                log.warning("sdr tuner stop failed: %s", e)
        try:
            response = await self.sdk.playback.stop(ctx.room_id)
        except Exception as e:
            log.warning("playback stop failed: %s", e)
            response = Response(text="Stopped.", music_action="stop")
        response.text = "Stopped."
        response.session_id = ctx.session_id
        response.matched_handler = self.name
        return response

    # ─── Helpers ────────────────────────────────────────────────────────

    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
