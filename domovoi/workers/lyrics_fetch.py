"""Timed lyrics from LRCLIB for the songs that have none of their own
(contract §7.2, [R9]–[R32]; §6 for the ``.lrc`` files).

**Opt-in, and egress-gated.** Nothing is asked until
``settings.lyrics_lrclib_enabled`` is on; its default follows the internet
answer (on for "always" and "sometimes", off for "never", off while
unanswered; a hand-set value wins). The worker is self-gated
(``enabled_setting`` None — the runner never starts a worker that was off
at boot, and the switch must apply without a restart): :func:`_gate` —
the setting, ``INTERNET_ACCESS`` not "never", the connectivity probe
online (none registered is NOT online), no shutdown — is checked at the
start of every tick and before every request.

**Which songs** ([R13], [R14]): those the local scan has looked at, whose
file is there, with no ``.lrc`` and no timed lyrics of their own, that
LRCLIB was never asked about, whose title / artist / album / length
changed since, or whose re-check timer is due — recently played ones first,
then favorites, then never asked, then those the enricher already judged.
30 per tick, about one request a second (the client paces).

**What is asked** (:func:`ask_lrclib`): ``/api/get`` with the song's
title, artist credit, album and length; when that has no timed lyrics,
``/api/search`` — whose records count only when the length is within
−2…+3 s of ours and the title and an artist agree.

**What is stored** (:func:`domovoi.lyrics.store.record_lrclib`): found
(timed or plain), instrumental, not found (asked again after 28, 56, then
112 days), skipped (no title / artist / length). LRCLIB refusing a request
is an ``error`` with a backoff; a rate limit, an unreachable LRCLIB, the
internet switched off or the server offline leave every row as it was and
pause the fetch ([M4]).

**And on disk** (:mod:`domovoi.lyrics.sidecar`): a found TIMED answer is
committed, then saved as a Domovoi-marked ``.lrc`` next to the song when
"Save lyrics as .lrc files" is on and the song has no ``.lrc`` yet; each
tick also writes those fetched while saving was off, and retries failed
writes once a day.

Logging: one INFO line per tick that asked something, one per tick that
wrote or failed a ``.lrc``; per row at most DEBUG, the track id and the
outcome. Never a title, an artist or a lyric at INFO, never a lyric at any
level, and no exception leaving :meth:`LyricsFetcher.tick` carries a
database error's text (it can quote the failing row).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from rapidfuzz import fuzz

from domovoi import egress
from domovoi.clients.lrclib import (
    LrclibClient,
    LrclibRateLimited,
    LrclibRecord,
    LrclibRejected,
    LrclibUnavailable,
    get_lrclib_client,
)
from domovoi.config import settings
from domovoi.handlers.shared.spoken_names import alias_key, clean_title, fold, split_credit
from domovoi.lyrics import domovoi_version, sidecar
from domovoi.lyrics import status as lyrics_state
from domovoi.lyrics import store
from domovoi.lyrics.lrc import format_lrc, parse_lrc, parse_plain
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

#: Tracks asked about per tick (at most two requests each).
BATCH_SIZE = 30
#: ``.lrc`` files written per tick by the catch-up step ([W14]).
LRC_BATCH = 200
#: Pause after LRCLIB answers 429 / 503 (or longer, when it says so).
RATE_LIMIT_MIN_PAUSE_SEC = 60.0
#: Pause after LRCLIB could not be reached, or refused five in a row.
UNAVAILABLE_PAUSE_SEC = 300.0
MAX_REFUSALS_IN_A_ROW = 5
#: [R23] (a): a search record's length, against our whole seconds ``d``
#: (which truncate the real length), within d − 2 … d + 3.
DURATION_BELOW_SEC = 2.0
DURATION_ABOVE_SEC = 3.0
#: [R23] (b), (c): a name agrees at this token-set ratio or more.
NAME_RATIO = 90
#: "done" (rather than "idle") when no re-check is due within this.
DONE_HORIZON = timedelta(days=1)

_KIND_RANK = {"timed": 0, "plain": 1, "instrumental": 2}


# ─── One track ([R15]–[R24], [R32]) ───────────────────────────────────────


@dataclass(frozen=True)
class TrackMeta:
    id: int
    title: str | None
    artist: str | None
    album: str | None
    duration_sec: int | None


@dataclass(frozen=True)
class LrclibAnswer:
    """What LRCLIB said about one track. ``status``: found | instrumental |
    not_found | skipped; ``match``: get | search (None for not_found /
    skipped); ``synced``: ``(ms, text)`` pairs or None (plain only);
    ``error``: skipped's reason (no_metadata | no_duration)."""

    status: str
    match: str | None = None
    record: LrclibRecord | None = None
    plain: str | None = None
    synced: tuple[tuple[int, str], ...] | None = None
    error: str | None = None

    def __repr__(self) -> str:  # never the lyrics
        return (
            f"LrclibAnswer(status={self.status!r}, match={self.match!r}, "
            f"record={getattr(self.record, 'id', None)}, plain={len(self.plain or '')} chars, "
            f"synced={len(self.synced or ())} lines, error={self.error!r})"
        )


@dataclass(frozen=True)
class _Judged:
    kind: str                                       # timed | plain | instrumental
    plain: str | None
    synced: tuple[tuple[int, str], ...] | None


def _judge(rec: LrclibRecord) -> _Judged | None:
    """What a record offers: timed lyrics (its LRC parses to at least one
    timed line with text), plain lyrics, or "instrumental" — None when it
    has no usable lyrics and is not instrumental. ``plain`` is LRCLIB's
    ``plainLyrics`` cleaned ([P13]–[P15]), or the timed texts when that is
    missing ([R25])."""
    timed = parse_lrc(rec.synced) if rec.synced else None
    plain = parse_plain(rec.plain).plain if rec.plain else None
    if timed is not None and timed.synced is not None:
        return _Judged("timed", plain or timed.plain, timed.synced)
    if plain is None and timed is not None:
        plain = timed.plain             # an untimed "synced" field
    if plain:
        return _Judged("plain", plain, None)
    if rec.instrumental:
        return _Judged("instrumental", None, None)
    return None


def _same_title(theirs: str, ours: str) -> bool:
    a, b = clean_title(theirs), clean_title(ours)
    ka, kb = alias_key(a), alias_key(b)
    if ka and ka == kb:
        return True
    fa, fb = fold(a), fold(b)
    return bool(fa.strip() and fb.strip()) and fuzz.token_set_ratio(fa, fb) >= NAME_RATIO


def _same_artist(theirs: str, ours: str) -> bool:
    ka = {alias_key(p) for p in split_credit(theirs)} - {""}
    kb = {alias_key(p) for p in split_credit(ours)} - {""}
    if ka & kb:
        return True
    fa, fb = fold(theirs), fold(ours)
    return bool(fa.strip() and fb.strip()) and fuzz.token_set_ratio(fa, fb) >= NAME_RATIO


def _accepted(rec: LrclibRecord, *, title: str, artist: str, d: int) -> _Judged | None:
    """[R23]: (a) the length agrees, (b) the title, (c) an artist, and
    (d) it has lyrics or is instrumental."""
    if rec.duration is None or not (d - DURATION_BELOW_SEC <= rec.duration <= d + DURATION_ABOVE_SEC):
        return None
    if not _same_title(rec.track_name, title) or not _same_artist(rec.artist_name, artist):
        return None
    return _judge(rec)


def _best(records: Sequence[LrclibRecord], *, title: str, artist: str,
          d: int) -> tuple[LrclibRecord, _Judged] | None:
    """[R24]: timed, then plain, then instrumental; then the length closest
    to ``d + 0.5``; then the lowest id."""
    accepted: list[tuple[tuple[int, float, int], LrclibRecord, _Judged]] = []
    for rec in records:
        judged = _accepted(rec, title=title, artist=artist, d=d)
        if judged is not None:
            key = (_KIND_RANK[judged.kind], abs(float(rec.duration or 0.0) - (d + 0.5)), rec.id)
            accepted.append((key, rec, judged))
    if not accepted:
        return None
    _key, rec, judged = min(accepted, key=lambda a: a[0])
    return rec, judged


def _answer(rec: LrclibRecord, judged: _Judged, match: str) -> LrclibAnswer:
    if judged.kind == "instrumental":
        return LrclibAnswer("instrumental", match=match, record=rec)
    return LrclibAnswer("found", match=match, record=rec, plain=judged.plain, synced=judged.synced)


async def ask_lrclib(client: LrclibClient, track: TrackMeta) -> LrclibAnswer:
    """[R15]–[R24] for ONE track, no database. Raises the client's
    :class:`~domovoi.clients.lrclib.LrclibRateLimited` /
    :class:`~domovoi.clients.lrclib.LrclibUnavailable` /
    :class:`~domovoi.clients.lrclib.LrclibRejected` and
    :class:`~domovoi.egress.InternetTurnedOff` for the caller ([R27])."""
    title = (track.title or "").strip()
    artist = (track.artist or "").strip()
    if not title or not artist:
        return LrclibAnswer("skipped", error="no_metadata")
    d = track.duration_sec
    if d is None or isinstance(d, bool) or not 1 <= int(d) <= 3600:
        return LrclibAnswer("skipped", error="no_duration")
    d = int(d)
    album = (track.album or "").strip() or None

    got_rec = await client.get(track=title, artist=artist, album=album, duration=d)
    got = _judge(got_rec) if got_rec is not None else None
    if got_rec is not None and got is not None and got.kind != "plain":
        return _answer(got_rec, got, "get")                       # [R18], [R19]

    parts = split_credit(artist)
    records = await client.search(track=clean_title(title), artist=parts[0] if parts else artist)
    best = _best(records, title=title, artist=artist, d=d)
    if got_rec is not None and got is not None:                   # [R20]: get had plain only
        if best is not None and best[1].kind == "timed":
            return _answer(best[0], best[1], "search")
        return _answer(got_rec, got, "get")
    if best is None:                                              # [R21] → nothing accepted
        return LrclibAnswer("not_found")
    return _answer(best[0], best[1], "search")


# ─── The store ────────────────────────────────────────────────────────────


class LyricsFetchStore(Protocol):
    """The worker's reads and writes (a fake stands in for it in tests)."""

    async def due(self, limit: int) -> tuple[list[store.DueTrack], int]: ...

    async def next_timer(self) -> datetime | None: ...

    async def save_answer(self, track_id: int, answer: LrclibAnswer, query_md5: str) -> None: ...

    async def save_failure(self, track_id: int, code: str, query_md5: str) -> None: ...

    async def lrc_due(self, limit: int) -> list[int]: ...

    async def lrc_row(self, track_id: int) -> store.LrcRow | None: ...

    async def record_lrc(self, track_id: int, outcome: sidecar.WriteOutcome) -> None: ...


class DbLyricsFetchStore:
    """The real store: each call in its own transaction, committed when it
    returns (so nothing is left uncommitted while waiting for
    SIDECAR_LOCK, [O4])."""

    async def due(self, limit: int) -> tuple[list[store.DueTrack], int]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.due_tracks(s, limit=limit)

    async def next_timer(self) -> datetime | None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.next_timer(s)

    async def save_answer(self, track_id: int, answer: LrclibAnswer, query_md5: str) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await store.record_lrclib(s, track_id, answer, query_md5=query_md5)

    async def save_failure(self, track_id: int, code: str, query_md5: str) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await store.record_failure(s, track_id, code=code, query_md5=query_md5)

    async def lrc_due(self, limit: int) -> list[int]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.lrc_due(s, limit=limit)

    async def lrc_row(self, track_id: int) -> store.LrcRow | None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.lrc_row(s, track_id)

    async def record_lrc(self, track_id: int, outcome: sidecar.WriteOutcome) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await store.record_lrc(s, track_id, outcome)


# ─── The worker ───────────────────────────────────────────────────────────


class LyricsFetchError(RuntimeError):
    """What a failed tick raises to the runner: the exception TYPE only (a
    database error's text can quote a row's lyrics)."""


class _GateClosed(Exception):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


class _Gated:
    """The client with THE gate in front of every request ([R10])."""

    def __init__(self, client: LrclibClient, counts: Counter[str]) -> None:
        self._client = client
        self._counts = counts

    def _check(self) -> None:
        state = lyrics_state.gate_state()
        if state is not None:
            raise _GateClosed(state)
        lyrics_state.FETCH.last_request_at = time.time()
        self._counts["requests"] += 1

    async def get(self, **kw: Any) -> LrclibRecord | None:
        self._check()
        return await self._client.get(**kw)

    async def search(self, **kw: Any) -> list[LrclibRecord]:
        self._check()
        return await self._client.search(**kw)


def _music_root() -> Path:
    return Path(settings.music_dir).expanduser().resolve(strict=False)


class LyricsFetcher(Worker):
    """Asks LRCLIB about the songs with no timed lyrics of their own, a
    small batch per tick, and saves timed answers as ``.lrc`` files. See
    the module docstring."""

    name = "lyrics_fetch"
    # Self-gated (module docstring): the opt-in applies without a restart.
    enabled_setting = None
    interval_setting = "lyrics_fetch_interval_sec"
    stub_suppressed = True
    # It reports "offline" itself (the runner would only skip the tick).
    requires_online = False

    def __init__(
        self,
        *,
        client: LrclibClient | None = None,
        store: LyricsFetchStore | None = None,
    ) -> None:
        self._client = client
        self._store: LyricsFetchStore = store or DbLyricsFetchStore()

    def _lrclib(self) -> LrclibClient:
        return self._client or get_lrclib_client()

    async def tick(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        try:
            await self._tick(counts)
        except Exception as e:  # noqa: BLE001 — reported by TYPE only, then re-raised
            lyrics_state.FETCH.state = "error"
            lyrics_state.FETCH.last_error = type(e).__name__[:64]
            raise LyricsFetchError(f"lyrics fetch failed ({type(e).__name__})") from None
        finally:
            self._log_lrc(counts)
        return dict(counts)

    async def _tick(self, counts: Counter[str]) -> None:
        F = lyrics_state.FETCH
        now = time.time()
        F.last_tick_at = now
        gate = lyrics_state.gate_state()
        if gate == "off":
            F.state = "off"
            return
        if gate == "shutting_down":
            return
        # (2) the .lrc catch-up: local disk only, no egress ([R11], [W14]).
        if settings.lyrics_write_lrc:
            await self._lrc_step(counts)
        if gate is not None:                       # internet_off | offline
            F.state = gate
            return
        if F.rate_limited_until is not None and now < F.rate_limited_until:
            F.state = "rate_limited"
            return
        if F.paused_until is not None and now < F.paused_until:
            F.state = "paused"
            return
        due, total = await self._store.due(BATCH_SIZE)
        F.due = total
        if not due:
            F.state = await self._quiet_state()
            return
        F.state = "running"
        started = time.monotonic()
        stopped = await self._batch(due, counts)
        elapsed = time.monotonic() - started
        F.due = max(0, total - counts["asked"])
        if counts["asked"]:
            log.info(
                "lyrics fetch: %d asked in %.1f s (%.2f requests/s): %d found (%d timed), "
                "%d not found, %d instrumental, %d skipped; %d still due",
                counts["asked"], elapsed, counts["requests"] / max(elapsed, 1e-6),
                counts["found"], counts["timed"], counts["not_found"], counts["instrumental"],
                counts["skipped"], F.due,
            )
        if stopped == "shutting_down":
            return
        if stopped is not None:
            F.state = stopped
        elif F.due:
            F.state = "running"
        else:
            F.state = await self._quiet_state()

    async def _quiet_state(self) -> str:
        """idle (a re-check timer within a day, or songs the local scan has
        not read yet) or done."""
        if lyrics_state.SCAN.unscanned:
            return "idle"
        nxt = await self._store.next_timer()
        if nxt is not None and nxt <= datetime.now(timezone.utc) + DONE_HORIZON:
            return "idle"
        return "done"

    async def _batch(self, due: Sequence[store.DueTrack], counts: Counter[str]) -> str | None:
        """Ask about each of ``due``. Returns the state to report when it
        stopped early, else None."""
        F = lyrics_state.FETCH
        client = _Gated(self._lrclib(), counts)
        refusals = 0
        for track in due:
            meta = TrackMeta(track.id, track.title, track.artist, track.album, track.duration_sec)
            md5 = store.query_md5(track.title, track.artist, track.album, track.duration_sec)
            try:
                answer = await ask_lrclib(client, meta)
            except _GateClosed as g:
                return g.state
            except egress.InternetTurnedOff:
                return "internet_off"
            except LrclibRateLimited as e:
                pause = max(RATE_LIMIT_MIN_PAUSE_SEC, e.retry_after or 0.0)
                F.rate_limited_until = time.time() + pause
                F.last_error = "rate_limited"
                log.info("LRCLIB asked us to slow down; pausing the lyrics fetch for %.0f s", pause)
                return "rate_limited"
            except LrclibUnavailable as e:
                F.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
                F.last_error = f"unavailable:{e.code}"[:64]
                log.info("LRCLIB unavailable (%s); pausing the lyrics fetch for %.0f s",
                         e.code, UNAVAILABLE_PAUSE_SEC)
                return "paused"
            except LrclibRejected as e:
                code = f"rejected:{e.status}"
                await self._store.save_failure(track.id, code, md5)
                counts["asked"] += 1
                counts["errors"] += 1
                F.last_error = code
                refusals += 1
                log.debug("lyrics fetch: track %d refused (%s)", track.id, code)
                if refusals >= MAX_REFUSALS_IN_A_ROW:
                    F.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
                    log.info("LRCLIB refused %d requests in a row; pausing the lyrics fetch "
                             "for %.0f s", refusals, UNAVAILABLE_PAUSE_SEC)
                    return "paused"
                continue
            refusals = 0
            if not store.answer_fits(answer):
                # [R28], decided before the row is sent: a CHECK refusing it
                # would put the lyrics' first characters in the database
                # server's own log.
                await self._store.save_failure(track.id, store.TOO_LARGE, md5)
                counts["asked"] += 1
                counts["errors"] += 1
                F.last_error = store.TOO_LARGE
                log.warning("lyrics fetch: track %d's lyrics are too big to keep", track.id)
                continue
            try:
                await self._store.save_answer(track.id, answer, md5)
            except Exception as e:  # noqa: BLE001 — one row's data never stalls the rest [R28]
                code = f"save:{type(e).__name__}"[:64]
                # If even this fails the database is down: it propagates.
                await self._store.save_failure(track.id, code, md5)
                counts["asked"] += 1
                counts["errors"] += 1
                F.last_error = code
                log.warning("lyrics fetch: couldn't save track %d's answer (%s)",
                            track.id, type(e).__name__)
                continue
            counts["asked"] += 1
            counts[answer.status] += 1
            if answer.synced:
                counts["timed"] += 1
            F.last_error = None
            log.debug("lyrics fetch: track %d → %s%s", track.id, answer.status,
                      f" ({answer.match}, timed)" if answer.synced else
                      (f" ({answer.match})" if answer.match else ""))
            if (answer.status == "found" and answer.synced
                    and settings.lyrics_lrclib_enabled and settings.lyrics_write_lrc):
                # [R29]: the answer is committed; now its .lrc ([O4]).
                await self._write_lrc(track.id, counts)
        return None

    async def _lrc_step(self, counts: Counter[str]) -> None:
        """[W14]: write the ``.lrc`` of timed lyrics fetched while saving was
        off, and retry failed writes once a day."""
        if counts["lrc_stop"]:
            return
        for track_id in await self._store.lrc_due(LRC_BATCH):
            if not (settings.lyrics_lrclib_enabled and settings.lyrics_write_lrc):
                break
            if lyrics_state.gate_state() == "shutting_down":
                break
            outcome = await self._write_lrc(track_id, counts)
            if outcome is not None and outcome.stop:
                break

    async def _write_lrc(self, track_id: int, counts: Counter[str]) -> sidecar.WriteOutcome | None:
        """One row's ``.lrc`` ([W1]–[W13]), with SIDECAR_LOCK held from
        reading the row to committing the outcome ([O4])."""
        if counts["lrc_stop"]:
            return None
        root = await asyncio.to_thread(_music_root)
        async with sidecar.SIDECAR_LOCK:
            # [W1]: both settings, read at write time.
            if not (settings.lyrics_lrclib_enabled and settings.lyrics_write_lrc):
                return None
            row = await self._store.lrc_row(track_id)
            if row is None or row.file_missing or not row.synced:
                return None
            if row.lrc_state in ("edited", "deleted"):
                return None
            if row.local_source == "sidecar":
                # The household's own .lrc is this song's lyrics ([M1]) —
                # an "Artist - Title" one too, which the writer cannot tell
                # by its name: no .lrc of Domovoi's goes beside it. One of
                # Domovoi's already there stays as it is ([W11]), not
                # refreshed while theirs is in use.
                if row.lrc_state not in (None, "failed"):
                    return None
                outcome = sidecar.WriteOutcome("exists", name=row.lrc_name)
                await self._store.record_lrc(track_id, outcome)
                counts["lrc_exists"] += 1
                return outcome
            content = format_lrc(
                row.synced, title=row.title or "", artist=row.artist or "", album=row.album,
                duration_sec=row.duration_sec, version=domovoi_version(),
            ).encode("utf-8")
            outcome = await asyncio.to_thread(
                sidecar.write_sidecar, row.file_path, content, music_root=root,
                lrc_state=row.lrc_state, lrc_name=row.lrc_name, lrc_sha256=row.lrc_sha256,
            )
            await self._store.record_lrc(track_id, outcome)
        if outcome.state == "written":
            counts["lrc_written"] += 1
        elif outcome.state == "exists":
            counts["lrc_exists"] += 1
        elif outcome.state == "failed" or outcome.error:
            counts["lrc_failed"] += 1
            counts[f"lrc_error:{outcome.error or 'io'}"] += 1
        if outcome.stop:
            counts["lrc_stop"] += 1
            log.warning("lyrics .lrc: the disk is full; no more .lrc files this round")
        log.debug("lyrics .lrc: track %d → %s%s", track_id, outcome.state,
                  f" ({outcome.error})" if outcome.error else "")
        return outcome

    @staticmethod
    def _log_lrc(counts: Counter[str]) -> None:
        if counts["lrc_written"] or counts["lrc_failed"]:
            codes = sorted(k.split(":", 1)[1] for k in counts if k.startswith("lrc_error:"))
            log.info(
                "lyrics .lrc: %d written, %d already had one, %d failed (%s)",
                counts["lrc_written"], counts["lrc_exists"], counts["lrc_failed"],
                ", ".join(codes) or "none",
            )


__all__ = [
    "BATCH_SIZE",
    "DbLyricsFetchStore",
    "LrclibAnswer",
    "LyricsFetchError",
    "LyricsFetchStore",
    "LyricsFetcher",
    "TrackMeta",
    "ask_lrclib",
]
