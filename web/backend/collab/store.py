"""Postgres storage for co-editing (V042): the path → id mapping, the
shared state, the edits since the last compaction, and version history.

Plain SQL through the web process's shared pool (``web.backend.db``), like
the rest of the web backend. Every function opens its own short session.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import text

from web.backend.db import session_scope


@dataclass
class DocRow:
    doc_id: str
    rel_path: str
    epoch: int
    state: Optional[bytes]
    seeded: bool
    file_sha256: Optional[str]
    file_size: Optional[int]
    file_mtime_ns: Optional[int]
    rev: int
    saved_rev: int


_DOC_COLUMNS = (
    "doc_id, rel_path, epoch, state, seeded, file_sha256, file_size, "
    "file_mtime_ns, rev, saved_rev"
)


def _row(r: Any) -> DocRow:
    return DocRow(
        doc_id=r.doc_id, rel_path=r.rel_path, epoch=r.epoch,
        state=bytes(r.state) if r.state is not None else None,
        seeded=r.seeded, file_sha256=r.file_sha256, file_size=r.file_size,
        file_mtime_ns=r.file_mtime_ns, rev=r.rev, saved_rev=r.saved_rev,
    )


def new_doc_id() -> str:
    # URL-safe, unguessable: a doc id is not a credential (the socket checks
    # the caller's tier and the path), but nothing is gained by making the
    # ids of other people's documents enumerable.
    return "d" + secrets.token_urlsafe(15)


async def get_or_create(rel_path: str) -> DocRow:
    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO collab_docs (doc_id, rel_path) VALUES (:id, :p) "
                "ON CONFLICT (rel_path) DO NOTHING"
            ),
            {"id": new_doc_id(), "p": rel_path},
        )
        r = (
            await s.execute(
                text(f"SELECT {_DOC_COLUMNS} FROM collab_docs WHERE rel_path = :p"),
                {"p": rel_path},
            )
        ).first()
    return _row(r)


async def get(doc_id: str) -> Optional[DocRow]:
    async with session_scope() as s:
        r = (
            await s.execute(
                text(f"SELECT {_DOC_COLUMNS} FROM collab_docs WHERE doc_id = :id"),
                {"id": doc_id},
            )
        ).first()
    return _row(r) if r is not None else None


async def get_by_path(rel_path: str) -> Optional[DocRow]:
    async with session_scope() as s:
        r = (
            await s.execute(
                text(f"SELECT {_DOC_COLUMNS} FROM collab_docs WHERE rel_path = :p"),
                {"p": rel_path},
            )
        ).first()
    return _row(r) if r is not None else None


async def pending_updates(doc_id: str, epoch: int) -> list[bytes]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT data FROM collab_updates WHERE doc_id = :id AND epoch = :e "
                    "ORDER BY id"
                ),
                {"id": doc_id, "e": epoch},
            )
        ).all()
    return [bytes(r.data) for r in rows]


async def append_update(doc_id: str, epoch: int, data: bytes, rev: int) -> None:
    async with session_scope() as s:
        await s.execute(
            text("INSERT INTO collab_updates (doc_id, epoch, data) VALUES (:id, :e, :d)"),
            {"id": doc_id, "e": epoch, "d": data},
        )
        await s.execute(
            text(
                "UPDATE collab_docs SET rev = GREATEST(rev, :rev), updated_at = now() "
                "WHERE doc_id = :id AND epoch = :e"
            ),
            {"id": doc_id, "e": epoch, "rev": rev},
        )


async def compact(doc_id: str, epoch: int, state: bytes, rev: int) -> None:
    """The whole state replaces the stored one; the update rows it already
    contains go. One transaction, so a crash leaves either the old state with
    its rows or the new state without them."""
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE collab_docs SET state = :st, rev = GREATEST(rev, :rev), "
                "updated_at = now() WHERE doc_id = :id AND epoch = :e"
            ),
            {"id": doc_id, "e": epoch, "st": state, "rev": rev},
        )
        await s.execute(
            text("DELETE FROM collab_updates WHERE doc_id = :id"),
            {"id": doc_id},
        )


async def mark_seeded(
    doc_id: str, epoch: int, fingerprint: tuple[str, int, int] | None, rev: int
) -> None:
    sha, size, mtime_ns = fingerprint or (None, None, None)
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE collab_docs SET seeded = TRUE, file_sha256 = :sha, file_size = :sz, "
                "file_mtime_ns = :mt, rev = GREATEST(rev, :rev), saved_rev = GREATEST(saved_rev, :rev), "
                "updated_at = now() WHERE doc_id = :id AND epoch = :e"
            ),
            {"id": doc_id, "e": epoch, "sha": sha, "sz": size, "mt": mtime_ns, "rev": rev},
        )


async def record_save(
    doc_id: str, epoch: int, fingerprint: tuple[str, int, int], saved_rev: int
) -> None:
    sha, size, mtime_ns = fingerprint
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE collab_docs SET file_sha256 = :sha, file_size = :sz, file_mtime_ns = :mt, "
                "saved_rev = GREATEST(saved_rev, :rev), updated_at = now() "
                "WHERE doc_id = :id AND epoch = :e"
            ),
            {"id": doc_id, "e": epoch, "sha": sha, "sz": size, "mt": mtime_ns, "rev": saved_rev},
        )


async def rebuild(doc_id: str) -> int:
    """Throw the shared state away so the document starts again from its
    file, under a new epoch. Returns the new epoch. Version history stays."""
    async with session_scope() as s:
        r = (
            await s.execute(
                text(
                    "UPDATE collab_docs SET epoch = epoch + 1, state = NULL, seeded = FALSE, "
                    "file_sha256 = NULL, file_size = NULL, file_mtime_ns = NULL, rev = 0, "
                    "saved_rev = 0, updated_at = now() WHERE doc_id = :id RETURNING epoch"
                ),
                {"id": doc_id},
            )
        ).first()
        await s.execute(text("DELETE FROM collab_updates WHERE doc_id = :id"), {"id": doc_id})
    return int(r.epoch) if r is not None else 0


async def forget(rel_paths: Iterable[str]) -> list[str]:
    """Delete the co-editing rows (state, edits, versions) of these paths and
    of anything under them when a path is a folder. Returns the doc ids."""
    gone: list[str] = []
    async with session_scope() as s:
        for rel in rel_paths:
            rows = (
                await s.execute(
                    text(
                        "DELETE FROM collab_docs WHERE rel_path = :p OR rel_path LIKE :prefix "
                        "ESCAPE '\\' RETURNING doc_id"
                    ),
                    {"p": rel, "prefix": _like_prefix(rel)},
                )
            ).all()
            gone.extend(r.doc_id for r in rows)
    return gone


async def move(old: str, new: str) -> None:
    """A file (or a folder of them) moved inside Documents: the mapping
    follows it, so its history does too."""
    async with session_scope() as s:
        await s.execute(
            text("UPDATE collab_docs SET rel_path = :n, updated_at = now() WHERE rel_path = :o"),
            {"o": old, "n": new},
        )
        await s.execute(
            text(
                "UPDATE collab_docs SET rel_path = :n || substr(rel_path, :cut), "
                "updated_at = now() WHERE rel_path LIKE :prefix ESCAPE '\\'"
            ),
            {"n": new, "cut": len(old) + 1, "prefix": _like_prefix(old)},
        )


def _like_prefix(rel: str) -> str:
    esc = rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc.rstrip("/") + "/%"


# ─── Version history ─────────────────────────────────────────────────────


@dataclass
class SnapshotRow:
    id: int
    doc_id: str
    epoch: int
    kind: str
    editors: list[str]
    size: int
    created_at: datetime
    state: Optional[bytes] = None


async def add_snapshot(
    doc_id: str, epoch: int, kind: str, state: bytes, editors: Iterable[str]
) -> int:
    names = sorted({e for e in editors if e})
    async with session_scope() as s:
        r = (
            await s.execute(
                text(
                    "INSERT INTO collab_snapshots (doc_id, epoch, kind, state, editors, size) "
                    "VALUES (:id, :e, :k, :st, CAST(:ed AS jsonb), :sz) RETURNING id"
                ),
                {"id": doc_id, "e": epoch, "k": kind, "st": state,
                 "ed": json.dumps(names), "sz": len(state)},
            )
        ).first()
    return int(r.id)


def _snap(r: Any, with_state: bool) -> SnapshotRow:
    editors = r.editors if isinstance(r.editors, list) else json.loads(r.editors or "[]")
    return SnapshotRow(
        id=r.id, doc_id=r.doc_id, epoch=r.epoch, kind=r.kind, editors=list(editors),
        size=r.size, created_at=r.created_at,
        state=bytes(r.state) if with_state and r.state is not None else None,
    )


async def list_snapshots(doc_id: str, limit: int = 500) -> list[SnapshotRow]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, doc_id, epoch, kind, editors, size, created_at "
                    "FROM collab_snapshots WHERE doc_id = :id "
                    "ORDER BY created_at DESC, id DESC LIMIT :lim"
                ),
                {"id": doc_id, "lim": limit},
            )
        ).all()
    return [_snap(r, False) for r in rows]


async def get_snapshot(doc_id: str, snapshot_id: int) -> Optional[SnapshotRow]:
    async with session_scope() as s:
        r = (
            await s.execute(
                text(
                    "SELECT id, doc_id, epoch, kind, editors, size, created_at, state "
                    "FROM collab_snapshots WHERE doc_id = :id AND id = :sid"
                ),
                {"id": doc_id, "sid": snapshot_id},
            )
        ).first()
    return _snap(r, True) if r is not None else None


async def latest_snapshot_of_epoch(doc_id: str, epoch: int) -> Optional[SnapshotRow]:
    async with session_scope() as s:
        r = (
            await s.execute(
                text(
                    "SELECT id, doc_id, epoch, kind, editors, size, created_at, state "
                    "FROM collab_snapshots WHERE doc_id = :id AND epoch = :e "
                    "AND kind <> 'offline_copy' ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"id": doc_id, "e": epoch},
            )
        ).first()
    return _snap(r, True) if r is not None else None


# Retention (owner brief: "keep the last N, or thin out older snapshots by
# time"). Both, in this order:
#   * everything from the last two hours is kept;
#   * older than that, one version per hour for a week, then one per day
#     for 90 days, then one per week — the NEWEST in each window stays and
#     takes the names of who edited in the ones it replaces;
#   * an "offline copy" (a phone's offline edits that could not be merged
#     because the file was changed outside the editor meanwhile) is never
#     thinned in its first 30 days;
#   * at most MAX_SNAPSHOTS versions and MAX_SNAPSHOT_BYTES per document:
#     past that, the oldest go.
KEEP_ALL_FOR = timedelta(hours=2)
HOURLY_FOR = timedelta(days=7)
DAILY_FOR = timedelta(days=90)
OFFLINE_COPY_KEEP = timedelta(days=30)
MAX_SNAPSHOTS = 200
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


def _bucket(age: timedelta, at: datetime) -> Optional[tuple[str, int]]:
    if age <= KEEP_ALL_FOR:
        return None
    epoch_s = int(at.timestamp())
    if age <= HOURLY_FOR:
        return ("h", epoch_s // 3600)
    if age <= DAILY_FOR:
        return ("d", epoch_s // 86400)
    return ("w", epoch_s // (7 * 86400))


def plan_thinning(
    snaps: list[SnapshotRow], now: datetime
) -> tuple[set[int], dict[int, set[str]]]:
    """Which snapshot ids to drop, and the editor names each survivor
    inherits. ``snaps`` newest first. Pure, so the tests can drive it."""
    drop: set[int] = set()
    inherit: dict[int, set[str]] = {}
    keeper_of: dict[tuple[str, int], int] = {}
    for sn in snaps:
        age = now - sn.created_at
        if sn.kind == "offline_copy" and age <= OFFLINE_COPY_KEEP:
            continue
        b = _bucket(age, sn.created_at)
        if b is None:
            continue
        keeper = keeper_of.get(b)
        if keeper is None:
            keeper_of[b] = sn.id
            continue
        drop.add(sn.id)
        inherit.setdefault(keeper, set()).update(sn.editors)
    kept = [sn for sn in snaps if sn.id not in drop]
    total = 0
    for i, sn in enumerate(kept):
        total += sn.size
        if i >= MAX_SNAPSHOTS or total > MAX_SNAPSHOT_BYTES:
            drop.add(sn.id)
    for sid in list(inherit):
        if sid in drop:
            del inherit[sid]
    return drop, inherit


async def thin_snapshots(doc_id: str, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    snaps = await list_snapshots(doc_id, limit=100000)
    drop, inherit = plan_thinning(snaps, now)
    if not drop:
        return 0
    by_id = {sn.id: sn for sn in snaps}
    async with session_scope() as s:
        for sid, names in inherit.items():
            merged = sorted(set(by_id[sid].editors) | names)
            await s.execute(
                text("UPDATE collab_snapshots SET editors = CAST(:ed AS jsonb) WHERE id = :sid"),
                {"sid": sid, "ed": json.dumps(merged)},
            )
        await s.execute(
            text("DELETE FROM collab_snapshots WHERE doc_id = :id AND id = ANY(:ids)"),
            {"id": doc_id, "ids": sorted(drop)},
        )
    return len(drop)
