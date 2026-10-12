"""Spare-device satellites, S0: the board is kept (V003 `satellites.board`).

Before S0 a hello's `board` was read only while the hello was parked for
approval, and approval dropped it (assessment 3(e)1). Now approval keeps
it, and so does every accepted hello that carries one — the
``_persist_sat_type`` rule: written when present, never cleared by a hello
without it, so USB adoption's board for a Pi survives that Pi's hellos.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi.db.repositories import SatelliteApprovalRepository, SatellitesRepository
from domovoi.db.session import engine, session_scope
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

pytestmark = requires_db

ROOM = "study"
HASH = "c" * 64
CODE = "482913"


@pytest.fixture(autouse=True)
async def _clean():
    async def wipe():
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE satellite_approvals, satellite_pairings, satellites CASCADE")
            )

    await wipe()
    yield
    await wipe()


async def _row(room: str) -> dict | None:
    async with session_scope() as s:
        return await SatellitesRepository(s).get(room)


async def _park(board: str | None, sat_type: str = "voice") -> None:
    async with session_scope() as s:
        outcome, _ = await SatelliteApprovalRepository(s).request(
            ROOM, token_hash=HASH, code=CODE, board=board, sat_type=sat_type,
        )
    assert outcome == "parked"


async def _approve() -> str:
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).approve(ROOM, CODE)


async def test_approval_keeps_the_board_the_parked_hello_named():
    await _park("linux_pc")
    assert await _row(ROOM) is None
    assert await _approve() == "approved"
    row = await _row(ROOM)
    assert row is not None and row["board"] == "linux_pc"
    assert row["sat_type"] == "voice" and row["adopted_via"] == "hello"


async def test_approving_a_pi_writes_no_inventory_row_as_before():
    await _park(None)
    assert await _approve() == "approved"
    assert await _row(ROOM) is None


async def test_approval_updates_only_the_board_of_an_existing_row():
    async with session_scope() as s:
        await SatellitesRepository(s).preseed_upsert(
            ROOM, sat_type="video", room_label="Upstairs", hardware="Laptop", board="old",
            adopted_via="manual",
        )
    await _park("linux_pc", sat_type="toaster")
    assert await _approve() == "approved"
    row = await _row(ROOM)
    assert row["board"] == "linux_pc"
    assert (row["sat_type"], row["room_label"], row["hardware"]) == ("video", "Upstairs", "Laptop")


class _WS:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(
            satellite_full_duplex={}, satellite_synced_sha={}, satellite_sat_type={},
            satellite_mic_enabled={},
        ))

    async def send_text(self, _t):
        return None

    async def close(self, code=1000):
        return None


@pytest.fixture
def paired(monkeypatch):
    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)


async def test_an_accepted_hello_keeps_its_board_and_one_without_never_clears_it(paired):
    async with session_scope() as s:
        await SatellitesRepository(s).preseed_upsert(ROOM, board="raspberry_pi_zero_2_w")
    # A Pi's hello: no board. The adopted one stays.
    await StreamSession(_WS(), ROOM)._on_control({"type": "hello", "sat_type": "voice"})  # type: ignore[arg-type]
    assert (await _row(ROOM))["board"] == "raspberry_pi_zero_2_w"
    # A device that names one: written.
    await StreamSession(_WS(), ROOM)._on_control(  # type: ignore[arg-type]
        {"type": "hello", "board": "linux_pc", "supports": []})
    assert (await _row(ROOM))["board"] == "linux_pc"
    # A new room: the row is created with the default type.
    await StreamSession(_WS(), "nursery")._on_control(  # type: ignore[arg-type]
        {"type": "hello", "board": "android_room", "supports": []})
    row = await _row("nursery")
    assert row["board"] == "android_room" and row["sat_type"] == "voice"
    assert row["adopted_via"] == "hello"
