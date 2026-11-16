"""People → a person's profile: the header and "rooms that have learned
this voice" (owner device check, 1.3.0).

* The header says where and when the person was last heard: "last heard
  in" (the room of their newest identified turn, "the phone" for a
  phone's chat mic, "chat" for a turn with no room, "—" before any) and
  "last heard" — no more voice profile / session / turn counts.
* The voice-rooms chips say which rooms have LEARNED the voice (a sample
  recorded there), never that a room doesn't know the person: every room
  compares against all of their samples. A room that had just named the
  owner read "not yet", which looked like it couldn't.

Driven through domovoi/tests/jsx_interact_harness.js. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/people.jsx"]

ALEX = {"id": 3, "name": "Alex", "device_count": 0, "created_at": "2026-11-01T00:00:00Z",
        "last_seen_at": "2026-11-01T00:00:00Z", "voice_profile_count": 2,
        "presence_tier": "low", "namesakes": 0}

SETUP = (
    "ClaimKey = { get: () => 'k', set: () => {} };"
    "DeviceIdentity.owner = () => null;"
    "DeviceIdentity.subscribeOwner = () => () => {};"
    "DeviceIdentity.sharedScreen = () => false;"
    "DeviceIdentity.canRegister = () => true;"
    "Auth.isLoggedIn = () => false;"
    "Auth.status = { setup_complete: true };"
)

_SCRIPT = r"""
  h.render();
  return { text: h.text().join(' | ') };
"""


def _profile(last_turn):
    return {
        "files": FILES, "component": "ProfileTab",
        "props": {"person": ALEX, "lastTurn": last_turn, "samples": [],
                  "voiceRooms": [], "voiceMatches": [], "voiceId": {"ready": True},
                  "listens": {"data": None}},
        "fnProps": ["fire", "onDeleteSample", "onForget", "onRenamed", "onDeviceChanged"],
        "setup": SETUP, "api": {}, "script": _SCRIPT,
    }


def _rooms(rooms):
    return {
        "files": FILES, "component": "PersonVoiceRooms",
        "props": {"person": ALEX, "rooms": rooms, "voiceId": {"ready": True}},
        "setup": SETUP, "api": {}, "script": _SCRIPT,
    }


SCENARIOS = {
    "room": _profile({"id": 9, "at": "2026-11-01T00:00:00Z", "room_id": "dining-room"}),
    "phone": _profile({"id": 9, "at": "2026-11-01T00:00:00Z", "room_id": "phone-android-1a2b"}),
    "chat": _profile({"id": 9, "at": "2026-11-01T00:00:00Z", "room_id": None}),
    "never": _profile(None),
    "rooms_mixed": _rooms([{"room_id": "garage", "current": True},
                           {"room_id": "dining-room", "current": False}]),
    "rooms_all": _rooms([{"room_id": "garage", "current": True}]),
}


@pytest.fixture(scope="module")
def ui(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the dashboard's JSX"
    spec = tmp_path_factory.mktemp("ph") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_header_says_where_and_when_they_were_last_heard(ui) -> None:
    text = ui["room"]["text"]
    assert "last heard in | dining room" in text
    assert "last heard |" in text and "presence: low" in text
    # The counts are gone: the tabs and the samples list say them.
    for gone in ("voice profiles", "conversation_log rows", "from the phone", "recent"):
        assert gone not in text, gone
    assert text.index("last heard in") < text.index("presence: low")


def test_the_phone_chat_and_never_heard(ui) -> None:
    assert "last heard in | the phone" in ui["phone"]["text"]
    assert "last heard in | chat" in ui["chat"]["text"]
    assert "last heard in | —" in ui["never"]["text"]


def test_rooms_say_learned_not_whether_they_know_the_voice(ui) -> None:
    text = ui["rooms_mixed"]["text"]
    assert "rooms that have learned this voice" in text
    assert "know this voice" not in text
    assert "garage · learned" in text
    assert "dining-room · not yet learned" in text
    assert "Every room can recognize Alex from any sample." in text
    assert ("A room recognizes a voice more reliably once it has learned it there: "
            "in a room marked “not yet learned”, Alex can say “my name is Alex” and answer yes."
            ) in " ".join(text.split())
    # Every room has learned it: no help line.
    assert "Every room can recognize" not in ui["rooms_all"]["text"]
    assert "garage · learned" in ui["rooms_all"]["text"]
