"""Timer fire history (V017) — every read and write the web process makes.

When a timer or a reminder goes off, the core moves its ``timers`` row
into ``timer_fires`` (same transaction) and records, per room, whether the
announcement was spoken there (``timer_fire_deliveries``). A row in
``timer_own_only_rooms`` is the per-satellite setting "Only reminders for
this device" turned ON: that room then announces only what was set on it.
The core writes the ledger; this process reads it and writes the flag.

All of the V017 SQL the web process runs lives here, so the routes
(web/backend/api/satellites.py) and the realtime helper
(web/backend/realtime.py) stay DB-shape-free and their tests can replace
these functions with fakes.

A missing V017 (Flyway not run yet) is never an error here: the reads
answer ``None`` (fires) or ``{}`` (the flag), and the one write raises
:class:`LedgerMissing` so its route can answer 503. The probe is
``to_regclass`` — it never aborts the caller's transaction, so a route
can go on using its session after asking.

What is never read here: ``base_text`` and ``spoken_text``, the words the
core spoke. They stay in the database (see docs/SECURITY_PRIVACY.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

# Delivery outcomes (V017 CHECK constraint).
HEARD_OUTCOMES = ("spoken", "interrupted")
OPEN_OUTCOMES = ("pending", "sending")


class LedgerMissing(RuntimeError):
    """V017's tables are not in this database (Flyway has not run)."""


_LEDGER_TABLES = text(
    "SELECT to_regclass('public.timer_fires') IS NOT NULL"
    " AND to_regclass('public.timer_fire_deliveries') IS NOT NULL"
)
_FLAG_TABLE = text("SELECT to_regclass('public.timer_own_only_rooms') IS NOT NULL")


async def _ready(s: Any, probe: Any) -> bool:
    return bool((await s.execute(probe)).scalar())


# ─── the ledger ───────────────────────────────────────────────────────────


async def recent_fires(
    s: Any,
    *,
    since_id: int | None = None,
    origin_room_id: str | None = None,
    timer_id: int | None = None,
    limit: int = 50,
    window_sec: float | None = None,
) -> list[dict[str, Any]] | None:
    """Fires as :class:`web.backend.schemas.TimerFire` dicts, UNMASKED
    (callers apply :func:`mask_fire` where the caller's credential asks for
    it), with ``deliveries`` / ``heard_in`` / ``summary`` filled in.

    * ``since_id`` — only ``id > since_id``, oldest first (a client
      catching up); without it, newest first.
    * ``origin_room_id`` — the room the fire was SET in.
    * ``window_sec`` — only fires this recent (``fired_at``, database clock).

    ``None`` when V017 is missing."""
    if not await _ready(s, _LEDGER_TABLES):
        return None
    where: list[str] = []
    params: dict[str, Any] = {"limit": max(1, int(limit))}
    if since_id is not None:
        where.append("id > :since_id")
        params["since_id"] = int(since_id)
    if origin_room_id is not None:
        where.append("origin_room_id = :origin_room_id")
        params["origin_room_id"] = origin_room_id
    if timer_id is not None:
        where.append("timer_id = :timer_id")
        params["timer_id"] = int(timer_id)
    if window_sec is not None:
        where.append("fired_at >= now() - make_interval(secs => :window_sec)")
        params["window_sec"] = float(window_sec)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = "id ASC" if since_id is not None else "fired_at DESC, id DESC"
    rows = (
        await s.execute(
            text(
                f"""
                SELECT id, timer_id, kind, label, message, origin_room_id,
                       created_at, due_at, fired_at, settled_at, acked_at, acked_by
                FROM timer_fires
                {clause}
                ORDER BY {order}
                LIMIT :limit
                """
            ),
            params,
        )
    ).all()
    if not rows:
        return []
    ids = [int(r[0]) for r in rows]
    by_fire: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    deliveries = (
        await s.execute(
            text(
                """
                SELECT fire_id, room_id, is_origin, outcome, detail, finished_at
                FROM timer_fire_deliveries
                WHERE fire_id = ANY(:ids)
                ORDER BY fire_id, is_origin DESC, room_id ASC
                """
            ),
            {"ids": ids},
        )
    ).all()
    for d in deliveries:
        by_fire.setdefault(int(d[0]), []).append(
            {
                "room_id": d[1],
                "is_origin": bool(d[2]),
                "outcome": d[3],
                "detail": d[4],
                "finished_at": d[5],
            }
        )
    return [
        fire_from_row(
            {
                "id": int(r[0]),
                "timer_id": int(r[1]),
                "kind": r[2],
                "label": r[3],
                "message": r[4],
                "room_id": r[5],
                "created_at": r[6],
                "due_at": r[7],
                "fired_at": r[8],
                "settled_at": r[9],
                "acked_at": r[10],
                "acked_by": r[11],
            },
            by_fire.get(int(r[0]), []),
        )
        for r in rows
    ]


def fire_from_row(row: dict[str, Any], deliveries: list[dict[str, Any]]) -> dict[str, Any]:
    """One Fire (docs/API_REFERENCE.md, "timer fires"): the ledger row, its
    deliveries (origin first, then rooms A→Z), ``heard_in`` in the same
    order, and the ``summary`` line the clients show verbatim.

    ``kind`` follows the ledger's own rule — a reminder iff ``message IS
    NOT NULL`` (an empty message is still a reminder) — so a row written
    by any core version reads the same way here."""
    ordered = sorted(deliveries, key=lambda d: (not d.get("is_origin"), d.get("room_id") or ""))
    is_reminder = row.get("message") is not None
    fire = {
        "id": row["id"],
        "timer_id": row["timer_id"],
        "kind": "reminder" if is_reminder else "timer",
        "is_reminder": is_reminder,
        "label": row.get("label"),
        "message": row.get("message"),
        "masked": False,
        "room_id": row.get("room_id"),
        "created_at": row.get("created_at"),
        "due_at": row.get("due_at"),
        "fired_at": row.get("fired_at"),
        "settled_at": row.get("settled_at"),
        "acked_at": row.get("acked_at"),
        "acked_by": row.get("acked_by"),
        "heard_in": [d["room_id"] for d in ordered if d.get("outcome") in HEARD_OUTCOMES],
        "deliveries": ordered,
    }
    fire["summary"] = fire_summary(fire)
    return fire


def fire_summary(fire: dict[str, Any]) -> str:
    """Where it was heard, in plain words. Room names are raw room ids and
    no speech is in it, so it is never masked.

    * ``heard in garage, kitchen`` (+ `` · still announcing`` while a room
      is still waiting its turn)
    * ``announcing…`` — nobody has heard it yet, somebody is about to
    * ``no satellite was online`` — nothing to announce it in
    * ``not heard in any room`` (+ `` (garage offline)`` when the origin
      room never came back)
    * any of those + `` · stopped in kitchen`` once acknowledged
    """
    deliveries = fire.get("deliveries") or []
    heard = [d.get("room_id") for d in deliveries if d.get("outcome") in HEARD_OUTCOMES]
    pending = [d.get("room_id") for d in deliveries if d.get("outcome") in OPEN_OUTCOMES]
    if heard:
        s = "heard in " + ", ".join(heard) + (" · still announcing" if pending else "")
    elif pending:
        s = "announcing…"
    elif not deliveries:
        s = "no satellite was online"
    else:
        origin = next((d for d in deliveries if d.get("is_origin")), None)
        s = "not heard in any room"
        if origin is not None and origin.get("outcome") == "offline":
            s += f" ({origin.get('room_id')} offline)"
    if fire.get("acked_by"):
        s += f" · stopped in {fire['acked_by']}"
    return s


def mask_fire(fire: dict[str, Any]) -> dict[str, Any]:
    """Rule M1 for a caller without a household credential: a reminder's
    words (``message``, and ``label``, which holds the same words) are
    held back and ``masked`` says so. Countdown times, rooms, kinds and
    delivery outcomes stay. A plain timer's label ("pasta") is not
    masked. Returns a copy."""
    out = dict(fire)
    if out.get("is_reminder"):
        out.update(message=None, label=None, masked=True)
    return out


# ─── the per-room flag ────────────────────────────────────────────────────


async def own_only_rooms(s: Any) -> dict[str, datetime]:
    """``{room_id: enabled_at}`` for every room whose "Only reminders for
    this device" is ON. ``{}`` when V017 is missing (every room is OFF,
    the default)."""
    if not await _ready(s, _FLAG_TABLE):
        return {}
    rows = (
        await s.execute(text("SELECT room_id, enabled_at FROM timer_own_only_rooms"))
    ).all()
    return {r[0]: r[1] for r in rows}


async def set_own_only(s: Any, room_id: str, on: bool) -> datetime | None:
    """Turn the flag ON (idempotent: a room already on keeps its original
    ``enabled_at``, which is returned) or OFF (returns ``None``). Raises
    :class:`LedgerMissing` when V017 is missing."""
    if not await _ready(s, _FLAG_TABLE):
        raise LedgerMissing("timer_own_only_rooms is missing")
    if not on:
        await s.execute(
            text("DELETE FROM timer_own_only_rooms WHERE room_id = :r"), {"r": room_id}
        )
        return None
    await s.execute(
        text(
            "INSERT INTO timer_own_only_rooms (room_id) VALUES (:r) "
            "ON CONFLICT (room_id) DO NOTHING"
        ),
        {"r": room_id},
    )
    return (
        await s.execute(
            text("SELECT enabled_at FROM timer_own_only_rooms WHERE room_id = :r"),
            {"r": room_id},
        )
    ).scalar_one()
