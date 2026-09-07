"""Pending satellite approvals (V009) — the portal path's answer to TOFU.

The DB-backed cases below need Postgres and SKIP without it. The checks that
can run everywhere are done at module scope, so a box with no database still
catches a renamed method or a missing migration at collection time rather
than reporting a green run over tests that never executed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.db.repositories import (
    SatelliteApprovalRepository,
    SatellitePairingRepository,
)
from domovoi.db.session import engine, session_scope
from domovoi.tests.conftest import requires_db

# ─── collection-time checks (these run with or without a database) ────────

_MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
assert (_MIGRATIONS / "V009__satellite_approvals.sql").is_file(), \
    "V009 migration is missing — approvals have no table to live in"

for _name in ("request", "list_pending", "get", "approve", "reject"):
    assert hasattr(SatelliteApprovalRepository, _name), \
        f"SatelliteApprovalRepository lost {_name}()"

pytestmark = requires_db

ROOM = "kitchen"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def _t():
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE satellite_approvals, satellite_pairings CASCADE")
            )
    _run(_t())
    yield
    _run(_t())


async def _request(**kw):
    async with session_scope() as s:
        await SatelliteApprovalRepository(s).request(
            kw.pop("room_id", ROOM), token_hash=kw.pop("token_hash", HASH_A), **kw
        )


async def _pending():
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).list_pending()


async def _pairing(room=ROOM):
    async with session_scope() as s:
        return await SatellitePairingRepository(s).get_pairing(room)


# ─── requesting ───────────────────────────────────────────────────────────


def test_a_request_shows_up_with_its_code():
    _run(_request(code="4821", mac="b8:27:eb:aa:bb:cc", board="pi02w"))
    rows = _run(_pending())
    assert len(rows) == 1
    assert rows[0]["room_id"] == ROOM
    assert rows[0]["code"] == "4821"
    assert rows[0]["attempts"] == 1


def test_the_token_hash_is_never_listed():
    """The dashboard's job is matching a four-digit code; it has no use for
    the hash, so it never leaves the database."""
    _run(_request(code="4821"))
    assert "token_hash" not in _run(_pending())[0]


def test_retries_bump_attempts_instead_of_piling_up():
    """The satellite reconnects on its own — a queue of duplicate rows would
    be an artefact of that, not information."""
    for _ in range(3):
        _run(_request(code="4821"))
    rows = _run(_pending())
    assert len(rows) == 1
    assert rows[0]["attempts"] == 3


def test_a_reprovisioned_device_replaces_its_hash():
    _run(_request(token_hash=HASH_A, code="1111"))
    _run(_request(token_hash=HASH_B, code="2222"))

    async def _get():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).get(ROOM)

    row = _run(_get())
    assert row["token_hash"] == HASH_B
    assert row["code"] == "2222"


# ─── approving ────────────────────────────────────────────────────────────


def test_approving_binds_the_room_to_that_device():
    _run(_request(token_hash=HASH_A, code="4821"))

    async def _approve():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).approve(ROOM)

    assert _run(_approve()) is True
    # The pairing carries the hash the device actually presented — approval
    # binds a device, not just a room name.
    assert _run(_pairing())[0] == HASH_A
    assert _run(_pending()) == []


def test_approving_nothing_is_refused():
    """A second click, or an approve racing a reject, must not invent a
    pairing out of nothing."""
    async def _approve():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).approve("nosuchroom")

    assert _run(_approve()) is False
    assert _run(_pairing("nosuchroom")) is None


def test_approving_twice_is_refused_the_second_time():
    _run(_request(token_hash=HASH_A))

    async def _approve():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).approve(ROOM)

    assert _run(_approve()) is True
    assert _run(_approve()) is False


# ─── rejecting ────────────────────────────────────────────────────────────


def test_rejecting_clears_the_request_without_pairing():
    _run(_request(token_hash=HASH_A))

    async def _reject():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).reject(ROOM)

    assert _run(_reject()) is True
    assert _run(_pending()) == []
    # Rejection is not a ban: no pairing was written, and the device is free
    # to ask again. Powering it off is what actually stops it.
    assert _run(_pairing()) is None


def test_rejecting_nothing_reports_false():
    async def _reject():
        async with session_scope() as s:
            return await SatelliteApprovalRepository(s).reject("nosuchroom")

    assert _run(_reject()) is False
