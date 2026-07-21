"""MusicBrainz lookup for cleaning up external-media metadata.

Best-effort: returns None on any error (network, timeout, no match,
malformed response, low score). Media-provider download pipelines call
this after a successful download to enrich metadata, and never replaces the existing
row when the lookup fails — the user always gets *some* metadata.

Rate limiting: MusicBrainz's terms of use ask for ≤1 req/sec and a
descriptive User-Agent. We honor both. Per-download lookups (not
per-request) keep us comfortably under the cap.

The cleanup pass before sending the query strips common external-upload
title noise ("(Official Music Video)", "[HD]", "feat. ..."). Without
this the search score for a typical fan-upload title hovers around
60–75 and we'd reject real matches.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from domovoi.config import settings

log = logging.getLogger(__name__)


@dataclass
class MusicBrainzMatch:
    recording_id: str
    artist_id: str | None
    release_id: str | None
    title: str
    artist: str
    album: str | None


class MusicBrainzClient(Protocol):
    async def lookup(self, *, title: str, artist: str | None) -> MusicBrainzMatch | None: ...


class MusicBrainzStubClient:
    """Always returns None — used in USE_STUBS mode."""

    async def lookup(self, *, title: str, artist: str | None) -> MusicBrainzMatch | None:
        return None


_NOISE_PATTERNS = [
    # Bracketed annotations: (Official Video), [HD], (Lyrics), etc.
    re.compile(
        r"\s*[\[(](?:official|lyrics?|hd|4k|live|mv|m/v|audio|video|visualizer|"
        r"music\s*video|topic|color\s*coded|sub|sped\s*up|slowed)\b[^\])]*[\])]",
        re.IGNORECASE,
    ),
    # "feat. X" / "ft. X" parentheticals
    re.compile(r"\s*[\[(](?:feat\.?|ft\.?)\s+[^\])]*[\])]", re.IGNORECASE),
    # Trailing year tags like "[2019]"
    re.compile(r"\s*[\[(]\d{4}[\])]"),
]


def _clean_media_title(title: str) -> tuple[str, str | None]:
    """Strip common external-upload noise from titles.

    Returns (cleaned_title, extracted_artist_or_None). The artist is only
    extracted from the "Artist - Title" pattern; in all other cases the
    caller's existing `artist` field is the source of truth.
    """
    t = title.strip()

    extracted_artist: str | None = None
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
    if m:
        extracted_artist = m.group(1).strip()
        t = m.group(2).strip()

    for pat in _NOISE_PATTERNS:
        t = pat.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()

    return t, extracted_artist


class HttpMusicBrainzClient:
    """Live MusicBrainz lookup over HTTP.

    Uses `requests` via `asyncio.to_thread` (small, already-common dep). Rate
    limiting is enforced by an asyncio.Lock + sleep based on the time of
    the last request.
    """

    USER_AGENT = "domovoi/1.0.0 (+https://github.com/coders-farm-official/domovoi)"
    BASE = "https://musicbrainz.org/ws/2"
    MIN_INTERVAL_SEC = 1.0
    REQUEST_TIMEOUT_SEC = 5.0
    MIN_SCORE = 80  # MB score is 0–100; below 80 is not reliable enough to overwrite metadata

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    async def lookup(self, *, title: str, artist: str | None) -> MusicBrainzMatch | None:
        cleaned_title, extracted_artist = _clean_media_title(title)
        if not cleaned_title:
            return None
        if not artist:
            artist = extracted_artist

        q_parts = [f'recording:"{cleaned_title}"']
        if artist:
            q_parts.append(f'artist:"{artist}"')
        query = " AND ".join(q_parts)

        data = await self._rate_limited_get(
            "recording", {"query": query, "fmt": "json", "limit": "1"}
        )
        if not data:
            return None

        recordings = data.get("recordings") or []
        if not recordings:
            return None

        rec = recordings[0]
        score = int(rec.get("score") or 0)
        if score < self.MIN_SCORE:
            log.debug(
                "MB low score (%d) for title=%r artist=%r — skipping",
                score, cleaned_title, artist,
            )
            return None

        artist_credit = rec.get("artist-credit") or []
        artist_name = artist_credit[0].get("name") if artist_credit else None
        artist_id = (artist_credit[0].get("artist") or {}).get("id") if artist_credit else None

        releases = rec.get("releases") or []
        release_id = releases[0].get("id") if releases else None
        album = releases[0].get("title") if releases else None

        recording_id = rec.get("id")
        if not recording_id:
            return None

        return MusicBrainzMatch(
            recording_id=recording_id,
            artist_id=artist_id,
            release_id=release_id,
            title=rec.get("title") or cleaned_title,
            artist=artist_name or artist or "",
            album=album,
        )

    async def _rate_limited_get(
        self, path: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        async with self._lock:
            wait = self.MIN_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                data = await asyncio.to_thread(self._sync_get, path, params)
            finally:
                self._last_request_at = time.monotonic()
            return data

    def _sync_get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        import requests

        try:
            r = requests.get(
                f"{self.BASE}/{path}",
                params=params,
                headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"},
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except Exception as e:
            log.debug("MusicBrainz request raised: %s", e)
            return None
        if r.status_code != 200:
            log.debug("MusicBrainz returned status %d", r.status_code)
            return None
        try:
            return r.json()
        except ValueError:
            return None


_client: MusicBrainzClient | None = None


def get_musicbrainz_client() -> MusicBrainzClient:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = MusicBrainzStubClient()
    else:
        _client = HttpMusicBrainzClient()
    return _client
