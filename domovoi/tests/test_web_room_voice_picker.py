"""Settings → Satellites: the room drawer's voice picker (2026-10-17).

The drawer's read-only voice row became a picker
(POST /api/satellites/{room}/voice, admin): a Pi is sent the change while
online, a phone in room mode has it kept on the server (`voice_on_server`),
offline too, and may go back to the server's own voice. A voice that isn't
downloaded is listed and marked, and can't be picked. Without an admin
sign-in, or for an offline Pi, the row stays read-only.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel and a small stateful React). No DB; needs ``node``.
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
window.confirm = () => false;
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""

_SIGNED_OUT = "Auth = { status: {}, subscribe: () => () => {}, isLoggedIn: () => false, headers: () => ({}), modalOpen: false };"

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
await h.click((e) => w.__cls(e).includes('sat-card')); await w.__settle(h);
const sel = h.find((e) => e.type === 'select' && String(e.props['aria-label'] || '').includes('voice'));
const options = sel ? h.findAll((e) => e.type === 'option' && h.ancestors(e).includes(sel))
  .map((o) => ({ value: o.props.value, text: w.__deep(o).trim(), disabled: !!o.props.disabled })) : null;
const value = sel ? sel.props.value : null;
if (sel && __PICK__ !== null) { await h.change(sel, __PICK__); await w.__settle(h); }
const posts = h.calls.filter((c) => c.path.endsWith('/voice'));
return { value, options, posts, overview: h.text().join(' | ') };
"""

REGISTRY = [
    {"id": 1, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium",
     "is_default": True, "sample": "voice", "problem": None},
    {"id": 2, "name": "Norman", "engine": "piper", "model_ref": "en_US-norman-medium",
     "is_default": False, "sample": "voice", "problem": None},
]
SHIPPED = {"voices": [
    {"id": "en_US-kristin-medium", "name": "Kristin", "sex": "female"},
    {"id": "en_US-norman-medium", "name": "Norman", "sex": "male"},
    {"id": "en_US-linda-medium", "name": "Linda", "sex": "female"},
], "server_voice": "en_US-kristin-medium", "pronunciations": []}
SAMPLES = {"samples": {"en_US-kristin-medium": "voice", "en_US-norman-medium": "voice",
                       "en_US-linda-medium": "clip"}}


def _row(room: str, *, status="online", board=None, supports=None, voice=None, on_server=False) -> dict:
    return {
        "room_id": room, "status": status, "last_connected_at": "2026-10-17T08:00:00Z",
        "wifi": None, "now_playing": None, "active_session_id": None,
        "mpd_ports": {"control": 6650, "http": 8050}, "version": "abc1234", "full_duplex": False,
        "in_call_with": None, "voice": voice, "voice_on_server": on_server, "volume": 60,
        "pairing": {"paired": True, "paired_at": "2026-10-01T10:00:00Z", "last_seen_at": None},
        "sat_type": "voice", "mic_enabled": True, "room_label": None, "hardware": None,
        "adopted_at": None, "display": None, "capture_commands": False, "capture_since": None,
        "timers_own_only": False, "board": board, "supports": supports,
    }


PHONE = ["announce_after_session_end", "capture_control", "sounds", "speech_pause", "wake_models"]
CASES = {
    "phone": (_row("mobile", board="android_room", supports=PHONE, voice="Norman", on_server=True), "Kristin", True),
    "phone_back_to_server": (_row("mobile", board="android_room", supports=PHONE, voice="Norman", on_server=True), "", True),
    "phone_offline": (_row("mobile", status="offline", board="android_room", voice="Norman", on_server=True), None, True),
    "pi": (_row("kitchen", board="raspberry_pi_zero_2_w"), "Norman", True),
    "pi_offline": (_row("kitchen", status="offline"), None, True),
    "signed_out": (_row("mobile", board="android_room", supports=PHONE, voice="Norman", on_server=True), None, False),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scenarios = {}
    for name, (row, pick, admin) in CASES.items():
        room = row["room_id"]
        scenarios[name] = {
            "files": FILES, "component": "SatellitesPanel",
            "setup": _SETUP + ("" if admin else _SIGNED_OUT),
            "api": {
                "GET /api/satellites": [row],
                "GET /api/satellites/approvals": [],
                "GET /api/satellites/pending": [],
                "GET /api/config/version": {"sha": "abc1234"},
                "GET /api/capabilities": {"host_kind": "linux"},
                "GET /api/voices": REGISTRY,
                "GET /api/voice": SHIPPED,
                "GET /api/voice/samples": SAMPLES,
                f"POST /api/satellites/{room}/voice": {
                    "room_id": room, "voice": pick or None,
                    "kept_on": "server" if row["voice_on_server"] else "device", "said": True},
            },
            "script": _SCRIPT.replace("__PICK__", json.dumps(pick)),
        }
    spec = tmp_path_factory.mktemp("room-voice") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_phone_room_picks_any_downloaded_voice_and_marks_the_rest(driven):
    r = driven["phone"]
    assert r["value"] == "Norman", "the current voice is shown"
    assert r["options"] == [
        {"value": "", "text": "the server’s voice (Kristin)", "disabled": False},
        {"value": "Kristin", "text": "Kristin", "disabled": False},
        {"value": "Norman", "text": "Norman", "disabled": False},
        {"value": "Linda", "text": "Linda · not downloaded yet", "disabled": True},
    ]
    assert r["posts"] == [{"method": "POST", "path": "/api/satellites/mobile/voice", "body": {"voice": "Kristin"}}]


def test_a_phone_room_can_go_back_to_the_servers_voice(driven):
    assert driven["phone_back_to_server"]["posts"][0]["body"] == {"voice": None}


def test_an_offline_phone_room_still_has_the_picker(driven):
    r = driven["phone_offline"]
    assert r["value"] == "Norman" and r["options"] is not None


def test_a_pi_is_offered_named_voices_only(driven):
    r = driven["pi"]
    assert r["value"] == "", "a Pi on the server's voice"
    assert r["options"][0] == {"value": "", "text": "the server’s voice (Kristin)", "disabled": True}
    assert r["posts"] == [{"method": "POST", "path": "/api/satellites/kitchen/voice", "body": {"voice": "Norman"}}]


def test_an_offline_pi_and_a_signed_out_browser_get_no_dead_picker(driven):
    for case in ("pi_offline", "signed_out"):
        r = driven[case]
        assert r["options"] is None, case
        assert r["posts"] == []
    assert "Norman" in driven["signed_out"]["overview"]
