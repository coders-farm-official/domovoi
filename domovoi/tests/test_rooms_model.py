"""Rooms with several satellites, slice 1: the data model (V073).

A room is the place (``rooms``); a satellite is a device in one room
(``satellites``, keyed by ``satellite_id``). The fixture here gives NO
satellite an id equal to its room's, so a query that mixes up a device and
a room shows up (every device from before 1.5.0 has the same id as its
room, which hides that mistake everywhere else).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from domovoi import quiet_hours, room_battery, wake_default
from domovoi.db.repositories import (
    RoomsRepository,
    SatellitePairingRepository,
    SatellitesRepository,
    ensure_satellite,
)
from domovoi.db.session import session_scope
from domovoi.handlers.shared.rooms import household_rooms
from domovoi.tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def house():
    """Two rooms, three satellites, no id shared between a room and a
    device: kitchen (two devices, one named), den (one device)."""
    tag = uuid.uuid4().hex[:6]
    kitchen, den = f"rm_kitchen_{tag}", f"rm_den_{tag}"
    a, b, c = f"rmsat_a_{tag}", f"rmsat_b_{tag}", f"rmsat_c_{tag}"
    async with session_scope() as s:
        rooms = RoomsRepository(s)
        assert await rooms.create(kitchen, "Kitchen")
        assert await rooms.create(den)
        sats = SatellitesRepository(s)
        for sid, room in ((a, kitchen), (b, kitchen), (c, den)):
            await ensure_satellite(s, sid)
            assert await sats.move(sid, room)
            # The device's own room (its id) is left empty by the move.
            assert await rooms.delete(sid) == "deleted"
        await sats.set_name(b, "Kitchen phone")
    yield {"kitchen": kitchen, "den": den, "a": a, "b": b, "c": c}
    async with session_scope() as s:
        ids = [a, b, c]
        for t in ("satellite_pairings", "room_battery"):
            await s.execute(text(f"DELETE FROM {t} WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM satellites WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM room_wake_words WHERE room_id = ANY(:r)"),
                        {"r": [kitchen, den]})
        await s.execute(text("DELETE FROM rooms WHERE room_id = ANY(:r)"),
                        {"r": [kitchen, den, *ids]})


async def test_devices_and_rooms_are_told_apart(house):
    h = house
    async with session_scope() as s:
        sats = SatellitesRepository(s)
        assert [d["satellite_id"] for d in await sats.in_room(h["kitchen"])] == [h["a"], h["b"]]
        assert [d["satellite_id"] for d in await sats.in_room(h["den"])] == [h["c"]]
        assert await sats.in_room(h["a"]) == []
        assert await sats.room_of(h["b"]) == h["kitchen"]
        assert await sats.room_of(h["kitchen"]) is None
        b = await sats.get(h["b"])
        assert (b["room_id"], b["name"], b["room_name"], b["room_label"]) == (
            h["kitchen"], "Kitchen phone", "Kitchen", "Kitchen")
        c = await sats.get(h["c"])
        # A room with no name of its own reads as no label, as before V073.
        assert (c["room_name"], c["room_label"]) == (h["den"], None)
        rooms = {r["room_id"]: r for r in await RoomsRepository(s).list()}
        assert rooms[h["kitchen"]]["satellites"] == 2
        assert h["a"] not in rooms


async def test_household_rooms_lists_rooms_not_devices(house):
    h = house
    async with session_scope() as s:
        found = {r.room_id: r for r in await household_rooms(s, None)}
    assert {h["kitchen"], h["den"]} <= set(found)
    assert not {h["a"], h["b"], h["c"]} & set(found)
    assert found[h["kitchen"]].label == "Kitchen"
    assert found[h["den"]].label is None
    assert not found[h["kitchen"]].seen
    async with session_scope() as s:
        await SatellitePairingRepository(s).pair(h["b"], "hash")
        found = {r.room_id: r for r in await household_rooms(s, None)}
    # A paired device makes its room "seen"; the device is still no room.
    assert found[h["kitchen"]].seen and h["b"] not in found


async def test_a_pairing_keeps_a_known_device_where_it_is(house):
    h = house
    async with session_scope() as s:
        await SatellitePairingRepository(s).pair(h["a"], "hash")
        await SatellitesRepository(s).upsert_board(h["a"], "pi4")
        assert await SatellitesRepository(s).room_of(h["a"]) == h["kitchen"]
        assert await RoomsRepository(s).get(h["a"]) is None


async def test_a_new_device_gets_a_room_of_its_own():
    sid = f"rm_new_{uuid.uuid4().hex[:6]}"
    try:
        async with session_scope() as s:
            await SatellitePairingRepository(s).pair(sid, "hash")
            assert await SatellitesRepository(s).room_of(sid) == sid
            assert (await RoomsRepository(s).get(sid))["name"] == sid
            assert await SatellitesRepository(s).room_is_known(sid)
        async with session_scope() as s:
            assert await SatellitesRepository(s).delete(sid)
            # Its own one-device room goes with it, as before V073.
            assert await RoomsRepository(s).get(sid) is None
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = :r"),
                            {"r": sid})
            await s.execute(text("DELETE FROM satellites WHERE satellite_id = :r"), {"r": sid})
            await s.execute(text("DELETE FROM rooms WHERE room_id = :r"), {"r": sid})


async def test_move_rename_merge_delete(house):
    h = house
    async with session_scope() as s:
        rooms, sats = RoomsRepository(s), SatellitesRepository(s)
        assert not await sats.move(h["c"], "rm_nowhere")
        assert await rooms.rename(h["den"], "  Den  ")
        assert (await rooms.get(h["den"]))["name"] == "Den"
        assert await rooms.delete(h["den"]) == "has_satellites"
        assert await rooms.merge(h["kitchen"], [h["den"], h["kitchen"]]) == [h["c"]]
        assert await sats.room_of(h["c"]) == h["kitchen"]
        assert await rooms.get(h["den"]) is None
        assert await rooms.create(h["den"])
        assert await rooms.delete(h["den"]) == "deleted"
        assert await rooms.delete(h["den"]) == "missing"


async def test_rooms_that_hear_each_other(house):
    h = house
    async with session_scope() as s:
        rooms = RoomsRepository(s)
        assert await rooms.link(h["kitchen"], h["den"])
        assert not await rooms.link(h["den"], h["kitchen"])
        assert not await rooms.link(h["den"], h["den"])
        assert await rooms.linked(h["den"]) == [h["kitchen"]]
        assert await rooms.unlink(h["den"], h["kitchen"])
        assert await rooms.linked(h["kitchen"]) == []


async def test_room_settings_live_on_the_room(house):
    h = house
    async with session_scope() as s:
        await SatellitesRepository(s).set_room_voice(h["kitchen"], "amy")
        await quiet_hours.set_room_choice(
            s, h["kitchen"], quiet_hours.RoomChoice("custom", "22:00", "07:00"))
    async with session_scope() as s:
        assert await RoomsRepository(s).voice(h["kitchen"]) == "amy"
        assert await SatellitesRepository(s).room_voice(h["a"]) is None
        assert (await quiet_hours.room_choice(s, h["kitchen"])).mode == "custom"
        assert (await quiet_hours.room_choice(s, h["a"])).mode == "house"


async def test_wake_limit_is_per_device_and_the_room_takes_the_least(house):
    h = house
    async with session_scope() as s:
        await wake_default.mark_chosen(s, h["kitchen"], "domovoi_v1")
        await wake_default.record_device_max(s, h["a"], 3)
        await wake_default.record_device_max(s, h["b"], 2)
    async with session_scope() as s:
        assert (await wake_default.room_state(s, h["kitchen"]))["device_max"] == 2
        assert (await SatellitesRepository(s).get(h["a"]))["wake_max"] == 3


async def test_battery_is_the_device_s_and_named_by_its_room(house):
    h = house
    async with session_scope() as s:
        await room_battery.set_switches(s, h["b"], say=False)
        assert (await room_battery.switches(s, h["b"])).say is False
        assert (await room_battery.switches(s, h["a"])).say is True
        assert await room_battery._label(s, h["b"]) == "Kitchen"
