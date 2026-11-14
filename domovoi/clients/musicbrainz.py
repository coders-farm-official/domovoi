"""MusicBrainz lookups: recording metadata cleanup, and artist search.

Two callers, one client:

* **Recording lookup** (:meth:`HttpMusicBrainzClient.lookup`) cleans up
  external-media metadata. Best-effort: returns None on any error
  (network, timeout, no match, malformed response, low score). Media-
  provider download pipelines call this after a successful download to
  enrich metadata, and never replace the existing row when the lookup
  fails — the user always gets *some* metadata.
* **Recording date** (:meth:`HttpMusicBrainzClient.recording_year`) feeds
  the library's year fallback (``domovoi/workers/library_year_fetch.py``):
  one recording id in, its ``first-release-date`` year out. It raises like
  the artist search, so the fetch backs off rather than recording "no
  year" for an outage. The recording lookup above keeps the same date
  from its search answer (:attr:`MusicBrainzMatch.first_release_date`),
  so a new enrichment carries the year with no extra request.
* **Recording details** (:meth:`HttpMusicBrainzClient.recording_details`)
  is the same lookup with ``inc=genres+artist-credits+releases+release-groups``
  (one request): the year as above, and a genre for a track whose file
  names none (:func:`recording_genres`: the recording's own genres, else
  its albums' (release groups'), else its first credited artist's — the
  ``genres`` inc applies to the linked release groups and artists in the
  same answer, so no per-artist request is made). A recording on hundreds
  of releases can answer more than :attr:`HttpMusicBrainzClient.MAX_BODY_BYTES`;
  then it is asked once more without ``releases`` (recording and artist
  genres only), so it is never "no answer" for ever.
* **Artist search** (:meth:`HttpMusicBrainzClient.search_artists`) feeds the
  opt-in "also called" fetch (``domovoi/workers/library_alias_fetch.py``):
  the spoken names people use for an artist ("Tec 9" for Tech N9ne).
  Unlike the lookup it says WHY nothing came back — a rate limit
  (:class:`MusicBrainzRateLimited`), an unreachable or failing server
  (:class:`MusicBrainzUnavailable`: no answer, a 5xx, a 200 that is not
  JSON) or a refused request (:class:`MusicBrainzRejected`: any other 4xx)
  — so the fetch can back off instead of recording "MusicBrainz has no
  such artist". Only a 200 with a JSON body is a verdict.

Rate limiting: MusicBrainz's terms of use ask for ≤1 req/sec and a
descriptive User-Agent. We honor both, process-wide: every request either
method makes goes through the one client's lock (``get_musicbrainz_client``
hands out a single instance), so the alias fetch and a download's lookup
never add up to more than one request a second between them.

The cleanup pass before sending the query strips common external-upload
title noise ("(Official Music Video)", "[HD]", "feat. ..."). Without
this the search score for a typical fan-upload title hovers around
60–75 and we'd reject real matches.

MusicBrainz data is CC0.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from domovoi import egress
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
    # The recording's first release ("1997", "1997-05-21"), as MusicBrainz
    # gives it; None when it gives none. ``year`` is its year.
    first_release_date: str | None = None

    @property
    def year(self) -> int | None:
        return year_of_date(self.first_release_date)


# A MusicBrainz id: a UUID, lower-case hex as MusicBrainz writes it. Only
# such a string is ever put into a request path.
_MBID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def is_mbid(value: object) -> bool:
    return isinstance(value, str) and _MBID_RE.fullmatch(value) is not None


def year_of_date(value: object) -> int | None:
    """The year of a MusicBrainz date ("1997", "1997-05", "1997-05-21");
    None for a missing, empty or implausible one (the library's year rules,
    ``domovoi.library_genres.parse_year``)."""
    from domovoi.library_genres import parse_year

    if not isinstance(value, str) or not value.strip():
        return None
    return parse_year(value.strip()[:4]) if value.strip()[:4].isdigit() else None


@dataclass(frozen=True)
class MbAlias:
    """One alias of a MusicBrainz artist, as the search API returns it.
    ``type`` is "Artist name", "Legal name", "Search hint" or None;
    ``locale`` e.g. "en", "en_US", "ja" or None."""

    name: str
    type: str | None = None
    locale: str | None = None
    primary: bool = False


@dataclass(frozen=True)
class MbArtist:
    """One artist hit of an artist search. ``score`` is MusicBrainz's own
    0-100 relevance; ``type`` "Person", "Group", "Orchestra", "Choir",
    "Character", "Other" or None (unknown)."""

    id: str
    name: str
    sort_name: str | None
    type: str | None
    score: int
    disambiguation: str | None = None
    aliases: list[MbAlias] = field(default_factory=list)


class MusicBrainzRateLimited(Exception):
    """MusicBrainz answered 503 or 429: slow down."""


class MusicBrainzUnavailable(Exception):
    """MusicBrainz could not be reached (network error, timeout) or did not
    answer properly (a 5xx other than 503, a 200 whose body is not JSON) —
    a temporary failure, never "no such artist"."""


class MusicBrainzRejected(Exception):
    """MusicBrainz refused the request (a 4xx other than 429): an error to
    retry later with a backoff, never "no such artist"."""


class MusicBrainzTooLarge(MusicBrainzUnavailable):
    """The answer was longer than the client reads (``MAX_BODY_BYTES``)."""


@dataclass(frozen=True)
class MbRecordingDetails:
    """What the library asks MusicBrainz about one recording: its
    first-release year, and its genre keys (``domovoi.library_genres``
    keys, best first, at most :data:`MAX_MB_GENRES`) with where they came
    from: "recording", "release-group", "artist", or None (no genre)."""

    year: int | None = None
    genres: tuple[str, ...] = ()
    genre_from: str | None = None


#: Most genres kept from MusicBrainz for one track (the top-voted).
MAX_MB_GENRES = 3
#: Most genre entries looked at in one list, releases looked at in one
#: answer, and artist credits looked at: an answer is whatever the server
#: (or something in between) says.
MAX_MB_GENRE_ENTRIES = 64
MAX_MB_RELEASES = 500
MAX_MB_CREDITS = 16


def _vote_count(value: Any) -> int:
    """A genre's vote count: an int above zero, else 0 (ignored). A bool
    is not a count; a float or a string is not either."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _genre_votes(genres: Any) -> dict[str, int]:
    """Normalised genre key -> votes, of one MusicBrainz ``genres`` list
    (``[{"name": "hip hop", "count": 4}, ...]``). Names go through
    ``domovoi.library_genres`` (bounded first, then normalised and
    aliased) so they match the keys the files' tags give; a name that
    normalises to nothing, or a vote count of 0 or less, is dropped."""
    from domovoi.library_genres import MAX_RAW_CHARS, genre_keys

    out: dict[str, int] = {}
    if not isinstance(genres, list):
        return out
    for entry in genres[:MAX_MB_GENRE_ENTRIES]:
        if not isinstance(entry, Mapping):
            continue
        name, votes = entry.get("name"), _vote_count(entry.get("count"))
        if votes <= 0 or not isinstance(name, str) or len(name) > MAX_RAW_CHARS:
            continue
        keys = genre_keys([name])
        if keys:
            out[keys[0]] = out.get(keys[0], 0) + votes
    return out


def _top(votes: Mapping[str, int]) -> tuple[str, ...]:
    """The top-voted keys, most votes first (ties: first seen), at most
    :data:`MAX_MB_GENRES`."""
    order = {k: i for i, k in enumerate(votes)}
    ranked = sorted(votes, key=lambda k: (-votes[k], order[k]))
    return tuple(ranked[:MAX_MB_GENRES])


def recording_genres(data: Any) -> tuple[tuple[str, ...], str | None]:
    """The genre keys of a recording lookup answered with
    ``inc=genres+artist-credits+releases+release-groups``, and where they
    came from. In order, the first that has any:

    1. the recording's own ``genres``;
    2. its release groups' (each album or single it is on, once): votes
       added up over the plain ones (no secondary type: not a compilation,
       live album, soundtrack, remix ...), or over all of them when no
       plain one has a genre;
    3. its first credited artist's.

    Only the top-voted :data:`MAX_MB_GENRES` are kept; votes of 0 or less
    are ignored. ``((), None)`` when none has a genre or the answer is not
    a lookup at all."""
    if not isinstance(data, Mapping):
        return (), None
    own = _genre_votes(data.get("genres"))
    if own:
        return _top(own), "recording"

    plain: dict[str, int] = {}
    every: dict[str, int] = {}
    seen: set[str] = set()
    releases = data.get("releases")
    for rel in (releases if isinstance(releases, list) else [])[:MAX_MB_RELEASES]:
        rg = rel.get("release-group") if isinstance(rel, Mapping) else None
        if not isinstance(rg, Mapping):
            continue
        rg_id = rg.get("id")
        if isinstance(rg_id, str):
            if rg_id in seen:
                continue
            seen.add(rg_id)
        votes = _genre_votes(rg.get("genres"))
        secondary = rg.get("secondary-types")
        is_plain = not (isinstance(secondary, list) and secondary)
        for k, n in votes.items():
            every[k] = every.get(k, 0) + n
            if is_plain:
                plain[k] = plain.get(k, 0) + n
    if plain or every:
        return _top(plain or every), "release-group"

    credits = data.get("artist-credit")
    for credit in (credits if isinstance(credits, list) else [])[:MAX_MB_CREDITS]:
        artist = credit.get("artist") if isinstance(credit, Mapping) else None
        if isinstance(artist, Mapping):
            votes = _genre_votes(artist.get("genres"))
            return (_top(votes), "artist") if votes else ((), None)
    return (), None


def recording_details_of(data: Any) -> MbRecordingDetails:
    """:class:`MbRecordingDetails` of one recording lookup's JSON."""
    if not isinstance(data, Mapping):
        return MbRecordingDetails()
    genres, source = recording_genres(data)
    return MbRecordingDetails(year=year_of_date(data.get("first-release-date")),
                              genres=genres, genre_from=source)


class MusicBrainzClient(Protocol):
    async def lookup(self, *, title: str, artist: str | None) -> MusicBrainzMatch | None: ...

    async def search_artists(self, name: str, *, limit: int = 5) -> list[MbArtist]: ...

    async def recording_year(self, recording_id: str) -> int | None: ...

    async def recording_details(self, recording_id: str) -> MbRecordingDetails: ...


class MusicBrainzStubClient:
    """Always returns nothing — used in USE_STUBS mode."""

    async def lookup(self, *, title: str, artist: str | None) -> MusicBrainzMatch | None:
        return None

    async def search_artists(self, name: str, *, limit: int = 5) -> list[MbArtist]:
        return []

    async def recording_year(self, recording_id: str) -> int | None:
        return None

    async def recording_details(self, recording_id: str) -> MbRecordingDetails:
        return MbRecordingDetails()


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


def artist_search_query(name: str) -> str:
    """The Lucene query for an artist search: the name as one quoted
    phrase in the ``artist`` field. Inside a quoted phrase only ``\\`` and
    ``"`` are special, so those two are escaped and nothing else is."""
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'artist:"{escaped}"'


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_artist_search(data: Mapping[str, Any] | None) -> list[MbArtist]:
    """The artists of an ``/ws/2/artist?query=…&fmt=json`` response, in the
    order MusicBrainz ranked them. Malformed entries (no id or name) are
    skipped; a malformed response is an empty list."""
    if not isinstance(data, Mapping):
        return []
    out: list[MbArtist] = []
    for raw in data.get("artists") or []:
        if not isinstance(raw, Mapping):
            continue
        artist_id, name = _opt_str(raw.get("id")), _opt_str(raw.get("name"))
        if not artist_id or not name:
            continue
        try:
            score = int(raw.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        aliases: list[MbAlias] = []
        for a in raw.get("aliases") or []:
            if not isinstance(a, Mapping) or not _opt_str(a.get("name")):
                continue
            aliases.append(MbAlias(
                name=a["name"],
                type=_opt_str(a.get("type")),
                locale=_opt_str(a.get("locale")),
                primary=bool(a.get("primary")),
            ))
        out.append(MbArtist(
            id=artist_id,
            name=name,
            sort_name=_opt_str(raw.get("sort-name")),
            type=_opt_str(raw.get("type")),
            score=score,
            disambiguation=_opt_str(raw.get("disambiguation")),
            aliases=aliases,
        ))
    return out


class HttpMusicBrainzClient:
    """Live MusicBrainz lookup over HTTP.

    Uses `requests` via `asyncio.to_thread` (small, already-common dep). Rate
    limiting is enforced by an asyncio.Lock + sleep based on the time of
    the last request.
    """

    USER_AGENT = "domovoi/1.2.1 (+https://github.com/coders-farm-official/domovoi)"
    BASE = "https://musicbrainz.org/ws/2"
    MIN_INTERVAL_SEC = 1.0
    REQUEST_TIMEOUT_SEC = 5.0
    # The search endpoint is slower than a lookup under load.
    SEARCH_TIMEOUT_SEC = 10.0
    MIN_SCORE = 80  # MB score is 0–100; below 80 is not reliable enough to overwrite metadata
    # Largest answer read (a search page is a few KB, a recording a few
    # hundred bytes); a longer body is "no proper answer", not a verdict.
    MAX_BODY_BYTES = 2 * 1024 * 1024

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
            first_release_date=_opt_str(rec.get("first-release-date")),
        )

    async def recording_year(self, recording_id: str) -> int | None:
        """The year of one recording's ``first-release-date``
        (``GET /ws/2/recording/<id>?fmt=json``): None when MusicBrainz
        answered and gives no date, or answered 404 (no such recording any
        more — a verdict too). Raises :class:`MusicBrainzRateLimited` on
        503/429, :class:`MusicBrainzUnavailable` when there was no proper
        answer (including internet access turned off), and
        :class:`MusicBrainzRejected` on another 4xx. ``recording_id`` must
        be a MusicBrainz id (:func:`is_mbid`); anything else is refused
        before a request is made. Only the id leaves the house."""
        if not is_mbid(recording_id):
            raise MusicBrainzRejected("not a MusicBrainz id")
        status, data = await self._rate_limited_fetch(
            f"recording/{recording_id}", {"fmt": "json"}, timeout=self.REQUEST_TIMEOUT_SEC,
        )
        if status in (429, 503):
            raise MusicBrainzRateLimited(f"HTTP {status}")
        if status == 404:
            return None
        if status >= 500:
            raise MusicBrainzUnavailable(f"HTTP {status}")
        if status != 200:
            raise MusicBrainzRejected(f"HTTP {status}")
        if not isinstance(data, Mapping):
            raise MusicBrainzUnavailable("not JSON")
        return year_of_date(data.get("first-release-date"))

    #: The lookup's ``inc``: genres of the recording AND of the release
    #: groups and artists linked in the same answer (MusicBrainz applies
    #: ``genres`` to the linked entities a lookup includes).
    DETAILS_INC = "genres+artist-credits+releases+release-groups"
    #: Asked once more with this when the full answer is too large (a
    #: recording on hundreds of releases): recording and artist genres.
    DETAILS_INC_SMALL = "genres+artist-credits"

    async def recording_details(self, recording_id: str) -> MbRecordingDetails:
        """One recording's year and genres (:func:`recording_details_of`):
        ``GET /ws/2/recording/<id>?fmt=json&inc=``:attr:`DETAILS_INC`. An
        answer over :attr:`MAX_BODY_BYTES` is asked again with
        :attr:`DETAILS_INC_SMALL` (that second request, paced like any
        other, is the only one; too large again is :class:`MusicBrainzRejected`).
        Answers and raises like
        :meth:`recording_year`: a 404 is a verdict with nothing in it.
        Only the id leaves the house."""
        if not is_mbid(recording_id):
            raise MusicBrainzRejected("not a MusicBrainz id")
        try:
            status, data = await self._rate_limited_fetch(
                f"recording/{recording_id}", {"fmt": "json", "inc": self.DETAILS_INC},
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except MusicBrainzTooLarge:
            try:
                status, data = await self._rate_limited_fetch(
                    f"recording/{recording_id}", {"fmt": "json", "inc": self.DETAILS_INC_SMALL},
                    timeout=self.REQUEST_TIMEOUT_SEC,
                )
            except MusicBrainzTooLarge as e:
                # Too large even without releases: not an answer this
                # client will ever read, so a refusal (the track is marked
                # asked), never an outage retried for ever.
                raise MusicBrainzRejected("answer too large") from e
        if status in (429, 503):
            raise MusicBrainzRateLimited(f"HTTP {status}")
        if status == 404:
            return MbRecordingDetails()
        if status >= 500:
            raise MusicBrainzUnavailable(f"HTTP {status}")
        if status != 200:
            raise MusicBrainzRejected(f"HTTP {status}")
        if not isinstance(data, Mapping):
            raise MusicBrainzUnavailable("not JSON")
        return recording_details_of(data)

    async def search_artists(self, name: str, *, limit: int = 5) -> list[MbArtist]:
        """Artists whose name matches ``name`` (MusicBrainz ranks them; the
        caller verifies). Only a 200 with a JSON body answers — an empty
        list then means MusicBrainz knows no such artist. Raises
        :class:`MusicBrainzRateLimited` on HTTP 503/429,
        :class:`MusicBrainzUnavailable` when the server cannot be reached,
        answers another 5xx, or answers 200 without JSON (a proxy's HTML
        page), and :class:`MusicBrainzRejected` on any other 4xx."""
        if not name.strip():
            return []
        status, data = await self._rate_limited_fetch(
            "artist",
            {
                "query": artist_search_query(name),
                "fmt": "json",
                "limit": str(max(1, min(int(limit), 25))),
            },
            timeout=self.SEARCH_TIMEOUT_SEC,
        )
        if status in (429, 503):
            raise MusicBrainzRateLimited(f"HTTP {status}")
        if status >= 500:
            raise MusicBrainzUnavailable(f"HTTP {status}")
        if status != 200:
            raise MusicBrainzRejected(f"HTTP {status}")
        if not isinstance(data, Mapping):
            raise MusicBrainzUnavailable("not JSON")
        return parse_artist_search(data)

    async def _paced(self, fn, *args):
        """Run one blocking request under the process-wide pacing lock:
        at most one request per ``MIN_INTERVAL_SEC``, whoever asks."""
        async with self._lock:
            wait = self.MIN_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await asyncio.to_thread(fn, *args)
            finally:
                self._last_request_at = time.monotonic()

    async def _rate_limited_get(
        self, path: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        return await self._paced(self._sync_get, path, params)

    async def _rate_limited_fetch(
        self, path: str, params: dict[str, str], *, timeout: float
    ) -> tuple[int, dict[str, Any] | None]:
        return await self._paced(self._sync_fetch, path, params, timeout)

    def _sync_get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        try:
            status, data = self._sync_fetch(path, params, self.REQUEST_TIMEOUT_SEC)
        except MusicBrainzUnavailable as e:
            log.debug("MusicBrainz request raised: %s", e)
            return None
        if status != 200:
            log.debug("MusicBrainz returned status %d", status)
            return None
        return data

    def _sync_fetch(
        self, path: str, params: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, Any] | None]:
        """(HTTP status, parsed JSON or None). Raises
        :class:`MusicBrainzUnavailable` when no answer came back —
        including when internet access is turned off for this box, so the
        alias fetch backs off instead of recording a verdict — and when the
        body is longer than :attr:`MAX_BODY_BYTES`.

        Redirects are not followed (the API answers in place): a 3xx comes
        back as its status, which every caller treats as no answer, so a
        request can never be sent on to a host ``egress`` did not check."""
        import json

        import requests

        url = f"{self.BASE}/{path}"
        try:
            egress.require_destination(url)
        except egress.InternetTurnedOff as e:
            raise MusicBrainzUnavailable("internet off") from e
        try:
            r = requests.get(
                url,
                params=params,
                headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"},
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except Exception as e:
            raise MusicBrainzUnavailable(type(e).__name__) from e
        try:
            if r.status_code != 200:
                return r.status_code, None
            body = bytearray()
            try:
                for chunk in r.iter_content(64 * 1024):
                    body += chunk
                    if len(body) > self.MAX_BODY_BYTES:
                        raise MusicBrainzTooLarge("answer too large")
            except MusicBrainzUnavailable:
                raise
            except Exception as e:
                raise MusicBrainzUnavailable(type(e).__name__) from e
            try:
                return 200, json.loads(bytes(body))
            except (ValueError, RecursionError):
                return 200, None
        finally:
            r.close()


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
