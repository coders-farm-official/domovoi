"""SpokenAudioHandler — voice surface for podcasts + audiobooks.

One handler for both spoken-audio kinds (they share every control verb:
resume, chapter nav, skip, speed, "what am I listening to"). Playback reuses
the per-room MPD path exactly like MusicHandler — the podcast/audiobook dirs
are mounted as nested subdirs of MPD's /music, so a downloaded episode or an
.m4b is just another indexed file the daemon can stream to the satellite.

``requires_network = "degraded"``: local playback of DOWNLOADED content works
fully offline (``fallback_offline`` serves it), while SUBSCRIBE / discovery /
import are the network-only parts.

Ordering (handlers/__init__.py): SpokenAudioHandler sits BEFORE RadioHandler /
PlaylistHandler / MusicHandler so its anchored phrases ("play the latest X",
"resume my book", "next chapter", "skip 30 seconds", "set speed to 1.5x") win
before MusicHandler's greedy ``^play (.+)$`` catch-all can poach them. Its
regexes never match a bare "play X" / "stream X" / "next" / "pause", so it
can't poach the media handlers below it — bare transport (pause/stop/next
track) intentionally falls through to MusicHandler, which drives the same MPD.

Person identity for per-person resume comes from ``ctx.person_id`` (attached
pre-router by the VoiceProfileHandler pass in streaming.py); ``device_id`` is
the room. Positions are per-(device × person × item) and never roam.

⚠ Satellite variable-speed: MPD has no native playback-rate control. "Set
speed" PERSISTS the per-item speed (playback_positions.speed) and reports it,
and the BROWSER applies it natively; the satellite audible-rate change is a
documented TODO (see INTEGRATION_podcasts.md — ffmpeg atempo transcode vs.
mpv on the Pi). We store real state and never fake the rate.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.mpd import get_mpd_client_for, mpd_stream_url_for
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi import spoken_audio as sa

log = logging.getLogger(__name__)


# ─── Fast-path regexes (all tightly anchored — see ordering note above) ────
# Requires a spoken-audio NOUN so bare "resume" / "continue" fall straight
# through to MusicHandler's "resume the music". "keep reading" is unambiguous
# enough to stand alone (→ audiobook).
_RESUME_RE = re.compile(
    r"^(?:"
    r"(?:resume|continue|pick up|go back to)(?: (?:my|the))? (?P<kind>book|audiobook|podcast|episode|show)"
    r"|keep reading(?: my (?P<kind2>book|audiobook))?"
    r")\b.*$"
)
# Only claim EXPLICIT podcast phrasing here — an "episode" keyword, or a show
# name trailed by "podcast"/"show". This handler runs before MusicHandler, so
# a bare `play the latest X` used to poach music ("play the latest from TI" →
# "no subscribed podcast matching from ti"). Ambiguous "play (the) latest X"
# now falls through to MusicHandler, which cascades local → subscribed podcast
# → streaming provider (see MusicHandler._play), so a subscribed show is still reachable
# — just music-first, per the desired fallback order.
_PLAY_LATEST_RE = re.compile(
    r"^play (?:me )?(?:the )?(?:latest|newest|last) "
    r"(?:"
    r"episode(?: of)? (?P<show>.+)"       # "play the latest episode of The Daily"
    r"|(?P<show2>.+) (?:podcast|show)"    # "play the newest Daily podcast"
    r")$"
)
_PLAY_BOOK_RE = re.compile(
    r"^(?:play|read|start)(?: me)? (?:the )?(?:audiobook|book) (?P<book>.+)$"
)
_NEXT_CHAPTER_RE = re.compile(r"^(?:next|skip to (?:the )?next) chapter$")
_PREV_CHAPTER_RE = re.compile(r"^(?:previous|prior|last|go back a) chapter$")
_SKIP_RE = re.compile(
    r"^(?:skip|jump|go|fast[- ]?forward|rewind)"
    r"(?: (?P<dir>forward|ahead|back|backward|backwards))?"
    r"(?: by)? (?P<n>\d{1,4}) seconds?$"
)
_SET_SPEED_RE = re.compile(
    r"^(?:set (?:the )?(?:playback )?speed to|(?:playback )?speed) "
    r"(?P<speed>\d(?:\.\d+)?)x?$"
)
_TIME_LEFT_RE = re.compile(
    r"^how (?:long|much(?: time)?) (?:is )?(?:left|remaining) (?:in|on) (?:this |the )?chapter$"
)
_NOW_LISTENING_RE = re.compile(
    r"^(?:what am i listening to|what(?:'s| is) (?:this )?(?:playing|book|podcast|episode|chapter))$"
)
# Keep any leading "the" — podcast names often include it ("The Daily").
_SUBSCRIBE_RE = re.compile(r"^subscribe(?: me)? to (?P<show>.+?)(?: podcast)?$")

# Session-context key holding the currently-playing spoken item so chapter /
# time-left / "what am I listening to" can resolve without re-querying MPD's
# tag DB. Shape: {item_type, item_id, title, chapters, duration_sec, uri}.
_CTX_KEY = "spoken_now"


class SpokenAudioHandler(Handler):
    name = "spoken_audio"
    # band rationale: anchored media BEFORE radio (plugin band 280) / playlist (290) /
    #   music (300) so podcast/audiobook phrasings win over music's greedy
    #   "^play (.+)$": "play the latest <show>", "resume my book", "next
    #   chapter". Every regex requires a spoken-audio noun or a distinctive
    #   verb tail, and the resume path requires the noun so a bare "resume"
    #   still falls through to music. Bare transport (pause/stop/next) is
    #   intentionally NOT claimed here.
    priority_band = 270
    display = HandlerDisplay(label="Podcasts & Books", tone="media")
    requires_network = "degraded"

    tool_schema = {
        "name": "spoken_audio",
        "description": (
            "Play and control podcasts and audiobooks: resume a book or "
            "podcast where you left off, play the latest episode of a show, "
            "navigate chapters, skip forward/back by seconds, set playback "
            "speed, ask what's playing, or subscribe to a podcast. Different "
            "from music (on-demand songs) and radio (live streams) — this is "
            "long-form spoken audio with resume + chapters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "resume", "play_latest", "play_book", "next_chapter",
                        "previous_chapter", "skip", "set_speed", "time_left",
                        "now_listening", "subscribe",
                    ],
                },
                "query": {"type": "string", "description": "Show or book name"},
                "seconds": {"type": "integer", "description": "Skip amount (negative = back)"},
                "speed": {"type": "number", "description": "Playback rate, e.g. 1.5"},
                "kind": {"type": "string", "enum": ["podcast", "audiobook", "book"]},
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_PLAY_LATEST_RE, SpokenAudioHandler._play_latest_from_match),
            FastPath(_PLAY_BOOK_RE, SpokenAudioHandler._play_book_from_match),
            FastPath(_NEXT_CHAPTER_RE, SpokenAudioHandler._next_chapter_from_match),
            FastPath(_PREV_CHAPTER_RE, SpokenAudioHandler._prev_chapter_from_match),
            FastPath(_SKIP_RE, SpokenAudioHandler._skip_from_match),
            FastPath(_SET_SPEED_RE, SpokenAudioHandler._set_speed_from_match),
            FastPath(_TIME_LEFT_RE, SpokenAudioHandler._time_left_from_match),
            FastPath(_NOW_LISTENING_RE, SpokenAudioHandler._now_listening_from_match),
            # Subscribe is the one network-only path on this degraded handler
            # (discovery + iTunes lookup). Mark it offline_ok=False so the
            # router auto-falls-back to fallback_offline while offline instead
            # of dispatching it into a doomed network call — this is what makes
            # the subscribe branch in fallback_offline reachable rather than
            # dead code.
            FastPath(_SUBSCRIBE_RE, SpokenAudioHandler._subscribe_from_match, offline_ok=False),
            # Resume last so its broad lead-in ("resume ...") doesn't shadow
            # the more specific play/skip/chapter phrasings above.
            FastPath(_RESUME_RE, SpokenAudioHandler._resume_from_match),
        ]

    # ─── Fast-path adapters ─────────────────────────────────────────────
    async def _play_latest_from_match(self, m, ctx, session):
        show = (m.group("show") or m.group("show2") or "").strip().rstrip(".,!?")
        return await self._play_latest(ctx, session, show)

    async def try_play_latest(
        self, ctx: Context, session: AsyncSession, show: str
    ) -> Response | None:
        """Public entry for MusicHandler's local-miss → podcast cascade.

        Plays the latest episode of a subscribed show matching ``show``, or
        returns ``None`` when there's no matching subscription — deliberately
        NOT the "no subscribed podcast" error ``_play_latest`` gives, because
        the caller (an ambiguous "play …") didn't explicitly ask for a
        podcast and needs to fall through to its next source (a streaming
        provider). Mirrors the streaming seam's None-on-miss contract."""
        show = (show or "").strip()
        if not show:
            return None
        if await sa.latest_episode_for_show(session, show) is None:
            return None
        return await self._play_latest(ctx, session, show)

    async def _play_book_from_match(self, m, ctx, session):
        return await self._play_book(ctx, session, m.group("book").strip().rstrip(".,!?"))

    async def _next_chapter_from_match(self, m, ctx, session):
        return await self._chapter_nav(ctx, session, +1)

    async def _prev_chapter_from_match(self, m, ctx, session):
        return await self._chapter_nav(ctx, session, -1)

    async def _skip_from_match(self, m, ctx, session):
        n = int(m.group("n"))
        direction = (m.group("dir") or "forward").lower()
        if direction in ("back", "backward", "backwards", "rewind"):
            n = -n
        return await self._skip(ctx, session, n)

    async def _set_speed_from_match(self, m, ctx, session):
        return await self._set_speed(ctx, session, float(m.group("speed")))

    async def _time_left_from_match(self, m, ctx, session):
        return await self._time_left(ctx, session)

    async def _now_listening_from_match(self, m, ctx, session):
        return await self._now_listening(ctx, session)

    async def _subscribe_from_match(self, m, ctx, session):
        return await self._subscribe(ctx, session, m.group("show").strip().rstrip(".,!?"))

    async def _resume_from_match(self, m, ctx, session):
        kind = (m.group("kind") or m.group("kind2") or "").lower()
        # "keep reading" with no explicit noun → audiobook.
        if not kind and m.group(0).startswith("keep reading"):
            kind = "book"
        item_type = (
            sa.ITEM_AUDIOBOOK if kind in ("book", "audiobook")
            else sa.ITEM_PODCAST if kind in ("podcast", "episode", "show")
            else None
        )
        return await self._resume(ctx, session, item_type)

    # ─── Handler entry points ───────────────────────────────────────────
    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._reply(ctx, "I didn't catch a podcast or audiobook command.")

    async def execute_from_tool(self, args: dict[str, Any], ctx: Context, session: AsyncSession) -> Response:
        action = args.get("action")
        query = (args.get("query") or "").strip()
        if action == "resume":
            k = (args.get("kind") or "").lower()
            it = sa.ITEM_AUDIOBOOK if k in ("book", "audiobook") else sa.ITEM_PODCAST if k == "podcast" else None
            return await self._resume(ctx, session, it)
        if action == "play_latest":
            return await self._play_latest(ctx, session, query)
        if action == "play_book":
            return await self._play_book(ctx, session, query)
        if action == "next_chapter":
            return await self._chapter_nav(ctx, session, +1)
        if action == "previous_chapter":
            return await self._chapter_nav(ctx, session, -1)
        if action == "skip":
            return await self._skip(ctx, session, int(args.get("seconds") or 30))
        if action == "set_speed":
            return await self._set_speed(ctx, session, float(args.get("speed") or 1.0))
        if action == "time_left":
            return await self._time_left(ctx, session)
        if action == "now_listening":
            return await self._now_listening(ctx, session)
        if action == "subscribe":
            return await self._subscribe(ctx, session, query)
        return self._reply(ctx, f"I don't know how to {action} spoken audio.")

    async def fallback_offline(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        """Offline path. Local playback of downloaded content works — resume,
        play-latest (downloaded only), chapter/skip/speed, what's-playing all
        run against local files + the DB. Only SUBSCRIBE / discovery need the
        network, so re-route everything else through the same fast paths and
        give subscribe a clear offline message."""
        transcript = intent.transcript.strip().lower()
        if _SUBSCRIBE_RE.match(transcript):
            return self._reply(
                ctx, "I need an internet connection to subscribe to a new podcast."
            )
        for pattern, method in self.fast_paths:
            m = pattern.match(transcript)
            if m:
                return await method(self, m, ctx, session)
        return self._reply(ctx, "I couldn't do that offline.")

    # ─── Core actions ───────────────────────────────────────────────────
    async def _resume(self, ctx: Context, session: AsyncSession, item_type: str | None) -> Response:
        entry = await sa.most_recent_in_progress(
            session, device_id=self._device(ctx), person_id=ctx.person_id, item_type=item_type
        )
        if entry is None:
            what = "book" if item_type == sa.ITEM_AUDIOBOOK else "podcast" if item_type == sa.ITEM_PODCAST else "spoken audio"
            return self._reply(ctx, f"I don't have a {what} in progress for you here.")
        return await self._play_resolved(
            ctx, session, entry["item_type"], entry["item_id"],
            resume_sec=entry["position_sec"], speed=entry["speed"],
        )

    async def _play_latest(self, ctx: Context, session: AsyncSession, show: str) -> Response:
        if not show:
            return self._reply(ctx, "Which show?")
        ep = await sa.latest_episode_for_show(session, show)
        if ep is None:
            return self._reply(
                ctx,
                f"I couldn't find a subscribed podcast matching {show}. "
                "Try 'subscribe to' it first.",
            )
        if ep["download_status"] != "downloaded" or not ep["file_path"]:
            return self._reply(
                ctx,
                f"The latest {ep['show_title'] or show} episode isn't downloaded "
                "yet — it'll be ready shortly.",
            )
        return await self._play_item(
            ctx, session,
            item_type=sa.ITEM_PODCAST, item_id=ep["id"],
            title=ep["title"] or ep["show_title"] or "episode",
            file_path=ep["file_path"], chapters=ep.get("chapters"),
            duration=ep.get("duration_sec"), resume=True,
        )

    async def _play_book(self, ctx: Context, session: AsyncSession, query: str) -> Response:
        if not query:
            return self._reply(ctx, "Which book?")
        book = await sa.find_audiobook(session, query)
        if book is None:
            return self._reply(ctx, f"I couldn't find {query} in your audiobooks.")
        return await self._play_item(
            ctx, session,
            item_type=sa.ITEM_AUDIOBOOK, item_id=book["id"],
            title=book["title"], file_path=book["file_path"],
            chapters=book.get("chapters"), duration=book.get("duration_sec"),
            resume=True,
        )

    async def _play_resolved(
        self, ctx: Context, session: AsyncSession, item_type: str, item_id: int,
        *, resume_sec: int, speed: float,
    ) -> Response:
        """Resume path: look the item's title/file up by id, then play."""
        if item_type == sa.ITEM_AUDIOBOOK:
            row = (await session.execute(
                text("SELECT title, file_path, chapters, duration_sec FROM audiobooks WHERE id=:id"),
                {"id": item_id},
            )).mappings().first()
        else:
            row = (await session.execute(
                text(
                    "SELECT title, file_path, chapters, duration_sec, download_status "
                    "FROM podcast_episodes WHERE id=:id"
                ),
                {"id": item_id},
            )).mappings().first()
        if row is None or not row.get("file_path"):
            return self._reply(ctx, "I couldn't find that to resume it.")
        return await self._play_item(
            ctx, session, item_type=item_type, item_id=item_id,
            title=row["title"] or "your audio", file_path=row["file_path"],
            chapters=row.get("chapters"), duration=row.get("duration_sec"),
            resume=True, resume_sec=resume_sec,
        )

    async def _play_item(
        self, ctx: Context, session: AsyncSession, *,
        item_type: str, item_id: int, title: str, file_path: str,
        chapters: Any, duration: int | None, resume: bool, resume_sec: int | None = None,
    ) -> Response:
        uri = sa.mpd_uri_for(item_type, file_path)
        if uri is None:
            return self._reply(
                ctx,
                "That file isn't in the spoken-audio library the player can "
                "reach. Try re-indexing.",
            )
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            song = await mpd.prepare_filename(uri)
        except Exception as e:
            log.warning("spoken_audio MPD prepare failed: %s", e)
            return self._reply(ctx, "I couldn't reach the audio player.")
        if not song:
            return self._reply(
                ctx,
                f"I found {title} but the player couldn't load it — "
                "try re-indexing the library.",
            )

        # Resume position: read saved unless an explicit resume_sec was given.
        pos = resume_sec
        speed = 1.0
        if resume:
            saved = await sa.get_position(
                session, item_type=item_type, item_id=item_id,
                device_id=self._device(ctx), person_id=ctx.person_id,
            )
            if saved:
                if pos is None:
                    pos = saved["position_sec"]
                speed = saved["speed"]
        if pos and pos > 0:
            try:
                await mpd.seek_to(float(pos))
            except Exception as e:
                log.debug("spoken_audio seek-to-resume failed: %s", e)

        # Remember what's playing for chapter / time-left / now-listening.
        await self._remember(ctx, session, {
            "item_type": item_type, "item_id": item_id, "title": title,
            "chapters": chapters, "duration_sec": duration, "uri": uri,
        })
        # Persist a fresh position row so "resume" finds it next time.
        await sa.upsert_position(
            session, item_type=item_type, item_id=item_id,
            device_id=self._device(ctx), person_id=ctx.person_id,
            position_sec=int(pos or 0),
        )

        resumed_note = ""
        if pos and pos > 0:
            resumed_note = f" Resuming at {self._fmt_dur(pos)}."
        return Response(
            text=f"Playing {title}.{resumed_note}",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"item_type": item_type, "item_id": item_id, "speed": speed},
            music_action="start",
            music_stream_url=mpd_stream_url_for(ctx.room_id),
        )

    async def _chapter_nav(self, ctx: Context, session: AsyncSession, direction: int) -> Response:
        now = await self._current(ctx, session)
        if now is None:
            return self._reply(ctx, "Nothing spoken is playing right now.")
        chapters = now.get("chapters") or []
        if not chapters:
            # Degrade gracefully — no chapter markers, so ±60s is the closest
            # we can offer (the plan's "degrade to time-skip only").
            return await self._skip(ctx, session, 60 * direction, save_only=False)
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            elapsed = await mpd.elapsed_sec() or 0.0
        except Exception:
            elapsed = 0.0
        starts = [int(c.get("start_sec", 0)) for c in chapters]
        # Index of the chapter we're in.
        cur_idx = 0
        for i, s in enumerate(starts):
            if elapsed >= s:
                cur_idx = i
        target_idx = max(0, min(len(starts) - 1, cur_idx + direction))
        if direction < 0 and elapsed - starts[cur_idx] > 3 and target_idx == cur_idx:
            target_idx = cur_idx  # restart current chapter if >3s in
        target_sec = starts[target_idx]
        try:
            await mpd.seek_to(float(target_sec))
        except Exception as e:
            log.warning("chapter seek failed: %s", e)
            return self._reply(ctx, "I couldn't move chapters.")
        await self._save_position(ctx, session, now, target_sec)
        ch_title = chapters[target_idx].get("title") or f"chapter {target_idx + 1}"
        return self._reply(ctx, f"{ch_title}.")

    async def _skip(self, ctx: Context, session: AsyncSession, delta: int, *, save_only: bool = False) -> Response:
        now = await self._current(ctx, session)
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            await mpd.seek_cur(float(delta))
            elapsed = await mpd.elapsed_sec()
        except Exception as e:
            log.warning("skip failed: %s", e)
            return self._reply(ctx, "I couldn't skip.")
        if now is not None and elapsed is not None:
            await self._save_position(ctx, session, now, int(elapsed))
        verb = "forward" if delta >= 0 else "back"
        return self._reply(ctx, f"Skipped {verb} {abs(delta)} seconds.")

    async def _set_speed(self, ctx: Context, session: AsyncSession, speed: float) -> Response:
        speed = max(0.5, min(3.0, speed))
        now = await self._current(ctx, session)
        if now is None:
            return self._reply(ctx, "Nothing spoken is playing to change the speed of.")
        await sa.set_speed(
            session, item_type=now["item_type"], item_id=now["item_id"],
            device_id=self._device(ctx), person_id=ctx.person_id, speed=speed,
        )
        # ⚠ Satellite audible-rate change is a documented TODO (MPD has no
        # native rate control). We persist the per-item speed so the browser
        # applies it natively and the value survives; the spoken confirmation
        # is honest about it taking effect on the browser player.
        return self._reply(
            ctx,
            f"Playback speed set to {self._fmt_speed(speed)}. "
            "It applies on the browser player; satellite speed is coming soon.",
        )

    async def _time_left(self, ctx: Context, session: AsyncSession) -> Response:
        now = await self._current(ctx, session)
        if now is None:
            return self._reply(ctx, "Nothing spoken is playing right now.")
        mpd = get_mpd_client_for(ctx.room_id)
        try:
            elapsed = await mpd.elapsed_sec() or 0.0
        except Exception:
            elapsed = 0.0
        chapters = now.get("chapters") or []
        duration = now.get("duration_sec")
        # End of current chapter, or end of item if no chapters.
        end = None
        if chapters:
            starts = [int(c.get("start_sec", 0)) for c in chapters]
            for i, s in enumerate(starts):
                if s > elapsed:
                    end = s
                    break
            if end is None:
                end = duration
        else:
            end = duration
        if end is None:
            return self._reply(ctx, "I don't know how long is left.")
        remaining = max(0, int(end - elapsed))
        return self._reply(ctx, f"About {self._fmt_dur(remaining)} left in this chapter.")

    async def _now_listening(self, ctx: Context, session: AsyncSession) -> Response:
        now = await self._current(ctx, session)
        if now is None:
            return self._reply(ctx, "Nothing spoken is playing right now.")
        kind = "audiobook" if now["item_type"] == sa.ITEM_AUDIOBOOK else "podcast"
        return self._reply(ctx, f"This is the {kind}, {now.get('title') or 'unknown'}.")

    async def _subscribe(self, ctx: Context, session: AsyncSession, show: str) -> Response:
        """Resolve a show NAME to an RSS feed via iTunes Search (keyless) and
        add it. Network path — ``fallback_offline`` short-circuits this."""
        if not show:
            return self._reply(ctx, "Which podcast should I subscribe to?")
        feed_url, title = await self._itunes_lookup(show)
        if not feed_url:
            return self._reply(
                ctx, f"I couldn't find a podcast called {show} to subscribe to."
            )
        await session.execute(
            text(
                """
                INSERT INTO podcast_subscriptions (feed_url, title, keep_n)
                VALUES (:url, :title, :keep)
                ON CONFLICT (feed_url) DO NOTHING
                """
            ),
            {"url": feed_url, "title": title or show, "keep": 5},
        )
        await session.execute(text("SELECT pg_notify('podcasts_changed', 'subscribe')"))
        return self._reply(
            ctx,
            f"Subscribed to {title or show}. I'll download new episodes as they come out.",
        )

    async def _itunes_lookup(self, show: str) -> tuple[str | None, str | None]:
        """iTunes Search API → (feedUrl, collectionName). Keyless, rate-
        limited; best-effort."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://itunes.apple.com/search",
                    params={"term": show, "media": "podcast", "limit": 1},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log.warning("itunes podcast lookup failed for %r: %s", show, e)
            return None, None
        results = data.get("results") or []
        if not results:
            return None, None
        top = results[0]
        return top.get("feedUrl"), top.get("collectionName")

    # ─── Session-context bookkeeping ────────────────────────────────────
    async def _current(self, ctx: Context, session: AsyncSession) -> dict[str, Any] | None:
        if not ctx.session_id:
            return None
        try:
            data = await SessionRepository(session).get_context(ctx.session_id) or {}
        except Exception:
            return None
        val = data.get(_CTX_KEY)
        return val if isinstance(val, dict) else None

    async def _remember(self, ctx: Context, session: AsyncSession, item: dict[str, Any]) -> None:
        if not ctx.session_id:
            return
        try:
            await SessionRepository(session).set_context_key(ctx.session_id, _CTX_KEY, item)
        except Exception as e:
            log.debug("couldn't remember spoken item: %s", e)

    async def _save_position(self, ctx: Context, session: AsyncSession, now: dict[str, Any], pos: int) -> None:
        try:
            await sa.upsert_position(
                session, item_type=now["item_type"], item_id=now["item_id"],
                device_id=self._device(ctx), person_id=ctx.person_id,
                position_sec=int(pos),
            )
        except Exception as e:
            log.debug("couldn't save position: %s", e)

    # ─── Small helpers ──────────────────────────────────────────────────
    def _device(self, ctx: Context) -> str:
        return ctx.room_id or "default"

    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name)

    @staticmethod
    def _fmt_dur(sec: int) -> str:
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h} hour{'s' if h != 1 else ''} {m} minute{'s' if m != 1 else ''}"
        if m:
            return f"{m} minute{'s' if m != 1 else ''}"
        return f"{s} second{'s' if s != 1 else ''}"

    @staticmethod
    def _fmt_speed(speed: float) -> str:
        return f"{speed:g}x"
