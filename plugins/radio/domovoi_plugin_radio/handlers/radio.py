"""RadioHandler — the plugin's voice surface.

Fast paths (all tightly anchored to avoid poaching MusicHandler's greedy
``^play (.+)$`` at band 300):

  * ``stream X``            — "stream NPR", "stream the river"
  * ``tune to X``            — "tune to KEXP"
  * ``play the station X``   — "play the station lofi 24/7", "play radio station KEXP"
  * ``play X radio``         — "play lofi radio", "play the jazz station"
  * ``play radio X``         — "play radio paradise" (saved stations only;
                               otherwise it is left to the rest of the router,
                               because "play radio ga ga" is a song)
  * ``play <freq> fm|am``    — "play 97.5 FM"
  * ``stop streaming``       — terminate playback in the active room
  * ``add this to favorites`` / ``favorite this station`` — star the
    station playing in this room (a "add this to favorites" with no station
    playing is left to the rest of the router)

# band rationale: 280 — the anchored-media range (270-349), between
# spoken_audio (270) and playlist (290), so "play 97.5 fm" is claimed
# before MusicHandler's catch-all (design §4.2, dossier §4 coupling 3).

``requires_network = "degraded"`` with per-path ``offline_ok`` (design
§4.3, audit M4 fix): station-name paths declare ``offline_ok=False`` so
the router auto-falls-back to :meth:`fallback_offline` while offline —
which tries FM-via-SDR, the half of the feature that genuinely works
without internet. The frequency, stop and favorite paths are
``offline_ok=True``.

Finding a station by name (:meth:`RadioHandler._stream_by_name`), in order:

  1. the household's favorites, compared by how names are SAID
     (:mod:`domovoi_plugin_radio.station_match`: "lo-fi 24 slash 7" is
     "Lofi 24/7"; "low 5 24-7" is close enough to ask about);
  2. every other station in the plugin's table (played before, imported
     from the FCC list);
  3. the internet station directory — only when the household lets
     Domovoi on the internet (``egress``) and the box is online, and only
     for a request that is clearly for a station.

A sure match plays. A near one is asked about ("Did you mean Lofi 24/7?"),
two close ones are offered ("Lofi 24/7 or Lofi Hip Hop?"), and a directory
search offers its best one or two. The question is parked as the
namespaced choice ``radio.station_choice`` (SDK 1.10 ``choice_kinds``), so
the reply may be "yes", "the second one", "no", or a name, read with the
core's own reader (``domovoi.sdk.choices``). Nothing is asked where nobody
can answer (``Context.answerable`` False: the dashboard's play box).

Streaming flow:
  1. Resolve the spoken station name/freq to a ``radio_stations`` row
     (a directory pick is saved as a played-once row first, the same row
     the Stations page's "play" writes, so it shows under Recent and
     "add this to favorites" can star it).
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

DB access: the router hands handlers a session whose search_path is the
core default, so every plugin-table reference below is schema-qualified
(``plugin_radio.radio_stations``).
"""

from __future__ import annotations

import logging
import re
import time
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
    egress,
    net_safety,
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
# "play the station lofi 24/7", "play radio station kexp", "play the radio
# station called the river".
_PLAY_STATION_RE = re.compile(
    r"^play (?:the |a )?(?:radio )?station,? (?:called |named )?(.+)$"
)
# "play lofi radio", "play the jazz station", "play kexp radio station".
_PLAY_X_RADIO_RE = re.compile(
    r"^play (?:the )?(.+?) (?:radio station|radio|station)$"
)
# "play radio paradise": saved stations only (see the module docstring).
_PLAY_RADIO_X_RE = re.compile(r"^play (?:the )?radio,? (.+)$")
# "add this to my favorites" — whatever is playing; declined when it isn't
# a station, so it stays free for anything else that plays.
_FAVORITE_THIS_RE = re.compile(
    r"^(?:add|put) (?:this|that|it)(?: one)? to (?:my |our |the )?"
    r"(?:radio )?favou?rites(?: list)?$"
)
# "favorite this station", "add this station to my favorites".
_FAVORITE_STATION_RE = re.compile(
    r"^(?:(?:favou?rite|star) (?:this|that|the) (?:radio )?station"
    r"|(?:add|put) (?:this|that|the) (?:radio )?station to (?:my |our |the )?"
    r"(?:radio )?favou?rites(?: list)?)$"
)

_STATION_CHOICE_KIND = "radio.station_choice"
#: How long a "did you mean …?" stays answerable, in seconds.
_CHOICE_TTL_SEC = 60.0
#: Most directory hits offered by voice.
_OFFERED = 2
#: Most non-favorite rows read for a name lookup (an FCC import is a few
#: thousand at most; the names are narrowed in memory before scoring).
_OTHERS_SCAN = 20000
_OTHERS_SHORTLIST = 25

_STATION_COLUMNS = (
    "id, name, source, stream_url, frequency_mhz, call_sign, favorited, external_id"
)


def _station(r: Any) -> dict[str, Any]:
    return {
        "id": int(r[0]),
        "name": r[1],
        "source": r[2],
        "stream_url": r[3],
        "frequency_mhz": float(r[4]) if r[4] is not None else None,
        "call_sign": r[5],
        "favorited": bool(r[6]),
        "external_id": r[7],
    }


def _short_name(name: str | None) -> str:
    """A directory name as said back: the part before a " - " or " | "
    tagline ("Lofi Girl - beats to relax/study to" → "Lofi Girl")."""
    head = re.split(r"\s+[-|–—:]\s+", (name or "").strip())[0].strip()
    return (head or (name or "").strip())[:60]


def _either(names: list[str]) -> str:
    return " or ".join(names)


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
    # SDK 1.10: the reply to "did you mean …?" may be free text.
    choice_kinds = (_STATION_CHOICE_KIND,)

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
            # Starring what plays is a local write.
            FastPath(_FAVORITE_STATION_RE, RadioHandler._favorite_station_from_match,
                     offline_ok=True),
            FastPath(_FAVORITE_THIS_RE, RadioHandler._favorite_this_from_match,
                     offline_ok=True),
            # Name resolution reaches for internet stations — offline
            # these auto-fallback to the FM-only resolver.
            FastPath(_STREAM_RE, RadioHandler._stream_from_match,
                     offline_ok=False),
            FastPath(_TUNE_RE, RadioHandler._tune_from_match,
                     offline_ok=False),
            FastPath(_PLAY_STATION_RE, RadioHandler._play_station_from_match,
                     offline_ok=False),
            FastPath(_PLAY_X_RADIO_RE, RadioHandler._play_x_radio_from_match,
                     offline_ok=False),
            FastPath(_PLAY_RADIO_X_RE, RadioHandler._play_radio_x_from_match,
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

    async def _play_station_from_match(self, m, ctx, session):
        return await self._stream_by_name(
            m.group(1).strip(), ctx, session, decline_if_vague=True
        )

    async def _play_x_radio_from_match(self, m, ctx, session):
        return await self._stream_by_name(
            m.group(1).strip(), ctx, session, decline_if_vague=True
        )

    async def _play_radio_x_from_match(self, m, ctx, session):
        # "play radio ga ga" is a song: only a saved station answers here.
        return await self._stream_by_name(
            m.group(1).strip(), ctx, session, directory=False,
            decline_if_vague=True, decline_if_none=True, also=m.string[5:],
        )

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

    async def _favorite_station_from_match(self, m, ctx, session):
        return await self._favorite_now_playing(ctx, session, declinable=False)

    async def _favorite_this_from_match(self, m, ctx, session):
        return await self._favorite_now_playing(ctx, session, declinable=True)

    # ─── Public entry points (execute_from_tool shares these) ──────────

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        # Routed here without a fast-path match (tool call / QA hint) —
        # best-effort treat the whole transcript as a station query.
        transcript = intent.transcript.strip()
        query = _name_query(transcript.lower()) or transcript
        return await self._stream_by_name(query, ctx, session)

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
        query = _name_query(transcript) or transcript
        return await self._stream_by_name(
            query, ctx, session, fm_only=True, directory=False
        )

    # ─── The parked question (a choice, SDK 1.10) ────────────────────────

    async def handle_choice_reply(
        self,
        kind: str,
        data: dict[str, Any],
        transcript: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """The reply to "Did you mean Lofi 24/7?" / "Lofi 24/7 or Lofi Hip
        Hop?" / an offer of directory stations: yes or an ordinal plays
        that one, no drops it, a name plays the offered station it names
        (or a saved station it names surely). Anything else — a new
        command, a question — returns None and is routed as a new turn."""
        if kind != _STATION_CHOICE_KIND:
            return None
        candidates = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
        if not candidates or _expired(data):
            return None
        from domovoi.sdk import choices

        from domovoi_plugin_radio import station_match as sm

        reply = choices.parse_choice_reply(transcript, len(candidates))
        if reply.action == "pick":
            return await self._play_candidate(candidates[reply.index], ctx, session)
        if reply.action == "decline":
            return self._reply(ctx, "OK.")
        if reply.action != "name":
            return None
        # A name: one of the stations just offered?
        ranked = sm.rank(reply.name, candidates)
        if ranked:
            best = ranked[0].station
            turned_down = reply.negated and len(candidates) == 1
            if not turned_down:
                return await self._play_candidate(best, ctx, session)
        # Another saved station it names for sure ("no, the jazz one").
        decision = await self._resolve_saved(reply.name, session, fm_only=False)
        if decision.action == "play":
            return await self._stream_station_row(dict(decision.stations[0]), ctx, session)
        if reply.otherwise == "pick":
            return await self._play_candidate(candidates[0], ctx, session)
        if reply.otherwise == "decline":
            return self._reply(ctx, "OK.")
        return None

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """A bare yes/no to the parked question when the choice reader
        was not reached (a question parked by an older plugin version):
        yes plays the first station offered."""
        if kind != _STATION_CHOICE_KIND:
            return self._reply(ctx, "I'm not sure what you're confirming.")
        candidates = data.get("candidates") or []
        if not candidates:
            return self._reply(ctx, "I lost track of those stations — say it again?")
        if affirmative:
            return await self._play_candidate(candidates[0], ctx, session)
        return self._reply(ctx, "OK, never mind.")

    # ─── Name resolution ────────────────────────────────────────────────

    async def _load_favorites(
        self, session: AsyncSession, *, fm_only: bool
    ) -> list[dict[str, Any]]:
        source_clause = "AND source = 'fm'" if fm_only else ""
        rows = await session.execute(
            text(
                f"SELECT {_STATION_COLUMNS} FROM {SCHEMA}.radio_stations "
                f"WHERE favorited {source_clause} ORDER BY name, id"
            )
        )
        return [_station(r) for r in rows.all()]

    async def _load_others(
        self, query: str, session: AsyncSession, *, fm_only: bool
    ) -> list[dict[str, Any]]:
        """The non-favorite stations whose names could be ``query``: every
        name read (id, name, call sign only), narrowed in memory to a short
        list by spelling and call sign, then the short list's rows."""
        from rapidfuzz import fuzz, process

        from domovoi_plugin_radio import station_match as sm

        source_clause = "AND source = 'fm'" if fm_only else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT id, name, call_sign FROM {SCHEMA}.radio_stations "
                    f"WHERE NOT favorited {source_clause} "
                    "ORDER BY last_played_at DESC NULLS LAST, id LIMIT :n"
                ),
                {"n": _OTHERS_SCAN},
            )
        ).all()
        if not rows:
            return []

        def key(s: str | None) -> str:
            return re.sub(r"[^a-z0-9]+", "", sm.radio_text(s))

        q = key(query)
        if not q:
            return []
        ids: list[int] = [
            int(r[0]) for r in rows if r[2] and key(r[2]) == q
        ]
        names = {int(r[0]): key(r[1]) for r in rows}
        for _, _, rid in process.extract(
            q, names, scorer=fuzz.partial_ratio, limit=_OTHERS_SHORTLIST,
            score_cutoff=60,
        ):
            ids.append(int(rid))
        if not ids:
            return []
        full = await session.execute(
            text(
                f"SELECT {_STATION_COLUMNS} FROM {SCHEMA}.radio_stations "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": list(dict.fromkeys(ids))},
        )
        return [_station(r) for r in full.all()]

    async def _resolve_saved(
        self, query: str, session: AsyncSession, *, fm_only: bool
    ) -> Any:
        """Favorites first; then the other stations in the table. A sure
        match among the others beats a mere "did you mean" among the
        favorites."""
        from domovoi_plugin_radio import station_match as sm

        favorites = sm.decide(
            sm.rank(query, await self._load_favorites(session, fm_only=fm_only))
        )
        if favorites.action in ("play", "choose"):
            return favorites
        others = sm.decide(
            sm.rank(query, await self._load_others(query, session, fm_only=fm_only))
        )
        if others.action == "play" or favorites.action == "none":
            return others
        return favorites

    async def _stream_by_name(
        self,
        query: str,
        ctx: Context,
        session: AsyncSession,
        *,
        fm_only: bool = False,
        directory: bool = True,
        decline_if_vague: bool = False,
        decline_if_none: bool = False,
        also: str | None = None,
    ) -> Response | None:
        """Find the station ``query`` names (module docstring) and play it,
        ask about it, or offer the directory's best. ``decline_if_vague``:
        a query of carrier words only ("play the radio") is not this
        handler's. ``decline_if_none``: nothing saved answers, so the turn
        is someone else's. ``also``: a second reading of the request to
        try ("radio paradise" for "play radio paradise")."""
        from domovoi_plugin_radio import station_match as sm

        query = (query or "").strip()
        if not query or sm.is_carrier_only(query):
            return None if decline_if_vague else self._reply(ctx, "Which station?")

        decision = await self._resolve_saved(query, session, fm_only=fm_only)
        if decision.action == "none" and also:
            decision = await self._resolve_saved(also, session, fm_only=fm_only)
        if decision.action == "play":
            return await self._stream_station_row(dict(decision.stations[0]), ctx, session)
        if decision.action == "choose":
            names = [s["name"] for s in decision.stations]
            return await self._ask(
                ctx, session, list(decision.stations), heard=query,
                prompt=f"{_either(names)}?",
                no_answer=f"I have {decision.close} stations like that: {_either(names)}.",
            )
        if decision.action == "ask":
            name = decision.stations[0]["name"]
            return await self._ask(
                ctx, session, list(decision.stations), heard=query,
                prompt=f"Did you mean {name}?",
                no_answer=f"I don't have {query}. The closest is {name}.",
            )

        if decline_if_none:
            return None
        if fm_only:
            return self._reply(ctx, f"I don't have an FM station called {query}.")
        if not directory:
            return self._reply(ctx, f"I don't have a station called {query}.")
        if egress.internet_turned_off():
            return self._reply(
                ctx,
                f"I don't have a station called {query}. "
                f"{egress.spoken_offline_phrase()}, so I can't look online.",
            )
        if not ctx.online:
            return self._reply(
                ctx,
                f"I don't have a station called {query}, and I can't reach "
                "the internet to look for it.",
            )
        hits = await self._search_directory(query)
        if not hits:
            return self._reply(ctx, f"I couldn't find a station called {query}.")
        names = [_short_name(h["name"]) for h in hits]
        if not ctx.answerable:
            # Nobody to answer a question (the dashboard's play box): play
            # the directory's best only when it surely is what was asked.
            if sm.score(query, hits[0]["name"]) >= sm.PLAY:
                return await self._play_candidate(hits[0], ctx, session)
            return self._reply(
                ctx, f"I don't have {query} saved. Online there's {_either(names)}."
            )
        if len(hits) == 1:
            prompt = f"It's not in your stations. I found {names[0]} online. Play it?"
        else:
            prompt = f"It's not in your stations. Online there's {_either(names)}. Which one?"
        return await self._ask(ctx, session, hits, heard=query, prompt=prompt, no_answer=prompt)

    async def _search_directory(self, query: str) -> list[dict[str, Any]]:
        """The internet directory's best one or two for ``query`` — the
        caller has checked the household allows the internet. Names that
        say what was asked come first; a name already offered once (the
        directory lists one station many times) is skipped."""
        from domovoi_plugin_radio import station_match as sm
        from domovoi_plugin_radio.clients.radio_browser import (
            get_radio_browser_client,
        )

        words = [w for w in sm.radio_text(query).split() if w not in sm.CARRIERS]
        term = " ".join(words) or query
        client = get_radio_browser_client(
            use_stubs=bool(getattr(self.sdk.core_config, "use_stubs", False))
        )
        try:
            found = await client.search(name=term, limit=10)
        except Exception as e:  # noqa: BLE001 — a directory hiccup is "found nothing"
            log.warning("radio: directory search failed: %s", e)
            return []
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for h in found:
            url = (h.stream_url or "").strip()
            name = (h.name or "").strip()
            if not url or not name or egress.check_destination(url) is not None:
                continue
            k = sm.radio_text(_short_name(name))
            if k in seen:
                continue
            seen.add(k)
            hits.append({
                "id": None,
                "name": name,
                "source": "online",
                "stream_url": url,
                "frequency_mhz": None,
                "call_sign": None,
                "external_id": h.external_id,
                "country_code": h.country_code,
                "language": h.language,
                "tags": list(h.tags or []),
            })
        # Stable: the directory's own order (most listened first) within
        # "names it" and "doesn't".
        hits.sort(key=lambda c: sm.score(query, c["name"]) < sm.ASK)
        return hits[:_OFFERED]

    async def _ask(
        self,
        ctx: Context,
        session: AsyncSession,
        candidates: list[dict[str, Any]],
        *,
        heard: str,
        prompt: str,
        no_answer: str,
    ) -> Response:
        """Park the question (or, where nobody can answer, just say so)."""
        if not ctx.answerable:
            return self._reply(ctx, no_answer)
        try:
            await self.sdk.sessions.request_confirmation(
                session,
                ctx.session_id or ctx.room_id,
                kind=_STATION_CHOICE_KIND,
                handler=self.name,
                data={
                    "candidates": [dict(c) for c in candidates],
                    "room_id": ctx.room_id,
                    "heard": heard,
                    "expires_at": time.time() + _CHOICE_TTL_SEC,
                },
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

    async def _play_candidate(
        self, candidate: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        """Play an offered station: a saved row as it is, a directory hit
        saved first (unless it already is, by its directory id)."""
        if candidate.get("id") is not None:
            return await self._stream_station_row(candidate, ctx, session)
        station = await self._save_directory_pick(candidate, ctx, session)
        if isinstance(station, Response):
            return station
        return await self._stream_station_row(station, ctx, session)

    async def _save_directory_pick(
        self, hit: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> dict[str, Any] | Response:
        """The row a directory pick plays from: the existing one for its
        directory id, else a new played-once row (``created_by_play``, not
        a favorite, stamped as played now — what the Stations page's play
        writes, so the Recent trim reclaims it unless it is starred)."""
        url = str(hit.get("stream_url") or "")
        reason = await net_safety.acheck_outbound_url(url, require_resolution=False)
        if reason is not None:
            log.warning("radio: refusing directory station %s — %s", hit.get("name"), reason)
            return self._reply(
                ctx,
                f"I can't play {_short_name(hit.get('name'))}: its stream address "
                "isn't one I'm allowed to fetch.",
            )
        eid = hit.get("external_id")
        if eid:
            row = (
                await session.execute(
                    text(
                        f"SELECT {_STATION_COLUMNS} FROM {SCHEMA}.radio_stations "
                        "WHERE external_id = :eid ORDER BY favorited DESC, id LIMIT 1"
                    ),
                    {"eid": eid},
                )
            ).first()
            if row is not None:
                return _station(row)
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.radio_stations
                        (name, source, stream_url, external_id, country_code,
                         language, tags, favorited, created_by_play, last_played_at)
                    VALUES
                        (:name, 'online', :url, :eid, :cc, :lang, :tags,
                         FALSE, TRUE, NOW())
                    RETURNING {_STATION_COLUMNS}
                    """
                ),
                {
                    "name": hit.get("name"),
                    "url": url,
                    "eid": eid,
                    "cc": hit.get("country_code"),
                    "lang": hit.get("language"),
                    "tags": list(hit.get("tags") or []),
                },
            )
        ).first()
        await _notify_stations(session, "played")
        return _station(row)

    # ─── "Add this to my favorites" ─────────────────────────────────────

    async def _favorite_now_playing(
        self, ctx: Context, session: AsyncSession, *, declinable: bool
    ) -> Response | None:
        """Star the station playing in this room. ``declinable``: the
        words didn't say "station", so with no station playing the turn is
        left to whatever else plays here."""
        stamp = self.sdk.now_playing.get(ctx.room_id) if ctx.room_id else None
        if stamp is None or getattr(stamp, "source", None) != "radio":
            if declinable:
                return None
            return self._reply(ctx, "There's no station playing here right now.")
        data = dict(getattr(stamp, "data", None) or {})
        row = None
        if data.get("station_id") is not None:
            row = (
                await session.execute(
                    text(
                        f"SELECT id, name, favorited FROM {SCHEMA}.radio_stations "
                        "WHERE id = :id"
                    ),
                    {"id": int(data["station_id"])},
                )
            ).first()
        if row is None and data.get("stream_url"):
            row = (
                await session.execute(
                    text(
                        f"SELECT id, name, favorited FROM {SCHEMA}.radio_stations "
                        "WHERE stream_url = :u ORDER BY favorited DESC, id LIMIT 1"
                    ),
                    {"u": data["stream_url"]},
                )
            ).first()
        if row is None:
            return self._reply(ctx, "I can't find that station any more.")
        name = row[1]
        if row[2]:
            return self._reply(ctx, f"{name} is already a favorite.")
        await session.execute(
            text(
                # created_by_play goes FALSE: a starred station is a keeper
                # the Recent trim never reclaims (web.py does the same).
                f"UPDATE {SCHEMA}.radio_stations SET favorited = TRUE, "
                "created_by_play = FALSE, updated_at = NOW() WHERE id = :id"
            ),
            {"id": int(row[0])},
        )
        await _notify_stations(session, "favorited")
        return self._reply(ctx, f"Added {name} to your favorites.")

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
            if egress.internet_turned_off():
                # The FCC import needs the internet, and it is greyed out:
                # don't send anyone to it.
                return self._reply(
                    ctx,
                    f"I don't know {frequency_mhz} FM in your market. "
                    f"{egress.spoken_offline_phrase()}, so I can't load the FCC "
                    "station list. You can add the station by hand; the radio "
                    "plugin's README says how.",
                )
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
            # A box set to stay off the internet never hands the music
            # player an internet stream (the offline fast-path fallback
            # covers most turns; this covers the tool path and the
            # "which one?" follow-up too).
            if egress.check_destination(str(url_to_play)) is not None:
                return self._reply(
                    ctx,
                    f"{egress.spoken_offline_phrase()}, so I can't stream "
                    f"{station['name']}. FM stations still work.",
                )
            # The room's music player fetches this URL, so it gets the
            # same outbound-URL check as every other fetch of a station
            # row: rows that predate the write-side check, or that a
            # directory filled, must not point the player into the house
            # (A7-06). The FM branch below plays the tuner's own
            # host-local URL and is not a station row's. A name that does
            # not resolve right now is let through: the player resolves
            # it itself and simply fails, and a DNS blip should not read
            # as "not allowed".
            reason = await net_safety.acheck_outbound_url(
                str(url_to_play), require_resolution=False
            )
            if reason is not None:
                log.warning(
                    "radio: refusing to stream %s (%s) — %s",
                    station.get("name"), url_to_play, reason,
                )
                return self._reply(
                    ctx,
                    f"I can't stream {station['name']}: its stream address "
                    "isn't one I'm allowed to fetch.",
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
            if (response.data or {}).get("status") == "internet_off":
                # The SDK refused an internet stream under never; its
                # reply already says so.
                return self._reply(ctx, response.text)
            # MPD refused the stream — play_url already phrased a
            # fallback; keep the canonical wording.
            refused = self._reply(
                ctx,
                f"I found {station['name']} but the music player wouldn't take it.",
            )
            # The room route (core.py) answers this one 502, not 409.
            refused.data = {"status": "player_refused"}
            return refused
        response.text = f"Streaming {_short_name(station['name'])}."
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


_NAME_PATTERNS = (_STREAM_RE, _TUNE_RE, _PLAY_STATION_RE, _PLAY_X_RADIO_RE, _PLAY_RADIO_X_RE)


def _name_query(transcript: str) -> str | None:
    """The station name in a request any of the name fast paths would
    take, or None."""
    for pattern in _NAME_PATTERNS:
        m = pattern.match(transcript)
        if m:
            return m.group(1).strip()
    return None


def _expired(data: dict[str, Any]) -> bool:
    try:
        return time.time() > float(data.get("expires_at"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # A question parked before expiry was recorded: still answerable.
        return False


async def _notify_stations(session: AsyncSession, reason: str) -> None:
    """The Stations page's realtime channel (the manifest's [[realtime]]
    NOTIFY name, as web.py sends it); delivered when the turn commits."""
    await session.execute(
        text("SELECT pg_notify('plugin_radio_stations_changed', :r)"),
        {"r": reason},
    )
