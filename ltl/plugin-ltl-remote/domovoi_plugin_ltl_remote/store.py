"""Every SQL statement the plugin runs, in one place.

Keeping persistence here means ``link.py`` is about the protocol and
``core.py`` is about registration, and neither of them grows an inline
query that drifts from the migration. All access goes through
``sdk.db.session_scope()``, which presets
``search_path = plugin_ltl_remote, public``, so table names below are
unqualified and can only ever resolve inside the plugin's own schema.

NOTIFY rides the caller's transaction via ``sdk.realtime.notify`` — never
a bare ``pg_notify`` — so the dashboard cannot be told about a change
that then rolls back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

# Realtime channel suffixes. The web side derives the full
# ``plugin_ltl_remote_<suffix>`` name from the manifest, so these two
# constants are the only place the names are written.
CH_LINK = "link_changed"
CH_DEVICES = "devices_changed"

_LINK_COLUMNS = """
    fingerprint, dh_public_key, sig_public_key,
    pairing_code, pairing_code_hash, pairing_expires_at,
    enrollment_id, enrollment_token,
    household_id, account_label, claimed_at,
    connection_state, last_connected_at, last_disconnected_at, last_error,
    plan_code, quota_used_bytes, quota_limit_bytes, quota_period_end,
    created_at, updated_at
"""

_DEVICE_COLUMNS = """
    id, device_id, label, public_key, fingerprint, status,
    registered_at, approved_at, revoked_at, last_seen_at, last_seen_country
"""


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping) if row is not None else {}


# ─── link state ────────────────────────────────────────────────────────────


async def read_link_state(session: Any) -> dict[str, Any]:
    row = (
        await session.execute(text(f"SELECT {_LINK_COLUMNS} FROM link_state WHERE id = 1"))
    ).first()
    return _row_to_dict(row)


async def get_link_state(sdk: Any) -> dict[str, Any]:
    async with sdk.db.session_scope() as s:
        return await read_link_state(s)


async def save_identity(sdk: Any, *, fingerprint: str, dh_pub: bytes, sig_pub: bytes) -> None:
    """Record the public half of the household identity.

    Called on every enable, not only the first: the keys on disk are
    authoritative, so if someone restored a database backup over a
    machine whose key files moved on, the database is what gets corrected.
    """
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET fingerprint = :fp, dh_public_key = :dh, "
                "sig_public_key = :sig, updated_at = NOW() WHERE id = 1"
            ),
            {"fp": fingerprint, "dh": dh_pub, "sig": sig_pub},
        )
        await sdk.realtime.notify(s, CH_LINK, "identity")


async def start_pairing(
    sdk: Any,
    *,
    code: str,
    code_hash: str,
    expires_at: datetime,
    enrollment_id: str,
    enrollment_token: str,
) -> None:
    """Open a pairing window.

    The enrollment id and poll token are persisted rather than kept in
    memory so that a core restart mid-pairing resumes the same window
    instead of stranding an admin who has already written eight words
    down.
    """
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET pairing_code = :code, "
                "pairing_code_hash = :hash, pairing_expires_at = :exp, "
                "enrollment_id = :eid, enrollment_token = :etoken, "
                "last_error = NULL, updated_at = NOW() WHERE id = 1"
            ),
            {
                "code": code, "hash": code_hash, "exp": expires_at,
                "eid": enrollment_id, "etoken": enrollment_token,
            },
        )
        await sdk.realtime.notify(s, CH_LINK, "pairing_started")


async def clear_pairing(sdk: Any) -> None:
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET pairing_code = NULL, "
                "pairing_code_hash = NULL, pairing_expires_at = NULL, "
                "enrollment_id = NULL, enrollment_token = NULL, "
                "updated_at = NOW() WHERE id = 1"
            )
        )
        await sdk.realtime.notify(s, CH_LINK, "pairing_cleared")


async def record_claim(
    sdk: Any, *, household_id: str, account_label: str | None
) -> None:
    """The household has been bound to an LTL account. Clears the pairing
    code in the same transaction that records the claim, so there is no
    instant where a claimed household still displays a live code."""
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET household_id = :hid, account_label = :label, "
                "claimed_at = NOW(), pairing_code = NULL, pairing_code_hash = NULL, "
                "pairing_expires_at = NULL, enrollment_id = NULL, "
                "enrollment_token = NULL, last_error = NULL, updated_at = NOW() "
                "WHERE id = 1"
            ),
            {"hid": household_id, "label": account_label},
        )
        await sdk.realtime.notify(s, CH_LINK, "claimed")


async def forget_claim(sdk: Any) -> None:
    """Unlink from LTL. Devices are revoked rather than deleted so the
    access log still resolves their labels."""
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET household_id = NULL, account_label = NULL, "
                "claimed_at = NULL, plan_code = NULL, quota_used_bytes = 0, "
                "quota_limit_bytes = NULL, quota_period_end = NULL, "
                "connection_state = 'idle', updated_at = NOW() WHERE id = 1"
            )
        )
        await s.execute(
            text(
                "UPDATE remote_devices SET status = 'revoked', revoked_at = NOW(), "
                "updated_at = NOW() WHERE status <> 'revoked'"
            )
        )
        await sdk.realtime.notify(s, CH_LINK, "unlinked")
        await sdk.realtime.notify(s, CH_DEVICES, "unlinked")


async def set_connection_state(
    sdk: Any, state: str, *, error: str | None = None
) -> None:
    """Record connect/disconnect. ``connected`` and ``disconnected`` also
    stamp their timestamps, so the dashboard can show "up for 3 days"
    without a separate heartbeat table."""
    stamps = ""
    if state == "connected":
        stamps = ", last_connected_at = NOW(), last_error = NULL"
    elif state == "disconnected":
        stamps = ", last_disconnected_at = NOW()"
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                f"UPDATE link_state SET connection_state = :state, "
                f"last_error = COALESCE(:err, last_error){stamps}, "
                f"updated_at = NOW() WHERE id = 1"
            ),
            {"state": state, "err": error},
        )
        await sdk.realtime.notify(s, CH_LINK, state)


async def record_quota(
    sdk: Any,
    *,
    plan_code: str | None,
    used_bytes: int,
    limit_bytes: int | None,
    period_end: datetime | None,
) -> None:
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE link_state SET plan_code = :plan, quota_used_bytes = :used, "
                "quota_limit_bytes = :limit, quota_period_end = :period_end, "
                "updated_at = NOW() WHERE id = 1"
            ),
            {
                "plan": plan_code,
                "used": int(used_bytes),
                "limit": limit_bytes,
                "period_end": period_end,
            },
        )
        await sdk.realtime.notify(s, CH_LINK, "quota")


# ─── devices ───────────────────────────────────────────────────────────────


async def list_devices(sdk: Any, *, status: str | None = None) -> list[dict[str, Any]]:
    clause = "WHERE status = :status " if status else ""
    async with sdk.db.session_scope() as s:
        rows = (
            await s.execute(
                text(
                    f"SELECT {_DEVICE_COLUMNS} FROM remote_devices {clause}"
                    "ORDER BY registered_at DESC"
                ),
                {"status": status} if status else {},
            )
        ).all()
    return [_row_to_dict(r) for r in rows]


async def register_pending_device(
    sdk: Any,
    *,
    device_id: str,
    label: str,
    public_key: bytes,
    fingerprint: str,
) -> bool:
    """Record a device LTL says wants in. Returns True if this was new.

    A re-registration of a device that is already ``approved`` is
    ignored on purpose — otherwise anyone who could push a
    ``device_pending`` frame could downgrade an approved device back to
    pending and then swap its public key. A device that changes keys must
    use a new device id and be approved again.
    """
    async with sdk.db.session_scope() as s:
        existing = (
            await s.execute(
                text("SELECT status FROM remote_devices WHERE device_id = :d"),
                {"d": device_id},
            )
        ).first()
        if existing is not None:
            return False
        await s.execute(
            text(
                "INSERT INTO remote_devices (device_id, label, public_key, "
                "fingerprint, status) VALUES (:d, :l, :k, :f, 'pending')"
            ),
            {"d": device_id, "l": label, "k": public_key, "f": fingerprint},
        )
        await sdk.realtime.notify(s, CH_DEVICES, "registered")
    return True


async def approved_device_key(sdk: Any, device_id: str) -> bytes | None:
    """The single lookup the handshake depends on.

    Returns the public key only for a row that is ``approved``. A
    pending or revoked device gets ``None`` and is refused before any
    key agreement happens.
    """
    async with sdk.db.session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT public_key FROM remote_devices "
                    "WHERE device_id = :d AND status = 'approved'"
                ),
                {"d": device_id},
            )
        ).first()
    return bytes(row[0]) if row is not None else None


async def set_device_status(sdk: Any, device_id: str, status: str) -> bool:
    if status not in ("pending", "approved", "revoked"):
        raise ValueError(f"invalid device status {status!r}")
    stamp = {
        "approved": ", approved_at = NOW(), revoked_at = NULL",
        "revoked": ", revoked_at = NOW()",
    }.get(status, "")
    async with sdk.db.session_scope() as s:
        result = await s.execute(
            text(
                f"UPDATE remote_devices SET status = :s{stamp}, updated_at = NOW() "
                "WHERE device_id = :d"
            ),
            {"s": status, "d": device_id},
        )
        if result.rowcount:
            await sdk.realtime.notify(s, CH_DEVICES, status)
    return bool(result.rowcount)


async def touch_device(sdk: Any, device_id: str, *, country: str | None) -> None:
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "UPDATE remote_devices SET last_seen_at = NOW(), "
                "last_seen_country = COALESCE(:c, last_seen_country), "
                "updated_at = NOW() WHERE device_id = :d"
            ),
            {"d": device_id, "c": country},
        )


# ─── access log ────────────────────────────────────────────────────────────


async def log_access(
    sdk: Any,
    *,
    device_id: str | None,
    method: str,
    path: str,
    status: int | None,
    outcome: str,
    denial_code: str | None = None,
    bytes_in: int = 0,
    bytes_out: int = 0,
    duration_ms: int | None = None,
) -> None:
    """One row per remote request. The query string is dropped here
    rather than at the call site, so no caller can forget to."""
    async with sdk.db.session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO remote_access_log (device_id, method, path, status, "
                "outcome, denial_code, bytes_in, bytes_out, duration_ms) "
                "VALUES (:d, :m, :p, :st, :o, :dc, :bi, :bo, :ms)"
            ),
            {
                "d": device_id,
                "m": method.upper()[:16],
                "p": path.split("?", 1)[0][:2000],
                "st": status,
                "o": outcome,
                "dc": denial_code,
                "bi": int(bytes_in),
                "bo": int(bytes_out),
                "ms": duration_ms,
            },
        )


async def read_access_log(
    sdk: Any, *, limit: int, offset: int, device_id: str | None = None
) -> list[dict[str, Any]]:
    clause = "WHERE device_id = :d " if device_id else ""
    async with sdk.db.session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, device_id, at, method, path, status, outcome, "
                    f"denial_code, bytes_in, bytes_out, duration_ms FROM remote_access_log "
                    f"{clause}ORDER BY at DESC LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset, "d": device_id},
            )
        ).all()
    return [_row_to_dict(r) for r in rows]


# ─── retention (the reaper's work) ─────────────────────────────────────────


async def reap(
    sdk: Any, *, access_log_days: int, pending_device_hours: int
) -> dict[str, int]:
    """Delete expired access-log rows and stale pending devices.

    ``access_log_days == 0`` means keep forever, matching the convention
    the radio plugin already set for retention settings.
    """
    deleted_logs = 0
    dropped_devices = 0
    async with sdk.db.session_scope() as s:
        if access_log_days > 0:
            result = await s.execute(
                text(
                    "DELETE FROM remote_access_log "
                    "WHERE at < NOW() - make_interval(days => :days)"
                ),
                {"days": access_log_days},
            )
            deleted_logs = result.rowcount or 0
        if pending_device_hours > 0:
            result = await s.execute(
                text(
                    "DELETE FROM remote_devices WHERE status = 'pending' "
                    "AND registered_at < NOW() - make_interval(hours => :hours)"
                ),
                {"hours": pending_device_hours},
            )
            dropped_devices = result.rowcount or 0
            if dropped_devices:
                await sdk.realtime.notify(s, CH_DEVICES, "expired")
    return {"access_log_rows": deleted_logs, "pending_devices": dropped_devices}
