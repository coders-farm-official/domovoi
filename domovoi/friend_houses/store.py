"""Friend Houses rows in Postgres (V066, V067).

Friend Houses ("serverless messaging and friend system between households").
SQL through ``text()`` with bound parameters only. The house's private keys
and the invite secrets are never here (domovoi/friend_houses/identity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _b(v: Any) -> bytes | None:
    return bytes(v) if v is not None else None


@dataclass(frozen=True)
class FriendRow:
    id: str
    house_key: bytes
    session_key: bytes
    onion_key: bytes
    reach_public: bytes
    reach_private: bytes | None
    local_name: str
    state: str
    code: str | None = None
    verified_at: datetime | None = None
    added_at: datetime | None = None


_FRIEND_COLS = ("id, house_key, session_key, onion_key, reach_public, reach_private, "
                "local_name, state, code, verified_at, added_at")


def _friend(r: Any) -> FriendRow:
    return FriendRow(str(r[0]), bytes(r[1]), bytes(r[2]), bytes(r[3]), bytes(r[4]), _b(r[5]),
                     str(r[6]), str(r[7]), r[8], r[9], r[10])


async def active_friends(s: AsyncSession) -> list[FriendRow]:
    rows = await s.execute(text(
        f"SELECT {_FRIEND_COLS} FROM friend_houses_friends WHERE state = 'active' ORDER BY id"))
    return [_friend(r) for r in rows]


async def friend(s: AsyncSession, friend_id: str) -> FriendRow | None:
    row = (await s.execute(text(f"SELECT {_FRIEND_COLS} FROM friend_houses_friends WHERE id = :i"),
                           {"i": friend_id})).first()
    return _friend(row) if row else None


async def friend_by_house(s: AsyncSession, house_key: bytes) -> FriendRow | None:
    row = (await s.execute(
        text(f"SELECT {_FRIEND_COLS} FROM friend_houses_friends WHERE house_key = :h"),
        {"h": house_key})).first()
    return _friend(row) if row else None


async def insert_friend(s: AsyncSession, *, id: str, house_key: bytes, session_key: bytes,
                        onion_key: bytes, statement: bytes, statement_at: int,
                        reach_public: bytes, reach_private: bytes | None, local_name: str,
                        code: str | None, added_by: str | None) -> None:
    await s.execute(
        text(
            "INSERT INTO friend_houses_friends (id, house_key, session_key, onion_key, statement, "
            "statement_at, reach_public, reach_private, local_name, state, code, added_by) "
            "VALUES (:i, :h, :s, :o, :st, :sa, :rp, :rv, :n, 'active', :c, :by)"
        ),
        {"i": id, "h": house_key, "s": session_key, "o": onion_key, "st": statement,
         "sa": statement_at, "rp": reach_public, "rv": reach_private, "n": local_name,
         "c": code, "by": added_by},
    )


async def delete_friend(s: AsyncSession, friend_id: str) -> bool:
    res = await s.execute(text("DELETE FROM friend_houses_friends WHERE id = :i"), {"i": friend_id})
    return bool(res.rowcount)


async def mark_verified(s: AsyncSession, friend_id: str) -> bool:
    res = await s.execute(text(
        "UPDATE friend_houses_friends SET verified_at = now() WHERE id = :i AND state = 'active'"),
        {"i": friend_id})
    return bool(res.rowcount)


async def rename_friend(s: AsyncSession, friend_id: str, local_name: str) -> bool:
    res = await s.execute(text("UPDATE friend_houses_friends SET local_name = :n WHERE id = :i"),
                          {"i": friend_id, "n": local_name})
    return bool(res.rowcount)


# ─── the epoch (V066) ─────────────────────────────────────────────────────


async def epoch_row(s: AsyncSession) -> tuple[bytes | None, str | None]:
    row = (await s.execute(text("SELECT epoch, marker FROM friend_houses_state WHERE id = 1"))).first()
    return (bytes(row[0]), str(row[1])) if row else (None, None)


async def set_epoch(s: AsyncSession, epoch: bytes, marker: str) -> None:
    """A new epoch restarts the sequence at 1."""
    await s.execute(
        text(
            "INSERT INTO friend_houses_state (id, epoch, marker, next_seq) VALUES (1, :e, :m, 1) "
            "ON CONFLICT (id) DO UPDATE SET epoch = EXCLUDED.epoch, marker = EXCLUDED.marker, "
            "next_seq = 1, updated_at = now()"
        ),
        {"e": epoch, "m": marker},
    )


# ─── friend files (V067) ──────────────────────────────────────────────────

_OPEN = "used_at IS NULL AND cancelled_at IS NULL AND expires_at > now()"


async def open_invites(s: AsyncSession) -> list[dict[str, Any]]:
    rows = await s.execute(text(
        f"SELECT id, label, created_at, expires_at FROM friend_houses_invites WHERE {_OPEN} "
        "ORDER BY created_at"))
    return [{"id": r[0], "label": r[1], "created_at": r[2], "expires_at": r[3]} for r in rows]


async def invite(s: AsyncSession, invite_id: str) -> dict[str, Any] | None:
    row = (await s.execute(text(
        "SELECT id, label, created_at, expires_at, used_at, cancelled_at FROM friend_houses_invites "
        "WHERE id = :i"), {"i": invite_id})).first()
    if row is None:
        return None
    return {"id": row[0], "label": row[1], "created_at": row[2], "expires_at": row[3],
            "used_at": row[4], "cancelled_at": row[5]}


async def insert_invite(s: AsyncSession, *, invite_id: str, label: str | None, days: int,
                        created_by: str | None) -> None:
    await s.execute(
        text(
            "INSERT INTO friend_houses_invites (id, label, expires_at, created_by) "
            "VALUES (:i, :l, now() + make_interval(days => :d), :by)"
        ),
        {"i": invite_id, "l": label, "d": days, "by": created_by},
    )


async def use_invite(s: AsyncSession, invite_id: str) -> bool:
    """Mark an invite used, atomically: True only for the first request
    that proved it while it was still open."""
    res = await s.execute(
        text(f"UPDATE friend_houses_invites SET used_at = now() WHERE id = :i AND {_OPEN}"),
        {"i": invite_id})
    return bool(res.rowcount)


async def cancel_invite(s: AsyncSession, invite_id: str) -> bool:
    res = await s.execute(text(
        f"UPDATE friend_houses_invites SET cancelled_at = now() WHERE id = :i AND {_OPEN}"),
        {"i": invite_id})
    return bool(res.rowcount)


# ─── friend requests (V067) ───────────────────────────────────────────────

_REQUEST_COLS = ("id, direction, state, house_key, session_key, statement, their_name, local_name, "
                 "code, invite_id, reach_private, reach_public, reach_gift, friend_file, created_at, "
                 "expires_at, attempts, last_error")


def _request(r: Any) -> dict[str, Any]:
    keys = [c.strip() for c in _REQUEST_COLS.split(",")]
    out = dict(zip(keys, r))
    for k in ("house_key", "session_key", "statement", "reach_private", "reach_public", "reach_gift"):
        out[k] = _b(out[k])
    return out


async def requests(s: AsyncSession, *, direction: str | None = None,
                   states: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    where, params = ["expires_at > now()"], {}
    if direction:
        where.append("direction = :d")
        params["d"] = direction
    if states:
        where.append("state = ANY(:st)")
        params["st"] = list(states)
    rows = await s.execute(text(
        f"SELECT {_REQUEST_COLS} FROM friend_houses_requests WHERE {' AND '.join(where)} "
        "ORDER BY created_at"), params)
    return [_request(r) for r in rows]


async def request(s: AsyncSession, request_id: str) -> dict[str, Any] | None:
    row = (await s.execute(text(
        f"SELECT {_REQUEST_COLS} FROM friend_houses_requests WHERE id = :i AND expires_at > now()"),
        {"i": request_id})).first()
    return _request(row) if row else None


async def request_by_house(s: AsyncSession, direction: str, house_key: bytes) -> dict[str, Any] | None:
    row = (await s.execute(text(
        f"SELECT {_REQUEST_COLS} FROM friend_houses_requests WHERE direction = :d AND house_key = :h"),
        {"d": direction, "h": house_key})).first()
    return _request(row) if row else None


async def insert_request(s: AsyncSession, **fields: Any) -> None:
    """``fields``: column names of friend_houses_requests, plus ``days``
    (how long until it expires)."""
    days = int(fields.pop("days"))
    cols = sorted(fields)
    await s.execute(
        text(
            f"INSERT INTO friend_houses_requests ({', '.join(cols)}, expires_at) VALUES "
            f"({', '.join(':' + c for c in cols)}, now() + make_interval(days => :days))"
        ),
        {**fields, "days": days},
    )


_REQUEST_UPDATABLE = frozenset({"state", "local_name", "code", "reach_public", "reach_gift",
                                "friend_file", "attempts", "last_error"})


async def update_request(s: AsyncSession, request_id: str, **fields: Any) -> None:
    bad = set(fields) - _REQUEST_UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    sets = ", ".join(f"{c} = :{c}" for c in sorted(fields))
    await s.execute(text(f"UPDATE friend_houses_requests SET {sets} WHERE id = :id"),
                    {**fields, "id": request_id})


async def delete_request(s: AsyncSession, request_id: str) -> bool:
    res = await s.execute(text("DELETE FROM friend_houses_requests WHERE id = :i"), {"i": request_id})
    return bool(res.rowcount)


async def drop_expired(s: AsyncSession) -> int:
    res = await s.execute(text("DELETE FROM friend_houses_requests WHERE expires_at <= now()"))
    return int(res.rowcount or 0)


# ─── household setting (household_settings, key 'friend_houses') ──────────

SETTINGS_KEY = "friend_houses"


async def household_settings(s: AsyncSession) -> dict[str, Any]:
    row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                           {"k": SETTINGS_KEY})).first()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


async def save_household_settings(s: AsyncSession, value: dict[str, Any]) -> None:
    import json

    await s.execute(
        text(
            "INSERT INTO household_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ),
        {"k": SETTINGS_KEY, "v": json.dumps(value)},
    )
