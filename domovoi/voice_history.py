"""Voice history: what Domovoi keeps of what people say, for how long, and
how it is deleted (owner, 2026-10-17).

What is "voice history" here — every place that holds what somebody said
to Domovoi, or what it answered, because of a turn:

* ``conversation_log`` — one row per routed turn: what was said
  (``user_text``), the answer, the room, the session, who spoke
  (``person_id``). The Conversations and Sessions tabs (Satellites → a
  room, People → a person) and the memory extractor read it.
* ``intents_log`` — one row per routed turn, and per dashboard action
  (``[ui] …``): the transcript, the handler and path, who spoke, the
  stage timings (with the fast lane's own hearing, ``fastlane_text``).
* ``sessions`` — a room's short-term conversation in ``context``: the
  last exchanges (``recent_turns``, each stamped ``at``), the last answer
  (``say that again``), and whatever a turn parked there (a question
  waiting for "yes", a song asked for, an introduction's clips).
* the satellites' logs kept on this server
  (``~/.domovoi/satellite-logs/<room>.log``, ``satellite_health.RetainedLog``),
  which quote every utterance (``heard: …``) and every reply.

"Keep voice history" (``settings.voice_history_keep``: 30, 90 — the
default — or 365 days, or forever) bounds all four. :func:`prune_before`
deletes everything older than a cutoff: the two tables' rows, the
sessions idle since before it, the exchanges older than it in a session
still in use, and the log lines before it. It works in small batches, one
short transaction each, with a pause between, so it never holds a table
for long; the event loop only awaits Postgres, and the file work runs in
a thread. The core's ``voice_history_pruner`` runs it once a day (and at
once when the setting is saved); "Delete all voice history" runs it with
the moment it was asked for as the cutoff, as a job written to
``voice_history_jobs`` (V030) first, so a restart in the middle finishes
it at the next boot.

"Forget me" and People → forget person (:func:`delete_person_history`)
delete that person's own spoken turns — the rows whose ``person_id`` is
theirs, which is who voice identification heard SPEAKING. A turn in which
somebody else said their name carries the speaker's id (or none) and
stays: the text is never searched. The short-term conversation of every
session they spoke in is cleared too (those entries aren't attributed).

Not voice history, and kept by their own rules: memories already
extracted, voice samples, voice notes, timers and their history, the
music played ("Recently played", ``media_plays``), chat threads (a chat-mic
turn is also a message in its thread, kept until the thread is deleted),
the opt-in command recordings (14 days at most), and the server's journal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.config import (
    VOICE_HISTORY_KEEP_LABELS,
    normalize_voice_history_keep,
    settings,
    voice_history_keep_days,
)
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

# Rows per DELETE. Each batch is its own transaction: at 2,000 rows a
# batch takes a few tens of milliseconds on the indexes it uses, so a
# turn being written never waits long behind it.
BATCH_ROWS = 2000
# The pause between batches, so the rest of the server's database work
# gets its turn while a big backlog (the first run after an update, a
# "delete all") is worked through.
BATCH_PAUSE_SEC = 0.02
# A retention run happens at most this often; the worker looks every
# ``voice_history_pruner_interval_sec`` whether one is due, and a saved
# setting runs one at once.
RETENTION_EVERY = timedelta(days=1)
# Finished job rows kept (the dashboard shows the newest of each kind).
JOB_ROWS_KEPT = 30
# How often a running delete-all writes its progress to its job row.
PROGRESS_EVERY_SEC = 1.0
# How far behind the newest turn's time the clock may be before a retention
# run is skipped (_clock_moved): a turn stamped by a database a moment
# ahead of this process is not a clock that moved.
CLOCK_SLACK = timedelta(minutes=5)

# The counts a run reports, per store. Never any text.
STORES = (
    "conversation_log",
    "intents_log",
    "sessions_deleted",
    "sessions_trimmed",
    "satellite_log_lines",
)

_NIL_UUID = "00000000-0000-0000-0000-000000000000"


# ─── The setting ────────────────────────────────────────────────────────────


def keep_choice() -> str:
    """The live ``voice_history_keep`` (one of the choices)."""
    return normalize_voice_history_keep(settings.voice_history_keep)


def keep_days() -> int | None:
    """Days kept, or None for forever."""
    return voice_history_keep_days(settings.voice_history_keep)


def cutoff_for(days: int | None, now: datetime | None = None) -> datetime | None:
    if days is None:
        return None
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


# ─── The satellites' logs on this server ───────────────────────────────────

# The core's own RetainedLog (app.state.satellite_retained_log): the same
# instance the satellites' log pushes append through, so a trim and an
# append never interleave (it holds the lock). Unbound in the web process
# and in tests that don't bind one: the file part is then skipped.
_RETAINED_LOG: Any = None


def use_retained_log(retained_log: Any) -> None:
    global _RETAINED_LOG
    _RETAINED_LOG = retained_log


def _drop_log_lines_before(cutoff: datetime) -> int:
    rl = _RETAINED_LOG
    if rl is None:
        return 0
    try:
        return int(rl.drop_before(cutoff))
    except Exception as e:  # noqa: BLE001 — the database part still counts
        log.warning("voice history: trimming the satellites' logs failed: %s", e)
        return 0


# ─── Deleting older history ────────────────────────────────────────────────

# One batch: the ids of the oldest ``n`` rows through the time index, then
# those rows through the primary key. Written as ``= ANY(ARRAY(...))``, not
# ``IN (SELECT ...)``: Postgres plans the IN form as a hash join over a
# scan of the WHOLE table, every batch (EXPLAIN on a 50,000-row lane),
# which makes a big backlog quadratic.
_DELETE_BATCH = {
    "conversation_log": text(
        "DELETE FROM conversation_log WHERE id = ANY(ARRAY("
        "SELECT id FROM conversation_log WHERE at < :cutoff ORDER BY at LIMIT :n))"
    ),
    "intents_log": text(
        "DELETE FROM intents_log WHERE id = ANY(ARRAY("
        "SELECT id FROM intents_log WHERE at < :cutoff ORDER BY at LIMIT :n))"
    ),
    # A session idle since before the cutoff: every exchange in it is
    # older, and so is anything a turn parked in it. conversation_log rows
    # still pointing at it lose the pointer (ON DELETE SET NULL). V030
    # indexes last_activity for this.
    "sessions_deleted": text(
        "DELETE FROM sessions WHERE id = ANY(ARRAY("
        "SELECT id FROM sessions WHERE last_activity < :cutoff "
        "ORDER BY last_activity LIMIT :n))"
    ),
}

# Sessions in use since the cutoff that began before it: the ones that can
# hold exchanges older than it.
_TRIM_CANDIDATES = text(
    "SELECT id FROM sessions "
    "WHERE last_activity >= :cutoff AND started_at < :cutoff "
    "AND id > CAST(:after AS uuid) ORDER BY id LIMIT :n"
)

# One statement per row set, computed from the row as it is when the
# UPDATE takes it, so an exchange a turn appended a moment ago is never
# lost. ``at`` is written by SessionRepository.record_exchange as UTC
# isoformat, which sorts as text; an entry with no ``at`` can't be dated
# and goes with the old ones.
_TRIM_TURNS = text(
    """
    UPDATE sessions
       SET context = jsonb_set(
             context, '{recent_turns}',
             COALESCE((SELECT jsonb_agg(e ORDER BY n)
                         FROM jsonb_array_elements(context->'recent_turns')
                              WITH ORDINALITY AS t(e, n)
                        WHERE e->>'at' >= :cutoff_iso), '[]'::jsonb))
     WHERE id = ANY(CAST(:ids AS uuid[]))
       AND jsonb_typeof(context->'recent_turns') = 'array'
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(context->'recent_turns') AS x(e)
                    WHERE (e->>'at') IS NULL OR e->>'at' < :cutoff_iso)
    """
)

# The last answer goes with the last exchange: once none is left younger
# than the cutoff, the answer is older too.
_DROP_LAST_ANSWER = text(
    """
    UPDATE sessions
       SET context = context - 'last_assistant_response'
     WHERE id = ANY(CAST(:ids AS uuid[]))
       AND context->'last_assistant_response' IS NOT NULL
       AND jsonb_typeof(context->'recent_turns') = 'array'
       AND jsonb_array_length(context->'recent_turns') = 0
    """
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


Progress = Callable[[dict[str, int]], Awaitable[None]]


async def prune_before(
    cutoff: datetime,
    *,
    batch_rows: int | None = None,
    pause_sec: float | None = None,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Delete everything voice history holds from before ``cutoff`` (see
    the module docstring). Batched; returns the counts per store."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    n = int(batch_rows or BATCH_ROWS)
    pause = BATCH_PAUSE_SEC if pause_sec is None else float(pause_sec)
    counts = dict.fromkeys(STORES, 0)

    async def _tick() -> None:
        if progress is not None:
            await progress(dict(counts))
        if pause > 0:
            await asyncio.sleep(pause)
        else:
            await asyncio.sleep(0)

    for store in ("conversation_log", "intents_log", "sessions_deleted"):
        while True:
            async with session_scope() as s:
                result = await s.execute(_DELETE_BATCH[store], {"cutoff": cutoff, "n": n})
            done = result.rowcount or 0
            counts[store] += done
            if done < n:
                break
            await _tick()

    after = _NIL_UUID
    cutoff_iso = _iso(cutoff)
    while True:
        async with session_scope() as s:
            ids = [
                r[0] for r in (
                    await s.execute(_TRIM_CANDIDATES, {"cutoff": cutoff, "after": after, "n": n})
                ).all()
            ]
            if ids:
                params = {"ids": [UUID(str(i)) for i in ids], "cutoff_iso": cutoff_iso}
                trimmed = await s.execute(_TRIM_TURNS, params)
                await s.execute(_DROP_LAST_ANSWER, {"ids": params["ids"]})
                counts["sessions_trimmed"] += trimmed.rowcount or 0
        if len(ids) < n:
            break
        after = str(ids[-1])
        await _tick()

    counts["satellite_log_lines"] = await asyncio.to_thread(_drop_log_lines_before, cutoff)
    if progress is not None:
        await progress(dict(counts))
    return counts


# ─── A person's own turns ──────────────────────────────────────────────────

_PERSON_SESSIONS = text(
    "SELECT DISTINCT session_id FROM conversation_log "
    "WHERE person_id = :pid AND session_id IS NOT NULL"
)
_CLEAR_SHORT_TERM = text(
    """
    UPDATE sessions
       SET context = (context - 'recent_turns') - 'last_assistant_response'
     WHERE id = ANY(CAST(:ids AS uuid[]))
       AND (context->'recent_turns' IS NOT NULL
            OR context->'last_assistant_response' IS NOT NULL)
    """
)


async def delete_person_history(s: AsyncSession, person_id: int) -> dict[str, int]:
    """Delete what ``person_id`` said: every ``conversation_log`` and
    ``intents_log`` row voice identification attributed to them as the
    SPEAKER, and the short-term conversation of each session those turns
    were in (its entries carry no speaker, so they all go). Runs in the
    caller's transaction, BEFORE the person row is deleted (that sets
    ``person_id`` to NULL on whatever is left, and their turns could no
    longer be found). A turn in which somebody else said their name is
    not theirs and stays: nothing here reads the text."""
    sids = [r[0] for r in (await s.execute(_PERSON_SESSIONS, {"pid": person_id})).all()]
    conv = await s.execute(
        text("DELETE FROM conversation_log WHERE person_id = :pid"), {"pid": person_id}
    )
    intents = await s.execute(
        text("DELETE FROM intents_log WHERE person_id = :pid"), {"pid": person_id}
    )
    cleared = 0
    if sids:
        res = await s.execute(
            _CLEAR_SHORT_TERM, {"ids": [UUID(str(i)) for i in sids]}
        )
        cleared = res.rowcount or 0
    counts = {
        "conversation_log": conv.rowcount or 0,
        "intents_log": intents.rowcount or 0,
        "sessions_cleared": cleared,
    }
    log.info(
        "voice history: deleted person_id=%d's turns (%d conversation, %d intent rows; "
        "%d session(s) cleared)",
        person_id, counts["conversation_log"], counts["intents_log"], cleared,
    )
    return counts


# ─── Jobs (V030 voice_history_jobs) ────────────────────────────────────────

_JOB_COLS = "id, kind, cutoff, requested_at, started_at, finished_at, deleted, error"

# One run at a time in this process (the worker's retention run, a saved
# setting's run, a delete-all). Per event loop: tests run several loops.
_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock


def _job(row: Any) -> dict[str, Any]:
    deleted = row[6]
    if isinstance(deleted, str):
        try:
            deleted = json.loads(deleted)
        except ValueError:
            deleted = None
    return {
        "id": int(row[0]),
        "kind": row[1],
        "cutoff": row[2],
        "requested_at": row[3],
        "started_at": row[4],
        "finished_at": row[5],
        "deleted": deleted,
        "error": row[7],
        # A delete-all that failed stays open (retried); a retention run
        # that failed is closed (the next one has a later cutoff).
        "state": (
            "failed" if row[7]
            else "done" if row[5] is not None
            else "running" if row[4] is not None
            else "queued"
        ),
    }


async def request_delete_all() -> dict[str, Any]:
    """Ask for everything said until now to be deleted. Run it with
    :func:`run_pending_jobs`. Pressing again:

    * while a job waits (never started, or failed and waiting for its
      retry) moves that job's cutoff up to now and answers it, so what was
      said between the two presses goes too (review 2026-10-17, N-V2);
    * while one is running answers a follow-up job with its own cutoff,
      which runs after it (one follow-up at most: a further press moves it).
    """
    async with session_scope() as s:
        await s.execute(text("LOCK TABLE voice_history_jobs IN SHARE ROW EXCLUSIVE MODE"))
        row = (
            await s.execute(
                text(
                    "UPDATE voice_history_jobs SET cutoff = GREATEST(cutoff, NOW()) "
                    "WHERE id = (SELECT id FROM voice_history_jobs "
                    "WHERE kind = 'delete_all' AND finished_at IS NULL "
                    "AND (started_at IS NULL OR error IS NOT NULL) ORDER BY id DESC LIMIT 1) "
                    f"RETURNING {_JOB_COLS}"
                )
            )
        ).first()
        if row is None:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO voice_history_jobs (kind, cutoff) "
                        f"VALUES ('delete_all', NOW()) RETURNING {_JOB_COLS}"
                    )
                )
            ).first()
            log.info("voice history: delete-all requested (job %d)", int(row[0]))
    return _job(row)


async def _set_progress(job_id: int, counts: dict[str, int]) -> None:
    async with session_scope() as s:
        await s.execute(
            text("UPDATE voice_history_jobs SET deleted = CAST(:d AS jsonb) WHERE id = :id"),
            {"d": json.dumps(counts), "id": job_id},
        )


async def _run_job(job_id: int, cutoff: datetime, **prune_kw: Any) -> dict[str, int]:
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE voice_history_jobs SET started_at = COALESCE(started_at, NOW()), "
                "error = NULL WHERE id = :id"
            ),
            {"id": job_id},
        )
    last = 0.0

    async def progress(counts: dict[str, int]) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= PROGRESS_EVERY_SEC:
            last = now
            await _set_progress(job_id, counts)

    try:
        counts = await prune_before(cutoff, progress=progress, **prune_kw)
    except Exception as e:
        async with session_scope() as s:
            await s.execute(
                text("UPDATE voice_history_jobs SET error = :e WHERE id = :id"),
                {"e": f"{type(e).__name__}: {e}"[:500], "id": job_id},
            )
        raise
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE voice_history_jobs SET finished_at = NOW(), "
                "deleted = CAST(:d AS jsonb), error = NULL WHERE id = :id"
            ),
            {"d": json.dumps(counts), "id": job_id},
        )
    return counts


async def _trim_job_rows() -> None:
    async with session_scope() as s:
        await s.execute(
            text(
                "DELETE FROM voice_history_jobs WHERE finished_at IS NOT NULL AND id NOT IN ("
                "SELECT id FROM voice_history_jobs WHERE finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT :keep)"
            ),
            {"keep": JOB_ROWS_KEPT},
        )


async def run_pending_jobs(**prune_kw: Any) -> list[dict[str, Any]]:
    """Run every open "delete all" job, oldest first (one a restart cut
    short included). A job that fails keeps its error and stays open for
    the next try. Returns what ran."""
    ran: list[dict[str, Any]] = []
    async with _lock():
        tried: set[int] = set()
        while True:
            async with session_scope() as s:
                row = (
                    await s.execute(
                        text(
                            f"SELECT {_JOB_COLS} FROM voice_history_jobs "
                            "WHERE kind = 'delete_all' AND finished_at IS NULL "
                            "ORDER BY id LIMIT 1"
                        )
                    )
                ).first()
            if row is None or int(row[0]) in tried:
                break
            job = _job(row)
            tried.add(job["id"])
            try:
                job["deleted"] = await _run_job(job["id"], job["cutoff"], **prune_kw)
                job["state"] = "done"
                log.info("voice history: delete-all job %d done: %s", job["id"], job["deleted"])
            except Exception as e:  # noqa: BLE001 — kept open, retried
                log.warning("voice history: delete-all job %d failed: %s", job["id"], e)
                job["state"] = "failed"
            ran.append(job)
        if ran:
            await _trim_job_rows()
    return ran


async def _clock_moved(cutoff: datetime, now: datetime) -> str | None:
    """Why a retention run must not trust this clock, or None (review
    2026-10-17, N-V3). Turns are stamped by the same clock that sets the
    cutoff, so:

    * now earlier than the newest turn: the clock went back (or was ahead
      when that turn was written);
    * the cutoff later than the newest turn: the run would delete every
      turn there is, the newest included — a clock that jumped ahead by
      more than the setting looks exactly like this.

    The second also holds back a house that has said nothing for longer
    than the setting: its last turns stay until someone speaks again."""
    async with session_scope() as s:
        newest = (
            await s.execute(
                text(
                    "SELECT GREATEST((SELECT MAX(at) FROM conversation_log), "
                    "(SELECT MAX(at) FROM intents_log))"
                )
            )
        ).scalar()
    if newest is None:
        return None
    if now < newest - CLOCK_SLACK:
        return f"the clock ({now.isoformat()}) is behind the newest turn ({newest.isoformat()})"
    if cutoff > newest:
        return (
            f"the cutoff ({cutoff.isoformat()}) is after the newest turn "
            f"({newest.isoformat()}): every turn would go"
        )
    return None


async def _last_retention_at() -> datetime | None:
    async with session_scope() as s:
        return (
            await s.execute(
                text(
                    "SELECT MAX(started_at) FROM voice_history_jobs "
                    "WHERE kind = 'retention' AND finished_at IS NOT NULL AND error IS NULL"
                )
            )
        ).scalar()


async def run_retention(*, force: bool = False, **prune_kw: Any) -> dict[str, Any] | None:
    """Apply "Keep voice history" now: delete everything older than it.
    Nothing for forever, and nothing when a run finished less than
    :data:`RETENTION_EVERY` ago (unless ``force``: the setting was just
    saved). Recorded as a ``retention`` job. Returns the job, or None."""
    async with _lock():
        days = keep_days()
        if days is None:
            return None
        if not force:
            last = await _last_retention_at()
            if last is not None and datetime.now(timezone.utc) - last < RETENTION_EVERY:
                return None
        now = datetime.now(timezone.utc)
        cutoff = cutoff_for(days, now)
        why = await _clock_moved(cutoff, now)
        if why is not None:
            log.warning("voice history: not deleting anything this time: %s", why)
            return None
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO voice_history_jobs (kind, cutoff, started_at) "
                        f"VALUES ('retention', :cutoff, NOW()) RETURNING {_JOB_COLS}"
                    ),
                    {"cutoff": cutoff},
                )
            ).first()
        job = _job(row)
        try:
            job["deleted"] = await _run_job(job["id"], cutoff, **prune_kw)
        except Exception:
            # A failed retention run is not retried as itself: the next one
            # has a later cutoff. Close it with its error.
            async with session_scope() as s:
                await s.execute(
                    text("UPDATE voice_history_jobs SET finished_at = NOW() WHERE id = :id"),
                    {"id": job["id"]},
                )
            raise
        job["state"] = "done"
        total = sum(job["deleted"].values())
        if total:
            log.info(
                "voice history: deleted what was older than %d days: %s", days, job["deleted"]
            )
        await _trim_job_rows()
        return job


# Background runs started outside the worker (a delete-all request, a saved
# setting): held here so they aren't garbage-collected mid-run.
_BACKGROUND: set[asyncio.Task] = set()


def _spawn(coro: Awaitable[Any], name: str) -> asyncio.Task | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # type: ignore[attr-defined]
        return None
    task = loop.create_task(coro, name=name)  # type: ignore[arg-type]
    _BACKGROUND.add(task)

    def _done(t: asyncio.Task) -> None:
        _BACKGROUND.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.warning("voice history: %s failed: %s", name, t.exception())

    task.add_done_callback(_done)
    return task


def schedule_pending_jobs() -> asyncio.Task | None:
    """Start :func:`run_pending_jobs` in the background (a delete-all was
    just asked for)."""
    return _spawn(run_pending_jobs(), "voice-history-delete-all")


def schedule_retention_now() -> None:
    """The reapply hook of ``voice_history_keep``: a saved setting applies
    at once, not at the next daily run."""
    _spawn(run_retention(force=True), "voice-history-retention")


# ─── What the dashboard shows ──────────────────────────────────────────────


async def status(s: AsyncSession, keep: str | None = None) -> dict[str, Any]:
    """The setting, how much is kept and since when, and the latest jobs.
    Counts and dates only, never what was said."""
    keep = normalize_voice_history_keep(keep if keep is not None else settings.voice_history_keep)
    turns, oldest = (
        await s.execute(text("SELECT COUNT(*), MIN(at) FROM conversation_log"))
    ).one()
    latest: dict[str, dict[str, Any] | None] = {}
    for kind in ("delete_all", "retention"):
        row = (
            await s.execute(
                text(
                    f"SELECT {_JOB_COLS} FROM voice_history_jobs WHERE kind = :k "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"k": kind},
            )
        ).first()
        latest[kind] = _job(row) if row is not None else None
    days = voice_history_keep_days(keep)
    return {
        "keep": keep,
        "keep_label": VOICE_HISTORY_KEEP_LABELS[keep],
        "keep_days": days,
        "choices": [
            {"value": v, "label": label} for v, label in VOICE_HISTORY_KEEP_LABELS.items()
        ],
        "turns": int(turns or 0),
        "oldest_at": oldest,
        "delete_all": latest["delete_all"],
        "last_cleanup": latest["retention"],
    }
