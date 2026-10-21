"""The printing tables (V033): printers, room defaults, the job log, limits.

Plain SQL through the core's session, like ``guest_bans``. Every function
opens its own ``session_scope`` unless handed a session.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from domovoi.db.session import session_scope

HISTORY_DAYS = 90

LIMIT_FIELDS = (
    "max_pages_per_job", "max_copies", "max_sheets_per_job",
    "max_jobs_per_hour_per_device", "max_sheets_per_day", "max_scan_pages",
)
LIMIT_DEFAULTS = {
    "max_pages_per_job": 100, "max_copies": 20, "max_sheets_per_job": 200,
    "max_jobs_per_hour_per_device": 20, "max_sheets_per_day": 500, "max_scan_pages": 50,
}
LIMIT_BOUNDS = {
    "max_pages_per_job": (1, 2000), "max_copies": (1, 999), "max_sheets_per_job": (1, 5000),
    "max_jobs_per_hour_per_device": (1, 1000), "max_sheets_per_day": (1, 20000),
    "max_scan_pages": (1, 500),
}

_PRINTER_COLS = (
    "id, name, uuid, make_and_model, location, host, ipp_scheme, ipp_address, ipp_port, ipp_path, "
    "escl_scheme, escl_address, escl_port, escl_path, cert_sha256, escl_cert_sha256, cert_seen_sha256, "
    "capabilities, scan_capabilities, status, status_at, misses, is_default, two_sided_default, "
    "color_default, added_via, operator_allowed, moved_note, moved_at, created_at, updated_at"
)


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    for k in ("capabilities", "scan_capabilities", "status", "options"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


async def list_printers(s=None) -> list[dict[str, Any]]:
    async def go(s):
        rows = (await s.execute(text(f"SELECT {_PRINTER_COLS} FROM printers ORDER BY id"))).all()
        rooms = (await s.execute(text("SELECT room_id, printer_id FROM printer_room_defaults"))).all()
        out = [_row(r) for r in rows]
        by_id = {p["id"]: p for p in out}
        for p in out:
            p["rooms"] = []
        for room_id, pid in rooms:
            if pid in by_id:
                by_id[pid]["rooms"].append(room_id)
        return out
    if s is not None:
        return await go(s)
    async with session_scope() as s:
        return await go(s)


async def get_printer(printer_id: int) -> dict[str, Any] | None:
    for p in await list_printers():
        if p["id"] == printer_id:
            return p
    return None


async def find_by_uuid(uuid: str) -> dict[str, Any] | None:
    if not uuid:
        return None
    for p in await list_printers():
        if (p.get("uuid") or "") == uuid:
            return p
    return None


async def insert_printer(values: dict[str, Any]) -> int:
    """Insert a printer; the first one that prints becomes the default."""
    casts = {k: "CAST(:%s AS JSONB)" % k for k in ("capabilities", "scan_capabilities", "status")}
    async with session_scope() as s:
        has_default = (await s.execute(text("SELECT count(*) FROM printers WHERE is_default"))).scalar()
        params = dict(values)
        if not has_default and params.get("ipp_address"):
            params["is_default"] = True
        for k in casts:
            if k in params and params[k] is not None:
                params[k] = json.dumps(params[k])
        cols = list(params)
        sql = "INSERT INTO printers (%s) VALUES (%s) RETURNING id" % (
            ", ".join(cols), ", ".join(casts.get(c, ":" + c) for c in cols))
        return int((await s.execute(text(sql), params)).scalar_one())


async def update_printer(printer_id: int, values: dict[str, Any]) -> None:
    if not values:
        return
    sets = []
    params: dict[str, Any] = {"id": printer_id}
    for k, v in values.items():
        if k in ("capabilities", "scan_capabilities", "status"):
            sets.append(f"{k} = CAST(:{k} AS JSONB)")
            params[k] = json.dumps(v) if v is not None else None
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    sets.append("updated_at = now()")
    async with session_scope() as s:
        await s.execute(text("UPDATE printers SET %s WHERE id = :id" % ", ".join(sets)), params)


async def set_status(printer_id: int, status: dict[str, Any], *, misses: int) -> None:
    async with session_scope() as s:
        await s.execute(
            text("UPDATE printers SET status = CAST(:st AS JSONB), status_at = now(), misses = :m WHERE id = :id"),
            {"st": json.dumps(status), "m": misses, "id": printer_id},
        )


async def mark_moved(printer_id: int) -> None:
    async with session_scope() as s:
        await s.execute(text("UPDATE printers SET moved_at = now() WHERE id = :id"), {"id": printer_id})


async def set_default(printer_id: int, on: bool) -> None:
    async with session_scope() as s:
        if on:
            await s.execute(text("UPDATE printers SET is_default = false WHERE is_default AND id <> :id"),
                            {"id": printer_id})
            await s.execute(text("UPDATE printers SET is_default = true WHERE id = :id"), {"id": printer_id})
        else:
            await s.execute(text("UPDATE printers SET is_default = false WHERE id = :id"), {"id": printer_id})


async def set_rooms(printer_id: int, rooms: list[str]) -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM printer_room_defaults WHERE printer_id = :id"), {"id": printer_id})
        for room in rooms:
            await s.execute(
                text("INSERT INTO printer_room_defaults (room_id, printer_id) VALUES (:r, :id) "
                     "ON CONFLICT (room_id) DO UPDATE SET printer_id = EXCLUDED.printer_id"),
                {"r": room, "id": printer_id},
            )


async def add_room(printer_id: int, room: str) -> None:
    async with session_scope() as s:
        await s.execute(
            text("INSERT INTO printer_room_defaults (room_id, printer_id) VALUES (:r, :id) "
                 "ON CONFLICT (room_id) DO UPDATE SET printer_id = EXCLUDED.printer_id"),
            {"r": room, "id": printer_id},
        )


async def delete_printer(printer_id: int) -> bool:
    async with session_scope() as s:
        res = await s.execute(text("DELETE FROM printers WHERE id = :id"), {"id": printer_id})
        return bool(res.rowcount)


async def known_rooms() -> list[dict[str, str]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT room_id, room_label FROM satellites ORDER BY room_id"))).all()
    return [{"id": r[0], "label": (r[1] or r[0].replace("_", " ").replace("-", " ").title())} for r in rows]


# ─── Limits ──────────────────────────────────────────────────────────────


async def get_limits() -> dict[str, int]:
    async with session_scope() as s:
        row = (await s.execute(text("SELECT %s FROM print_limits WHERE id = 1" % ", ".join(LIMIT_FIELDS)))).first()
    out = dict(LIMIT_DEFAULTS)
    if row is not None:
        out.update({k: int(v) for k, v in row._mapping.items()})
    out["history_days"] = HISTORY_DAYS
    return out


async def set_limits(values: dict[str, int]) -> dict[str, int]:
    clean = {}
    for k, v in values.items():
        if k not in LIMIT_FIELDS or v is None:
            continue
        lo, hi = LIMIT_BOUNDS[k]
        clean[k] = max(lo, min(hi, int(v)))
    if clean:
        async with session_scope() as s:
            await s.execute(text("INSERT INTO print_limits (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
            await s.execute(
                text("UPDATE print_limits SET %s, updated_at = now() WHERE id = 1"
                     % ", ".join(f"{k} = :{k}" for k in clean)),
                clean,
            )
    return await get_limits()


async def reset_limits() -> None:
    await set_limits(dict(LIMIT_DEFAULTS))


# ─── Print jobs ──────────────────────────────────────────────────────────

_JOB_COLS = (
    "id, printer_id, printer_name, ipp_job_id, title, library_id, path, pages, copies, sheets, options, "
    "state, pages_done, error, who, device_key, source, room_id, speaker, announced, created_at, finished_at"
)


async def insert_job(values: dict[str, Any]) -> int:
    params = dict(values)
    params["options"] = json.dumps(params.get("options") or {})
    cols = list(params)
    sql = "INSERT INTO print_jobs (%s) VALUES (%s) RETURNING id" % (
        ", ".join(cols), ", ".join("CAST(:options AS JSONB)" if c == "options" else ":" + c for c in cols))
    async with session_scope() as s:
        return int((await s.execute(text(sql), params)).scalar_one())


async def update_job(job_id: int, **values: Any) -> None:
    if not values:
        return
    params = dict(values, id=job_id)
    sets = []
    for k in values:
        if k == "finished_at" and values[k] == "now":
            sets.append("finished_at = now()")
            params.pop(k)
        else:
            sets.append(f"{k} = :{k}")
    async with session_scope() as s:
        await s.execute(text("UPDATE print_jobs SET %s WHERE id = :id" % ", ".join(sets)), params)


async def get_job(job_id: int) -> dict[str, Any] | None:
    async with session_scope() as s:
        row = (await s.execute(text(f"SELECT {_JOB_COLS} FROM print_jobs WHERE id = :id"), {"id": job_id})).first()
    return _row(row) if row is not None else None


async def list_jobs(*, printer_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
    where = "WHERE printer_id = :pid" if printer_id is not None else ""
    async with session_scope() as s:
        rows = (await s.execute(
            text(f"SELECT {_JOB_COLS} FROM print_jobs {where} ORDER BY created_at DESC, id DESC LIMIT :lim"),
            {"pid": printer_id, "lim": max(1, min(limit, 200))},
        )).all()
    return [_row(r) for r in rows]


async def active_jobs() -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(
            text(f"SELECT {_JOB_COLS} FROM print_jobs WHERE state IN ('queued', 'printing') ORDER BY id")
        )).all()
    return [_row(r) for r in rows]


async def last_job_for_room(room_id: str | None) -> dict[str, Any] | None:
    async with session_scope() as s:
        row = (await s.execute(
            text(f"SELECT {_JOB_COLS} FROM print_jobs WHERE room_id IS NOT DISTINCT FROM :r "
                 "AND state IN ('queued', 'printing') ORDER BY id DESC LIMIT 1"),
            {"r": room_id},
        )).first()
    return _row(row) if row is not None else None


async def usage(device_key: str) -> tuple[int, int]:
    """(jobs this device sent in the last hour, sheets the household
    printed in the last 24 hours)."""
    async with session_scope() as s:
        jobs = (await s.execute(text(
            "SELECT count(*) FROM print_jobs WHERE device_key = :d AND created_at > now() - interval '1 hour' "
            "AND source <> 'test'"), {"d": device_key})).scalar() or 0
        sheets = (await s.execute(text(
            "SELECT coalesce(sum(sheets), 0) FROM print_jobs WHERE created_at > now() - interval '1 day' "
            "AND state <> 'failed' AND state <> 'canceled'"))).scalar() or 0
    return int(jobs), int(sheets)


async def clear_history() -> int:
    async with session_scope() as s:
        a = await s.execute(text("DELETE FROM print_jobs WHERE state NOT IN ('queued', 'printing')"))
        b = await s.execute(text("DELETE FROM scan_jobs WHERE state NOT IN ('scanning', 'waiting', 'saving')"))
    return int(a.rowcount or 0) + int(b.rowcount or 0)


async def prune_history() -> int:
    async with session_scope() as s:
        a = await s.execute(text(
            f"DELETE FROM print_jobs WHERE created_at < now() - interval '{HISTORY_DAYS} days' "
            "AND state NOT IN ('queued', 'printing')"))
        b = await s.execute(text(
            f"DELETE FROM scan_jobs WHERE created_at < now() - interval '{HISTORY_DAYS} days'"))
    return int(a.rowcount or 0) + int(b.rowcount or 0)


# ─── Scan jobs ───────────────────────────────────────────────────────────


async def upsert_scan(values: dict[str, Any]) -> None:
    cols = list(values)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
    sql = "INSERT INTO scan_jobs (%s) VALUES (%s) ON CONFLICT (id) DO UPDATE SET %s" % (
        ", ".join(cols), ", ".join(":" + c for c in cols), updates)
    async with session_scope() as s:
        await s.execute(text(sql), values)


async def list_scans(*, printer_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
    where = "WHERE printer_id = :pid" if printer_id is not None else ""
    async with session_scope() as s:
        rows = (await s.execute(
            text("SELECT id, printer_id, scanner_name, state, source_used, pages, name, path, incomplete, "
                 f"error, who, source, room_id, created_at, finished_at FROM scan_jobs {where} "
                 "ORDER BY created_at DESC LIMIT :lim"),
            {"pid": printer_id, "lim": max(1, min(limit, 200))},
        )).all()
    return [_row(r) for r in rows]


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt is not None else None
