"""The room drawer's 1.5.0 lines (web/static/satellites.jsx), driven through
the real JSX (jsx_interact_harness.js):

* Conversations and the drawer say "Answered in the Kitchen. Heard best by
  Kitchen phone." when the room had a choice of microphones (design §3.2
  item 10), and nothing when it didn't;
* a listener that can't claim (an older app) reads "Update the app on this
  phone." (owner answer Q6), and nothing when it can;
* "Rooms that can hear this one": linked rooms listed, an admin adds and
  removes them (removal through askConfirm), a household member only reads;
* the Rooms page's link suggestion: "Link them" links, "Keep them separate"
  dismisses (both admin only);
* "Add a satellite to the Kitchen": the drawer's button, and the spare
  device card asking for a code with `in_room`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellites.jsx"]

HEARD = "Answered in the Kitchen. Heard best by Kitchen phone."


def _sat(sid, name=None, *, board=None, needs_update=False, connected=True):
    return {"satellite_id": sid, "name": name, "sat_type": "voice", "board": board,
            "paired": True, "connected": connected, "speaks_for_room": False,
            "output_latency_us": None, "calibrated_at": None, "needs_update": needs_update}


KITCHEN = {"room_id": "kitchen", "name": "Kitchen", "shared": True, "linked_rooms": ["den"],
           "tunable_speakers": 0, "calibration": None,
           "satellites": [_sat("kitchen"), _sat("kitchen_2", "Kitchen phone", board="android_room",
                                                needs_update=True)]}
DEN = {"room_id": "den", "name": "Den", "shared": False, "linked_rooms": ["kitchen"],
       "tunable_speakers": 0, "calibration": None, "satellites": [_sat("den")]}
PORCH = {"room_id": "porch", "name": "Porch", "shared": False, "linked_rooms": [],
         "tunable_speakers": 0, "calibration": None, "satellites": []}
ROOMS = [KITCHEN, DEN, PORCH]

TURN = {"id": 7, "at": "2026-11-22T10:00:00Z", "room_id": "kitchen", "user_text": "what time is it",
        "assistant_text": "Ten o'clock.", "status": "answered", "utterance_trigger": "wake_word",
        "satellite_id": "kitchen", "mic_satellite_id": "kitchen_2",
        "room_name": "Kitchen", "heard_by": "Kitchen phone"}
PLAIN_TURN = {**TURN, "id": 8, "room_name": None, "heard_by": None}

API = {
    "GET /api/satellites/kitchen/conversations": [TURN],
    "POST /api/rooms/kitchen/links": {"room_id": "kitchen", "linked_rooms": ["den", "porch"]},
    "DELETE /api/rooms/kitchen/links/den": {"room_id": "kitchen", "linked_rooms": []},
    "POST /api/rooms/kitchen/links/den/dismiss": {"dismissed": True},
}

READ = """
  h.render(); await h.settle();
  const buttons = h.findAll({ type: 'button' }).map((b) => String(b.text).trim());
  return { texts: h.text(), buttons };
"""

MUTATIONS = "h.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.path, c.body])"


def _drawer(admin=True, room=KITCHEN, script=READ):
    return {"files": FILES, "component": "RoomDrawer",
            "props": {"room": room, "rooms": ROOMS, "sats": [], "admin": admin},
            "fnProps": ["onClose", "onOpenSatellite", "onAddSatellite", "fire", "refresh"],
            "api": API, "script": script}


SCENARIOS = {
    "drawer_admin": _drawer(),
    "drawer_household": _drawer(admin=False),
    "drawer_single": _drawer(room={**DEN, "linked_rooms": []}),
    "turn_heard": {"files": FILES, "component": "SatConversationTurn", "props": {"c": TURN},
                   "api": {}, "script": READ},
    "turn_plain": {"files": FILES, "component": "SatConversationTurn", "props": {"c": PLAIN_TURN},
                   "api": {}, "script": READ},
    "row_pi_old": {"files": FILES, "component": "RoomSatelliteRow",
                   "props": {"room": KITCHEN, "sat": _sat("kitchen", needs_update=True),
                             "rooms": ROOMS, "admin": True, "canOpen": False},
                   "fnProps": ["onOpen", "fire", "refresh"], "api": {}, "script": READ},
    "row_ok": {"files": FILES, "component": "RoomSatelliteRow",
               "props": {"room": KITCHEN, "sat": _sat("kitchen"), "rooms": ROOMS,
                         "admin": True, "canOpen": False},
               "fnProps": ["onOpen", "fire", "refresh"], "api": {}, "script": READ},
    "link_add": _drawer(script="""
      h.render(); await h.settle();
      await h.click({ type: 'button', text: 'Add a room' });
      await h.settle();
      const options = h.findAll({ type: 'option' }).map((o) => String(o.text).trim());
      await h.change((el) => el.type === 'select' && el.props['aria-label'] === 'room that can hear this one', 'porch');
      await h.settle();
      await h.click({ type: 'button', text: 'Link' });
      await h.settle();
      return { options, calls: """ + MUTATIONS + """, fired: h.fnCalls };
    """),
    "link_remove": {**_drawer(script="""
      h.render(); await h.settle();
      await h.click({ type: 'button', title: 'Remove the Den' });
      await h.settle();
      return { calls: """ + MUTATIONS + """, asked: h.global('__asked') };
    """), "setup": "var __asked = []; window.confirm = (m) => { __asked.push(m); return true; };"},
    "add_satellite": _drawer(script="""
      h.render(); await h.settle();
      await h.click({ type: 'button', text: 'Add a satellite to the Kitchen' });
      await h.settle();
      return { fired: h.fnCalls };
    """),
    "offer": {"files": FILES, "component": "RoomsCard",
              "props": {"rooms": ROOMS, "merge": [], "admin": True,
                        "links": [{"rooms": ["den", "kitchen"], "count": 3}]},
              "fnProps": ["onOpenRoom", "fire", "refresh"],
              "api": {"POST /api/rooms/den/links": {}, "POST /api/rooms/den/links/kitchen/dismiss": {}},
              "script": """
      h.render(); await h.settle();
      const texts = h.text();
      await h.click({ type: 'button', text: 'Link them' });
      await h.settle();
      await h.click({ type: 'button', text: 'Keep them separate' });
      await h.settle();
      return { texts, calls: """ + MUTATIONS + """ };
    """},
    "offer_household": {"files": FILES, "component": "RoomsCard",
                        "props": {"rooms": ROOMS, "merge": [], "admin": False,
                                  "links": [{"rooms": ["den", "kitchen"], "count": 3}]},
                        "fnProps": ["onOpenRoom", "fire", "refresh"], "api": {}, "script": READ},
    "spare_in_room": {"files": FILES, "component": "SpareDeviceCard",
                      "props": {"target": {"room_id": "kitchen", "name": "Kitchen", "at": 1}},
                      "fnProps": ["fire"],
                      "api": {"POST /api/satellites/enrolment": {
                          "payload": "domovoi-room:1?core=x&fp=y&room=Kitchen%202&in=kitchen",
                          "join_url": "http://192.168.1.20:6370/join#x", "room": "Kitchen 2",
                          "room_id": "kitchen_2", "in_room": "kitchen",
                          "core": "http://192.168.1.20:6370", "fingerprint": "SHA256:x",
                          "bundle_sha256": "ab" * 32, "command": "bash x", "conflict": None}},
                      "setup": "var Auth = Object.assign(globalThis.Auth || {}, {});",
                      "script": """
      h.render(); await h.settle(); await h.settle(); h.rerender(); await h.settle();
      return { texts: h.text(), calls: """ + MUTATIONS + """ };
    """},
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    f = tmp_path_factory.mktemp("jsx") / "scenarios.json"
    f.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(f)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _t(d) -> str:
    texts = d["texts"] if "texts" in d else d
    return " | ".join(texts) if isinstance(texts, list) else str(texts)


def test_a_turn_from_another_mic_says_who_heard_it_best(driven):
    assert HEARD in _t(driven["turn_heard"])
    assert "Heard best by" not in _t(driven["turn_plain"])


def test_the_drawer_shows_the_latest_turns_mic(driven):
    assert HEARD in _t(driven["drawer_admin"])
    # A room with one satellite never fetches or shows it.
    assert "Heard best by" not in _t(driven["drawer_single"])


def test_an_old_phone_app_is_told_to_update(driven):
    assert "Update the app on this phone." in _t(driven["drawer_admin"])
    assert "Update the software on this satellite." in _t(driven["row_pi_old"])
    assert "Update" not in _t(driven["row_ok"])


def test_linked_rooms_are_listed_and_only_an_admin_changes_them(driven):
    admin = driven["drawer_admin"]
    assert "Rooms that can hear this one" in _t(admin)
    assert "Den" in _t(admin)
    assert "Add a room" in admin["buttons"]
    household = driven["drawer_household"]
    assert "Rooms that can hear this one" in _t(household)
    assert "Add a room" not in household["buttons"]
    assert "Add a satellite to the Kitchen" not in household["buttons"]


def test_an_admin_links_a_room(driven):
    d = driven["link_add"]
    assert "Porch" in d["options"] and "Den" not in d["options"] and "Kitchen" not in d["options"]
    assert d["calls"] == [["POST", "/api/rooms/kitchen/links", {"room_id": "porch"}]]
    assert any(c["name"] == "refresh" for c in d["fired"])


def test_removing_a_link_asks_first(driven):
    d = driven["link_remove"]
    assert d["asked"] and "Den" in d["asked"][0]
    assert d["calls"] == [["DELETE", "/api/rooms/kitchen/links/den", None]]


def test_add_a_satellite_hands_the_room_to_the_spare_card(driven):
    fired = driven["add_satellite"]["fired"]
    assert [c["name"] for c in fired] == ["onAddSatellite"]


def test_the_suggestion_links_or_is_dismissed(driven):
    d = driven["offer"]
    assert "The Den and the Kitchen often both answer the same wake word." in _t(d)
    assert d["calls"] == [
        ["POST", "/api/rooms/den/links", {"room_id": "kitchen"}],
        ["POST", "/api/rooms/den/links/kitchen/dismiss", {}],
    ]
    assert "Link them" not in driven["offer_household"]["buttons"]


def test_the_spare_card_asks_for_a_code_into_the_room(driven):
    d = driven["spare_in_room"]
    assert d["calls"] == [["POST", "/api/satellites/enrolment", {"in_room": "kitchen"}]]
    t = _t(d)
    assert "Adding a satellite to the Kitchen." in t
    assert "It joins the Kitchen as Kitchen 2. It connects as" in t
