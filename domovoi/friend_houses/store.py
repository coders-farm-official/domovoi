"""Friend Houses rows in Postgres (V066): the friend list and the epoch.

Friend Houses ("serverless messaging and friend system between households").
SQL through ``text()`` with bound parameters only. The house's private keys
are never here (domovoi/friend_houses/identity.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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


async def active_friends(s: AsyncSession) -> list[FriendRow]:
    rows = await s.execute(text(
        "SELECT id, house_key, session_key, onion_key, reach_public, reach_private, "
        "local_name, state FROM friend_houses_friends WHERE state = 'active' ORDER BY id"
    ))
    return [FriendRow(str(r[0]), bytes(r[1]), bytes(r[2]), bytes(r[3]), bytes(r[4]),
                      bytes(r[5]) if r[5] is not None else None, str(r[6]), str(r[7]))
            for r in rows]


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
