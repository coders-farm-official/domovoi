"""The shared fixtures' `rooms` section (docs/uml/satellite-fixtures.json),
from the core's side: one room-id rule for everything that names a room
from outside, `ready.room` without a name when the room's row can't be
read, and the refusal of a hello that names another device.

The Pi and PC client check the same section in
satellite/tests/test_rooms_client.py. No database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domovoi import room_ids, streaming
from domovoi.tests.test_wake_claims import _app, _session

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
ROOMS = FIXTURES["rooms"]


# ─── one rule for a room id from outside ─────────────────────────────────


def test_the_fixture_rule_is_the_core_s_rule() -> None:
    assert room_ids.ACCEPTED_RE.pattern == ROOMS["in_room_rule"]


def test_the_satellite_s_in_rule_is_the_core_s_rule() -> None:
    from satellite import enrolment

    assert enrolment.IN_ROOM_RE.pattern == room_ids.ACCEPTED_RE.pattern


@pytest.mark.parametrize("value", ROOMS["in_room_valid"])
def test_every_side_takes_a_valid_room_id(value: str) -> None:
    from satellite import enrolment
    from web.backend.schemas import SpareDeviceEnrolRequest

    assert room_ids.is_room_id(value)
    assert streaming._requested_room({"requested_room": value}) == value
    assert enrolment.is_in_room(value)
    assert SpareDeviceEnrolRequest(in_room=value).in_room == value


@pytest.mark.parametrize("value", ROOMS["in_room_invalid"])
def test_every_side_refuses_an_invalid_one(value: str) -> None:
    from pydantic import ValidationError

    from satellite import enrolment
    from web.backend.schemas import SpareDeviceEnrolRequest

    assert not room_ids.is_room_id(value)
    assert streaming._requested_room({"requested_room": value}) is None
    assert not enrolment.is_in_room(value)
    with pytest.raises(ValidationError):
        SpareDeviceEnrolRequest(in_room=value)


def test_a_portal_named_room_can_be_joined_by_a_code() -> None:
    """The dashboard's "Add a satellite to the Living-room" builds and parses
    (it refused before: the builder used the stricter rule for a NEW id)."""
    from satellite import enrolment

    payload = enrolment.build("http://192.168.1.20:6370", "SHA256:" + "A" * 43,
                              "Living room 2", in_room=ROOMS["hello_requested_room"])
    assert payload.endswith("&in=living-room")
    assert enrolment.parse(payload).in_room == "living-room"


# ─── ready.room without a name ────────────────────────────────────────────


async def test_an_unreadable_room_row_sends_no_name(monkeypatch) -> None:
    def broken():
        raise RuntimeError("the database blinked")

    monkeypatch.setattr(streaming, "session_scope", broken)
    s = _session(_app(), "kitchen", "kitchen")
    s._room_row = None
    info = await s._room_info()
    assert info == ROOMS["ready_room_unnamed"]
    assert info["name"] is None, "never the id in the name's place"


async def test_a_readable_row_sends_its_name() -> None:
    s = _session(_app(), "kitchen", "kitchen")
    s._room_row = {"room_id": "kitchen", "name": "Kitchen", "satellites": 1}
    assert (await s._room_info())["name"] == "Kitchen"


# ─── the hello ────────────────────────────────────────────────────────────


async def test_a_hello_naming_another_device_gets_the_fixture_error() -> None:
    s = _session(_app(), "kitchen", "kitchen")
    closed: list[int] = []

    async def close(code: int) -> None:
        closed.append(code)

    s._close_quietly = close  # type: ignore[method-assign]
    await s._on_control({"type": "hello", "satellite_id": "den"})
    assert s.ws.sent[-1] == ROOMS["mismatch_error"]
    assert closed == [1008]

