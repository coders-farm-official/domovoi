"""Lyric search's line index: V021's ``track_lyric_lines``, kept current.

Every song whose shown lyrics (the view ``track_lyrics_shown``: the
owner's ``.lrc``, else timed beats plain, else the song's own tags beat
LRCLIB) changed since it was last indexed gets its lines rebuilt by
:func:`domovoi.handlers.shared.lyric_search.build_lines`: every distinct
line and every pair of consecutive lines, normalized by
``spoken_names.lyric_words``, with how often it is sung. A song is current
when ``lines_md5 = plain_md5`` and ``lines_version`` is today's
``LYRIC_NORM_VERSION`` (so bumping the version re-indexes everything); a
song whose lyrics went away has its lines removed.

This worker writes ONLY ``track_lyric_lines`` and the two bookkeeping
columns ``track_lyrics.lines_md5`` / ``lines_version`` (never
``updated_at``): the local scan and the LRCLIB fetch own every other
column, so the three never undo each other (contract §3.1).

It runs whether or not lyric search is switched on — switching it on is
then instant — and only reads the database, so it needs no network.
Nothing here logs a lyric: counts and track ids only.

Imported by the core at start-up (main.py): the lyric-search modules (and
their rapidfuzz / Metaphone dependencies) load on the first tick, never at
import.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

#: One tick's work: at most this long, in batches of this many songs.
TICK_BUDGET_SEC = 20.0
TICK_BATCH = 200

_PRESENT_SQL = text(
    "SELECT to_regclass('public.track_lyrics') IS NOT NULL"
    " AND to_regclass('public.track_lyric_lines') IS NOT NULL"
)
# The contract's pending query ([Q3]), walked in track id order from a
# cursor so a song that cannot be indexed is passed over, not retried in a
# loop, within one call.
_PENDING_SQL = text(
    "SELECT l.track_id, s.plain, l.plain_md5"
    " FROM track_lyrics l JOIN track_lyrics_shown s USING (track_id)"
    " WHERE ((l.plain_md5 IS NOT NULL"
    "        AND (l.lines_md5 IS DISTINCT FROM l.plain_md5"
    "             OR l.lines_version IS DISTINCT FROM :version))"
    "    OR (l.plain_md5 IS NULL AND l.lines_md5 IS NOT NULL))"
    "   AND l.track_id > :after"
    " ORDER BY l.track_id"
    " LIMIT :batch"
)
_DELETE_SQL = text("DELETE FROM track_lyric_lines WHERE track_id = :id")
_INSERT_SQL = text(
    "INSERT INTO track_lyric_lines (track_id, span, line_no, repeats, text)"
    " VALUES (:track_id, :span, :line_no, :repeats, :text)"
)
# Stamped only if the lyrics are still the ones the lines were built from;
# otherwise nothing is updated and the next tick does the song again.
_STAMP_SQL = text(
    "UPDATE track_lyrics SET lines_md5 = :md5, lines_version = :version"
    " WHERE track_id = :id AND plain_md5 IS NOT DISTINCT FROM :md5"
)
_UNSTAMP_SQL = text(
    "UPDATE track_lyrics SET lines_md5 = NULL, lines_version = NULL"
    " WHERE track_id = :id AND plain_md5 IS NULL"
)
_COUNTS_SQL = text(
    "SELECT"
    " count(*) FILTER (WHERE (plain_md5 IS NOT NULL"
    "                         AND (lines_md5 IS DISTINCT FROM plain_md5"
    "                              OR lines_version IS DISTINCT FROM :version))"
    "                     OR (plain_md5 IS NULL AND lines_md5 IS NOT NULL)) AS pending,"
    " count(*) FILTER (WHERE plain_md5 IS NOT NULL AND lines_md5 = plain_md5"
    "                    AND lines_version = :version) AS indexed"
    " FROM track_lyrics"
)


class LyricsTablesMissing(RuntimeError):
    """V021 is not applied to this database."""


# ─── Status (in-memory; the core snapshot's "lyrics_index") ───────────────


@dataclass
class _IndexStatus:
    state: str = "idle"
    pending: int | None = None
    indexed: int | None = None
    last_tick_at: float | None = None
    last_error: str | None = None


_STATUS = _IndexStatus()
# Songs a call could not index, warned about once each (by id, never text).
_WARNED: set[int] = set()
_warned_missing = False


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def lyrics_index_status() -> dict[str, Any]:
    """The core snapshot's ``"lyrics_index"`` key ([Q4]): ``state`` idle
    (not ticked yet) | running (catching up: songs still to do) | done |
    error; ``pending`` = songs whose lines are not current and ``indexed``
    = songs whose lines are, as of the last tick (None before it);
    ``search_enabled`` = this process's LIVE ``lyrics_search_enabled`` (the
    web's own copy of the settings goes stale on a dashboard save).
    In-memory, never a database read, never raises."""
    try:
        search_enabled = bool(settings.lyrics_search_enabled)
    except Exception:  # noqa: BLE001 — a status read never fails
        search_enabled = True
    return {
        "state": _STATUS.state,
        "pending": _STATUS.pending,
        "indexed": _STATUS.indexed,
        "last_tick_at": _iso(_STATUS.last_tick_at),
        "last_error": _STATUS.last_error,
        "search_enabled": search_enabled,
    }


def reset_status_for_tests() -> None:
    global _STATUS, _warned_missing
    _STATUS = _IndexStatus()
    _WARNED.clear()
    _warned_missing = False


# ─── The work ─────────────────────────────────────────────────────────────


async def _index_one(session: Any, track_id: int, plain: str | None, md5: str | None, version: int) -> str:
    """Rebuild one song's lines (or remove them) in the session's
    transaction; the caller commits. Returns "indexed" or "cleared"."""
    from domovoi.handlers.shared.lyric_search import build_lines

    await session.execute(_DELETE_SQL, {"id": track_id})
    if md5 is None or plain is None:
        await session.execute(_UNSTAMP_SQL, {"id": track_id})
        return "cleared"
    rows = await asyncio.to_thread(build_lines, plain)
    if rows:
        await session.execute(_INSERT_SQL, [
            {"track_id": track_id, "span": r.span, "line_no": r.line_no,
             "repeats": r.repeats, "text": r.text}
            for r in rows
        ])
    await session.execute(_STAMP_SQL, {"id": track_id, "md5": md5, "version": version})
    return "indexed"


async def catch_up(*, budget_sec: float = TICK_BUDGET_SEC, batch: int = TICK_BATCH) -> dict[str, int]:
    """Bring the line index up to date ([Q3]), for at most ``budget_sec``:
    every pending song rebuilt in its own transaction, a song whose lyrics
    went away cleared. Returns ``{"indexed", "cleared", "pending"}`` —
    what this call did, and how many songs are still not current (also
    what the tests and the evaluation call). Raises
    :class:`LyricsTablesMissing` without V021, and any database error."""
    from domovoi.db.session import SessionLocal
    from domovoi.handlers.shared.spoken_names import LYRIC_NORM_VERSION

    version = int(LYRIC_NORM_VERSION)
    deadline = time.monotonic() + float(budget_sec)
    indexed = cleared = failed = 0
    after = 0
    async with SessionLocal() as s:
        if not bool((await s.execute(_PRESENT_SQL)).scalar()):
            raise LyricsTablesMissing("track_lyrics / track_lyric_lines are missing (V021)")
        out_of_time = False
        while not out_of_time:
            rows = (await s.execute(
                _PENDING_SQL, {"version": version, "after": after, "batch": int(batch)}
            )).all()
            await s.commit()
            for row in rows:
                if time.monotonic() >= deadline:
                    out_of_time = True
                    break
                tid = int(row.track_id)
                after = tid
                try:
                    done = await _index_one(s, tid, row.plain, row.plain_md5, version)
                    await s.commit()
                except Exception as e:  # noqa: BLE001 — one song never stops the rest
                    await s.rollback()
                    failed += 1
                    _STATUS.last_error = type(e).__name__[:64]
                    if tid not in _WARNED:
                        _WARNED.add(tid)
                        log.warning("lyrics index: track %d could not be indexed (%s)", tid, type(e).__name__)
                    continue
                if done == "indexed":
                    indexed += 1
                else:
                    cleared += 1
            if len(rows) < int(batch):
                break
        counts = (await s.execute(_COUNTS_SQL, {"version": version})).one()
        await s.commit()
    pending = int(counts.pending or 0)
    _STATUS.pending = pending
    _STATUS.indexed = int(counts.indexed or 0)
    if not failed:
        _STATUS.last_error = None
    return {"indexed": indexed, "cleared": cleared, "pending": pending}


class LyricsIndexer(Worker):
    """Keeps lyric search's line index current ([Q2]). Always on — it runs
    while search is switched off too, so switching it on is instant — and
    local-only (a database read and write)."""

    name = "lyrics_index"
    enabled_setting = None
    interval_setting = "lyrics_index_interval_sec"
    stub_suppressed = True
    requires_online = False

    async def tick(self) -> dict[str, int] | None:
        global _warned_missing
        t0 = time.monotonic()
        _STATUS.last_tick_at = time.time()
        if _STATUS.state != "done":
            _STATUS.state = "running"
        try:
            result = await catch_up(budget_sec=TICK_BUDGET_SEC, batch=TICK_BATCH)
        except LyricsTablesMissing:
            _STATUS.state = "error"
            _STATUS.last_error = "no_lyrics_tables"
            if not _warned_missing:
                _warned_missing = True
                log.warning("lyrics index: V021 (track_lyrics) is not applied — nothing to index")
            return None
        except Exception as e:  # noqa: BLE001 — the next tick tries again
            _STATUS.state = "error"
            _STATUS.last_error = type(e).__name__[:64]
            log.warning("lyrics index: tick failed (%s)", type(e).__name__)
            return None
        _STATUS.state = "running" if result["pending"] else "done"
        if result["indexed"] or result["cleared"]:
            log.info(
                "lyrics index: %d songs indexed, %d cleared, %d still to do (%.1f s)",
                result["indexed"], result["cleared"], result["pending"], time.monotonic() - t0,
            )
        return result


__all__ = [
    "LyricsIndexer",
    "LyricsTablesMissing",
    "TICK_BATCH",
    "TICK_BUDGET_SEC",
    "catch_up",
    "lyrics_index_status",
    "reset_status_for_tests",
]
