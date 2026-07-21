from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.mpd import get_mpd_client_for
from domovoi.db.repositories import utcnow
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


def _try_announce(app: Any, room_id: str | None, text: str) -> None:
    """Best-effort fan an announcement into the originating room's
    WebSocket. Mirrors timer_watcher's reminder dispatch — same
    look-up, same failure semantics (drop silently if the Pi went
    away). Logs at WARNING when the room can't be reached so a missed
    completion announcement is at least visible in journalctl.
    """
    if app is None or room_id is None:
        log.warning(
            "library enrichment done; can't announce (no app/room): %r",
            text,
        )
        return
    sessions = getattr(app.state, "active_sessions", None) or {}
    target = sessions.get(room_id)
    if target is None:
        log.warning(
            "library enrichment done for room=%s but Pi is offline; "
            "dropping announcement: %r",
            room_id, text,
        )
        return
    import asyncio
    asyncio.create_task(
        target.announce(text), name=f"library-enrich-done-{room_id}",
    )


def _format_enrich_summary(counts: dict[str, int]) -> str:
    """Voice-friendly one-liner reporting how the sweep went. Picks
    just the headline numbers — full counts go to the log."""
    matched = counts.get("matched", 0)
    no_match = counts.get("no_match", 0)
    errors = counts.get("errors", 0)
    skipped = counts.get("skipped_missing_file", 0)
    total = matched + no_match + errors + skipped
    if total == 0:
        return "Library enrichment done — nothing needed updating."
    pieces = [f"identified {matched} of {total} tracks"]
    if no_match:
        pieces.append(f"{no_match} couldn't be matched")
    if errors:
        pieces.append(f"{errors} errored")
    if skipped:
        pieces.append(f"{skipped} files were missing")
    detail = ", ".join(pieces)
    return f"Library enrichment done — {detail}."


_FIND_RE = re.compile(r"^(?:find|search(?: for)?) (.+?) in my library$")
_HAVE_RE = re.compile(r"^(?:do i have|is|have i got) (.+?)(?: in my library)?$")
_ADDED_WHEN_RE = re.compile(r"^what did i add (today|yesterday|this week|recently)$")
_COUNT_RE = re.compile(r"^(?:how many (?:songs|tracks|albums)(?: do i have)?|library count)$")
# Voice trigger for the AcoustID/Shazam fingerprint enrichment pass.
# Phrasings tuned for the most natural spoken intent: clean up tags,
# fingerprint, identify what's in the library. Anchors block the
# pattern from poaching unrelated phrases like "enrich me spiritually."
_ENRICH_RE = re.compile(
    r"^(?:"
    r"enrich (?:my |the )?library"
    r"|fingerprint (?:my |the )?(?:music|library|tracks)"
    r"|(?:identify|tag|clean up|fix) (?:my |the )?(?:music|library|tracks)(?: tags)?"
    r"|tag my (?:music|library|tracks)"
    r")$"
)


class LibraryHandler(Handler):
    """Query the local music library — what's in it, what was added when, counts.

    `requires_network="no"`: MPD + library_tracks are both local. Playback itself
    is MusicHandler's job; LibraryHandler is strictly metadata.

    Note: the ``enrich`` action's *worker* hits AcoustID + Shazam (network),
    but the handler itself returns immediately — the enrichment runs in a
    detached background task. The handler being marked
    ``requires_network="no"`` reflects the routing decision; the worker
    short-circuits gracefully when offline.
    """

    name = "library"
    # band rationale: "find X in my library" before any greedier "find X" (a media-
    #   provider plugin's catch-all lives in band 900, dead last).
    priority_band = 310
    display = HandlerDisplay(label="Library", tone="media")
    chat_exposed = True  # organic media tool in chat mode (#8)
    requires_network = "no"

    tool_schema = {
        "name": "library",
        "description": (
            "Search the local music library for tracks by title, artist, or album; "
            "ask what was recently added; count tracks. Does NOT play music — use "
            "the `music` tool for playback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "have", "added_recently", "count", "enrich"],
                },
                "query": {"type": "string"},
                "window": {
                    "type": "string",
                    "enum": ["today", "yesterday", "this_week", "recently"],
                    "default": "recently",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_FIND_RE, LibraryHandler._find_from_match),
            FastPath(_HAVE_RE, LibraryHandler._have_from_match),
            FastPath(_ADDED_WHEN_RE, LibraryHandler._added_from_match),
            FastPath(_COUNT_RE, LibraryHandler._count_from_match),
            FastPath(_ENRICH_RE, LibraryHandler._enrich_from_match),
        ]

    async def execute(self, intent, ctx, session):
        return Response(
            text="I didn't catch a library command.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(self, args, ctx, session):
        action = args.get("action")
        if action == "search":
            return await self._search(args.get("query") or "", ctx, session)
        if action == "have":
            return await self._have(args.get("query") or "", ctx, session)
        if action == "added_recently":
            window = args.get("window") or "recently"
            return await self._added_within(window, ctx, session)
        if action == "count":
            return await self._count(ctx, session)
        if action == "enrich":
            return await self._enrich(ctx, session)
        return Response(
            text=f"I don't know how to {action} the library.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ─────────────────────────────────────────────
    async def _find_from_match(self, m, ctx, session):
        return await self._search(m.group(1).strip(), ctx, session)

    async def _have_from_match(self, m, ctx, session):
        return await self._have(m.group(1).strip(), ctx, session)

    async def _added_from_match(self, m, ctx, session):
        return await self._added_within(m.group(1).strip().replace(" ", "_"), ctx, session)

    async def _count_from_match(self, m, ctx, session):
        return await self._count(ctx, session)

    async def _enrich_from_match(self, m, ctx, session):
        return await self._enrich(ctx, session)

    # ─── Core actions ───────────────────────────────────────────────────
    async def search_library(
        self, query: str, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Public helper so a media-provider plugin's fallback_offline can
        query locally.

        Every per-room MPD indexes the same shared ``/music`` mount so the
        answer to "do I have X?" is the same regardless of which daemon
        we ask. Callers that have a ``room_id`` should pass it so the
        connection lands on the right daemon (avoids needlessly waking
        the default room's MPD on a query from another room).
        """
        mpd = get_mpd_client_for(room_id)
        try:
            # Read-only search — a metadata question ("do I have X?", "find X")
            # must NOT clear the queue and start playing. play_search would.
            song = await mpd.search_only({"any": query})
        except Exception as e:
            log.warning("library search via MPD failed: %s", e)
            return []
        return [song] if song else []

    async def _search(self, query: str, ctx: Context, session: AsyncSession) -> Response:
        if not query:
            return Response(
                text="What should I search for?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        hits = await self.search_library(query, ctx.room_id)
        if not hits:
            return Response(
                text=f"I didn't find {query} in your library.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        song = hits[0]
        title = song.get("title") or song.get("file", "").split("/")[-1]
        artist = song.get("artist")
        if artist:
            text_out = f"Yes — I have {title} by {artist}."
        else:
            text_out = f"Yes — I have {title}."
        return Response(
            text=text_out,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"hits": hits},
        )

    async def _have(self, query: str, ctx: Context, session: AsyncSession) -> Response:
        if not query:
            return Response(
                text="Have what?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        hits = await self.search_library(query, ctx.room_id)
        if hits:
            song = hits[0]
            detail = song.get("title") or song.get("file", "").split("/")[-1]
            return Response(
                text=f"Yes — I have {detail}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"No — I don't have {query} in your library.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _added_within(
        self, window: str, ctx: Context, session: AsyncSession
    ) -> Response:
        delta = {
            "today": timedelta(days=1),
            "yesterday": timedelta(days=2),
            "this_week": timedelta(days=7),
            "recently": timedelta(days=30),
        }.get(window, timedelta(days=30))
        since = utcnow() - delta

        result = await session.execute(
            text(
                """
                SELECT title, artist, added_at
                FROM library_tracks
                WHERE added_at >= :since
                ORDER BY added_at DESC
                LIMIT 10
                """
            ),
            {"since": since},
        )
        rows = result.all()
        if not rows:
            return Response(
                text="Nothing added in that window.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        names = [f"{r[0]} by {r[1]}" if r[1] else (r[0] or "untitled") for r in rows]
        if len(names) == 1:
            text_out = f"One addition: {names[0]}."
        else:
            text_out = f"{len(names)} additions — including {names[0]} and {names[1]}."
        return Response(
            text=text_out,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"tracks": [{"title": r[0], "artist": r[1], "added_at": r[2].isoformat()} for r in rows]},
        )

    async def _count(self, ctx: Context, session: AsyncSession) -> Response:
        result = await session.execute(text("SELECT COUNT(*) FROM library_tracks"))
        n = int(result.scalar_one() or 0)
        return Response(
            text=f"Your library has {n} track{'s' if n != 1 else ''}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def _enrich(self, ctx: Context, session: AsyncSession) -> Response:
        """Voice-triggered AcoustID/Shazam enrichment pass.

        Kicks the enricher worker in a detached background task and
        returns immediately — the actual sweep takes ~1 second per
        unenriched track, so a 100-track first pass is ~2 minutes
        and we'd otherwise hold the WebSocket open the whole time.
        Reports the queued-count up front so the user knows whether
        there's work to do or everything's already enriched.

        On completion, the worker announces the result summary into
        the originating room's WebSocket via ``StreamSession.announce``
        — same plumbing reminders use. Resilient to reconnects: the
        announcement looks up the active session at completion time,
        so a Pi that drops and rejoins still hears the result.
        """
        unenriched = int((await session.execute(
            text("SELECT count(*) FROM library_tracks WHERE enriched_at IS NULL")
        )).scalar_one() or 0)

        if unenriched == 0:
            return Response(
                text=(
                    "Your library is already fully enriched — every "
                    "track has been fingerprinted and tagged."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )

        # Capture references the detached task needs. Don't capture
        # `session` — it'll be closed when this request returns. The
        # worker opens its own session via session_scope().
        room_id = ctx.room_id
        app = ctx.app

        async def _run_then_announce() -> None:
            from domovoi.workers.library_enricher import enrich_library
            try:
                counts = await enrich_library()
            except Exception as e:
                log.warning("voice-triggered enricher failed: %s", e)
                _try_announce(
                    app, room_id,
                    "I hit an error while enriching your library — "
                    "check the logs.",
                )
                return
            _try_announce(app, room_id, _format_enrich_summary(counts))

        import asyncio
        asyncio.create_task(_run_then_announce(), name="library-enricher-voice")

        from domovoi.config import settings as _settings
        # Best-guess time estimate: ~1 sec per track (rate-limit) plus
        # API roundtrip, ~1.3 sec/track in practice. Round to whole
        # minutes; "a few minutes" beats "73 minutes" for short answers.
        eta_min = max(1, round(unenriched * (_settings.library_enricher_delay_sec + 0.3) / 60))
        eta_phrase = f"about {eta_min} minute{'s' if eta_min != 1 else ''}"
        return Response(
            text=(
                f"Got it — fingerprinting {unenriched} tracks now. "
                f"This'll take {eta_phrase}; I'll let you know when "
                f"it's done."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
