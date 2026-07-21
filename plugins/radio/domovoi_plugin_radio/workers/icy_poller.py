"""ICY-metadata "now playing" poller for favorited radio stations.

Lighter-weight companion to :mod:`.sampler`: one HTTP GET with
``Icy-MetaData: 1`` and a regex on the ``StreamTitle`` block — enough
for ~90 % of internet stations and essentially free per poll.

The two detectors coexist:

* ``icy_supported = TRUE`` stations are served entirely by this worker
  (the sampler's due-SELECT excludes them).
* ``icy_supported = FALSE`` (confirmed no ICY after consecutive misses)
  fall through to the sampler only.
* ``NULL`` (unknown) get both — the poller probes while the sampler
  keeps detection alive.

On every successful title *transition*, write a detection row with
``fingerprint_source = 'icy'`` and, when the track isn't in the library
already, enqueue an acquisition — the same
:mod:`.detection_store` tail the sampler uses.

A sanity filter guards the enqueue (ICY values are wilder than the
identify chain's — stations advertise callsigns, ads, weather in
StreamTitle): too-short strings, URL/filename-looking strings, a small
non-song denylist, and the station's own name are never enqueued. A
detection row IS still written for every transition so the UI's
now-playing display stays honest ("the DJ is talking about weather").

**Single-process landmine, preserved deliberately (design §9.1):** the
consecutive-miss counter behind the ``icy_supported`` tristate lives in
memory (``self._misses``) because (a) it's short-lived (resets on first
success), (b) plugins run in-process on the core event loop — locked 1 —
so there is no other writer, and (c) it stays out of migration churn.
If workers ever leave the process, THIS counter is the thing to move
into a column.
"""

from __future__ import annotations

import logging
import asyncio
import re
from typing import Any, NamedTuple

from sqlalchemy import text

from domovoi.sdk import PluginSDK, Worker

from domovoi_plugin_radio.clients import icy_metadata
from domovoi_plugin_radio.workers import detection_store

log = logging.getLogger(__name__)


# After this many consecutive polls without an icy-metaint header, flip
# ``icy_supported`` to FALSE so the sampler takes over. Three rides out
# one flaky response without letting an unsupported station ping forever.
MISSES_BEFORE_UNSUPPORTED = 3

# "This StreamTitle is obviously not a song." Case-folded full-title
# match. Keep it SMALL — long denylists swallow real songs ("News for
# Lulu" is a Sonny Sharrock album).
_NON_SONG_DENYLIST = frozenset({
    "ad break",
    "ads",
    "advertisement",
    "commercial",
    "commercial break",
    "news",
    "news break",
    "talk",
    "talk segment",
    "weather",
    "traffic",
    "live",
    "live stream",
    "now playing",
    "unknown",
    "unknown song",
    "untitled",
})

# A StreamTitle that looks like a URL or filename — usually a station
# leaking its automation playlist path.
_URL_OR_FILE_RE = re.compile(
    r"^\s*(?:https?://|file://|/|[A-Za-z]:\\|.+\.(?:mp3|m4a|wav|ogg|flac|aac)\s*$)",
    re.IGNORECASE,
)


class _StationRow(NamedTuple):
    """One favorited station the poller is about to ICY-probe.
    ``now_playing`` is the cached title — transitions are detected by
    comparing against it, no second SQL round-trip."""

    id: int
    name: str
    stream_url: str
    icy_supported: bool | None
    now_playing: str | None


class RadioIcyPoller(Worker):
    name = "radio_icy_poller"
    enabled_setting = "icy_poller_enabled"
    interval_setting = "icy_poll_interval_sec"
    stub_suppressed = True
    requires_online = True       # pure HTTP polling; skip ticks offline

    def __init__(self, sdk: PluginSDK) -> None:
        self.sdk = sdk
        self._sem = asyncio.Semaphore(
            int(getattr(sdk.config, "icy_concurrency", 10))
        )
        # Per-station consecutive-miss count — in-memory ON PURPOSE, see
        # module docstring.
        self._misses: dict[int, int] = {}

    @property
    def _config(self) -> Any:
        return self.sdk.config

    def _client(self) -> icy_metadata.IcyClient:
        return icy_metadata.get_icy_client(
            use_stubs=bool(self.sdk.core_config.use_stubs),
            timeout_sec=float(self._config.icy_request_timeout_sec),
        )

    async def tick(self) -> int:
        """Poll every due station; returns how many were polled."""
        due = await self._select_due_stations()
        if not due:
            return 0
        await asyncio.gather(
            *(self._poll_one(row) for row in due),
            return_exceptions=True,
        )
        return len(due)

    # ─── Selection ─────────────────────────────────────────────────────

    async def _select_due_stations(self) -> list[_StationRow]:
        """Favorited stations whose tristate is not FALSE and whose
        ``last_icy_poll_at`` is older than the interval. The WHERE
        clause matches the V001 partial index TEXTUALLY — keep them in
        lockstep."""
        async with self.sdk.db.session_scope() as s:
            rows = await s.execute(
                text(
                    """
                    SELECT id, name, stream_url, icy_supported, now_playing
                    FROM radio_stations
                    WHERE favorited
                      AND stream_url IS NOT NULL
                      AND (icy_supported IS NULL OR icy_supported = TRUE)
                      AND (last_icy_poll_at IS NULL
                           OR last_icy_poll_at
                              + (:interval * INTERVAL '1 second') < NOW())
                    ORDER BY last_icy_poll_at NULLS FIRST, id
                    LIMIT 100
                    """
                ),
                {"interval": int(self._config.icy_poll_interval_sec)},
            )
            return [
                _StationRow(
                    id=int(r[0]),
                    name=str(r[1]),
                    stream_url=str(r[2]),
                    icy_supported=r[3],
                    now_playing=r[4],
                )
                for r in rows.all()
            ]

    # ─── Per-station pipeline ──────────────────────────────────────────

    async def _poll_one(self, row: _StationRow) -> None:
        async with self._sem:
            await self._do_poll(row)

    async def _do_poll(self, row: _StationRow) -> None:
        result = await self._client().fetch(row.stream_url)

        if result.supported:
            self._misses.pop(row.id, None)
        else:
            self._misses[row.id] = self._misses.get(row.id, 0) + 1

        # Resolve the new tristate before writing.
        new_icy_supported: bool | None
        if result.supported:
            new_icy_supported = True
        elif self._misses.get(row.id, 0) >= MISSES_BEFORE_UNSUPPORTED:
            new_icy_supported = False
        else:
            new_icy_supported = row.icy_supported     # still probing

        # No usable title → bookkeeping only; the cached now_playing
        # stays put.
        if not result.stream_title:
            await self._write_poll_bookkeeping(
                row.id, icy_supported=new_icy_supported, title_update=None
            )
            return

        new_title = result.stream_title.strip()
        is_transition = new_title != (row.now_playing or "")

        await self._write_poll_bookkeeping(
            row.id, icy_supported=new_icy_supported, title_update=new_title
        )

        if is_transition:
            await self._record_transition(
                station_id=row.id, station_name=row.name, new_title=new_title
            )

    async def _write_poll_bookkeeping(
        self,
        station_id: int,
        *,
        icy_supported: bool | None,
        title_update: str | None,
    ) -> None:
        """``last_icy_poll_at`` + optionally the tristate + the
        now-playing cache in one round-trip; always NOTIFY (the UI's
        "X seconds ago" freshness indicator depends on no-op polls too;
        the payload cost is tiny)."""
        async with self.sdk.db.session_scope() as s:
            if title_update is None:
                await s.execute(
                    text(
                        """
                        UPDATE radio_stations
                        SET last_icy_poll_at = NOW(),
                            icy_supported = :icy
                        WHERE id = :id
                        """
                    ),
                    {"id": station_id, "icy": icy_supported},
                )
            else:
                await s.execute(
                    text(
                        """
                        UPDATE radio_stations
                        SET last_icy_poll_at = NOW(),
                            icy_supported = :icy,
                            now_playing = :title,
                            now_playing_updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": station_id, "icy": icy_supported, "title": title_update},
                )
            await self.sdk.realtime.notify(
                s, "now_playing_changed", str(station_id)
            )

    # ─── Transition handling (detection recording) ─────────────────────

    async def _record_transition(
        self, *, station_id: int, station_name: str, new_title: str
    ) -> None:
        artist, title = split_artist_title(new_title)

        async with self.sdk.db.session_scope() as s:
            in_library = await detection_store.is_in_library_fuzzy(
                self.sdk, s, title, artist
            )
            det_id = await detection_store.insert_detection(
                s,
                station_id=station_id,
                artist=artist,
                title=title,
                fingerprint_source="icy",
                in_library=in_library,
            )

            await self.sdk.realtime.notify(s, "detections_changed", "icy")

        if det_id is not None:
            # Broadcast the observation AFTER commit. Radio only reports
            # what it heard; whether any subscriber acts on it is that
            # subscriber's business. ICY titles include ads/show names,
            # so the payload carries the is_likely_song verdict.
            self.sdk.events.emit(
                "detection_recorded",
                {
                    "detection_id": det_id,
                    "station_id": station_id,
                    "station_name": station_name,
                    "artist": artist,
                    "title": title,
                    "fingerprint_source": "icy",
                    "in_library": in_library,
                    "library_track_id": None,
                    "likely_song": is_likely_song(
                        new_title, station_name=station_name
                    ),
                },
            )


# ─── Pure helpers ───────────────────────────────────────────────────────


def split_artist_title(stream_title: str) -> tuple[str | None, str]:
    """Best-effort split of an ICY StreamTitle into (artist, title).

    Stations overwhelmingly use ``"Artist - Title"``; some send a bare
    title. A minority use ``"Title - Artist"`` with no reliable way to
    detect it — accept the dominant convention.
    """
    # Em/en-dash variants show up in European automation systems.
    normalized = stream_title.replace("—", "-").replace("–", "-")
    parts = normalized.split(" - ", 1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None, stream_title.strip()


def is_likely_song(stream_title: str, *, station_name: str) -> bool:
    """Should this title trigger an acquisition? Errs on the side of NOT
    enqueuing: a missed song re-detects later (it will play again), but
    a spurious enqueue costs provider time and pollutes the library with
    whatever a search for "weather" returns."""
    title = stream_title.strip()
    if len(title) < 5:
        return False
    folded = title.casefold()
    if folded in _NON_SONG_DENYLIST:
        return False
    if folded == station_name.strip().casefold():
        return False
    if _URL_OR_FILE_RE.match(title):
        return False
    return True
