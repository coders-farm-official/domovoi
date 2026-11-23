"""Settings → Rooms with several satellites per room (1.5.0, slice 8).

The room drawer lists each room's satellites and gives the signed-in admin
the controls to rename, move, create, delete and merge. Pinned here:

* the admin controls show only to a signed-in admin, the list to everyone;
* a move calls the right endpoint with exactly one of room_id/new_room_name;
* a merge is OFFERED from merge_offers and never runs until the admin
  clicks (and confirms);
* approving a new satellite asks "Which room is it in?", pre-filled from
  requested_room, and sends the choice with the code.

Driven through domovoi/tests/jsx_interact_harness.js. No DB; needs node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx",
         "web/static/satellites.jsx", "web/static/settings.jsx"]

_SETUP = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
const __si = setInterval;
setInterval = (fn, ms, ...a) => { const t = __si(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.addEventListener = () => {}; window.removeEventListener = () => {};
window.__asked = [];
window.confirm = (m) => { window.__asked.push(m); return window.__confirmAnswer !== false; };
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
const __admin = %ADMIN%;
Auth = { status: {}, subscribe: () => () => {}, isLoggedIn: () => __admin, headers: () => ({}), modalOpen: false };
"""

_ROOMS = {
    "rooms": [
        {"room_id": "kitchen", "name": "Kitchen", "shared": True, "linked_rooms": [],
         "satellites": [
             {"satellite_id": "kitchen", "name": None, "sat_type": "voice", "board": None,
              "paired": True, "connected": True, "speaks_for_room": True},
             {"satellite_id": "kitchen_2", "name": "Kitchen phone", "sat_type": "voice",
              "board": "android_room", "paired": True, "connected": False,
              "speaks_for_room": False}]},
        {"room_id": "den", "name": "Den", "shared": False, "linked_rooms": [], "satellites": []},
        {"room_id": "kitchen_3", "name": "kitchen", "shared": False, "linked_rooms": [],
         "satellites": []},
    ],
    "merge_offers": [{"name": "Kitchen", "rooms": ["kitchen", "kitchen_3"]}],
}


def _sat(room: str) -> dict:
    return {
        "room_id": room, "status": "online", "last_connected_at": "2026-11-22T15:00:00Z",
        "wifi": {"rx_mbits": 55.0, "tx_mbits": 50.0, "ssid": "home"}, "now_playing": None,
        "active_session_id": None, "mpd_ports": {"control": 6650, "http": 8050},
        "version": "abc1234", "full_duplex": False, "in_call_with": None, "voice": None,
        "volume": 60, "pairing": {"paired": True, "paired_at": "2026-10-01T10:00:00Z",
                                  "last_seen_at": "2026-11-22T15:00:00Z"},
        "sat_type": "voice", "mic_enabled": True, "room_label": None,
        "hardware": "Raspberry Pi Zero 2 W Rev 1.0", "adopted_at": None, "display": None,
        "capture_commands": False, "capture_since": None, "timers_own_only": False,
        "board": None, "supports": None,
    }


def _api(approvals=None) -> dict:
    return {
        "GET /api/satellites": [_sat("kitchen"), _sat("kitchen_2")],
        "GET /api/rooms": _ROOMS,
        "GET /api/satellites/approvals": approvals or [],
        "GET /api/satellites/pending": [],
        "GET /api/config/version": {"sha": "44f0fff"},
        "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/rooms/kitchen/merge": {"room_id": "kitchen", "merged": ["kitchen_3"]},
        "POST /api/satellites/kitchen_2/room": {"moved": True, "learned_samples_dropped": 0},
        "PATCH /api/satellites/kitchen_2/name": {"satellite_id": "kitchen_2", "name": "Pantry phone"},
        "POST /api/satellites/approvals/kitchen_4/approve": {"approved": True},
    }


def _scenario(script: str, *, admin: bool = True, approvals=None) -> dict:
    return {"files": FILES, "component": "SatellitesPanel", "api": _api(approvals),
            "setup": _SETUP.replace("%ADMIN%", "true" if admin else "false"),
            "script": "const w = h.global('window'); h.render(); await w.__settle(h);\n" + script}


_OPEN_KITCHEN = """
await h.click((e) => e.type === 'button' && w.__cls(e).includes('room-row') && w.__deep(e).includes('Kitchen')); await w.__settle(h);
const labelsNow = () => h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim());
const moveBtn = () => h.findAll({ type: 'button' }).filter((b) => w.__deep(b).includes('Move to another room'));
const writes = () => h.calls.filter((c) => c.method !== 'GET');
"""

_APPROVE_SEL = "(e) => e.type === 'select' && e.props['aria-label'] === 'which room is it in'"
_CODE = "{ type: 'input', placeholder: '000000' }"

_SCRIPTS = {
    "admin_list": _OPEN_KITCHEN + """
const all = h.text();
return { text: all.join(' | '), labels: all };
""",
    "guest_list": _OPEN_KITCHEN + """
const all = h.text();
return { text: all.join(' | '), labels: all, offer: all.some((t) => t.includes('Are they the same room')) };
""",
    "offer_shown_not_applied": """
const offer = h.find((e) => w.__cls(e).includes('room-merge-offer'));
return { offer: offer ? w.__deep(offer) : null, calls: h.calls.filter((c) => c.method !== 'GET') };
""",
    "merge_confirmed": """
await h.click({ type: 'button', text: 'Merge into one' }); await w.__settle(h);
return { calls: h.calls.filter((c) => c.method !== 'GET'), asked: w.__asked };
""",
    "merge_declined": """
w.__confirmAnswer = false;
await h.click({ type: 'button', text: 'Merge into one' }); await w.__settle(h);
return { calls: h.calls.filter((c) => c.method !== 'GET'), asked: w.__asked };
""",
    "move_existing": _OPEN_KITCHEN + """
await h.click({ type: 'button', text: 'Move to another room', nth: 1 }); await w.__settle(h);
const before = writes().length;
await h.change((e) => e.type === 'select' && e.props['aria-label'] === 'move to room', 'den'); await w.__settle(h);
await h.click((e) => e.type === 'button' && w.__deep(e).trim() === 'Move'); await w.__settle(h);
return { before, calls: writes() };
""",
    "move_new": _OPEN_KITCHEN + """
await h.click({ type: 'button', text: 'Move to another room', nth: 1 }); await w.__settle(h);
await h.change((e) => e.type === 'select' && e.props['aria-label'] === 'move to room', '__new__'); await w.__settle(h);
const blocked = h.findAll({ type: 'button' }).filter((b) => w.__deep(b).trim() === 'Move').map((b) => !!b.props.disabled);
await h.type((e) => e.type === 'input' && e.props['aria-label'] === 'new room name', 'Pantry'); await w.__settle(h);
await h.click((e) => e.type === 'button' && w.__deep(e).trim() === 'Move'); await w.__settle(h);
return { blocked, calls: writes() };
""",
    "rename_satellite": _OPEN_KITCHEN + """
await h.click({ type: 'button', text: 'Rename', nth: 1 }); await w.__settle(h);
await h.type((e) => e.type === 'input' && e.props['aria-label'] === 'satellite name', 'Pantry phone'); await w.__settle(h);
await h.click((e) => e.type === 'button' && w.__deep(e).trim() === 'Save'); await w.__settle(h);
return { calls: writes() };
""",
    "approval_card": f"""
const sel = h.find({_APPROVE_SEL});
return {{ value: sel ? sel.props.value : null, text: h.text().join(' | '),
         options: sel ? h.findAll({{ type: 'option' }}).map((o) => [o.props.value, w.__deep(o)]) : [] }};
""",
    "approve": f"""
await h.type({_CODE}, '123456'); await w.__settle(h);
await h.click({{ type: 'button', text: 'approve' }}); await w.__settle(h);
return {{ calls: h.calls.filter((c) => c.method === 'POST') }};
""",
    "approve_new_room": f"""
await h.change({_APPROVE_SEL}, '__new__'); await w.__settle(h);
await h.type({_CODE}, '123456'); await w.__settle(h);
const blocked = h.findAll({{ type: 'button' }}).filter((b) => w.__deep(b).trim() === 'approve').map((b) => !!b.props.disabled);
await h.type((e) => e.type === 'input' && e.props['aria-label'] === 'new room name', 'Back porch'); await w.__settle(h);
await h.click({{ type: 'button', text: 'approve' }}); await w.__settle(h);
return {{ blocked, calls: h.calls.filter((c) => c.method === 'POST') }};
""",
}


def _approval(**kw) -> dict:
    base = {"satellite_id": "kitchen_4", "room_id": "kitchen_4", "requested_room": "kitchen",
            "requested_room_name": "Kitchen", "has_code": True, "proof": False,
            "waiting_for_device": False, "code_rejected": False, "attempts": 1,
            "mac": None, "board": "android_room", "sat_type": "voice"}
    base.update(kw)
    return base


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    s = _SCRIPTS
    one = [_approval()]
    sc = {
        "admin_list": _scenario(s["admin_list"]),
        "guest_list": _scenario(s["guest_list"], admin=False),
        "offer_shown_not_applied": _scenario(s["offer_shown_not_applied"]),
        "merge_confirmed": _scenario(s["merge_confirmed"]),
        "merge_declined": _scenario(s["merge_declined"]),
        "move_existing": _scenario(s["move_existing"]),
        "move_new": _scenario(s["move_new"]),
        "rename_satellite": _scenario(s["rename_satellite"]),
        "approval_card": _scenario(s["approval_card"], approvals=one),
        "approval_unknown_room": _scenario(
            s["approval_card"], approvals=[_approval(requested_room="porch", requested_room_name=None)]),
        "approve_into_room": _scenario(s["approve"], approvals=one),
        "approve_new_room": _scenario(s["approve_new_room"], approvals=one),
        "approve_own_room": _scenario(
            s["approve"], approvals=[_approval(requested_room=None, requested_room_name=None)]),
    }
    spec = tmp_path_factory.mktemp("rooms-drawer") / "scenarios.json"
    spec.write_text(json.dumps(sc), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_admin_sees_satellites_and_controls(driven):
    d = driven["admin_list"]
    assert "Satellite for the Kitchen" in d["text"]       # an unnamed one reads gently
    assert "Kitchen phone" in d["text"] and "Android room" in d["text"]
    assert "speaks for the room" in d["text"]
    assert "not connected" in d["text"]
    assert "Rename" in d["labels"] and "Move to another room" in d["labels"]


def test_guest_sees_the_list_but_no_controls_or_offer(driven):
    d = driven["guest_list"]
    assert "Kitchen phone" in d["text"]
    assert not any(x in d["labels"] for x in ("Rename", "Move to another room", "Add a room"))
    assert d["offer"] is False


def test_a_merge_is_offered_and_never_applied_on_its_own(driven):
    d = driven["offer_shown_not_applied"]
    assert d["offer"] and "Are they the same room?" in d["offer"]
    assert d["calls"] == []


def test_a_merge_runs_only_after_the_admin_clicks_and_confirms(driven):
    ok = driven["merge_confirmed"]
    assert ok["asked"], "the admin is asked before a merge"
    assert ok["calls"] == [{"method": "POST", "path": "/api/rooms/kitchen/merge",
                            "body": {"rooms": ["kitchen_3"]}}]
    assert driven["merge_declined"]["calls"] == []


def test_moving_a_satellite_posts_its_room(driven):
    d = driven["move_existing"]
    assert d["before"] == 0, "choosing a room moves nothing until Move is pressed"
    assert d["calls"] == [{"method": "POST", "path": "/api/satellites/kitchen_2/room",
                           "body": {"room_id": "den"}}]


def test_moving_to_a_new_room_needs_a_name_and_sends_only_it(driven):
    d = driven["move_new"]
    assert d["blocked"] == [True]
    assert d["calls"] == [{"method": "POST", "path": "/api/satellites/kitchen_2/room",
                           "body": {"new_room_name": "Pantry"}}]


def test_renaming_a_satellite_patches_its_name(driven):
    assert driven["rename_satellite"]["calls"] == [
        {"method": "PATCH", "path": "/api/satellites/kitchen_2/name",
         "body": {"name": "Pantry phone"}}]


def test_the_approval_asks_which_room_and_preselects_the_request(driven):
    d = driven["approval_card"]
    assert "Which room is it in?" in d["text"]
    assert d["value"] == "kitchen"
    assert ["__new__", "a new room…"] in d["options"]
    # a requested room that does not exist falls back to "a room of its own"
    assert driven["approval_unknown_room"]["value"] == ""


def test_approving_sends_the_room_with_the_code(driven):
    assert driven["approve_into_room"]["calls"] == [
        {"method": "POST", "path": "/api/satellites/approvals/kitchen_4/approve",
         "body": {"code": "123456", "room_id": "kitchen"}}]
    assert driven["approve_own_room"]["calls"][0]["body"] == {"code": "123456"}


def test_approving_into_a_new_room_needs_its_name(driven):
    d = driven["approve_new_room"]
    assert d["blocked"] == [True]
    assert d["calls"][0]["body"] == {"code": "123456", "new_room_name": "Back porch"}
