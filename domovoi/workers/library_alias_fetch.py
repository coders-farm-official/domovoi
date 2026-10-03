"""Opt-in MusicBrainz "also called" fetch for the library's artists.

People say "Tec 9" for Tech N9ne and "The Farriss Brothers" for INXS. The
spoken-name resolver (``domovoi/handlers/shared/library_match.py``)
already hears most stylized names through normalization ("Suicide Boys"
IS ``$uicideboy$``); what it cannot work out is a spoken name that is
spelled differently. MusicBrainz lists those as artist aliases. This
worker looks each library artist up there and stores the spoken ones as
``library_aliases`` rows with ``source='musicbrainz'`` (V019), which the
resolver then treats as extra names of that artist.

**Off by default, and egress-gated.** It sends artist names from the
library to musicbrainz.org, so it does nothing — no request, no database
read — until ``settings.music_alias_fetch_enabled`` is switched on, and
the switch applies on the next tick without a restart (the worker is
self-gated: the runner never starts a worker that was disabled at boot,
so ``enabled_setting`` is None and :meth:`LibraryAliasFetcher.tick`
checks the setting itself). :func:`_egress_allowed` is THE single gate,
checked before every tick and again before every request: the setting,
the connectivity probe reporting online, and no shutdown in progress.
There is no household-wide "go dark" switch in core yet; when one lands,
it plugs in there and nowhere else.

**Polite.** Every request goes through the shared MusicBrainz client
(``domovoi/clients/musicbrainz.py``): one request a second process-wide,
the project User-Agent. At most :data:`BATCH_SIZE` artists per tick;
MusicBrainz answering 503/429 pauses the fetch for
:data:`RATE_LIMIT_PAUSE_SEC`, an unreachable server for
:data:`UNAVAILABLE_PAUSE_SEC`. Each artist is looked up once (the
``library_alias_lookups`` row remembers the outcome); an artist that
appears in the library later is picked up on the next tick (top-up).

**Strict.** A hit counts only when MusicBrainz's own name, sort name or a
non-legal alias is the same name as the library's spelling
(:func:`verify_artist` — a search for "B.o.B" returns Bob Dylan first,
and "MAGIC!" Yellow Magic Orchestra; neither is kept). Of the matched
artist's aliases only spoken stage names survive
(:func:`classify_aliases`): never a legal name or anything sharing a word
with one (50 Cent's eight "Jackson" forms, P!nk's "Alecia Moore"), never
a non-English or non-Latin form, never for a person a name that does not
sound like the stage name ("Hova", "Lord Flacko"), and never a name
something in the library is already called. The dropped names are never
stored, only counted.

Writes are ``INSERT … ON CONFLICT (alias_key) DO NOTHING``: a household
alias (or one an earlier artist's fetch already took) always wins, and a
MusicBrainz alias someone removed stays as a suppressed row, so it is
never fetched back.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from sqlalchemy import text

from domovoi import lifecycle
from domovoi.clients.musicbrainz import (
    MbArtist,
    MusicBrainzClient,
    MusicBrainzRateLimited,
    MusicBrainzUnavailable,
    get_musicbrainz_client,
)
from domovoi.config import settings
from domovoi.handlers.shared.spoken_names import (
    KEY_VERSION,
    alias_key,
    entity_names,
    fold,
    same_name,
    sounds_like,
    split_credit,
)
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

#: Artists looked up per tick (the client paces them at one a second).
BATCH_SIZE = 25
#: Artists asked for per search; the verification picks among them.
SEARCH_LIMIT = 5
#: MusicBrainz's own relevance score (0-100) a hit needs to be considered.
MIN_MB_SCORE = 90
#: Pause after MusicBrainz answers 503 / 429.
RATE_LIMIT_PAUSE_SEC = 60.0
#: Pause after MusicBrainz could not be reached (so an outage does not use
#: up one artist's retries per tick).
UNAVAILABLE_PAUSE_SEC = 300.0
#: An artist whose lookup failed this many times is not tried again.
MAX_ERROR_ATTEMPTS = 5
#: Shortest alias key kept / artist key looked up.
MIN_KEY_LEN = 3
#: Longest alias kept, in words.
MAX_ALIAS_WORDS = 6
#: V019's limit on ``library_aliases.alias``.
MAX_ALIAS_CHARS = 120
#: MusicBrainz artist types that are not one person.
GROUP_TYPES = frozenset({"Group", "Orchestra", "Choir"})
#: Alias types kept (plus untyped aliases and the sort name).
KEPT_ALIAS_TYPES = frozenset({"Artist name", "Search hint"})
LEGAL_NAME = "Legal name"
#: ``mb_alias_type`` stored for an alias taken from the sort name.
SORT_NAME = "sort name"
#: Placeholder credits that are not an artist.
SKIP_ARTIST_KEYS = frozenset({"variousartists", "unknownartist", "va", "unknown"})
#: Lookup outcomes that are final.
DONE_STATUSES = ("matched", "no_match", "ambiguous")

# A multi-artist credit is looked up as a whole (a band whose name has a
# comma or an ampersand in it) only when nothing but , ; & separates its
# parts — never a "feat." / "x" / "vs" credit, whose parts are separate
# people.
_FEATURE_SEP_RE = re.compile(r"\s(?:feat\.?|ft\.?|featuring|x|vs\.?)\s", re.I)
_MAX_BAND_PARTS = 4


# ─── Status (in-memory; the core snapshot's "music_alias_fetch") ──────────


@dataclass
class _FetchStatus:
    state: str = "idle"
    last_tick_at: float | None = None
    last_request_at: float | None = None
    last_error: str | None = None
    rate_limited_until: float | None = None
    paused_until: float | None = None
    artists_total: int | None = None
    artists_due: int | None = None


_STATUS = _FetchStatus()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _probe_online() -> bool:
    """The connectivity probe's verdict. No probe registered (a unit
    context, or the core still booting) is NOT online: this gate decides
    whether household data leaves the house, so it does not assume."""
    from domovoi.connectivity import current_probe

    probe = current_probe()
    return probe is not None and bool(probe.online)


def _egress_allowed() -> bool:
    """THE single egress gate for the fetch: the opt-in setting, the
    connectivity probe, and no shutdown under way. Checked at the start of
    every tick and before every request. A future household-wide
    "go dark" switch belongs here."""
    if not settings.music_alias_fetch_enabled:
        return False
    if lifecycle.shutting_down():
        return False
    return _probe_online()


def alias_fetch_status() -> dict[str, Any]:
    """For the core snapshot (``/v1/admin/snapshot`` → ``music_alias_fetch``)
    and the dashboard's "Also-called names" card. ``state``: off | offline |
    rate_limited | running | idle | done | error. The counts of matched /
    unmatched artists and stored aliases live in the database
    (``library_alias_lookups``, ``library_aliases``); ``artists_total`` /
    ``artists_due`` are the last tick's view of the library (None before
    the first tick)."""
    enabled = bool(settings.music_alias_fetch_enabled)
    now = time.time()
    if not enabled:
        state = "off"
    elif not _probe_online():
        state = "offline"
    elif _STATUS.rate_limited_until is not None and now < _STATUS.rate_limited_until:
        state = "rate_limited"
    elif _STATUS.state in ("off", "offline", "rate_limited"):
        state = "idle"  # switched back on / back online; the next tick decides
    else:
        state = _STATUS.state
    rate_limited_until = _STATUS.rate_limited_until
    if rate_limited_until is not None and rate_limited_until <= now:
        rate_limited_until = None
    return {
        "enabled": enabled,
        "state": state,
        "last_tick_at": _iso(_STATUS.last_tick_at),
        "last_request_at": _iso(_STATUS.last_request_at),
        "last_error": _STATUS.last_error,
        "rate_limited_until": _iso(rate_limited_until),
        "artists_total": _STATUS.artists_total,
        "artists_due": _STATUS.artists_due,
    }


def reset_status_for_tests() -> None:
    global _STATUS
    _STATUS = _FetchStatus()


# ─── The library's artists (pure) ─────────────────────────────────────────


@dataclass(frozen=True)
class LibraryArtists:
    """What the library offers the fetch: every artist worth looking up
    (``alias_key`` → its most frequent spelling, most tracks first) and the
    key of every name the library knows (artists, credits, titles,
    albums) — a fetched alias never takes one of those."""

    artists: dict[str, str]
    track_counts: dict[str, int]
    name_keys: frozenset[str]


def _is_band_credit(credit: str, parts: Sequence[str]) -> bool:
    return 2 <= len(parts) <= _MAX_BAND_PARTS and not _FEATURE_SEP_RE.search(credit)


def scan_library(rows: Iterable[Sequence[Any]]) -> LibraryArtists:
    """Pure: ``rows`` are (title, artist, album) tuples of
    ``library_tracks``. Artist names come from ``entity_names``, so a
    filename-derived "Artist - Title" counts. Looked up: (a) every
    single-performer credit; (b) a multi-part credit as a whole when only
    , ; & separate at most four parts ("Earth, Wind & Fire"); never a part
    of a credit on its own (a featured-only guest, or "Fire") — a part that
    is somebody's own credit elsewhere is covered by (a)."""
    spellings: dict[str, Counter[str]] = {}
    tracks: Counter[str] = Counter()
    name_keys: set[str] = set()
    for row in rows:
        title, artist, album = (list(row) + [None, None, None])[:3]
        names = entity_names(title, artist, album)
        credit = next((n for kind, n in names if kind == "credit"), None)
        performers = [n for kind, n in names if kind == "artist"]
        for _kind, name in names:
            k = alias_key(name)
            if k:
                name_keys.add(k)
        if credit is None:
            looked_up = performers[:1]
        elif _is_band_credit(credit, split_credit(credit)):
            looked_up = [credit]
        else:
            looked_up = []
        for name in looked_up:
            k = alias_key(name)
            if len(k) < MIN_KEY_LEN or k in SKIP_ARTIST_KEYS:
                continue
            spellings.setdefault(k, Counter())[name] += 1
            tracks[k] += 1
    order = sorted(spellings, key=lambda k: (-tracks[k], k))
    return LibraryArtists(
        artists={k: spellings[k].most_common(1)[0][0] for k in order},
        track_counts={k: tracks[k] for k in order},
        name_keys=frozenset(name_keys),
    )


# ─── Verification (pure) ──────────────────────────────────────────────────


def uninvert_sort_name(sort_name: str | None) -> str | None:
    """MusicBrainz sort names put a person's surname first ("Dylan, Bob");
    the spoken order is "Bob Dylan". Anything that is not exactly
    "Last, First" is returned unchanged."""
    if not sort_name:
        return None
    if sort_name.count(",") == 1:
        last, first = (p.strip() for p in sort_name.split(","))
        if first and last:
            return f"{first} {last}"
    return sort_name


def _artist_names(artist: MbArtist) -> list[str]:
    names = [artist.name, uninvert_sort_name(artist.sort_name) or ""]
    names += [a.name for a in artist.aliases if a.type != LEGAL_NAME]
    return [n for n in names if n]


@dataclass(frozen=True)
class Verification:
    """``status``: matched | no_match | ambiguous."""

    status: str
    artist: MbArtist | None = None


def verify_artist(library_name: str, hits: Sequence[MbArtist]) -> Verification:
    """Which MusicBrainz artist the library's ``library_name`` is, if any.
    The library has no MusicBrainz ids for artists, so a hit must earn it:
    score ≥ :data:`MIN_MB_SCORE` AND its name, uninverted sort name or a
    non-legal alias is the same name as the library spelling. No such hit →
    no_match; two or more different artists sharing the top score →
    ambiguous (better nothing than another artist's names); else the best
    one."""
    verified: dict[str, MbArtist] = {}
    for hit in hits:
        if hit.score < MIN_MB_SCORE:
            continue
        if not any(same_name(n, library_name) for n in _artist_names(hit)):
            continue
        prev = verified.get(hit.id)
        if prev is None or hit.score > prev.score:
            verified[hit.id] = hit
    if not verified:
        return Verification("no_match")
    ranked = sorted(verified.values(), key=lambda a: -a.score)
    if len(ranked) > 1 and ranked[0].score == ranked[1].score:
        return Verification("ambiguous")
    return Verification("matched", ranked[0])


# ─── The strict real-name filter (pure) ───────────────────────────────────

_DOTTED_RE = re.compile(r"(?<![a-z0-9])((?:[a-z]\.\s?)+[a-z])\.?(?![a-z0-9])")


def _word_list(text_: str) -> list[str]:
    """The words of a name, folded: dotted initials joined ("T.I." → ti),
    apostrophes dropped, ``$`` read as s, an in-word ``!`` as i."""
    s = fold(text_)
    s = _DOTTED_RE.sub(lambda m: re.sub(r"[.\s]", "", m.group(1)), s)
    s = re.sub(r"['`´‘’]", "", s).replace("$", "s")
    s = re.sub(r"(?<=[a-z])!(?=[a-z])", "i", s)
    return re.findall(r"[a-z0-9]+", s)


def _is_latin(text_: str) -> bool:
    for ch in unicodedata.normalize("NFKC", text_):
        if ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            return False
    return True


def _english_or_none(locale: str | None) -> bool:
    return not locale or locale == "en" or locale.startswith("en_")


def _symbol_count(name: str) -> int:
    return sum(1 for ch in name if not (ch.isalnum() or ch.isspace()))


@dataclass(frozen=True)
class AliasDecision:
    """One candidate name of a matched artist and what the filter made of
    it: ``reason`` None = kept; else legal_name | alias_type | locale |
    script | too_short | too_long | redundant | legal_word | unlike_name |
    library_name | duplicate (same key as a name kept before it)."""

    name: str
    key: str
    mb_type: str | None
    reason: str | None


def classify_aliases(
    library_name: str, artist: MbArtist, library_keys: Iterable[str] = ()
) -> list[AliasDecision]:
    """Every candidate spoken name of ``artist`` — its aliases, then its
    uninverted sort name — with the filter's verdict. Pure; see
    :func:`filter_aliases` for the rules."""
    keys = frozenset(library_keys)
    person = artist.type not in GROUP_TYPES
    legal_words: set[str] = set()
    for a in artist.aliases:
        if a.type == LEGAL_NAME:
            legal_words.update(_word_list(a.name))
    legal_words -= set(_word_list(library_name)) | set(_word_list(artist.name))

    candidates: list[tuple[str, str | None, str | None]] = [
        (a.name, a.type, a.locale) for a in artist.aliases
    ]
    sort_spoken = uninvert_sort_name(artist.sort_name)
    if sort_spoken:
        candidates.append((sort_spoken, SORT_NAME, None))

    decisions: list[AliasDecision] = []
    for name, mb_type, locale in candidates:
        name = " ".join(name.split())
        key = alias_key(name)
        words = _word_list(name)
        reason: str | None = None
        if mb_type == LEGAL_NAME:
            reason = "legal_name"
        elif mb_type is not None and mb_type != SORT_NAME and mb_type not in KEPT_ALIAS_TYPES:
            reason = "alias_type"
        elif not _english_or_none(locale):
            reason = "locale"
        elif not _is_latin(name):
            reason = "script"
        elif len(key) < MIN_KEY_LEN:
            reason = "too_short"
        elif len(words) > MAX_ALIAS_WORDS or len(name) > MAX_ALIAS_CHARS:
            reason = "too_long"
        elif same_name(name, library_name):
            reason = "redundant"
        elif person and legal_words.intersection(words):
            reason = "legal_word"
        elif person and not sounds_like(name, library_name):
            reason = "unlike_name"
        elif key in keys:
            reason = "library_name"
        decisions.append(AliasDecision(name=name, key=key, mb_type=mb_type, reason=reason))

    # One name per key (a key means one thing): of the kept spellings of a
    # key, keep the plainest ("50 Cents" over ".50 Cents"), first on a tie.
    best: dict[str, int] = {}
    for i, d in enumerate(decisions):
        if d.reason is not None:
            continue
        j = best.get(d.key)
        if j is None or _symbol_count(d.name) < _symbol_count(decisions[j].name):
            best[d.key] = i
    chosen = set(best.values())
    return [
        d if d.reason is not None or i in chosen
        else AliasDecision(d.name, d.key, d.mb_type, "duplicate")
        for i, d in enumerate(decisions)
    ]


def filter_aliases(
    library_name: str, artist: MbArtist, library_keys: Iterable[str] = ()
) -> tuple[list[AliasDecision], int]:
    """The STRICT real-name filter: (kept, dropped_count). A candidate is
    dropped when any of these holds:

    * its type is "Legal name", or anything but "Artist name" / "Search
      hint" / untyped / the sort name;
    * its locale is set and not English, or it has a letter outside the
      Latin script;
    * its key is shorter than 3 characters, or it is longer than 6 words;
    * it is already the same name as the library spelling (normalization
      hears it without an alias);
    * the artist is a person (not a Group / Orchestra / Choir; unknown
      counts as a person) and the name shares a word with one of their
      legal names (words of the stage name excepted), or does not sound
      like the stage name;
    * something in the library is already called that.

    Spellings of a kept key after the first are neither kept nor counted
    as dropped."""
    decisions = classify_aliases(library_name, artist, library_keys)
    kept = [d for d in decisions if d.reason is None]
    dropped = sum(1 for d in decisions if d.reason not in (None, "duplicate"))
    return kept, dropped


# ─── Storage ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LookupRow:
    key: str
    status: str
    attempts: int
    looked_up_at: datetime


class AliasFetchStore(Protocol):
    """The fetch's reads and writes (a fake stands in for it in tests)."""

    async def library_fingerprint(self) -> tuple[Any, ...]: ...

    async def library_rows(self) -> list[tuple[Any, Any, Any]]: ...

    async def lookup_rows(self) -> list[LookupRow]: ...

    async def record_result(
        self,
        *,
        artist_key: str,
        artist_name: str,
        verification: Verification,
        kept: Sequence[AliasDecision],
        dropped: int,
    ) -> int: ...

    async def record_error(self, *, artist_key: str, artist_name: str, code: str) -> None: ...


_FINGERPRINT_SQL = text(
    "SELECT count(*), coalesce(max(id), 0), "
    "max(greatest(added_at, coalesce(enriched_at, added_at))) FROM library_tracks"
)
_INSERT_ALIAS_SQL = text(
    "INSERT INTO library_aliases (alias, alias_key, key_version, target_type, "
    "target_key, target_name, source, created_by_kind, mb_artist_id, mb_alias_type) "
    "VALUES (:alias, :alias_key, :key_version, 'artist', :target_key, :target_name, "
    "'musicbrainz', 'system', :mb_artist_id, :mb_alias_type) "
    "ON CONFLICT (alias_key) DO NOTHING RETURNING id"
)
_UPSERT_LOOKUP_SQL = text(
    "INSERT INTO library_alias_lookups (artist_key, artist_name, status, "
    "mb_artist_id, mb_name, mb_type, aliases_added, aliases_dropped, attempts, "
    "last_error, looked_up_at) "
    "VALUES (:artist_key, :artist_name, :status, :mb_artist_id, :mb_name, :mb_type, "
    ":added, :dropped, 1, :last_error, now()) "
    "ON CONFLICT (artist_key) DO UPDATE SET "
    "artist_name = EXCLUDED.artist_name, status = EXCLUDED.status, "
    "mb_artist_id = EXCLUDED.mb_artist_id, mb_name = EXCLUDED.mb_name, "
    "mb_type = EXCLUDED.mb_type, aliases_added = EXCLUDED.aliases_added, "
    "aliases_dropped = EXCLUDED.aliases_dropped, "
    "attempts = library_alias_lookups.attempts + 1, "
    "last_error = EXCLUDED.last_error, looked_up_at = now()"
)


class DbAliasFetchStore:
    """The real store: ``library_tracks`` reads, V019 writes. Each artist's
    outcome commits on its own."""

    async def library_fingerprint(self) -> tuple[Any, ...]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return tuple((await s.execute(_FINGERPRINT_SQL)).one())

    async def library_rows(self) -> list[tuple[Any, Any, Any]]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            res = await s.execute(text("SELECT title, artist, album FROM library_tracks"))
            return [tuple(r) for r in res.all()]

    async def lookup_rows(self) -> list[LookupRow]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            res = await s.execute(text(
                "SELECT artist_key, status, attempts, looked_up_at FROM library_alias_lookups"
            ))
            return [LookupRow(r[0], r[1], int(r[2]), r[3]) for r in res.all()]

    async def record_result(
        self,
        *,
        artist_key: str,
        artist_name: str,
        verification: Verification,
        kept: Sequence[AliasDecision],
        dropped: int,
    ) -> int:
        from domovoi.db.session import session_scope

        mb = verification.artist if verification.status == "matched" else None
        added = 0
        async with session_scope() as s:
            if mb is not None:
                for d in kept:
                    row = (await s.execute(_INSERT_ALIAS_SQL, {
                        "alias": d.name,
                        "alias_key": d.key,
                        "key_version": KEY_VERSION,
                        "target_key": artist_key,
                        "target_name": artist_name,
                        "mb_artist_id": mb.id,
                        "mb_alias_type": d.mb_type,
                    })).first()
                    added += 1 if row is not None else 0
            await s.execute(_UPSERT_LOOKUP_SQL, {
                "artist_key": artist_key,
                "artist_name": artist_name,
                "status": verification.status,
                "mb_artist_id": mb.id if mb else None,
                "mb_name": mb.name if mb else None,
                "mb_type": mb.type if mb else None,
                "added": added,
                "dropped": dropped if mb else 0,
                "last_error": None,
            })
        return added

    async def record_error(self, *, artist_key: str, artist_name: str, code: str) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(_UPSERT_LOOKUP_SQL, {
                "artist_key": artist_key,
                "artist_name": artist_name,
                "status": "error",
                "mb_artist_id": None,
                "mb_name": None,
                "mb_type": None,
                "added": 0,
                "dropped": 0,
                "last_error": code[:64],
            })


def _retry_at(row: LookupRow) -> datetime | None:
    """When an errored lookup may be tried again (2^attempts hours after
    the last try), or None when it never will be."""
    if row.attempts >= MAX_ERROR_ATTEMPTS:
        return None
    return row.looked_up_at + timedelta(hours=2 ** max(row.attempts, 0))


# ─── The worker ───────────────────────────────────────────────────────────


@dataclass
class _Due:
    artists: list[tuple[str, str]] = field(default_factory=list)  # (key, name)
    total: int = 0
    name_keys: frozenset[str] = frozenset()
    next_retry_at: datetime | None = None


class LibraryAliasFetcher(Worker):
    """Looks up the library's artists on MusicBrainz, a small batch per
    tick, and stores their spoken aliases. See the module docstring."""

    name = "library_alias_fetch"
    # Self-gated (see the module docstring): the opt-in must apply without
    # a restart, and the runner never starts a worker disabled at boot.
    enabled_setting = None
    interval_setting = "music_alias_fetch_interval_sec"
    stub_suppressed = True
    requires_online = True

    def __init__(
        self,
        *,
        client: MusicBrainzClient | None = None,
        store: AliasFetchStore | None = None,
    ) -> None:
        self._client = client
        self._store: AliasFetchStore = store or DbAliasFetchStore()
        self._scan: tuple[tuple[Any, ...], LibraryArtists] | None = None
        # (fingerprint, next error retry) when the last tick found nothing
        # due: nothing changes until the library does or that time comes.
        self._quiet: tuple[tuple[Any, ...], datetime | None] | None = None

    def _mb(self) -> MusicBrainzClient:
        return self._client or get_musicbrainz_client()

    async def _library(self) -> tuple[tuple[Any, ...], LibraryArtists]:
        fingerprint = await self._store.library_fingerprint()
        if self._scan is not None and self._scan[0] == fingerprint:
            return self._scan
        rows = await self._store.library_rows()
        scan = await asyncio.to_thread(scan_library, rows)
        self._scan = (fingerprint, scan)
        return self._scan

    async def _due(self, scan: LibraryArtists) -> _Due:
        now = datetime.now(timezone.utc)
        skip: set[str] = set()
        next_retry: datetime | None = None
        for row in await self._store.lookup_rows():
            if row.status in DONE_STATUSES:
                skip.add(row.key)
                continue
            retry_at = _retry_at(row)
            if retry_at is None or retry_at > now:
                skip.add(row.key)
                if retry_at is not None and row.key in scan.artists:
                    next_retry = retry_at if next_retry is None else min(next_retry, retry_at)
        due = [(k, n) for k, n in scan.artists.items() if k not in skip]
        return _Due(
            artists=due,
            total=len(scan.artists),
            name_keys=scan.name_keys,
            next_retry_at=next_retry,
        )

    async def tick(self) -> dict[str, int]:
        result = {"looked_up": 0, "matched": 0, "aliases_added": 0}
        if not settings.music_alias_fetch_enabled:
            _STATUS.state = "off"
            return result
        if not _egress_allowed():
            _STATUS.state = "offline"
            return result
        now = time.time()
        if _STATUS.rate_limited_until is not None and now < _STATUS.rate_limited_until:
            _STATUS.state = "rate_limited"
            return result
        if _STATUS.paused_until is not None and now < _STATUS.paused_until:
            return result
        _STATUS.last_tick_at = now

        fingerprint, scan = await self._library()
        if self._quiet is not None and self._quiet[0] == fingerprint:
            retry_at = self._quiet[1]
            if retry_at is None or datetime.now(timezone.utc) < retry_at:
                _STATUS.state = "done"
                return result
        self._quiet = None

        due = await self._due(scan)
        _STATUS.artists_total = due.total
        _STATUS.artists_due = len(due.artists)
        if not due.artists:
            self._quiet = (fingerprint, due.next_retry_at)
            _STATUS.state = "done"
            return result

        _STATUS.state = "running"
        started = time.monotonic()
        try:
            stopped = await self._batch(due, result)
        except Exception as e:
            # A database or parsing failure: the runner logs it and keeps
            # the cadence; the dashboard shows the fetch as stuck.
            _STATUS.state = "error"
            _STATUS.last_error = type(e).__name__[:64]
            raise
        if result["looked_up"]:
            elapsed = time.monotonic() - started
            log.info(
                "MusicBrainz alias fetch: %d artists in %.1f s (%.2f requests/s), "
                "%d matched, %d aliases added; %d still to look up",
                result["looked_up"], elapsed, result["looked_up"] / max(elapsed, 1e-6),
                result["matched"], result["aliases_added"], _STATUS.artists_due or 0,
            )
        if result["aliases_added"]:
            from domovoi.handlers.shared import library_match

            library_match.invalidate()
        if stopped is not None:
            _STATUS.state = stopped
        else:
            _STATUS.state = "running" if _STATUS.artists_due else "done"
        return result

    async def _batch(self, due: _Due, result: dict[str, int]) -> str | None:
        """Look up the next :data:`BATCH_SIZE` due artists, one request
        each. Returns the state to report when it stopped early (off |
        offline | rate_limited | error), else None."""
        client = self._mb()
        stopped: str | None = None
        for artist_key, artist_name in due.artists[:BATCH_SIZE]:
            if not _egress_allowed():  # switched off / offline / shutting down
                stopped = "off" if not settings.music_alias_fetch_enabled else "offline"
                break
            _STATUS.last_request_at = time.time()
            try:
                hits = await client.search_artists(artist_name, limit=SEARCH_LIMIT)
            except MusicBrainzRateLimited:
                _STATUS.rate_limited_until = time.time() + RATE_LIMIT_PAUSE_SEC
                _STATUS.last_error = "rate_limited"
                stopped = "rate_limited"
                log.info("MusicBrainz asked us to slow down; pausing the alias fetch for %.0f s",
                         RATE_LIMIT_PAUSE_SEC)
                break
            except MusicBrainzUnavailable as e:
                await self._store.record_error(
                    artist_key=artist_key, artist_name=artist_name, code="unavailable"
                )
                _STATUS.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
                _STATUS.last_error = "unavailable"
                stopped = "error"
                log.info("MusicBrainz unreachable (%s); pausing the alias fetch for %.0f s",
                         e, UNAVAILABLE_PAUSE_SEC)
                break
            result["looked_up"] += 1
            verification = verify_artist(artist_name, hits)
            kept: list[AliasDecision] = []
            dropped = 0
            if verification.status == "matched" and verification.artist is not None:
                kept, dropped = filter_aliases(artist_name, verification.artist, due.name_keys)
                result["matched"] += 1
            added = await self._store.record_result(
                artist_key=artist_key,
                artist_name=artist_name,
                verification=verification,
                kept=kept,
                dropped=dropped,
            )
            result["aliases_added"] += added
            _STATUS.artists_due = max(0, (_STATUS.artists_due or 1) - 1)
            _STATUS.last_error = None
            log.debug("MusicBrainz aliases for %r: %s, %d added, %d dropped",
                      artist_name, verification.status, added, dropped)
        return stopped


__all__ = [
    "AliasDecision",
    "AliasFetchStore",
    "DbAliasFetchStore",
    "LibraryAliasFetcher",
    "LibraryArtists",
    "LookupRow",
    "Verification",
    "alias_fetch_status",
    "classify_aliases",
    "filter_aliases",
    "scan_library",
    "uninvert_sort_name",
    "verify_artist",
]
