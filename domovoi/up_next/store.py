"""Up next, kept: sessions and their picks in the database (V053, phase P3).

The SQL half of :mod:`.session` (which stays pure): one row per session in
``up_next_sessions`` (the columns the plan names beside a JSON ``state``),
one row per pick in ``up_next_picks``. Both processes may use it: the web
for browser and phone sessions (phase P3), the core for rooms (phase P4).

Writes happen inside the caller's transaction, after :func:`lock` has taken
the session's row (``SELECT … FOR UPDATE``): two requests for the same
session (a retried "next", a "less like this" while a refill runs) queue
behind each other instead of both drawing a batch. Every save bumps
``version``, so a writer that read without the lock can tell it lost.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from sqlalchemy import text

from .model import Pick
from .session import SeedRef, UpNextSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Ended sessions are deleted after this many days (plan §3).
ENDED_KEEP_DAYS = 30
PICK_STATUSES = frozenset({"queued", "played", "skipped", "removed"})

_SESSION_COLS = (
    "id, target, person_id, mode, seed, autoplay, rng_seed, batch_no, state, version, "
    "created_at, updated_at, ended_at"
)


@dataclass
class Stored:
    """A session as read: the engine's object, plus what only the row has."""

    sess: UpNextSession
    version: int
    #: The seed's JSON as stored: SeedRef's keys plus ``name`` (the list's
    #: name as people know it: "Road Trip", an artist, "search results").
    seed_json: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    ended_at: datetime | None = None

    @property
    def list_name(self) -> str | None:
        return self.seed_json.get("name") or None


def _json(v: Any) -> Any:
    if isinstance(v, (dict, list)) or v is None:
        return v
    return json.loads(v)


def _from_row(r: Mapping[str, Any]) -> Stored:
    seed_json = _json(r["seed"]) or {}
    sess = UpNextSession.from_json({
        "id": str(r["id"]), "target": r["target"], "mode": r["mode"], "seed": seed_json,
        "person_id": r["person_id"], "autoplay": r["autoplay"], "rng_seed": r["rng_seed"],
        "batch_no": r["batch_no"], "state": _json(r["state"]) or {},
    })
    return Stored(sess=sess, version=int(r["version"]), seed_json=dict(seed_json),
                  created_at=r["created_at"], updated_at=r["updated_at"], ended_at=r["ended_at"])


def seed_json(seed: SeedRef, name: str | None) -> dict[str, Any]:
    out = seed.to_json()
    out["name"] = name
    return out


async def get(db: "AsyncSession", session_id: str) -> Stored | None:
    try:
        sid = str(uuid.UUID(str(session_id)))
    except ValueError:
        return None
    r = (await db.execute(text(f"SELECT {_SESSION_COLS} FROM up_next_sessions WHERE id = :id"),
                          {"id": sid})).mappings().first()
    return _from_row(r) if r is not None else None


async def lock(db: "AsyncSession", session_id: str) -> Stored | None:
    """The session, its row locked until the caller's transaction ends."""
    try:
        sid = str(uuid.UUID(str(session_id)))
    except ValueError:
        return None
    r = (await db.execute(
        text(f"SELECT {_SESSION_COLS} FROM up_next_sessions WHERE id = :id FOR UPDATE"),
        {"id": sid})).mappings().first()
    return _from_row(r) if r is not None else None


async def active_for(db: "AsyncSession", target: str) -> Stored | None:
    r = (await db.execute(
        text(f"SELECT {_SESSION_COLS} FROM up_next_sessions WHERE target = :t AND ended_at IS NULL"),
        {"t": target})).mappings().first()
    return _from_row(r) if r is not None else None


async def create(db: "AsyncSession", sess: UpNextSession, *, name: str | None) -> Stored:
    """Keep a new session; the target's active one (if any) ends first: a
    new explicit start supersedes it (plan §2.1). Ended sessions past
    :data:`ENDED_KEEP_DAYS` are deleted on the way."""
    await db.execute(
        text("UPDATE up_next_sessions SET ended_at = now(), updated_at = now() "
             "WHERE target = :t AND ended_at IS NULL"),
        {"t": sess.target})
    await db.execute(
        text("DELETE FROM up_next_sessions WHERE ended_at < now() - make_interval(days => :d)"),
        {"d": ENDED_KEEP_DAYS})
    sj = seed_json(sess.seed, name)
    await db.execute(
        text("INSERT INTO up_next_sessions "
             "(id, target, person_id, mode, seed, autoplay, rng_seed, batch_no, state) "
             "VALUES (:id, :target, :person, :mode, CAST(:seed AS jsonb), :autoplay, :rng, :batch, "
             "CAST(:state AS jsonb))"),
        {"id": sess.id, "target": sess.target, "person": sess.person_id, "mode": sess.mode,
         "seed": json.dumps(sj), "autoplay": sess.autoplay, "rng": sess.rng_seed,
         "batch": sess.batch_no, "state": json.dumps(sess.to_state())})
    return Stored(sess=sess, version=0, seed_json=sj)


async def save(db: "AsyncSession", st: Stored) -> bool:
    """Write the session back. False when someone else saved it since it
    was read (optimistic locking: reload and try again)."""
    s = st.sess
    res = await db.execute(
        text("UPDATE up_next_sessions SET target = :target, person_id = :person, autoplay = :autoplay, "
             "batch_no = :batch, state = CAST(:state AS jsonb), version = version + 1, updated_at = now() "
             "WHERE id = :id AND version = :v"),
        {"id": s.id, "target": s.target, "person": s.person_id, "autoplay": s.autoplay,
         "batch": s.batch_no, "state": json.dumps(s.to_state()), "v": st.version})
    if res.rowcount == 1:
        st.version += 1
        return True
    return False


async def max_seq(db: "AsyncSession", session_id: str) -> int:
    return int((await db.execute(
        text("SELECT COALESCE(max(seq), 0) FROM up_next_picks WHERE session_id = :s"),
        {"s": session_id})).scalar_one())


async def add_picks(db: "AsyncSession", session_id: str, batch_no: int, picks: Sequence[Pick],
                    labels: Mapping[int, str] | None = None) -> int:
    """Number ``picks`` on from the session's last seq; returns the first
    seq used. ``labels`` overrides a pick's label by position."""
    first = await max_seq(db, session_id) + 1
    for i, p in enumerate(picks):
        await db.execute(
            text("INSERT INTO up_next_picks "
                 "(session_id, seq, batch_no, track_id, reason_code, reason_label, seed_track_id) "
                 "VALUES (:s, :seq, :b, :t, :code, :label, :seed)"),
            {"s": session_id, "seq": first + i, "b": batch_no, "t": p.track_id,
             "code": p.reason_code, "label": (labels or {}).get(i, p.reason_label),
             "seed": p.seed_track_id})
    return first


_PICKS_SQL = """
SELECT p.seq, p.batch_no, p.track_id, p.reason_code, p.reason_label, p.seed_track_id,
       p.mpd_song_id, p.status, t.title, t.artist, t.album, t.duration_sec, t.artist_norm
FROM up_next_picks p JOIN library_tracks t ON t.id = p.track_id
WHERE p.session_id = :s {where}
ORDER BY p.seq {limit}
"""


async def picks(db: "AsyncSession", session_id: str, *, after_seq: int | None = None,
                statuses: Iterable[str] | None = None, batch_no: int | None = None,
                newest: int | None = None) -> list[dict[str, Any]]:
    where, params = "", {"s": session_id}
    if after_seq is not None:
        where += " AND p.seq > :after"
        params["after"] = int(after_seq)
    if statuses is not None:
        where += " AND p.status = ANY(CAST(:st AS text[]))"
        params["st"] = sorted(statuses)
    if batch_no is not None:
        where += " AND p.batch_no = :b"
        params["b"] = int(batch_no)
    sql = _PICKS_SQL.format(where=where, limit="")
    if newest is not None:
        sql = (f"SELECT * FROM ({_PICKS_SQL.format(where=where, limit='DESC LIMIT :n')}) x "
               "ORDER BY seq")
        params["n"] = int(newest)
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def set_status(db: "AsyncSession", session_id: str, seqs: Iterable[int], status: str) -> list[int]:
    """Set the status of the picks named (only those still queued); returns
    the seqs that changed."""
    if status not in PICK_STATUSES:
        raise ValueError(f"unknown pick status {status!r}")
    want = sorted({int(s) for s in seqs})
    if not want:
        return []
    rows = (await db.execute(
        text("UPDATE up_next_picks SET status = :st WHERE session_id = :s AND status = 'queued' "
             "AND seq = ANY(CAST(:seqs AS int[])) RETURNING seq"),
        {"st": status, "s": session_id, "seqs": want})).all()
    return sorted(int(r[0]) for r in rows)


async def queued_by(db: "AsyncSession", session_id: str, *, artist_norm: str | None = None,
                    track_id: int | None = None) -> list[int]:
    """Seqs of the picks still waiting that are by ``artist_norm`` (or are
    ``track_id``): what "less like this" swaps out."""
    if artist_norm:
        sql = ("SELECT p.seq FROM up_next_picks p JOIN library_tracks t ON t.id = p.track_id "
               "WHERE p.session_id = :s AND p.status = 'queued' AND t.artist_norm = :a ORDER BY p.seq")
        params: dict[str, Any] = {"s": session_id, "a": artist_norm}
    elif track_id is not None:
        sql = ("SELECT seq FROM up_next_picks WHERE session_id = :s AND status = 'queued' "
               "AND track_id = :t ORDER BY seq")
        params = {"s": session_id, "t": int(track_id)}
    else:
        return []
    return [int(r[0]) for r in (await db.execute(text(sql), params)).all()]


async def removed_seqs(db: "AsyncSession", session_id: str) -> list[int]:
    return [int(r[0]) for r in (await db.execute(
        text("SELECT seq FROM up_next_picks WHERE session_id = :s AND status = 'removed' ORDER BY seq"),
        {"s": session_id})).all()]
