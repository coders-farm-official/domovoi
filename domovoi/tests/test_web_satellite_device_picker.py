"""The room drawer's device picker (1.5.0): a room with several satellites
checks each one on its own. The Diagnostics tab shows a picker when the
room has more than one device, every check goes to the chosen satellite,
and the support switch follows the same choice; a single device, or a row
from an older server (no ``devices``), shows no picker and addresses the
room as before. The web half (``devices`` on GET /api/satellites) is
checked against ``_satellite_for`` directly.

Driven through domovoi/tests/jsx_interact_harness.js like
test_web_satellite_diagnostics.py. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_satellite_diagnostics import _RESULTS
from domovoi.tests.test_web_satellite_supports import FILES, _SETUP, _row

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

_AUTH = r"""
globalThis.Auth = {
  status: { authenticated: true }, isLoggedIn: () => true,
  subscribe: () => () => {}, refreshStatus: () => Promise.resolve({ authenticated: true }),
};
// The dashboard's window is its global scope; this sandbox keeps them apart,
// so the drawer's bare SatSupportControl reads what support_session.jsx put
// on window.
Object.defineProperty(globalThis, 'SatSupportControl',
                      { get: () => window.SatSupportControl, configurable: true });
"""

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
await h.click((e) => w.__cls(e).includes('sat-card')); await w.__settle(h);
await h.click({ type: 'button', title: 'Diagnostics' }); await w.__settle(h);
const picker = h.find({ type: 'select' });
const chosen = picker ? picker.props.value : null;
const options = h.findAll({ type: 'option' }).map((o) => o.props.value);
await h.click((e) => e.type === 'button' && w.__deep(e).includes('Run all')); await w.__settle(h);
let other = null;
if (picker) {
  await h.change({ type: 'select' }, 'office'); await w.__settle(h);
  other = h.text().join(' | ');
  await h.change({ type: 'select' }, 'office_b'); await w.__settle(h);
}
await h.click({ type: 'button', title: 'Settings' }); await w.__settle(h);
const settingsPicker = !!h.find({ type: 'select' });
const isBox = (e) => e.type === 'input' && e.props.type === 'checkbox'
  && h.inside(e, (a) => w.__deep(a).includes('Allow support sessions'));
const box = h.find(isBox);
if (box) { await h.change(isBox, true); await w.__settle(h); }
return { picker: !!picker, chosen, options, other, settingsPicker, box: !!box,
         calls: h.calls.map((c) => `${c.method} ${c.path}`) };
"""


def _devices(*devs: dict) -> list[dict]:
    base = {"name": None, "connected": True, "diagnostics": False, "support_allowed": False,
            "support_shell": False, "support_key_pinned": True, "support_key_changed": False}
    return [{**base, **d} for d in devs]


def _scenario(row: dict) -> dict:
    api = {
        "GET /api/satellites": [row],
        "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [],
        "GET /api/config/version": {"sha": "44f0fff"},
        "GET /api/capabilities": {"host_kind": "linux"},
    }
    for sid in ("office", "office_b"):
        api[f"POST /api/satellites/{sid}/diagnostics"] = {**_RESULTS, "room_id": sid}
        api[f"POST /api/satellites/{sid}/support/allow"] = {"satellite_id": sid, "allowed": True}
    return {
        "files": [*FILES, "web/static/support_session.jsx"], "component": "SatellitesPanel",
        "setup": _SETUP + _AUTH, "api": api, "script": _SCRIPT,
    }


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    shared = _row("office", board=None, supports=None)
    shared["diagnostics"] = True
    # The office's speaker (its id is the room's) offers no checks; the
    # second satellite does.
    shared["devices"] = _devices({"satellite_id": "office", "name": "Office speaker"},
                                 {"satellite_id": "office_b", "name": "Office desk",
                                  "diagnostics": True, "support_shell": True})
    single = _row("office", board=None, supports=None)
    single["diagnostics"] = True
    single["devices"] = _devices({"satellite_id": "office", "diagnostics": True})
    older = _row("office", board=None, supports=None)
    older["diagnostics"] = True  # an older server: no "devices"
    for row in (single, older):
        row["support_key_pinned"] = True  # the switch asks nothing first
    scenarios = {"shared": _scenario(shared), "single": _scenario(single),
                 "older": _scenario(older)}
    spec = tmp_path_factory.mktemp("sat-dev") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_shared_room_picks_the_device_that_offers_checks(driven):
    r = driven["shared"]
    assert r["picker"] is True and r["options"] == ["office", "office_b"]
    assert r["chosen"] == "office_b"
    assert "POST /api/satellites/office_b/diagnostics" in r["calls"]
    assert "POST /api/satellites/office/diagnostics" not in r["calls"]
    assert "didn’t offer diagnostics" in r["other"]


def test_the_support_switch_follows_the_chosen_device(driven):
    r = driven["shared"]
    assert r["settingsPicker"] is True and r["box"] is True
    assert "POST /api/satellites/office_b/support/allow" in r["calls"]
    assert "POST /api/satellites/office/support/allow" not in r["calls"]


@pytest.mark.parametrize("case", ["single", "older"])
def test_one_device_or_an_older_server_has_no_picker(driven, case):
    r = driven[case]
    assert r["picker"] is False and r["settingsPicker"] is False
    assert "POST /api/satellites/office/diagnostics" in r["calls"]
    assert "POST /api/satellites/office/support/allow" in r["calls"]


# --- the web half: GET /api/satellites carries the room's devices ----------


async def test_the_list_names_each_device_with_its_own_facts(monkeypatch):
    from web.backend.api import satellites as web_sats

    async def no_np(*a, **kw):
        return None

    monkeypatch.setattr(web_sats, "_now_playing_for", no_np)
    row = {"room_id": "office", "control_port": 6650, "http_port": 8050, "last_connected_at": None}
    meta = {"office": {"room_id": "office", "name": "Office speaker", "support_allowed": True},
            "office_b": {"room_id": "office", "name": "Office desk", "support_key_pinned": True},
            "office_c": {"room_id": "office"}, "den": {"room_id": "den"}}
    snap = {"active_rooms": ["office", "den"], "active_satellites": ["den", "office", "office_b"],
            "satellite_diagnostics": ["office_b"], "satellite_support_shell": ["office_b"],
            "satellite_support_key_changed": ["office"]}
    sat = await web_sats._satellite_for(row, snap, {}, meta, {})
    devs = {d.satellite_id: d for d in sat.devices}
    assert list(devs) == ["office", "office_b", "office_c"]
    assert sat.diagnostics is True  # the room offers the tab: one device has checks
    assert (devs["office"].connected, devs["office"].diagnostics) == (True, False)
    assert devs["office"].support_allowed and devs["office"].support_key_changed
    assert devs["office"].name == "Office speaker"
    b = devs["office_b"]
    assert b.connected and b.diagnostics and b.support_shell and b.support_key_pinned
    assert not b.support_allowed and not b.support_key_changed
    assert devs["office_c"].connected is False and devs["office_c"].diagnostics is False
    # Offline, nothing is live; the stored switch stays.
    off = await web_sats._satellite_for(row, {**snap, "active_rooms": ["den"],
                                              "active_satellites": ["den"]}, {}, meta, {})
    assert off.diagnostics is False
    assert not any(d.connected or d.diagnostics for d in off.devices)
    assert {d.satellite_id: d.support_allowed for d in off.devices}["office"] is True


async def test_an_older_servers_snapshot_counts_the_rooms_own_device(monkeypatch):
    from web.backend.api import satellites as web_sats

    async def no_np(*a, **kw):
        return None

    monkeypatch.setattr(web_sats, "_now_playing_for", no_np)
    row = {"room_id": "office", "control_port": 6650, "http_port": 8050, "last_connected_at": None}
    meta = {"office": {"room_id": "office"}}
    snap = {"active_rooms": ["office"], "satellite_diagnostics": ["office"]}  # no active_satellites
    sat = await web_sats._satellite_for(row, snap, {}, meta, {})
    assert sat.diagnostics is True
    assert [(d.satellite_id, d.connected, d.diagnostics) for d in sat.devices] == [
        ("office", True, True)]
