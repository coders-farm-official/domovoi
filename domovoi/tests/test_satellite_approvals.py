"""Pending satellite approvals (V009) — the portal path's answer to TOFU.

The DB-backed cases below need Postgres and SKIP without it. The checks that
can run everywhere are done at module scope, so a box with no database still
catches a renamed method or a missing migration at collection time rather
than reporting a green run over tests that never executed.
"""

from __future__ import annotations

import asyncio
import inspect
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
CODE = "481502"
OTHER_CODE = "930071"


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
        return await SatelliteApprovalRepository(s).request(
            kw.pop("room_id", ROOM), token_hash=kw.pop("token_hash", HASH_A), **kw
        )


async def _pending():
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).list_pending()


async def _pairing(room=ROOM):
    async with session_scope() as s:
        return await SatellitePairingRepository(s).get_pairing(room)


async def _get(room=ROOM):
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).get(room)


async def _approve(room=ROOM, code=CODE):
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).approve(room, code)


# ─── requesting ───────────────────────────────────────────────────────────


def test_a_request_shows_up_waiting():
    outcome, code = _run(_request(code=CODE, mac="b8:27:eb:aa:bb:cc", board="pi02w"))
    assert (outcome, code) == ("parked", CODE)
    rows = _run(_pending())
    assert len(rows) == 1
    assert rows[0]["room_id"] == ROOM
    assert rows[0]["has_code"] is True
    assert rows[0]["attempts"] == 1


def test_neither_the_hash_nor_the_code_is_listed():
    """The operator types the code in from the device. Printing it next to
    the approve button would make it a label again, and the hash has never
    had a reader outside the database."""
    _run(_request(code=CODE))
    row = _run(_pending())[0]
    assert "token_hash" not in row
    assert "code" not in row
    assert CODE not in str(row)


def test_retries_bump_attempts_instead_of_piling_up():
    """The satellite reconnects on its own — a queue of duplicate rows would
    be an artefact of that, not information."""
    for _ in range(3):
        assert _run(_request(code=CODE))[0] == "parked"
    rows = _run(_pending())
    assert len(rows) == 1
    assert rows[0]["attempts"] == 3


def test_a_second_device_under_the_same_room_is_a_conflict():
    """The first device to park holds the room name until a human decides.
    A second one arriving with a different token is told so, and the parked
    request keeps its hash and its code — otherwise the row on screen would
    stop describing the device that made it."""
    _run(_request(token_hash=HASH_A, code=CODE))
    assert _run(_request(token_hash=HASH_B, code=OTHER_CODE)) == ("conflict", None)

    row = _run(_get())
    assert row["token_hash"] == HASH_A
    assert row["code"] == CODE
    # The conflict is not counted as a retry of the parked request either.
    assert _run(_pending())[0]["attempts"] == 1


def test_the_same_device_keeps_the_code_the_customer_was_shown():
    """A retry refreshes the row but never swaps the code out from under
    whoever is already holding it."""
    _run(_request(token_hash=HASH_A, code=CODE))
    assert _run(_request(token_hash=HASH_A, code=OTHER_CODE)) == ("parked", CODE)
    assert _run(_get())["code"] == CODE


def test_a_request_without_a_code_parks_with_none():
    """The repository stores what it is given; minting a code for a device
    that brought none is the caller's job (streaming does it)."""
    assert _run(_request(token_hash=HASH_A, code=None)) == ("parked", None)
    assert _run(_pending())[0]["has_code"] is False


# ─── approving ────────────────────────────────────────────────────────────


def test_approving_with_the_right_code_binds_the_room_to_that_device():
    _run(_request(token_hash=HASH_A, code=CODE))
    assert _run(_approve()) == "approved"
    # The pairing carries the hash the device actually presented — approval
    # binds a device, not just a room name.
    assert _run(_pairing())[0] == HASH_A
    assert _run(_pending()) == []


def test_approving_nothing_is_refused():
    """A second click, or an approve racing a reject, must not invent a
    pairing out of nothing."""
    assert _run(_approve("nosuchroom")) == "not_pending"
    assert _run(_pairing("nosuchroom")) is None


def test_approving_twice_is_refused_the_second_time():
    _run(_request(token_hash=HASH_A, code=CODE))
    assert _run(_approve()) == "approved"
    assert _run(_approve()) == "not_pending"


def test_the_wrong_code_binds_nothing_and_leaves_the_request_parked():
    """The device that IS in the room keeps its place in the queue: a
    failed attempt must not cost it the request it already made."""
    _run(_request(token_hash=HASH_A, code=CODE))
    assert _run(_approve(code=OTHER_CODE)) == "mismatch"
    assert _run(_pairing()) is None
    assert _run(_get())["token_hash"] == HASH_A
    assert len(_run(_pending())) == 1


def test_an_empty_code_never_matches():
    _run(_request(token_hash=HASH_A, code=CODE))
    for candidate in ("", "   ", "0"):
        assert _run(_approve(code=candidate)) == "mismatch"
    assert _run(_pairing()) is None


def test_a_request_with_no_code_on_file_cannot_be_approved():
    """A row parked by an older server has nothing to compare. Rather than
    waving it through, it waits for the device to ask again with a code."""
    _run(_request(token_hash=HASH_A, code=None))
    assert _run(_approve(code=CODE)) == "no_code"
    assert _run(_approve(code="")) == "no_code"
    assert _run(_pairing()) is None
    assert len(_run(_pending())) == 1


def test_approving_after_a_conflict_binds_the_device_that_parked_first():
    """The end-to-end shape of the conflict rule: the second device's code
    is not the one that works, and approving binds the first device."""
    _run(_request(token_hash=HASH_A, code=CODE))
    _run(_request(token_hash=HASH_B, code=OTHER_CODE))
    assert _run(_approve(code=OTHER_CODE)) == "mismatch"
    assert _run(_approve(code=CODE)) == "approved"
    assert _run(_pairing())[0] == HASH_A


# ─── rejecting ────────────────────────────────────────────────────────────


def test_rejecting_clears_the_request_without_pairing():
    _run(_request(token_hash=HASH_A, code=CODE))

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
