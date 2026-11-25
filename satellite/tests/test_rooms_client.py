"""Rooms (1.5.0) from the Pi and PC client's side, against the shared
fixtures' `rooms` section (docs/uml/satellite-fixtures.json): the hello
names the device (`satellite_id`), `requested_room` goes only until the
first `ready`, `ready.room` and the `room` frame are kept (a move without
reconnecting), a missing room name never becomes the id, and
`satellite_id_mismatch` is said in plain words. An older core (no `room`
anywhere) changes nothing.

The core's side is domovoi/tests/test_rooms_fixtures.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from satellite import enrolment, wake_claim
from satellite.tests.test_wake_ack import client

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
ROOMS = FIXTURES["rooms"]


def _sat(room_id: str = "kitchen", requested: str = "", *, profile: str = "respeaker_2mic_hat"):
    sat = client.Satellite(client.Config.from_toml({
        "satellite": {"room_id": room_id, "requested_room": requested},
        "device": {"profile": profile},
    }))
    return sat


def _ready(sat, monkeypatch, frame: dict) -> None:
    """Hand ``frame`` (a `ready`) to the receiver, with the side trips a
    real session takes stubbed."""
    monkeypatch.setattr(client, "_promote_pending_server", lambda path: None)
    sat._upgrade_confirmed = False
    sat._sync_time_with_server = lambda: None
    sat._note_session_accepted = lambda: None
    sat._start_room_player = lambda: None
    sat._mic_dsp_reapply = lambda why: None
    sat._on_core_accepted_health = lambda: None
    sat._handle_text_frame(frame)


# ─── the hello ────────────────────────────────────────────────────────────


def test_the_hello_names_the_device_by_the_path_s_id() -> None:
    hello = _sat("living-room")._hello_frame()
    for key in ROOMS["hello_adds"]:
        assert key in hello, key
    assert hello["satellite_id"] == hello["room_id"] == "living-room"


def test_a_pc_names_itself_too() -> None:
    hello = _sat("study", profile="generic_pc")._hello_frame()
    assert hello["satellite_id"] == "study"


def test_the_older_hello_is_still_the_fixture_s_plus_the_adds() -> None:
    """The fixtures' Pi hellos are the shape from before 1.5.0 (an older
    device must keep working); a 1.5.0 Pi sends that plus `hello_adds`
    and its `supports` list, nothing else."""
    hello = _sat()._hello_frame()
    older = set(FIXTURES["devices"]["pi_2mic_hat"]["hello"])
    extra = set(hello) - older
    assert set(ROOMS["hello_adds"]) <= extra
    assert extra - set(ROOMS["hello_adds"]) <= {"supports", "prev_health", "room_audio",
                                                 "settings_ignored", "requested_room"}


def test_the_requested_room_goes_when_it_is_a_room_id() -> None:
    hello = _sat("living_room_2", ROOMS["hello_requested_room"])._hello_frame()
    assert hello["requested_room"] == "living-room"


@pytest.mark.parametrize("value", [v for v in ROOMS["in_room_invalid"] if v])
def test_a_requested_room_that_is_not_a_room_id_is_left_out(value: str) -> None:
    sat = _sat()
    sat.cfg.requested_room = value
    assert "requested_room" not in sat._hello_frame()


def test_the_client_s_rule_is_the_fixture_s() -> None:
    assert enrolment.IN_ROOM_RE.pattern == ROOMS["in_room_rule"]
    for v in ROOMS["in_room_valid"]:
        assert enrolment.is_in_room(v), v
    for v in ROOMS["in_room_invalid"]:
        assert not enrolment.is_in_room(v), v


def test_an_enrolment_may_join_a_portal_named_room() -> None:
    payload = ("domovoi-room:1?core=http%3A%2F%2F192.168.1.20%3A6370&fp=SHA256%3A"
               + "A" * 43 + "&room=Living%20room%202&in=living-room")
    assert enrolment.parse(payload).in_room == "living-room"


# ─── requested_room stops at the first ready ─────────────────────────────


def test_the_requested_room_is_forgotten_at_the_first_ready(monkeypatch, tmp_path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[satellite]\nroom_id = "living_room_2"\nrequested_room = "living-room"\n'
        '\n[device]\nprofile = "respeaker_2mic_hat"\n', encoding="utf-8")
    monkeypatch.setattr(client, "CONFIG_PATH", cfg_path)
    sat = _sat("living_room_2", "living-room")
    assert sat._hello_frame()["requested_room"] == "living-room"

    _ready(sat, monkeypatch, {"type": "ready", "satellite_id": "living_room_2",
                              "room": {**ROOMS["ready_room"], "room_id": "living-room"}})
    assert "requested_room" not in sat._hello_frame(), "asked for no more"
    deadline = time.monotonic() + 5
    while 'requested_room = ""' not in cfg_path.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, cfg_path.read_text(encoding="utf-8")
        time.sleep(0.02)
    assert client.Config.load(cfg_path).requested_room == ""
    # A second ready writes nothing more.
    cfg_path.unlink()
    _ready(sat, monkeypatch, {"type": "ready"})
    time.sleep(0.1)
    assert not cfg_path.exists()


# ─── ready.room and the room frame ───────────────────────────────────────


def test_ready_room_is_kept(monkeypatch) -> None:
    sat = _sat()
    _ready(sat, monkeypatch, {"type": "ready", "room": ROOMS["ready_room"]})
    assert sat._room_place == ROOMS["ready_room"]
    assert sat._room_claim is True


def test_an_older_core_s_ready_changes_nothing(monkeypatch) -> None:
    sat = _sat()
    _ready(sat, monkeypatch, {"type": "ready", "protocol_version": "0.1"})
    assert sat._room_place is None
    assert sat._room_claim is False


def test_a_move_carries_on_without_reconnecting(monkeypatch) -> None:
    sat = _sat()
    _ready(sat, monkeypatch, {"type": "ready", "room": ROOMS["ready_room"]})
    sat._handle_text_frame(ROOMS["room_move"])
    assert sat._room_place == ROOMS["room_move"]["room"]
    assert sat._room_claim is False


def test_no_name_keeps_the_same_room_s_name_and_never_shows_the_id(monkeypatch) -> None:
    sat = _sat()
    _ready(sat, monkeypatch, {"type": "ready", "room": ROOMS["ready_room"]})
    _ready(sat, monkeypatch, {"type": "ready", "room": ROOMS["ready_room_unnamed"]})
    assert sat._room_place["name"] == "Kitchen"
    # Moved into a room whose name the core could not read: no name at all.
    sat._handle_text_frame(ROOMS["room_move_unnamed"])
    assert sat._room_place["room_id"] == "den"
    assert sat._room_place["name"] is None


@pytest.mark.parametrize("name", [None, "", "   "])
def test_null_missing_and_blank_names_are_the_same(name) -> None:
    room = {"room_id": "den", "shared": False, "claim": False}
    if name is not None:
        room["name"] = name
    assert wake_claim.room_place(room)["name"] is None
    assert wake_claim.room_place({**room, "name": name}) == wake_claim.room_place(room)


def test_a_room_without_a_room_id_is_ignored() -> None:
    before = {"room_id": "kitchen", "name": "Kitchen", "shared": False, "claim": False}
    for bad in (None, "x", {}, {"room_id": "Kitchen"}, {"room_id": 7}):
        assert wake_claim.room_place(bad, before) is before


def test_a_pc_s_tray_shows_the_server_s_room_name(monkeypatch) -> None:
    sat = _sat("study_2", profile="generic_pc")
    sat.cfg.room_name = "Study 2"
    assert sat.control_status()["room"]["name"] == "Study 2"
    _ready(sat, monkeypatch, {"type": "ready", "room": {**ROOMS["ready_room"], "name": "Study"}})
    assert sat.control_status()["room"] == {"id": "study_2", "name": "Study"}
    sat._handle_text_frame(ROOMS["room_move_unnamed"])
    assert sat.control_status()["room"]["name"] == "Study 2", "the set-up name, never an id"


# ─── satellite_id_mismatch ───────────────────────────────────────────────


def test_a_mismatch_is_said_in_plain_words(caplog) -> None:
    sat = _sat("study", profile="generic_pc")
    sat._handle_text_frame(ROOMS["mismatch_error"])
    assert sat._conn_state == "refused"
    assert sat._conn_reason == client.MISMATCH_TEXT
    assert len(client.MISMATCH_TEXT) <= 200, "the tray keeps 200 characters"
    assert "satellite_id" not in client.MISMATCH_TEXT
    assert sat._last_server_error == "satellite_id_mismatch"
    assert sat.response_done.is_set(), "the mic thread is not left waiting"
    assert client.MISMATCH_TEXT in caplog.text


def test_the_client_handles_the_fixture_frames() -> None:
    from satellite.tests.test_protocol_fixtures import _handled_frame_types

    handled = _handled_frame_types()
    for key in ("room_move", "room_move_unnamed", "mismatch_error"):
        assert ROOMS[key]["type"] in handled, key
