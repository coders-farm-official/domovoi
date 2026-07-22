"""WebSocket fanout + state poll loop + Postgres LISTEN bridge.

A single async task polls Postgres + the Domovoi server's admin
snapshot every ``WEB_POLL_INTERVAL_SEC`` seconds, diffs against the
previous snapshot, and pushes change events to subscribed WebSocket
clients. A second task runs ``LISTEN`` on a dedicated asyncpg
connection so events that need sub-second freshness — primarily
media-acquisition status flips — wake the snapshot pipeline immediately
instead of waiting for the next poll tick.

Channels emitted (core; enabled plugins add their own via manifest
``[[realtime]]`` entries — design §5.3):

* ``music.now_playing`` — per-room playback state (state + song)
* ``acquisitions`` — live rows in the generic media-acquisition queue
* ``satellites.presence`` — which rooms the Domovoi server counts as
  online (read from the admin snapshot when reachable; absent
  otherwise)
* ``satellites.wifi`` — per-room WiFi telemetry from the admin snapshot
* ``satellites.display`` — per-room screen/kiosk state video satellites
  report (display toggles, kiosk-browser death/recovery)
* ``satellites.pending`` — unprovisioned satellites presenting a USB
  adoption volume on the server (plug/unplug/status flips)
* ``people.last_seen`` — fires when a person's ``last_seen_at`` advances
* ``calendar.events`` — current 30-day window of events; emits any time
  the window changes
* ``library.indexer`` — library_tracks row count + newest added_at;
  fires when an indexer sweep inserts (upload / rescan) or a track is
  deleted, so the Library + Stats views refetch
* ``wake_words`` — the custom wake-word registry; fires on any
  wake_words mutation (web CRUD, a recorded clip landing via the
  Domovoi server's streaming path, a trainer status flip) so the Wake
  Words tab's clip-count / status pills refresh sub-second

Snapshot diffs are coarse: we emit the full new value rather than a
field-level patch. The frontend rerenders on receipt; payloads are
small (a few KB each), and at 1.5s cadence the bandwidth is fine.

LISTEN/NOTIFY pipeline:

* Mutation sites (the core acquisition queue, web CRUD, voice adds)
  call ``SELECT pg_notify('<channel>', '<reason>')`` in the same
  transaction as their UPDATE/INSERT. Postgres delivers NOTIFY only
  on COMMIT, so a rollback never produces a phantom event.
* The web backend's ``ListenTask`` opens one long-lived asyncpg
  connection, runs ``LISTEN`` on every channel in the merged map
  (core + enabled plugins), and on each notification triggers the
  matching channel's snapshot through the existing diff-and-broadcast
  pipeline. Connection drops are recovered with exponential backoff;
  a plugin-registry change re-establishes the listener set live.
* The poll loop is still authoritative — LISTEN is an accelerator,
  not a replacement. If a notification is missed (network blip,
  asyncpg quirk), the next 1.5 s poll catches the change anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import WebSocket
from sqlalchemy import text

from domovoi.config import settings as core_settings

from web.backend import satellite_adoption
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    fetch_admin_snapshot,
    set_cached_snapshot,
)

log = logging.getLogger(__name__)


# Default poll cadence. Env-tunable via WEB_POLL_INTERVAL_SEC.
DEFAULT_POLL_INTERVAL_SEC = 1.5

# Maps a NOTIFY channel name (Postgres-side) to the realtime snapshot
# channel it should refresh. This dict holds the CORE channels only;
# plugin channels arrive via :func:`set_plugin_notify_channels` (design
# §5.3 — the web process wires them straight from manifest ``[[realtime]]``
# entries, never importing plugin code). The LISTEN task reads the merged
# view through :func:`notify_channel_map` to translate "this just changed
# in the DB" into "rerun this snapshot helper through the
# diff-and-broadcast pipeline."
NOTIFY_CHANNEL_TO_REALTIME: dict[str, str] = {
    # Generic media-acquisition queue (design §4.8). The core fires
    # `acquisitions_changed` on every enqueue/claim/done/fail; the web
    # Music page's Jobs readout rides the `acquisitions` realtime
    # channel. Provider plugins layer richer views on their own channels.
    "acquisitions_changed": "acquisitions",
    "calendar_changed": "calendar.events",
    # Playlists. Fires on create / rename / delete /
    # add-track / remove-track AND on a library_tracks.favorited
    # flip (since the virtual Favorites playlist's track_count
    # changes). One channel covers all three because the dashboard
    # refreshes the whole tab anyway — the payload is small.
    "playlists_changed": "playlists",
    # Library indexing. Fires from index_music_dir() whenever a sweep
    # inserts new rows (web Upload, voice "rescan", startup sweep with
    # fresh files). The Library tab + Stats refetch off the resulting
    # `library.indexer.changed` event so an upload surfaces the moment
    # indexing lands instead of after a timed guess.
    "library_changed": "library.indexer",
    # Custom wake words. Net-new realtime plumbing (the Voices
    # page it clones uses no realtime). Fires from THREE sites, each in
    # its own transaction: the web CRUD endpoints, the Domovoi server's
    # streaming clip-write (as each recorded positive clip bumps the
    # clip count), and the trainer worker (status flips). The Wake Words
    # tab subscribes via the resulting `wake_words.changed` event so the
    # live clip-count / status pills update without a poll-cadence lag.
    "wake_words_changed": "wake_words",
    # Spoken audio. `podcasts_changed` fires on any subscription /
    # episode / audiobook mutation (subscribe, unsubscribe, feed poll,
    # download landing, keep-N eviction, audiobook (re)index) so the
    # Podcasts + Audiobooks tabs refetch. `podcast_positions_changed` fires
    # on a resume-position save (browser or satellite) so a "continue"
    # affordance stays fresh across devices viewing the same library.
    "podcasts_changed": "podcasts",
    "podcast_positions_changed": "podcast_positions",
    # Models page pull jobs. Fires from web/backend/api/models.py's
    # pull task on each throttled progress write (and create / finish /
    # cancel) so the install progress bar tracks the Ollama download live
    # rather than on the poll cadence. The Models page subscribes via the
    # resulting `model_jobs.changed` event.
    "model_jobs_changed": "model_jobs",
    # Satellite media-prep builds (V004). Fires from the prepare/progress/
    # finish writes in web/backend/api/satellite_media.py so the card's
    # progress bar tracks the build live. Subscribers see the resulting
    # `satellites.media.changed` event.
    "satellite_media_jobs_changed": "satellites.media",
    # News. Fires on any topic/feed/item/briefing mutation (web CRUD,
    # the daily fetcher, a favorite toggle, a manual poll). The News page
    # subscribes via the resulting `news.changed` event and refetches the
    # selected person's topics / feeds / saved items / briefing.
    "news_changed": "news",
}

# Plugin NOTIFY → realtime channel entries, replaced wholesale on every
# registry resync (web/backend/plugin_host.py). Kept separate from the
# core map so a disable cleanly drops a plugin's channels.
_PLUGIN_NOTIFY_CHANNELS: dict[str, str] = {}

# Registry-change NOTIFY channel: the ListenTask always listens on this
# and invokes ON_PLUGINS_CHANGED (installed by main.py → HOST.resync)
# so installs/enables/disables mount live without a web restart.
PLUGINS_CHANGED_CHANNEL = "plugins_changed"
ON_PLUGINS_CHANGED: Callable[[], Awaitable[None]] | None = None


def set_plugin_notify_channels(mapping: dict[str, str]) -> None:
    """Replace the plugin channel map (design §5.3)."""
    _PLUGIN_NOTIFY_CHANNELS.clear()
    _PLUGIN_NOTIFY_CHANNELS.update(mapping)


def notify_channel_map() -> dict[str, str]:
    """Merged NOTIFY→realtime view (core + currently-enabled plugins)."""
    return {**NOTIFY_CHANNEL_TO_REALTIME, **_PLUGIN_NOTIFY_CHANNELS}


class StateBroadcaster:
    """Tracks connected WebSocket clients + their subscriptions and
    fans out state-change events as they're emitted by the poll
    loop. Single instance, attached to ``app.state.broadcaster`` in
    main.py.

    Subscriptions are channel-scoped: a client subscribes to
    ``["music", "satellites"]`` and only sees events whose ``type``
    starts with one of those prefixes (e.g. ``music.now_playing.changed``).
    """

    def __init__(self) -> None:
        # ws → set of subscribed channels. Empty set = subscribed to
        # everything (server-side wildcard, simpler than enumerating).
        self._clients: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients[ws] = set()
        log.info("ws connected; total=%d", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)
        log.info("ws disconnected; total=%d", len(self._clients))

    async def set_subscriptions(self, ws: WebSocket, channels: list[str]) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients[ws] = set(channels)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        """Push a single event to all subscribed clients. Drops clients
        whose send fails — the disconnect path will clean up the
        registry entry."""
        message = json.dumps({"type": event_type, **payload}, default=_json_default)
        async with self._lock:
            targets = [
                ws
                for ws, subs in self._clients.items()
                if not subs or any(event_type.startswith(c) for c in subs)
            ]
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception as e:
                log.debug("ws send failed (will clean up on disconnect): %s", e)


class StatePollLoop:
    """Background task that snapshots state on a cadence, diffs
    against the previous snapshot, and emits change events through
    the broadcaster.

    One tick:
      1. Fetch admin snapshot from domovoi (best-effort; cached
         for route handlers).
      2. Snapshot DB state (acquisitions, calendar window, people last_seen).
      3. Snapshot per-room MPD state (current song / state).
      4. Diff each channel against its previous snapshot.
      5. For each channel that changed, broadcast the new full value.

    The per-channel snapshot helpers are also reachable via
    :py:meth:`emit_for_channel`, which the LISTEN task uses to
    accelerate "this row just changed" feedback to ~50 ms instead
    of waiting for the next 1.5 s tick. Both call sites share the
    same ``_diff_lock`` so a poll-tick diff and a notify-driven diff
    can't race on ``_previous``.

    Exceptions in any one snapshot don't kill the loop — they're
    logged and the next tick proceeds.
    """

    # Channels with a per-channel snapshot helper. The LISTEN task
    # (and the poll tick) calls these by name through
    # :py:meth:`emit_for_channel`. Channels NOT in this map (e.g.
    # ``satellites.presence``, ``satellites.wifi``) are derived from
    # the Domovoi server admin snapshot inside ``_tick`` and only
    # update on the poll cadence.
    _CHANNEL_HELPERS: dict[str, Callable[[], Awaitable[Any]]] = {}

    def __init__(
        self,
        broadcaster: StateBroadcaster,
        interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    ) -> None:
        self._broadcaster = broadcaster
        self._interval_sec = interval_sec
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._previous: dict[str, Any] = {}
        # Held around any "snapshot one channel + diff vs previous +
        # broadcast + update previous" sequence. Without it, a poll
        # tick and a NOTIFY-triggered emit could simultaneously read
        # the same `_previous` value, both decide their fresh
        # snapshot is "different," and double-broadcast the same
        # state.
        self._diff_lock = asyncio.Lock()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="web-state-poll")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def emit_for_channel(self, channel: str) -> None:
        """Run one channel's snapshot helper, diff against previous,
        broadcast if changed. Used both by the poll loop's full sweep
        and by the LISTEN task's NOTIFY-triggered wake-ups."""
        helper = self._CHANNEL_HELPERS.get(channel)
        if helper is None:
            return
        try:
            new_value = await helper()
        except Exception as e:
            log.debug("snapshot %s failed: %s", channel, e)
            return
        async with self._diff_lock:
            old_value = self._previous.get(channel)
            if new_value == old_value:
                return
            self._previous[channel] = new_value
        await self._broadcaster.broadcast(
            f"{channel}.changed", {"data": new_value}
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                log.warning("state poll tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_sec)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        snapshot = await fetch_admin_snapshot()
        set_cached_snapshot(snapshot)

        # Per-channel helpers run via emit_for_channel so they share
        # the diff-lock with the LISTEN task. Concurrent gather is
        # safe — different channels never touch the same _previous key.
        await asyncio.gather(
            *(self.emit_for_channel(c) for c in self._CHANNEL_HELPERS),
            return_exceptions=True,
        )

        # Satellites presence/wifi come from the Domovoi server admin
        # snapshot (in-memory state, not the DB), so there's no
        # per-channel helper for them — diff inline.
        if snapshot is not None:
            for key, new_value in (
                ("satellites.presence", sorted(snapshot.get("active_rooms") or [])),
                ("satellites.wifi", snapshot.get("wifi_status") or {}),
                # Live drop-in pairings (Feature 4). Normalized + sorted so
                # the diff only fires when a call actually starts or ends;
                # the page reacts by refetching /api/satellites (which
                # carries in_call_with).
                (
                    "satellites.dropins",
                    sorted(
                        (
                            {"initiator": c.get("initiator"), "target": c.get("target")}
                            for c in (snapshot.get("active_dropins") or [])
                        ),
                        key=lambda c: (c["initiator"] or "", c["target"] or ""),
                    ),
                ),
                # Video-satellite screen/kiosk state ({room: {on, kiosk_alive,
                # brightness, idle_mode}}). Fires on display toggles and
                # kiosk-death/recovery; the satellites page (and the kiosk
                # display page itself) refetch on it.
                ("satellites.display", snapshot.get("satellite_display") or {}),
            ):
                async with self._diff_lock:
                    old_value = self._previous.get(key)
                    if new_value == old_value:
                        continue
                    self._previous[key] = new_value
                await self._broadcaster.broadcast(
                    f"{key}.changed", {"data": new_value}
                )


# ─── Snapshot helpers ──────────────────────────────────────────────────────


async def _snapshot_acquisitions() -> list[dict[str, Any]]:
    """Live media-acquisition queue rows (pending or claimed — design
    §4.8). Finished rows are stable enough that we don't stream their
    changes; the Jobs readout's list call refreshes them on demand."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, kind, text, requested_by, status, claimed_by,
                       attempts, error, requested_at, completed_at
                FROM media_acquisitions
                WHERE status IN ('pending', 'claimed')
                ORDER BY requested_at DESC
                """
            )
        )
        return [
            {
                "id": int(r[0]),
                "kind": r[1],
                "text": r[2],
                "requested_by": r[3],
                "status": r[4],
                "claimed_by": r[5],
                "attempts": int(r[6]),
                "error": r[7],
                "requested_at": _isoformat(r[8]),
                "completed_at": _isoformat(r[9]),
            }
            for r in rows.all()
        ]


async def _snapshot_calendar() -> list[dict[str, Any]]:
    """Events in the next 30 days (rolling window).

    The Calendar page queries an explicit date range; this stream
    notifies that the window has changed without specifying *what* —
    the page re-fetches its own view.
    """
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=30)
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, title, starts_at, ends_at
                FROM calendar_events
                WHERE starts_at >= :start AND starts_at < :end
                ORDER BY starts_at ASC
                """
            ),
            {"start": now, "end": end},
        )
        return [
            {
                "id": int(r[0]),
                "title": r[1],
                "starts_at": _isoformat(r[2]),
                "ends_at": _isoformat(r[3]),
            }
            for r in rows.all()
        ]


async def _snapshot_people_last_seen() -> dict[str, str | None]:
    """``person_id → last_seen_at iso string`` for everyone known.

    Diff-on-value catches the "Sarah just spoke" case without
    streaming every utterance — the next tick after VoiceProfileHandler
    bumps last_seen_at, the dashboard reflects.
    """
    async with session_scope() as s:
        rows = await s.execute(
            text("SELECT id, last_seen_at FROM people")
        )
        return {str(int(r[0])): _isoformat(r[1]) for r in rows.all()}


async def _snapshot_playlists() -> list[dict[str, Any]]:
    """All playlists with track counts, plus the virtual Favorites
    row pinned at the head — the same shape ``GET /api/playlists``
    returns. The dashboard's Playlists tab subscribes via
    ``useApiList('/api/playlists', { eventTypes: ['playlists.changed'] })``
    and refetches when the diff fires."""
    async with session_scope() as s:
        fav_count = (
            await s.execute(
                text("SELECT COUNT(*) FROM library_tracks WHERE favorited")
            )
        ).scalar_one()
        rows = await s.execute(
            text(
                """
                SELECT p.id, p.name, p.created_at,
                       p.description, p.cover_color, p.cover_emoji,
                       COALESCE(c.track_count, 0)::bigint
                FROM playlists p
                LEFT JOIN (
                    SELECT playlist_id, COUNT(*) AS track_count
                    FROM playlist_tracks
                    GROUP BY playlist_id
                ) c ON c.playlist_id = p.id
                ORDER BY LOWER(p.name) ASC
                """
            )
        )
    out: list[dict[str, Any]] = [
        {
            "id": 0,
            "name": "Favorites",
            "created_at": None,
            "track_count": int(fav_count),
            "is_virtual": True,
            "description": None,
            "cover_color": None,
            "cover_emoji": None,
        }
    ]
    for r in rows.all():
        out.append(
            {
                "id": int(r[0]),
                "name": r[1],
                "created_at": _isoformat(r[2]),
                "description": r[3],
                "cover_color": r[4],
                "cover_emoji": r[5],
                "track_count": int(r[6]),
                "is_virtual": False,
            }
        )
    return out


async def _snapshot_library_index() -> dict[str, Any]:
    """Row count + newest ``added_at`` for ``library_tracks``.

    The Library and Stats views are paginated / filtered, so there's no
    single list to snapshot-and-diff — they just need a "the library
    changed, refetch" nudge. Count + latest-added is a cheap value that
    moves on every insert (upload, indexer sweep) and every delete, so
    the diff fires exactly when there's something new to show and stays
    quiet otherwise. The ``library_changed`` NOTIFY (emitted by
    ``index_music_dir``) accelerates this to sub-second; being in the
    poll loop's helper set means a missed NOTIFY still converges within
    one ~1.5 s tick.

    Deliberately excludes enrichment fields — the enricher updates rows
    continuously during a sweep, and folding ``enriched_at`` in here
    would fire a refetch on every tick of a long enrich run. Enrichment
    progress isn't what this channel is for.
    """
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT COUNT(*)::bigint, MAX(added_at) FROM library_tracks")
            )
        ).one()
    return {"count": int(row[0]), "latest_added": _isoformat(row[1])}


async def _snapshot_wake_words() -> list[dict[str, Any]]:
    """The full custom wake-word registry, default first then
    alphabetical — the same shape ``GET /api/wake-words`` returns.

    Diff-on-value catches the three things the Wake Words tab watches
    live: a recorded clip landing (``clip_count`` bumps from the
    Domovoi server's streaming path), a training status flip (the trainer
    worker), and any web CRUD edit. The dashboard subscribes via
    ``useApiList('/api/wake-words', { eventTypes: ['wake_words.changed'] })``
    and refetches when the diff fires.

    Unlike most snapshot helpers this returns the repository rows
    directly rather than re-deriving the SELECT — wake_words is small and
    the repo already orders + maps the columns the API serves.
    """
    from domovoi.db.repositories import WakeWordsRepository

    async with session_scope() as s:
        return await WakeWordsRepository(s).all()


async def _snapshot_podcasts() -> dict[str, int]:
    """Coarse "spoken-audio library changed" nudge — subscription /
    episode / downloaded / audiobook counts. Diff-on-value fires exactly
    when something the Podcasts or Audiobooks tab shows has changed (a new
    subscription, a downloaded episode, a keep-N eviction, a (re)indexed
    book); the tabs refetch their own detailed lists on the event."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM podcast_subscriptions),
                      (SELECT COUNT(*) FROM podcast_episodes),
                      (SELECT COUNT(*) FROM podcast_episodes WHERE download_status = 'downloaded'),
                      (SELECT COUNT(*) FROM audiobooks)
                    """
                )
            )
        ).one()
    return {
        "subscriptions": int(row[0]),
        "episodes": int(row[1]),
        "downloaded": int(row[2]),
        "audiobooks": int(row[3]),
    }


async def _snapshot_podcast_positions() -> dict[str, Any]:
    """Row count + newest ``updated_at`` for ``playback_positions``. Moves on
    every resume-position save; the tabs refetch their per-item positions on
    the event."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT COUNT(*)::bigint, MAX(updated_at) FROM playback_positions")
            )
        ).one()
    return {"count": int(row[0]), "latest": _isoformat(row[1])}


async def _snapshot_media_jobs() -> list[dict[str, Any]]:
    """Active + just-finished satellite media-prep builds — the same shape
    ``GET /api/satellites/media/jobs`` returns (minus the server-local
    artifact path). The 30 s finished-window mirrors _snapshot_model_jobs."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, board, mic_profile, target_kind, target_ref,
                       status, phase, pct, status_text, error,
                       requested_at, completed_at,
                       (artifact_path IS NOT NULL) AS has_artifact
                FROM satellite_media_jobs
                WHERE status IN ('pending', 'running')
                   OR completed_at > now() - interval '30 seconds'
                ORDER BY requested_at DESC
                """
            )
        )
        return [
            {
                "id": int(r[0]),
                "board": r[1],
                "mic_profile": r[2],
                "target_kind": r[3],
                "target_ref": r[4],
                "status": r[5],
                "phase": r[6],
                "pct": int(r[7]) if r[7] is not None else None,
                "status_text": r[8],
                "error": r[9],
                "requested_at": r[10].isoformat() if r[10] else None,
                "completed_at": r[11].isoformat() if r[11] else None,
                "has_artifact": bool(r[12]),
            }
            for r in rows.all()
        ]


async def _snapshot_model_jobs() -> list[dict[str, Any]]:
    """Active + just-finished Ollama pull jobs — the same
    shape ``GET /api/models/jobs`` returns. Diff-on-value fires as a pull's
    pct advances, so the Models page's install progress bar tracks live. A
    30 s window on finished jobs lets the page reconcile a completed pull
    (flip the card to installed) before it drops off the stream."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, model, status, pct, status_text, error,
                       requested_at, updated_at, completed_at
                FROM model_jobs
                WHERE status IN ('pending', 'running')
                   OR completed_at > now() - interval '30 seconds'
                ORDER BY requested_at DESC
                """
            )
        )
        return [
            {
                "id": int(r[0]),
                "model": r[1],
                "status": r[2],
                "pct": int(r[3]) if r[3] is not None else None,
                "status_text": r[4],
                "error": r[5],
                "requested_at": _isoformat(r[6]),
                "updated_at": _isoformat(r[7]),
                "completed_at": _isoformat(r[8]),
            }
            for r in rows.all()
        ]


async def _snapshot_news() -> dict[str, Any]:
    """Coarse "news library changed" nudge — topic / feed / item / briefing
    counts plus newest item fetched_at. Diff-on-value fires exactly when
    something a News page view shows has changed (a topic added, a feed
    discovered, a fetch landing items, a favorite toggle, a briefing
    regenerated); the page refetches the selected person's detailed lists on
    the event."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM news_topics),
                      (SELECT COUNT(*) FROM news_feeds),
                      (SELECT COUNT(*) FROM news_items),
                      (SELECT COUNT(*) FROM news_items WHERE favorited),
                      (SELECT COUNT(*) FROM news_briefings)
                    """
                )
            )
        ).one()
        latest = (
            await s.execute(text("SELECT MAX(fetched_at) FROM news_items"))
        ).scalar()
    return {
        "topics": int(row[0]),
        "feeds": int(row[1]),
        "items": int(row[2]),
        "favorited": int(row[3]),
        "briefings": int(row[4]),
        "latest_fetched": _isoformat(latest) if latest else None,
    }


async def _snapshot_now_playing() -> dict[str, dict[str, Any]]:
    """Per-room playback state for the diff-and-broadcast pipeline.

    **Intentionally excludes** ``elapsed_sec``. The realtime layer
    diffs whole-snapshot values; if ``elapsed_sec`` were in here, the
    field would change every poll tick (~1.5 s) as a song plays and
    fire ``music.now_playing.changed`` continuously. Every subscriber
    (Satellites page, Music page) would then re-fetch its list every
    cycle — exactly the GET-flood reported on 2026-05-10.

    The frontend already extrapolates elapsed time with a local 1 Hz
    tick from the most recently fetched ``elapsed_sec``; the broadcast
    only needs to fire on real transitions (play / pause / stop /
    skip / new track / new stream). On event, ``useApiList`` re-fetches
    ``/api/music/now-playing`` which DOES return a fresh
    ``elapsed_sec``, so the local clock re-syncs to canonical state.

    Importing `_now_playing_for` here (not at module top) breaks an
    otherwise-cyclic relationship between realtime.py and music.py —
    both want to share the same MPD connection logic but only
    realtime should pull music.py at all.
    """
    from web.backend.api.music import _list_provisioned_rooms, _now_playing_for

    rooms = await _list_provisioned_rooms()
    out: dict[str, dict[str, Any]] = {}
    for room in rooms:
        np_model = await _now_playing_for(room)
        out[room[0]] = {
            "state": np_model.state,
            "song": np_model.song.model_dump() if np_model.song else None,
            # elapsed_sec deliberately omitted — see docstring above.
            "stream_url": np_model.stream_url,
        }
    return out


# Bind the snapshot helpers to the StatePollLoop so emit_for_channel
# can resolve them by channel name. Done outside the class body
# because the helpers themselves are defined further down in the
# module and Python's class-body name resolution doesn't reach them
# at class-definition time.
StatePollLoop._CHANNEL_HELPERS = {
    "acquisitions":      _snapshot_acquisitions,
    "calendar.events":   _snapshot_calendar,
    "people.last_seen":  _snapshot_people_last_seen,
    "music.now_playing": _snapshot_now_playing,
    "playlists":         _snapshot_playlists,
    "library.indexer":   _snapshot_library_index,
    "wake_words":        _snapshot_wake_words,
    "podcasts":          _snapshot_podcasts,
    "podcast_positions": _snapshot_podcast_positions,
    "model_jobs":        _snapshot_model_jobs,
    "news":              _snapshot_news,
    # USB satellite adoption: pending gadget volumes on the server's USB
    # ports (web/backend/satellite_adoption.py — TTL-cached scan, [] when
    # the feature is off). Plug/unplug/status flips push
    # `satellites.pending.changed` so the adopt card appears within a tick.
    "satellites.pending": satellite_adoption.snapshot_pending,
    # Satellite media-prep build jobs (progress bar on the prepare card).
    "satellites.media": _snapshot_media_jobs,
}

# The core realtime channels, frozen BEFORE any plugin mutates the helper
# map at resync. A plugin's [[realtime]] ``realtime_channel`` that collides
# with one of these is refused at mount time (web/backend/plugin_host.py) so
# a plugin declaring e.g. realtime_channel="news" can never overwrite or pop
# the core snapshot helper (design §5.3 namespacing corollary).
CORE_REALTIME_CHANNELS: frozenset[str] = frozenset(StatePollLoop._CHANNEL_HELPERS)


# ─── LISTEN task ───────────────────────────────────────────────────────────


class ListenTask:
    """Long-lived asyncpg connection running ``LISTEN`` on the channels
    declared in :py:data:`NOTIFY_CHANNEL_TO_REALTIME`.

    On each notification, schedules an immediate
    :py:meth:`StatePollLoop.emit_for_channel` for the realtime channel
    that maps to the NOTIFY channel. The poll loop's own diff
    machinery then decides whether to broadcast.

    Designed to be best-effort: if asyncpg raises or the connection
    drops, the task logs, sleeps with exponential backoff, and
    reconnects. The poll loop is still running underneath, so a
    completely-broken LISTEN just degrades us back to 1.5 s polling
    rather than breaking realtime entirely.

    asyncpg requires a bare ``postgresql://...`` DSN — sqlalchemy's
    ``postgresql+asyncpg://...`` form is sqla-specific. The conversion
    happens in :py:meth:`_asyncpg_dsn`.
    """

    _RECONNECT_INITIAL_SEC = 1.0
    _RECONNECT_MAX_SEC = 30.0

    def __init__(self, poll_loop: StatePollLoop) -> None:
        self._poll_loop = poll_loop
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._refresh = asyncio.Event()

    def refresh_channels(self) -> None:
        """Drop and re-establish the LISTEN set (design §5.3) — called
        after a plugin registry resync changes the channel map. The
        current listen session exits cleanly and the run loop reconnects
        against the merged :func:`notify_channel_map`."""
        self._refresh.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="web-state-listen")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        backoff = self._RECONNECT_INITIAL_SEC
        while not self._stop.is_set():
            try:
                await self._listen_session()
                # Clean exit (stop event flipped). Reset backoff for
                # the next start cycle.
                backoff = self._RECONNECT_INITIAL_SEC
            except Exception as e:
                log.warning(
                    "listen session ended with %s: %s; retrying in %.1fs",
                    type(e).__name__, e, backoff,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 1.6, self._RECONNECT_MAX_SEC)

    async def _listen_session(self) -> None:
        """One connect-and-listen cycle. Exits cleanly when
        ``self._stop`` flips; raises on connection errors so ``_run``
        applies its backoff."""
        try:
            import asyncpg  # type: ignore[import]
        except ImportError:
            log.warning(
                "asyncpg not installed — LISTEN/NOTIFY accelerator disabled, "
                "falling back to %.1fs polling for realtime channels."
                " (Install via: pip install asyncpg)",
                self._poll_loop._interval_sec,
            )
            await self._stop.wait()
            return

        dsn = self._asyncpg_dsn()
        conn = await asyncpg.connect(dsn)
        try:
            channel_map = notify_channel_map()
            for notify_channel, realtime_channel in channel_map.items():
                await conn.add_listener(
                    notify_channel,
                    self._make_callback(realtime_channel),
                )
            # The plugin registry channel: any plugins row mutation
            # re-syncs the web host (mount/unmount, manifest cache,
            # realtime map) — design §3.2 step 15.
            await conn.add_listener(
                PLUGINS_CHANGED_CHANNEL, self._make_registry_callback()
            )
            log.info(
                "listen task connected; channels=%s",
                list(channel_map) + [PLUGINS_CHANGED_CHANNEL],
            )
            # Park here until stop OR a channel-map refresh. asyncpg
            # pumps notifications via the event loop; the registered
            # callbacks fire without an explicit poll on this side.
            self._refresh.clear()
            stop_wait = asyncio.create_task(self._stop.wait())
            refresh_wait = asyncio.create_task(self._refresh.wait())
            try:
                await asyncio.wait(
                    {stop_wait, refresh_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                stop_wait.cancel()
                refresh_wait.cancel()
        finally:
            try:
                await asyncio.wait_for(conn.close(), timeout=2.0)
            except Exception:
                pass

    def _make_callback(self, realtime_channel: str):
        """Build an asyncpg listener callback bound to the realtime
        channel. asyncpg invokes callbacks synchronously from the event
        loop, so we kick the actual snapshot work as a fresh task to
        keep the listener responsive to subsequent notifications."""
        loop = asyncio.get_event_loop()

        def _cb(_conn, _pid, _channel, _payload) -> None:
            # `loop.create_task` (rather than `asyncio.create_task`) so
            # we don't accidentally bind to a different event loop if
            # asyncpg ever surprises us by invoking the callback from
            # a worker thread.
            loop.create_task(
                self._poll_loop.emit_for_channel(realtime_channel),
                name=f"listen-emit-{realtime_channel}",
            )

        return _cb

    def _make_registry_callback(self):
        """Callback for ``plugins_changed``: run the plugin-host resync
        (installed by main.py as :data:`ON_PLUGINS_CHANGED`)."""
        loop = asyncio.get_event_loop()

        def _cb(_conn, _pid, _channel, _payload) -> None:
            handler = ON_PLUGINS_CHANGED
            if handler is None:
                return
            loop.create_task(handler(), name="listen-plugins-resync")

        return _cb

    @staticmethod
    def _asyncpg_dsn() -> str:
        """Convert the sqlalchemy DSN (``postgresql+asyncpg://...``) to
        the bare ``postgresql://...`` form asyncpg's ``connect``
        accepts. No-op if the DSN is already bare."""
        return re.sub(
            r"^postgresql\+asyncpg://",
            "postgresql://",
            core_settings.database_url,
            count=1,
        )


# ─── JSON helpers ──────────────────────────────────────────────────────────


def _isoformat(value: Any) -> str | None:
    """``datetime`` → ISO 8601 string. Pass-through for ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_default(value: Any) -> Any:
    """Fallback for `json.dumps` so datetimes serialize without an
    explicit conversion at every snapshot site."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unserializable: {type(value).__name__}")
