"""Settings → Satellites with spare devices (S0, 2026-10-12): the dashboard.

`/api/satellites` rows gained `board` and `supports`. The gate is "Pi cards
look exactly as before": a Pi row — `supports` null, and perhaps the board
USB adoption recorded — must render the very same card and drawer as the
row shape from before S0, which had neither key. A row that declared a list
gets a board badge and has what it left out greyed with a reason.

Rows come from the shared fixtures (docs/uml/satellite-fixtures.json).
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
FIXTURES = json.loads(
    (REPO_ROOT / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
DEVICES = FIXTURES["devices"]

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

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const card = h.find((e) => w.__cls(e).includes('sat-card'));
const cardText = w.__deep(card);
const badge = (h.find((e) => w.__cls(e).includes('sat-board-badge')) || null);
await h.click((e) => w.__cls(e).includes('sat-card')); await w.__settle(h);
const buttons = h.findAll({ type: 'button' })
  .filter((b) => h.ancestors(b).some((a) => a.type === 'aside'))
  .map((b) => ({ text: w.__deep(b).trim(), disabled: !!b.props.disabled }))
  .filter((b) => b.text);
const overview = h.text().join(' | ');
await h.click({ type: 'button', title: 'Settings' }); await w.__settle(h);
const settings = h.text().join(' | ');
return { cardText, badge: badge ? w.__deep(badge).trim() : null, buttons, overview, settings };
"""


def _row(room: str, *, board=None, supports=None, full_duplex=False, legacy=False) -> dict:
    row = {
        "room_id": room, "status": "online", "last_connected_at": "2026-10-12T15:00:00Z",
        "wifi": {"rx_mbits": 55.0, "tx_mbits": 50.0, "ssid": "home"}, "now_playing": None,
        "active_session_id": None, "mpd_ports": {"control": 6650, "http": 8050},
        "version": "abc1234", "full_duplex": full_duplex, "in_call_with": None, "voice": None,
        "volume": 60, "pairing": {"paired": True, "paired_at": "2026-10-01T10:00:00Z",
                                  "last_seen_at": "2026-10-12T15:00:00Z"},
        "sat_type": "voice", "mic_enabled": True, "room_label": None,
        "hardware": "Raspberry Pi Zero 2 W Rev 1.0", "adopted_at": None, "display": None,
        "capture_commands": False, "capture_since": None, "timers_own_only": False,
    }
    if not legacy:  # the row shape since S0
        row["board"] = board
        row["supports"] = supports
    return row


def _scenario(device: str, row: dict, sample: dict | None = None) -> dict:
    room = row["room_id"]
    return {
        "files": FILES, "component": "SatellitesPanel", "setup": _SETUP,
        "api": {
            "GET /api/satellites": [row],
            "GET /api/satellites/approvals": [],
            "GET /api/satellites/pending": [],
            "GET /api/config/version": {"sha": "44f0fff"},
            "GET /api/capabilities": {"host_kind": "linux"},
            f"GET /api/satellites/{room}/health": {
                "room_id": room, "connected": True, "last_outage": None, "outages": [],
                "history": None,
                "latest": {"received_at": "2026-10-12T15:00:00Z",
                           "sample": sample or DEVICES[device]["health"]["stored"]},
            },
        },
        "script": _SCRIPT,
    }


def _rows() -> dict[str, dict]:
    out = {}
    for name, d in DEVICES.items():
        room = d["hello"]["room_id"]
        fd = "full_duplex" in d["expect"]["allowed"]
        board = d["expect"]["board"] or ("raspberry_pi_zero_2_w" if name.startswith("pi_") else None)
        out[name] = _row(room, board=board, supports=d["expect"]["supports_shown"], full_duplex=fd)
        if name.startswith("pi_"):
            out[name + "__before_s0"] = _row(room, full_duplex=fd, legacy=True)
    return out


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    rows = _rows()
    scenarios = {name: _scenario(name.split("__")[0], row) for name, row in rows.items()}
    # S1: the battery as laptops and phones send it, and S0's draft object.
    pc = DEVICES["linux_pc"]["health"]["stored"]
    for name, battery in _BATTERIES.items():
        sample = {k: v for k, v in pc.items() if not k.startswith("battery")}
        sample.update(battery)
        scenarios[name] = _scenario("linux_pc", rows["linux_pc"], sample)
    spec = tmp_path_factory.mktemp("sat-supports") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


_BATTERIES = {
    "battery__full_on_charger": {"battery_percent": 100, "battery_charging": True},
    "battery__low_on_battery": {"battery_percent": 15, "battery_charging": False},
    "battery__s0_draft_object": {"battery": {"percent": 70, "charging": False, "on_power": False}},
    "battery__none": {},
}


def _button(result: dict, label: str) -> dict:
    return next(b for b in result["buttons"] if label in b["text"])


@pytest.mark.parametrize("pi", ["pi_2mic_hat", "pi_xvf3800"])
def test_a_pi_row_renders_exactly_as_the_row_from_before_s0(driven, pi):
    now, before = driven[pi], driven[pi + "__before_s0"]
    assert now == before
    assert now["badge"] is None, "no board badge on a Pi, even with an adopted board"
    assert not _button(now, "Restart satellite")["disabled"]
    assert "settings stay on the device" not in now["settings"]
    assert "battery" not in now["overview"] and "mic blocked" not in now["overview"]


def test_a_linux_pc_shows_its_board_and_keeps_what_it_declared(driven):
    r = driven["linux_pc"]
    assert r["badge"] == "Linux PC"
    assert not _button(r, "Restart satellite")["disabled"]
    assert not _button(r, "Upgrade satellite")["disabled"]
    assert "battery" in r["overview"] and "81%" in r["overview"]
    assert "on the charger" in r["overview"]
    assert "settings stay on the device" not in r["settings"]


def test_a_battery_held_full_on_the_charger_asks_for_a_charge_limit(driven):
    assert "100%" in driven["battery__full_on_charger"]["overview"]
    assert "set a charge limit" in driven["battery__full_on_charger"]["overview"]
    low = driven["battery__low_on_battery"]["overview"]
    assert "15%" in low and "on battery" in low and "charge limit" not in low


def test_s0_s_draft_battery_object_still_shows(driven):
    r = driven["battery__s0_draft_object"]["overview"]
    assert "70%" in r and "on battery" in r


def test_a_spare_device_without_a_battery_shows_none(driven):
    assert "battery" not in driven["battery__none"]["overview"]


def test_an_android_room_greys_what_it_left_out_with_a_reason(driven):
    r = driven["android_room"]
    assert r["badge"] == "Android room"
    assert _button(r, "Restart satellite")["disabled"]
    assert _button(r, "Upgrade satellite")["disabled"]
    assert not _button(r, "Reset pairing")["disabled"], "pairing is the core's, always offered"
    assert "this Android room restarts and updates itself" in r["overview"]
    assert "mic blocked" in r["overview"] and "64%" in r["overview"]
    assert "settings stay on the device" in r["settings"]
