"""The guard's database half: incidents (V024 ``container_incidents``) and
baselines (``container_guard_baselines``).

Every function takes an open ``AsyncSession`` so both processes use one
code path: the core's guard writes through ``domovoi.db.session``, the web
process reads (and the freeze check runs) through ``web.backend.db``.
Writers queue a ``container_incidents_changed`` NOTIFY in the same
transaction, which the web's realtime layer turns into the
``containers.changed`` event the banner listens to.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.container_guard.model import TAMPER, Finding

NOTIFY_CHANNEL = "container_incidents_changed"

# A failure that comes back this soon after resolving reopens its own row
# (no new incident, no new notification): a flapping container is one
# problem (L6).
REOPEN_WINDOW_SEC = 15 * 60
# Resolved incidents are kept this long, then pruned (open ones and cleared
# findings still accepted are kept whatever their age).
RETENTION_DAYS = 90

_COLUMNS = (
    "id, incident_key, class, kind, severity, container, owner, label, summary, "
    "expected, found, opened_at, last_seen_at, resolved_at, resolution, "
    "acknowledged_at, acknowledged_by, cleared_at, cleared_by, clear_note, accepted"
)


class IncidentError(ValueError):
    """An acknowledge or clear that doesn't apply (wrong class, already
    resolved, no such incident). ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if value is not None else {}


def _row(r: Any) -> dict[str, Any]:
    m = r._mapping
    out = {k: m[k] for k in m.keys()}
    out["class"] = out.pop("class")
    out["expected"] = _json(out.get("expected"))
    out["found"] = _json(out.get("found"))
    return out


async def notify(s: AsyncSession, reason: str) -> None:
    await s.execute(text("SELECT pg_notify(:c, :r)"), {"c": NOTIFY_CHANNEL, "r": reason[:120]})


async def open_incidents(s: AsyncSession) -> list[dict[str, Any]]:
    rows = await s.execute(
        text(f"SELECT {_COLUMNS} FROM container_incidents WHERE resolved_at IS NULL ORDER BY id")
    )
    return [_row(r) for r in rows]


async def recent_incidents(
    s: AsyncSession, *, hours: int = 24, limit: int = 50
) -> list[dict[str, Any]]:
    """Every open incident plus those resolved in the last ``hours``,
    newest first."""
    rows = await s.execute(
        text(
            f"SELECT {_COLUMNS} FROM container_incidents "
            "WHERE resolved_at IS NULL OR resolved_at > now() - make_interval(hours => :h) "
            "ORDER BY (resolved_at IS NULL) DESC, opened_at DESC, id DESC LIMIT :n"
        ),
        {"h": int(hours), "n": int(limit)},
    )
    return [_row(r) for r in rows]


async def get_incident(s: AsyncSession, incident_id: int) -> dict[str, Any] | None:
    row = (
        await s.execute(
            text(f"SELECT {_COLUMNS} FROM container_incidents WHERE id = :i"),
            {"i": int(incident_id)},
        )
    ).first()
    return _row(row) if row is not None else None


async def frozen_by(s: AsyncSession) -> list[dict[str, Any]]:
    """The open tamper incidents: while there is one, the risky operations
    are frozen (domovoi/container_guard/freeze.py)."""
    rows = await s.execute(
        text(
            f"SELECT {_COLUMNS} FROM container_incidents "
            "WHERE class = 'tamper' AND resolved_at IS NULL ORDER BY id"
        )
    )
    return [_row(r) for r in rows]


def _params(f: Finding) -> dict[str, Any]:
    return {
        "k": f.key, "c": f.cls, "kind": f.kind, "sev": f.severity,
        "ctr": f.container, "o": f.owner, "l": f.label[:200], "s": f.summary[:500],
        "e": json.dumps(f.expected, default=str), "f": json.dumps(f.found, default=str),
    }


async def open_incident(s: AsyncSession, f: Finding) -> tuple[int, bool]:
    """``(id, created)``: a new open incident for ``f.key``, or the one
    already open for it. A FAILURE whose same problem recovered less than
    :data:`REOPEN_WINDOW_SEC` ago reopens that row instead (``created`` is
    False then: one flapping container, one incident, one notification)."""
    if f.cls != TAMPER:
        reopened = (
            await s.execute(
                text(
                    "UPDATE container_incidents SET resolved_at = NULL, resolution = NULL, "
                    "last_seen_at = now(), kind = :kind, severity = :sev, summary = :s, "
                    "found = CAST(:f AS JSONB) "
                    "WHERE id = (SELECT id FROM container_incidents WHERE incident_key = :k "
                    "AND resolution IN ('recovered', 'no_longer_expected') "
                    "AND resolved_at > now() - make_interval(secs => :w) "
                    "AND NOT EXISTS (SELECT 1 FROM container_incidents o "
                    "WHERE o.incident_key = :k AND o.resolved_at IS NULL) "
                    "ORDER BY resolved_at DESC LIMIT 1) RETURNING id"
                ),
                {**_params(f), "w": REOPEN_WINDOW_SEC},
            )
        ).first()
        if reopened is not None:
            await notify(s, f"reopened:{reopened[0]}")
            return int(reopened[0]), False
    row = (
        await s.execute(
            text(
                "INSERT INTO container_incidents "
                "(incident_key, class, kind, severity, container, owner, label, summary, expected, found) "
                "VALUES (:k, :c, :kind, :sev, :ctr, :o, :l, :s, CAST(:e AS JSONB), CAST(:f AS JSONB)) "
                "ON CONFLICT (incident_key) WHERE resolved_at IS NULL DO NOTHING RETURNING id"
            ),
            _params(f),
        )
    ).first()
    if row is not None:
        await notify(s, f"opened:{row[0]}")
        return int(row[0]), True
    existing = (
        await s.execute(
            text("SELECT id FROM container_incidents WHERE incident_key = :k AND resolved_at IS NULL"),
            {"k": f.key},
        )
    ).first()
    return int(existing[0]), False


async def touch_incident(
    s: AsyncSession, incident_id: int, f: Finding, *, changed: bool = False
) -> None:
    """The problem is still there: refresh what was found and when.
    ``changed``: it is a different problem for the same container now
    (stopped, then removed): it takes the new words, and an acknowledgement
    of the old problem no longer hides it."""
    if changed:
        await s.execute(
            text(
                "UPDATE container_incidents SET last_seen_at = now(), kind = :kind, "
                "severity = :sev, summary = :s, found = CAST(:f AS JSONB), "
                "acknowledged_at = NULL, acknowledged_by = NULL WHERE id = :i"
            ),
            {"i": int(incident_id), "kind": f.kind, "sev": f.severity, "s": f.summary[:500],
             "f": json.dumps(f.found, default=str)},
        )
        await notify(s, f"changed:{incident_id}")
        return
    await s.execute(
        text(
            "UPDATE container_incidents SET last_seen_at = now(), found = CAST(:f AS JSONB) "
            "WHERE id = :i"
        ),
        {"i": int(incident_id), "f": json.dumps(f.found, default=str)},
    )


async def resolve_incident(s: AsyncSession, incident_id: int, resolution: str) -> bool:
    result = await s.execute(
        text(
            "UPDATE container_incidents SET resolved_at = now(), resolution = :r "
            "WHERE id = :i AND resolved_at IS NULL"
        ),
        {"i": int(incident_id), "r": resolution},
    )
    if result.rowcount:
        await notify(s, f"resolved:{incident_id}")
        return True
    return False


async def acknowledge(s: AsyncSession, incident_id: int, by: str) -> dict[str, Any]:
    """Hide an open FAILURE from the banner until something new happens.
    A tamper incident can't be acknowledged — it is cleared after review."""
    inc = await get_incident(s, incident_id)
    if inc is None:
        raise IncidentError(f"no incident #{incident_id}", status=404)
    if inc["class"] == TAMPER:
        raise IncidentError(
            f"incident #{incident_id} is a tamper incident: review it, then clear it"
        )
    if inc["resolved_at"] is not None:
        raise IncidentError(f"incident #{incident_id} is already resolved")
    await s.execute(
        text(
            "UPDATE container_incidents SET acknowledged_at = now(), acknowledged_by = :b "
            "WHERE id = :i"
        ),
        {"i": int(incident_id), "b": by[:200]},
    )
    await notify(s, f"acknowledged:{incident_id}")
    return await get_incident(s, incident_id)  # type: ignore[return-value]


async def clear(s: AsyncSession, incident_id: int, by: str, note: str | None) -> dict[str, Any]:
    """Close an open TAMPER incident after an admin's review (recorded).
    The finding stays ACCEPTED — it doesn't reopen while it persists, a box
    switched off overnight included — until the guard itself sees it gone
    (:func:`release_acceptance`); anything different opens anew."""
    inc = await get_incident(s, incident_id)
    if inc is None:
        raise IncidentError(f"no incident #{incident_id}", status=404)
    if inc["class"] != TAMPER:
        raise IncidentError(
            f"incident #{incident_id} is a failure: it resolves by itself when the "
            f"container recovers (acknowledge it to hide the banner)"
        )
    if inc["resolved_at"] is not None:
        raise IncidentError(f"incident #{incident_id} is already cleared")
    await s.execute(
        text(
            "UPDATE container_incidents SET resolved_at = now(), resolution = 'cleared', "
            "cleared_at = now(), cleared_by = :b, clear_note = :n, accepted = true WHERE id = :i"
        ),
        {"i": int(incident_id), "b": by[:200], "n": (note or "").strip()[:500] or None},
    )
    await notify(s, f"cleared:{incident_id}")
    return await get_incident(s, incident_id)  # type: ignore[return-value]


async def accepted(s: AsyncSession) -> list[tuple[str, str | None]]:
    """``[(incident_key, container)]``: the cleared findings still accepted."""
    rows = await s.execute(
        text("SELECT DISTINCT incident_key, container FROM container_incidents WHERE accepted")
    )
    return [(r[0], r[1]) for r in rows]


async def touch_accepted(s: AsyncSession, keys: list[str]) -> None:
    """The accepted finding is still there (for the record's last-seen)."""
    if keys:
        await s.execute(
            text(
                "UPDATE container_incidents SET last_seen_at = now() "
                "WHERE incident_key = ANY(:ks) AND accepted"
            ),
            {"ks": list(keys)},
        )


async def release_acceptance(s: AsyncSession, keys: list[str]) -> None:
    """The guard looked and the accepted finding is gone: if it ever comes
    back, it is news again (M3)."""
    if keys:
        await s.execute(
            text("UPDATE container_incidents SET accepted = false WHERE incident_key = ANY(:ks) AND accepted"),
            {"ks": list(keys)},
        )


async def prune(s: AsyncSession, *, days: int = RETENTION_DAYS) -> int:
    """Delete resolved incidents older than ``days``, keeping every open
    one and every still-accepted clear. Returns how many went."""
    result = await s.execute(
        text(
            "DELETE FROM container_incidents WHERE resolved_at IS NOT NULL AND NOT accepted "
            "AND resolved_at < now() - make_interval(days => :d)"
        ),
        {"d": int(days)},
    )
    return int(result.rowcount or 0)


async def get_baseline(s: AsyncSession, key: str) -> dict[str, Any] | None:
    row = (
        await s.execute(
            text("SELECT key, value, source, recorded_at FROM container_guard_baselines WHERE key = :k"),
            {"k": key},
        )
    ).first()
    if row is None:
        return None
    return {"key": row[0], "value": row[1], "source": row[2], "recorded_at": row[3]}


async def set_baseline(s: AsyncSession, key: str, value: str, source: str) -> None:
    await s.execute(
        text(
            "INSERT INTO container_guard_baselines (key, value, source) VALUES (:k, :v, :s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, "
            "recorded_at = now()"
        ),
        {"k": key, "v": value, "s": source},
    )


def iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def drop_baseline(s: AsyncSession, key: str, value: str) -> None:
    """Forget a baseline, only while it still says ``value`` (a newer one
    written meanwhile stays)."""
    await s.execute(
        text("DELETE FROM container_guard_baselines WHERE key = :k AND value = :v"),
        {"k": key, "v": value},
    )


async def note_incident(s: AsyncSession, incident_id: int, note: str) -> bool:
    """Add a sentence to an open incident's words, once (the guard's own
    finding about it, for the admin who reviews it). True when added."""
    result = await s.execute(
        text(
            "UPDATE container_incidents SET summary = summary || ' ' || :n "
            "WHERE id = :i AND resolved_at IS NULL AND strpos(summary, :n) = 0"
        ),
        {"i": int(incident_id), "n": note[:400]},
    )
    if result.rowcount:
        await notify(s, f"changed:{incident_id}")
        return True
    return False
