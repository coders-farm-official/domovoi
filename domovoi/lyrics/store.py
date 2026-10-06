"""The database side of the lyrics (V021 ``track_lyrics``; contract §3).

Column ownership (§3.1) — each writer updates only its own columns, so the
three workers never undo each other:

* the local scan (``workers/lyrics_scan.py``): row creation, ``file_*``,
  ``local_*``, ``sidecar_*``, ``scan_version``, and ``lrc_state`` only for
  ``written → edited`` and ``written → deleted``;
* the LRCLIB fetch (``workers/lyrics_fetch.py``): ``lrclib_*`` and
  ``lrc_*``;
* lyric search's index (B2): ``lines_md5``, ``lines_version``.

Every write here that changes a lyric, a status or a stat sets
``updated_at = now()`` (the web's realtime digest watches it). Nothing is
logged here, and no exception raised here carries a lyric: callers log the
exception TYPE only (a database error's text can quote the failing row).

Status follows V020's lesson ([M4]): only an answer stamps. The internet
being off, the server offline, a rate limit or an unreachable LRCLIB never
reach these functions at all.

Public for the tests and the evaluation ([R33]): :func:`query_md5`,
:func:`ensure_rows`, :func:`record_lrclib`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from sqlalchemy import text

from domovoi.lyrics import SCAN_VERSION
from domovoi.lyrics.lrc import MAX_ENTRIES, ParsedLyrics, from_entries

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

#: ``track_lyrics_size_chk``: a plain text column holds at most this many
#: bytes, a timed column at most :data:`~domovoi.lyrics.lrc.MAX_ENTRIES`.
MAX_PLAIN_BYTES = 262_144

#: §3.5 retry windows.
FOUND_PLAIN_RECHECK = timedelta(days=28)
INSTRUMENTAL_RECHECK = timedelta(days=180)
NOT_FOUND_BASE = timedelta(days=28)
NOT_FOUND_MAX_FACTOR = 4
ERROR_BACKOFF_CAP = timedelta(days=7)

#: [R16]'s SQL twin of :func:`query_md5`, over ``library_tracks t``.
QUERY_MD5_SQL = (
    "md5(coalesce(t.title,'') || chr(31) || coalesce(t.artist,'') || chr(31) || "
    "coalesce(t.album,'') || chr(31) || coalesce(t.duration_sec::text,''))"
)


def query_md5(title: str | None, artist: str | None, album: str | None,
              duration_sec: int | None) -> str:
    """md5 of what LRCLIB is asked about a track ([R16]): title, artist,
    album, duration, joined by U+001F. A change re-asks the track."""
    parts = [
        title or "", artist or "", album or "",
        "" if duration_sec is None else str(int(duration_sec)),
    ]
    return hashlib.md5("\x1f".join(parts).encode("utf-8")).hexdigest()


def fits(parsed: ParsedLyrics) -> bool:
    """Whether lyrics fit ``track_lyrics_size_chk``."""
    if parsed.plain is None:
        return False
    if parsed.synced is not None and len(parsed.synced) > MAX_ENTRIES:
        return False
    return len(parsed.plain.encode("utf-8")) <= MAX_PLAIN_BYTES


def synced_json(synced: Sequence[tuple[int, str]] | None) -> str | None:
    """Timed lyrics as stored: a JSON array of ``[ms, "text"]`` pairs."""
    if synced is None:
        return None
    return json.dumps([[int(ms), str(body)] for ms, body in synced], ensure_ascii=False)


def synced_from_json(value: Any) -> tuple[tuple[int, str], ...] | None:
    """The stored JSON (text or already decoded) back as pairs; None for
    NULL or for nothing usable."""
    if value is None:
        return None
    try:
        data = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    pairs: list[tuple[int, str]] = []
    for item in data:
        if (isinstance(item, (list, tuple)) and len(item) == 2
                and isinstance(item[0], int) and not isinstance(item[0], bool)
                and isinstance(item[1], str)):
            pairs.append((item[0], item[1]))
    return tuple(pairs) if pairs else None


def lyrics_from_json(value: Any) -> ParsedLyrics:
    """Stored timed lyrics as :class:`ParsedLyrics` (cleaned again)."""
    pairs = synced_from_json(value)
    return from_entries(pairs) if pairs else from_entries(())


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def not_found_window(attempts: int) -> timedelta:
    """28 days × min(2^(attempts−1), 4): 28, 56, 112, 112 … days."""
    return NOT_FOUND_BASE * min(2 ** max(attempts - 1, 0), NOT_FOUND_MAX_FACTOR)


def error_backoff(attempts: int) -> timedelta:
    """min(2^(attempts−1) hours, 7 days)."""
    n = max(attempts - 1, 0)
    if n >= 8:
        return ERROR_BACKOFF_CAP
    return min(timedelta(hours=2 ** n), ERROR_BACKOFF_CAP)


# ─── Rows ─────────────────────────────────────────────────────────────────


async def ensure_rows(session: "AsyncSession", track_ids: Iterable[int]) -> int:
    """A ``track_lyrics`` row for each of ``track_ids`` that is a library
    track and has none yet (``INSERT … ON CONFLICT DO NOTHING``; the scan's
    columns untouched). Returns the number of rows created."""
    ids = sorted({int(i) for i in track_ids})
    if not ids:
        return 0
    res = await session.execute(text(
        "INSERT INTO track_lyrics (track_id) "
        "SELECT t.id FROM library_tracks t WHERE t.id = ANY(:ids) "
        "ON CONFLICT (track_id) DO NOTHING RETURNING track_id"
    ), {"ids": ids})
    return len(res.all())


# ─── LRCLIB answers ([R25], [R26], §3.5) ──────────────────────────────────

_PREV_SQL = text(
    "SELECT lrclib_status, lrclib_attempts, lrclib_query_md5, lrclib_error "
    "FROM track_lyrics WHERE track_id = :id FOR UPDATE"
)

_ANSWER_SQL = text(
    "UPDATE track_lyrics SET "
    "lrclib_status = :status, lrclib_id = :lrclib_id, lrclib_match = :match, "
    "lrclib_plain = :plain, lrclib_synced = CAST(:synced AS JSONB), "
    "lrclib_instrumental = :instrumental, lrclib_query_md5 = :md5, "
    "lrclib_checked_at = :now, lrclib_next_at = :next_at, lrclib_attempts = :attempts, "
    "lrclib_error = :error, updated_at = now() "
    "WHERE track_id = :id"
)

_KEEP_FOUND_SQL = text(
    "UPDATE track_lyrics SET lrclib_checked_at = :now, lrclib_next_at = :next_at, "
    "lrclib_error = NULL, updated_at = now() WHERE track_id = :id"
)


async def _previous(session: "AsyncSession", track_id: int) -> tuple[Any, int, Any, Any]:
    row = (await session.execute(_PREV_SQL, {"id": track_id})).first()
    if row is None:
        await ensure_rows(session, [track_id])
        row = (await session.execute(_PREV_SQL, {"id": track_id})).first()
        if row is None:
            raise LookupError(f"track {track_id} is not in the library")
    return row[0], int(row[1] or 0), row[2], row[3]


async def record_lrclib(
    session: "AsyncSession", track_id: int, answer: Any, *,
    query_md5: str, now: datetime | None = None,
) -> None:
    """Store LRCLIB's answer about one track ([R25], [R26], §3.5).
    ``answer`` is a :class:`~domovoi.workers.lyrics_fetch.LrclibAnswer`
    (status found | instrumental | not_found | skipped). Reads the row's
    current LRCLIB columns (locking it) to decide keep / clear and the
    attempt count. The caller commits."""
    at = _now(now)
    prev_status, prev_attempts, prev_md5, _prev_error = await _previous(session, track_id)
    status = answer.status
    record = getattr(answer, "record", None)
    values: dict[str, Any] = {
        "id": track_id, "md5": query_md5, "now": at, "lrclib_id": None, "match": None,
        "plain": None, "synced": None, "instrumental": False, "error": None,
    }
    if status == "found":
        if answer.plain is None:
            raise ValueError("a found answer carries lyrics")
        values.update(
            status="found", lrclib_id=getattr(record, "id", None), match=answer.match,
            plain=answer.plain, synced=synced_json(answer.synced), attempts=0,
            next_at=None if answer.synced else at + FOUND_PLAIN_RECHECK,
        )
    elif status == "instrumental":
        values.update(
            status="instrumental", lrclib_id=getattr(record, "id", None), match=answer.match,
            instrumental=True, attempts=0, next_at=at + INSTRUMENTAL_RECHECK,
        )
    elif status == "not_found":
        if prev_status == "found" and prev_md5 == query_md5:
            # [R26]: a timed re-check of plain-only lyrics found nothing
            # better — keep them, ask again in four weeks.
            await session.execute(_KEEP_FOUND_SQL, {
                "id": track_id, "now": at, "next_at": at + FOUND_PLAIN_RECHECK,
            })
            return
        attempts = prev_attempts + 1 if prev_status == "not_found" else 1
        values.update(status="not_found", attempts=attempts, next_at=at + not_found_window(attempts))
    elif status == "skipped":
        values.update(status="skipped", attempts=0, next_at=None, error=answer.error)
    else:
        raise ValueError(f"not an LRCLIB answer status: {status!r}")
    await session.execute(_ANSWER_SQL, values)


_KEEP_FOUND_FAILURE_SQL = text(
    "UPDATE track_lyrics SET lrclib_checked_at = :now, lrclib_next_at = :next_at, "
    "lrclib_attempts = :attempts, lrclib_error = :error, updated_at = now() "
    "WHERE track_id = :id"
)


async def record_failure(
    session: "AsyncSession", track_id: int, *, code: str, query_md5: str,
    now: datetime | None = None,
) -> None:
    """LRCLIB refused the request (``rejected:<status>``, [R27]) or this
    row's answer could not be saved (``save:<ExceptionType>``, [R28]):
    status ``error`` with the §3.5 backoff (min(2^(attempts−1) h, 7 d)).

    A row that holds lyrics LRCLIB found for the SAME question keeps them
    and its ``found`` status (a refusal says nothing about the lyrics, and
    ``track_lyrics_lrclib_text_chk`` allows lyrics only beside ``found``);
    only the error, the attempt count and the timer move. When the question
    changed, the old lyrics may be another song's, so they go."""
    at = _now(now)
    prev_status, prev_attempts, prev_md5, prev_error = await _previous(session, track_id)
    code = code[:64]
    if prev_status == "found" and prev_md5 == query_md5:
        failing = isinstance(prev_error, str) and prev_error.startswith(("rejected:", "save:"))
        attempts = prev_attempts + 1 if failing else 1
        await session.execute(_KEEP_FOUND_FAILURE_SQL, {
            "id": track_id, "now": at, "next_at": at + error_backoff(attempts),
            "attempts": attempts, "error": code,
        })
        return
    attempts = prev_attempts + 1 if prev_status == "error" else 1
    await session.execute(_ANSWER_SQL, {
        "id": track_id, "status": "error", "lrclib_id": None, "match": None, "plain": None,
        "synced": None, "instrumental": False, "md5": query_md5, "now": at,
        "next_at": at + error_backoff(attempts), "attempts": attempts, "error": code,
    })


# ─── Which tracks LRCLIB is asked about ([R13], [R14]) ────────────────────

_ELIGIBLE = (
    "l.local_checked_at IS NOT NULL AND NOT l.file_missing "
    "AND l.local_source IS DISTINCT FROM 'sidecar' AND l.local_synced IS NULL "
    "AND (l.lrclib_status IS NULL "
    f"OR l.lrclib_query_md5 IS DISTINCT FROM {QUERY_MD5_SQL} "
    "OR l.lrclib_next_at <= now())"
)

_DUE_SQL = text(
    "WITH recent AS (SELECT DISTINCT library_track_id AS id FROM media_plays "
    "WHERE library_track_id IS NOT NULL AND started_at > now() - interval '30 days') "
    "SELECT t.id, t.title, t.artist, t.album, t.duration_sec "
    "FROM track_lyrics l JOIN library_tracks t ON t.id = l.track_id "
    "LEFT JOIN recent r ON r.id = t.id "
    f"WHERE {_ELIGIBLE} "
    "ORDER BY (r.id IS NOT NULL) DESC, t.favorited DESC, (l.lrclib_status IS NULL) DESC, "
    "(t.enriched_at IS NOT NULL) DESC, t.id "
    "LIMIT :limit"
)

_DUE_COUNT_SQL = text(
    f"SELECT count(*) FROM track_lyrics l JOIN library_tracks t ON t.id = l.track_id WHERE {_ELIGIBLE}"
)

_NEXT_TIMER_SQL = text(
    "SELECT min(l.lrclib_next_at) FROM track_lyrics l "
    "WHERE l.local_checked_at IS NOT NULL AND NOT l.file_missing "
    "AND l.local_source IS DISTINCT FROM 'sidecar' AND l.local_synced IS NULL "
    "AND l.lrclib_next_at > now()"
)


@dataclass(frozen=True)
class DueTrack:
    id: int
    title: str | None
    artist: str | None
    album: str | None
    duration_sec: int | None


async def due_tracks(session: "AsyncSession", *, limit: int) -> tuple[list[DueTrack], int]:
    """The next ``limit`` tracks to ask LRCLIB about, in [R14]'s order, and
    how many are due in all."""
    rows = (await session.execute(_DUE_SQL, {"limit": int(limit)})).all()
    count = int((await session.execute(_DUE_COUNT_SQL)).scalar_one())
    return [DueTrack(int(r[0]), r[1], r[2], r[3], r[4]) for r in rows], count


async def next_timer(session: "AsyncSession") -> datetime | None:
    """The earliest LRCLIB re-check timer still to come."""
    return (await session.execute(_NEXT_TIMER_SQL)).scalar_one_or_none()


# ─── The local scan ([K2]–[K6]) ───────────────────────────────────────────


@dataclass(frozen=True)
class ScanRow:
    """A library track and what the scan last recorded about it."""

    id: int
    file_path: str
    title: str | None
    artist: str | None
    has_row: bool
    checked: bool
    scan_version: int
    file_mtime_ns: int | None
    file_size: int | None
    file_missing: bool
    sidecar_name: str | None
    sidecar_mtime_ns: int | None
    sidecar_size: int | None

    @property
    def never_scanned(self) -> bool:
        return not self.has_row or not self.checked


_SCAN_ROWS_SQL = text(
    "SELECT t.id, t.file_path, t.title, t.artist, l.track_id IS NOT NULL, "
    "l.local_checked_at IS NOT NULL, l.scan_version, l.file_mtime_ns, l.file_size, "
    "l.file_missing, l.sidecar_name, l.sidecar_mtime_ns, l.sidecar_size "
    "FROM library_tracks t LEFT JOIN track_lyrics l ON l.track_id = t.id "
    "ORDER BY (l.track_id IS NULL) DESC, (l.local_checked_at IS NULL) DESC, t.id"
)


async def scan_rows(session: "AsyncSession") -> list[ScanRow]:
    """Every library track, never-seen ones first ([K2])."""
    out: list[ScanRow] = []
    for r in (await session.execute(_SCAN_ROWS_SQL)).all():
        out.append(ScanRow(
            id=int(r[0]), file_path=r[1], title=r[2], artist=r[3], has_row=bool(r[4]),
            checked=bool(r[5]), scan_version=int(r[6] or 0), file_mtime_ns=r[7],
            file_size=r[8], file_missing=bool(r[9]), sidecar_name=r[10],
            sidecar_mtime_ns=r[11], sidecar_size=r[12],
        ))
    return out


@dataclass(frozen=True)
class LrcState:
    state: str | None
    name: str | None
    sha256: str | None


async def lrc_states(session: "AsyncSession", track_ids: Sequence[int]) -> dict[int, LrcState]:
    """The ``lrc_*`` columns of ``track_ids`` as they are NOW (read inside
    SIDECAR_LOCK, [O4])."""
    ids = sorted({int(i) for i in track_ids})
    if not ids:
        return {}
    res = await session.execute(text(
        "SELECT track_id, lrc_state, lrc_name, lrc_sha256 FROM track_lyrics "
        "WHERE track_id = ANY(:ids)"
    ), {"ids": ids})
    return {int(r[0]): LrcState(r[1], r[2], r[3]) for r in res.all()}


@dataclass(frozen=True)
class LocalWrite:
    """One row as the scan read it ([K6])."""

    track_id: int
    file_mtime_ns: int
    file_size: int
    source: str | None              # sidecar | embedded | None
    detail: str | None
    lyrics: ParsedLyrics | None
    sidecar_name: str | None
    sidecar_mtime_ns: int | None
    sidecar_size: int | None
    lrc_transition: str | None      # edited | deleted | None


_LOCAL_SQL = text(
    "INSERT INTO track_lyrics AS l (track_id, file_mtime_ns, file_size, file_missing, "
    "local_checked_at, scan_version, local_source, local_detail, local_plain, local_synced, "
    "local_offset_ms, sidecar_name, sidecar_mtime_ns, sidecar_size, updated_at) "
    "VALUES (:id, :mtime, :size, FALSE, now(), :version, :source, :detail, :plain, "
    "CAST(:synced AS JSONB), :offset, :sc_name, :sc_mtime, :sc_size, now()) "
    "ON CONFLICT (track_id) DO UPDATE SET "
    "file_mtime_ns = EXCLUDED.file_mtime_ns, file_size = EXCLUDED.file_size, "
    "file_missing = FALSE, local_checked_at = now(), scan_version = EXCLUDED.scan_version, "
    "local_source = EXCLUDED.local_source, local_detail = EXCLUDED.local_detail, "
    "local_plain = EXCLUDED.local_plain, local_synced = EXCLUDED.local_synced, "
    "local_offset_ms = EXCLUDED.local_offset_ms, sidecar_name = EXCLUDED.sidecar_name, "
    "sidecar_mtime_ns = EXCLUDED.sidecar_mtime_ns, sidecar_size = EXCLUDED.sidecar_size, "
    "lrc_state = CASE WHEN l.lrc_state = 'written' AND CAST(:lrc_to AS TEXT) IS NOT NULL "
    "THEN CAST(:lrc_to AS TEXT) ELSE l.lrc_state END, "
    "updated_at = now()"
)

_MISSING_SQL = text(
    "INSERT INTO track_lyrics AS l (track_id, file_missing, local_checked_at, updated_at) "
    "VALUES (:id, TRUE, now(), now()) "
    "ON CONFLICT (track_id) DO UPDATE SET file_missing = TRUE, local_checked_at = now(), "
    "updated_at = now()"
)


async def write_local(session: "AsyncSession", w: LocalWrite) -> None:
    """[K6]: one upsert per row (the row is created on first sight)."""
    parsed = w.lyrics if w.source is not None else None
    if w.lrc_transition not in (None, "edited", "deleted"):
        raise ValueError(f"the scan never sets lrc_state {w.lrc_transition!r}")
    await session.execute(_LOCAL_SQL, {
        "id": w.track_id, "mtime": w.file_mtime_ns, "size": w.file_size,
        "version": SCAN_VERSION, "source": w.source if parsed is not None else None,
        "detail": w.detail if parsed is not None else None,
        "plain": parsed.plain if parsed is not None else None,
        "synced": synced_json(parsed.synced) if parsed is not None else None,
        "offset": (parsed.offset_ms if parsed is not None and parsed.synced is not None else None),
        "sc_name": w.sidecar_name, "sc_mtime": w.sidecar_mtime_ns, "sc_size": w.sidecar_size,
        "lrc_to": w.lrc_transition,
    })


async def write_missing(session: "AsyncSession", track_id: int) -> None:
    """[K4]: the audio file is gone — flagged; lyrics already stored kept."""
    await session.execute(_MISSING_SQL, {"id": track_id})


async def scan_counts(session: "AsyncSession") -> tuple[int, int]:
    """(library tracks, tracks the scan has not looked at)."""
    row = (await session.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE l.local_checked_at IS NULL) "
        "FROM library_tracks t LEFT JOIN track_lyrics l ON l.track_id = t.id"
    ))).one()
    return int(row[0]), int(row[1])


# ─── The .lrc writer ([W12]–[W14]) ────────────────────────────────────────


@dataclass(frozen=True)
class LrcRow:
    """What the writer needs to know about one row, read inside the lock."""

    track_id: int
    file_path: str
    title: str | None
    artist: str | None
    album: str | None
    duration_sec: int | None
    file_missing: bool
    synced: tuple[tuple[int, str], ...] | None
    lrc_state: str | None
    lrc_name: str | None
    lrc_sha256: str | None

    def __repr__(self) -> str:  # never the lyrics ([C3])
        return (
            f"LrcRow(track_id={self.track_id}, file_missing={self.file_missing}, "
            f"synced={'None' if self.synced is None else f'{len(self.synced)} lines'}, "
            f"lrc_state={self.lrc_state!r}, lrc_name={self.lrc_name!r})"
        )


async def lrc_row(session: "AsyncSession", track_id: int) -> LrcRow | None:
    r = (await session.execute(text(
        "SELECT t.id, t.file_path, t.title, t.artist, t.album, t.duration_sec, "
        "l.file_missing, l.lrclib_synced, l.lrc_state, l.lrc_name, l.lrc_sha256 "
        "FROM track_lyrics l JOIN library_tracks t ON t.id = l.track_id WHERE l.track_id = :id"
    ), {"id": int(track_id)})).first()
    if r is None:
        return None
    return LrcRow(
        track_id=int(r[0]), file_path=r[1], title=r[2], artist=r[3], album=r[4],
        duration_sec=r[5], file_missing=bool(r[6]), synced=synced_from_json(r[7]),
        lrc_state=r[8], lrc_name=r[9], lrc_sha256=r[10],
    )


async def lrc_due(session: "AsyncSession", *, limit: int) -> list[int]:
    """[W14]: timed LRCLIB lyrics never written (saving was off when they
    came), and failed writes not tried in the last 24 h."""
    res = await session.execute(text(
        "SELECT l.track_id FROM track_lyrics l "
        "WHERE l.lrclib_synced IS NOT NULL AND NOT l.file_missing "
        "AND (l.lrc_state IS NULL OR (l.lrc_state = 'failed' AND "
        "(l.lrc_attempted_at IS NULL OR l.lrc_attempted_at <= now() - interval '24 hours'))) "
        "ORDER BY l.track_id LIMIT :limit"
    ), {"limit": int(limit)})
    return [int(r[0]) for r in res.all()]


async def record_lrc(session: "AsyncSession", track_id: int, outcome: Any) -> None:
    """Record one write attempt's outcome
    (:class:`~domovoi.lyrics.sidecar.WriteOutcome`). The caller commits
    before releasing SIDECAR_LOCK ([W12], [O4])."""
    state = outcome.state
    params: dict[str, Any] = {"id": int(track_id), "name": outcome.name,
                              "sha": outcome.sha256, "error": outcome.error}
    if state == "written":
        sql = (
            "UPDATE track_lyrics SET lrc_state = 'written', lrc_name = :name, lrc_sha256 = :sha, "
            "lrc_attempted_at = now(), lrc_error = NULL, updated_at = now() WHERE track_id = :id"
        )
    elif state == "exists":
        sql = (
            "UPDATE track_lyrics SET lrc_state = CASE WHEN lrc_state IN ('edited', 'deleted', 'written') "
            "THEN lrc_state ELSE 'exists' END, lrc_attempted_at = now(), lrc_error = NULL, "
            "updated_at = now() WHERE track_id = :id"
        )
    elif state == "failed":
        sql = (
            "UPDATE track_lyrics SET lrc_state = CASE WHEN lrc_state IN ('edited', 'deleted', 'written') "
            "THEN lrc_state ELSE 'failed' END, lrc_name = COALESCE(lrc_name, :name), "
            "lrc_attempted_at = now(), lrc_error = :error, updated_at = now() WHERE track_id = :id"
        )
    elif state in ("edited", "deleted"):
        params["state"] = state
        sql = (
            "UPDATE track_lyrics SET lrc_state = :state, updated_at = now() "
            "WHERE track_id = :id AND lrc_state = 'written'"
        )
    elif state is None and outcome.error:
        # Refreshing Domovoi's own file failed: it stays Domovoi's.
        sql = (
            "UPDATE track_lyrics SET lrc_attempted_at = now(), lrc_error = :error, "
            "updated_at = now() WHERE track_id = :id"
        )
    else:
        return
    await session.execute(text(sql), params)


__all__ = [
    "DueTrack",
    "LocalWrite",
    "LrcRow",
    "LrcState",
    "MAX_PLAIN_BYTES",
    "QUERY_MD5_SQL",
    "ScanRow",
    "due_tracks",
    "ensure_rows",
    "error_backoff",
    "fits",
    "lrc_due",
    "lrc_row",
    "lrc_states",
    "lyrics_from_json",
    "next_timer",
    "not_found_window",
    "query_md5",
    "record_failure",
    "record_lrc",
    "record_lrclib",
    "scan_counts",
    "scan_rows",
    "synced_from_json",
    "synced_json",
    "write_local",
    "write_missing",
]
