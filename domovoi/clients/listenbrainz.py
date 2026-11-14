"""ListenBrainz: similar artists and song popularity, for Up next (P7).

Only the core's background worker (:mod:`domovoi.workers.listenbrainz_fetch`)
uses this client. The Up next picker (:mod:`domovoi.up_next`) never does:
it reads what the worker stored, so building a queue never waits on, or
changes with, the network (owner rule 2026-11-14;
``tests/test_up_next_offline.py`` checks the picker doesn't import this).

What is sent: MusicBrainz ids (public catalogue identifiers) of the
library's artists and songs, and a User-Agent naming the project. No
titles, no names, no play history, no person. ListenBrainz needs no
account or key for these reads, and its data is CC0.

* :meth:`HttpListenBrainzClient.similar_artists` —
  ``GET labs.api.listenbrainz.org/similar-artists/json`` for one artist
  (its similar-artists dataset; :data:`SIMILAR_ALGORITHM` is the dataset's
  name, which ListenBrainz changes now and then: pinned here, one place).
* :meth:`HttpListenBrainzClient.recording_popularity` —
  ``POST api.listenbrainz.org/1/popularity/recording`` with up to
  :data:`POPULARITY_BATCH` recording ids (a read: no token, nothing is
  written there).

Polite: one request at a time, at least :attr:`MIN_INTERVAL_SEC` apart,
process-wide; a 429 or 503 (or the rate-limit header saying none are
left) raises :class:`ListenBrainzRateLimited` with how long to wait.
Every request passes ``egress.require_destination`` first, so nothing
leaves the house under the internet answer "No". Redirects are never
followed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Mapping, Protocol, Sequence

from domovoi import egress

log = logging.getLogger(__name__)

API_BASE = "https://api.listenbrainz.org/1"
LABS_BASE = "https://labs.api.listenbrainz.org"
#: The similar-artists dataset (verified 2026-11-14: 100 artists with a
#: score each for a well-known artist).
SIMILAR_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
)
#: Recording ids per popularity request.
POPULARITY_BATCH = 100


def user_agent() -> str:
    """The project's name, version and address: no person, no house."""
    try:
        from importlib.metadata import version

        v = version("domovoi")
    except Exception:  # noqa: BLE001 — a source checkout without metadata
        v = "dev"
    return f"domovoi/{v} (+https://github.com/coders-farm-official/domovoi)"


class ListenBrainzUnavailable(Exception):
    """No proper answer (unreachable, refused, internet off, too large)."""


class ListenBrainzRateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited for {retry_after:.0f} s")
        self.retry_after = retry_after


class ListenBrainzClient(Protocol):
    async def similar_artists(self, artist_mbid: str) -> list[tuple[str, float]]: ...

    async def recording_popularity(
        self, recording_mbids: Sequence[str],
    ) -> dict[str, tuple[int | None, int | None]]: ...


def parse_similar(data: Any, artist_mbid: str) -> list[tuple[str, float]]:
    """``[(similar mbid, score)]`` from the labs answer, best first,
    without the artist itself or anything malformed."""
    out: dict[str, float] = {}
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, Mapping):
                continue
            mbid = row.get("artist_mbid")
            score = row.get("score")
            if not isinstance(mbid, str) or len(mbid) != 36 or mbid == artist_mbid:
                continue
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            out[mbid] = max(out.get(mbid, 0.0), float(score))
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


def parse_popularity(data: Any, asked: Sequence[str]) -> dict[str, tuple[int | None, int | None]]:
    """``{recording mbid: (listens, listeners)}`` for every id asked;
    (None, None) for one ListenBrainz had no figure for."""
    out: dict[str, tuple[int | None, int | None]] = {m: (None, None) for m in asked}
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, Mapping):
                continue
            mbid = row.get("recording_mbid")
            if mbid not in out:
                continue
            lc, uc = row.get("total_listen_count"), row.get("total_user_count")
            lc = int(lc) if isinstance(lc, int) and not isinstance(lc, bool) and lc >= 0 else None
            uc = int(uc) if isinstance(uc, int) and not isinstance(uc, bool) and uc >= 0 else None
            out[mbid] = (lc, uc)
    return out


class HttpListenBrainzClient:
    MIN_INTERVAL_SEC = 1.0
    TIMEOUT_SEC = 10.0
    MAX_BODY_BYTES = 2 * 1024 * 1024
    #: Pause when ListenBrainz says to slow down without saying how long.
    DEFAULT_RETRY_SEC = 60.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def similar_artists(self, artist_mbid: str) -> list[tuple[str, float]]:
        data = await self._request(
            "GET", f"{LABS_BASE}/similar-artists/json",
            params={"artist_mbids": artist_mbid, "algorithm": SIMILAR_ALGORITHM},
        )
        return parse_similar(data, artist_mbid)

    async def recording_popularity(
        self, recording_mbids: Sequence[str],
    ) -> dict[str, tuple[int | None, int | None]]:
        asked = list(dict.fromkeys(recording_mbids))[:POPULARITY_BATCH]
        if not asked:
            return {}
        data = await self._request(
            "POST", f"{API_BASE}/popularity/recording", body={"recording_mbids": asked})
        return parse_popularity(data, asked)

    async def _request(self, method: str, url: str, *, params: dict | None = None,
                       body: dict | None = None) -> Any:
        async with self._lock:
            wait = self._last + self.MIN_INTERVAL_SEC - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            extra = 0.0
            try:
                data, extra = await asyncio.to_thread(self._sync, method, url, params, body)
                return data
            finally:
                # Rate-limit header at 0: this answer counts, the next waits.
                self._last = time.monotonic() + extra

    def _sync(self, method: str, url: str, params: dict | None,
              body: dict | None) -> tuple[Any, float]:
        import requests

        try:
            egress.require_destination(url)
        except egress.InternetTurnedOff as e:
            raise ListenBrainzUnavailable("internet off") from e
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        try:
            r = requests.request(
                method, url, params=params, json=body, headers=headers,
                timeout=self.TIMEOUT_SEC, allow_redirects=False, stream=True,
            )
        except Exception as e:
            raise ListenBrainzUnavailable(type(e).__name__) from e
        try:
            if r.status_code in (429, 503):
                raise ListenBrainzRateLimited(_retry_after(r.headers, self.DEFAULT_RETRY_SEC))
            if r.status_code == 404:
                return [], 0.0  # ListenBrainz has nothing for that id
            if r.status_code != 200:
                raise ListenBrainzUnavailable(f"HTTP {r.status_code}")
            raw = bytearray()
            for chunk in r.iter_content(64 * 1024):
                raw += chunk
                if len(raw) > self.MAX_BODY_BYTES:
                    raise ListenBrainzUnavailable("answer too large")
            try:
                data = json.loads(bytes(raw))
            except (ValueError, RecursionError) as e:
                raise ListenBrainzUnavailable("not JSON") from e
            extra = 0.0
            if str(r.headers.get("X-RateLimit-Remaining", "")).strip() == "0":
                extra = _retry_after(r.headers, 0.0)
            return data, extra
        finally:
            r.close()


def _retry_after(headers: Mapping[str, str], default: float) -> float:
    for key in ("X-RateLimit-Reset-In", "Retry-After"):
        try:
            v = float(str(headers.get(key, "")).strip())
        except ValueError:
            continue
        if 0 <= v <= 3600:
            return max(v, 1.0)
    return default


_client: ListenBrainzClient | None = None


def get_listenbrainz_client() -> ListenBrainzClient:
    global _client
    if _client is None:
        _client = HttpListenBrainzClient()
    return _client


__all__ = [
    "API_BASE", "LABS_BASE", "POPULARITY_BATCH", "SIMILAR_ALGORITHM",
    "HttpListenBrainzClient", "ListenBrainzClient", "ListenBrainzRateLimited",
    "ListenBrainzUnavailable", "get_listenbrainz_client", "parse_popularity",
    "parse_similar", "user_agent",
]
