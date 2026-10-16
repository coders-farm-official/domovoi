"""The dashboard while the core doesn't answer (incident 2026-10-14).

On 2026-10-14 the core froze; the dashboard stayed up and showed four
satellite cards reading "offline · 19d ago" (mpd_rooms.last_connected_at,
which changes only when a room's MPD row is allocated, never at a
connect), the Version card a dash, and nothing to press. Now:

* Settings → Satellites says the core isn't answering so satellites can't
  connect, cards say "can't connect" and when each room was LAST HEARD
  FROM (``last_seen_at``: its newest health sample or paired hello), and
  an admin gets "Restart the core" (CoreRestart, components.jsx);
* the Version card, whose version read the web can't answer (502), says
  the same and offers the same, and shows the last restart for freezing;
* Home's "isn't answering" row carries the restart for an admin;
* where the web can't restart it: the reason and the command by hand;
* nobody without the admin sign-in is offered the restart.

Driven through domovoi/tests/jsx_interact_harness.js. No DB; needs node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
SAT_FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx",
             "web/static/satellites.jsx", "web/static/settings.jsx"]
HOME_FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx",
              "web/static/satellites.jsx", "web/static/home.jsx"]

NOW = datetime.now(timezone.utc)
HEARD = (NOW - timedelta(minutes=12)).isoformat()
ALLOCATED = (NOW - timedelta(days=19)).isoformat()

_SETUP = r"""
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""
_HOUSEHOLD = _SETUP + r"""
Auth = { status: { authenticated: false, setup_complete: true }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false };
"""
# The core comes back at once when asked: the wait after a press ends on
# the first /api/health.
_ADMIN_PRESS = _SETUP + r"""
fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ domovoi_reachable: true }) });
"""

_SAT_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const cards = h.findAll((e) => w.__cls(e).includes('sat-card')).map((c) => w.__deep(c));
const banner = h.find((e) => w.__cls(e).includes('sat-core-down'));
const buttons = h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean);
return { cards, banner: banner ? w.__deep(banner) : null, buttons, text: h.text().join(' | '),
         hooks: h.hookCalls };
"""

_PRESS_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
await h.click({ type: 'button', text: 'Restart the core' });
await new Promise((r) => setTimeout(r, 2600));
await w.__settle(h, 8);
return { calls: h.calls.map((c) => `${c.method} ${c.path}`), text: h.text().join(' | ') };
"""

_TEXT_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const buttons = h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean);
return { text: h.text().join(' | '), buttons };
"""


def _row(room: str, status: str = "offline") -> dict:
    return {
        "room_id": room, "status": status, "last_connected_at": ALLOCATED, "last_seen_at": HEARD,
        "wifi": None, "now_playing": None, "active_session_id": None,
        "mpd_ports": {"control": 6650, "http": 8050}, "version": "abc1234", "full_duplex": False,
        "in_call_with": None, "voice": None, "volume": 60,
        "pairing": {"paired": True, "paired_at": ALLOCATED, "last_seen_at": HEARD},
        "sat_type": "voice", "mic_enabled": True, "room_label": None, "hardware": None,
        "adopted_at": None, "display": None, "capture_commands": False, "capture_since": None,
        "timers_own_only": False, "board": None, "supports": None,
    }


ROOMS = [_row(r) for r in ("dining_room", "garage", "office", "master")]
CAN = {"answering": False, "capable": True, "how": "signal", "why": None, "starting": False,
       "manual_command": "sudo systemctl restart domovoi-core"}
CANNOT = {"answering": False, "capable": False, "how": None, "starting": False,
          "why": "domovoi-core.service has Restart=no, so systemd would not start it again after "
                 "the dashboard ended it (docs/LINUX_HOST.md, The loop watchdog)",
          "manual_command": "sudo systemctl restart domovoi-core"}


def _sat(setup: str, health: dict, status: dict | None, rows=ROOMS, script=_SAT_SCRIPT) -> dict:
    api = {
        "GET /api/satellites": rows,
        "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [],
        "GET /api/capabilities": {"host_kind": "linux"},
        "GET /api/health": health,
        "POST /api/config/core/restart": {"ok": True, "how": "signal", "error": None,
                                          "answering": False, "starting": False,
                                          "manual_command": None},
    }
    if status is not None:
        api["GET /api/config/core/restart"] = status
    return {"files": SAT_FILES, "component": "SatellitesPanel", "setup": setup, "api": api,
            "script": script}


DOWN = {"status": "degraded", "db_reachable": True, "domovoi_reachable": False}
UP = {"status": "ok", "db_reachable": True, "domovoi_reachable": True}
# Review 2026-10-15: the database down (the web's own ping fails too), and a
# core whose loop answers /v1/alive but can't serve. Neither is the core's
# to fix by restarting, so neither gets the button.
DB_DOWN = {"status": "degraded", "db_reachable": False, "domovoi_reachable": False, "core_alive": True}
SICK = {"status": "degraded", "db_reachable": True, "domovoi_reachable": False, "core_alive": True}


def _version(setup: str, version, status: dict | None) -> dict:
    api = {"GET /api/config": {"web_version": "0.1.0-dev"}, "GET /api/config/version": version}
    if status is not None:
        api["GET /api/config/core/restart"] = status
    return {"files": SAT_FILES, "component": "VersionSection", "setup": setup, "api": api,
            "script": _TEXT_SCRIPT}


def _home(setup: str, admin: bool, status: dict | None) -> dict:
    row = {"key": "core", "tone": "err", "scope": "open", "href": "#satellites", "coreDown": True,
           "text": "the Domovoi server isn't answering · rooms show the last known state"}
    api = {}
    if status is not None:
        api["GET /api/config/core/restart"] = status
    return {"files": HOME_FILES, "component": "HomeAttention", "setup": setup, "api": api,
            "props": {"view": {"mode": "rows", "rows": [row]},
                      "viewer": {"isAdmin": admin, "known": True, "paired": True},
                      "shared": False, "checking": False, "failed": [], "refused": False,
                      "checkedAt": None},
            "fnProps": ["onClaim", "onSignIn"], "script": _TEXT_SCRIPT}


SCENARIOS = {
    "sat_down_household": _sat(_HOUSEHOLD, DOWN, CAN),
    "sat_down_admin": _sat(_SETUP, DOWN, CAN),
    "sat_down_admin_cannot": _sat(_SETUP, DOWN, CANNOT),
    "sat_down_admin_starting": _sat(_SETUP, DOWN, {**CAN, "capable": False, "starting": True,
                                                   "why": "the core is starting; give it a minute"}),
    "sat_up_offline": _sat(_SETUP, UP, {"answering": True}),
    "sat_db_down_admin": _sat(_SETUP, DB_DOWN, CAN),
    "sat_sick_admin": _sat(_SETUP, SICK, CAN),
    "sat_down_press": _sat(_ADMIN_PRESS, DOWN, CAN, script=_PRESS_SCRIPT),
    "version_down_admin": _version(_SETUP, {"__error": {"status": 502, "message": "domovoi unreachable"}}, CAN),
    "version_down_household": _version(_HOUSEHOLD, {"__error": {"status": 502, "message": "domovoi unreachable"}}, CAN),
    "version_last_freeze": _version(_SETUP, {
        "sha": "44f0fff", "running_sha": "44f0fff", "checkout_sha": "44f0fff", "restart_required": False,
        "restart_capable": True, "restart_mode": "update", "restart_in_progress": False,
        "last_watchdog_restart": {"at": "2026-10-15T17:25:00+00:00",
                                  "reason": "the event loop has not run for 31 s", "pid": 812,
                                  "log": "/home/domovoi/.domovoi/logs/core-watchdog.log"},
    }, {"answering": True}),
    "home_admin": _home(_SETUP, True, CAN),
    "home_household": _home(_HOUSEHOLD, False, CAN),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("core-down") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_satellites_say_the_core_is_down_not_that_they_are(driven) -> None:
    r = driven["sat_down_household"]
    assert r["banner"] and "core isn’t answering, so satellites can’t connect" in r["banner"]
    assert "the core isn’t answering · 4 known" in r["text"]
    assert len(r["cards"]) == 4
    for card in r["cards"]:
        assert "can’t connect" in card and "offline" not in card
        assert "last heard 12m ago" in card
        assert "19d" not in card       # the MPD row's allocation time, never a last-seen
    # Nobody without the admin sign-in is offered a restart, or asks for its status.
    assert "Restart the core" not in r["buttons"]
    assert "/api/config/core/restart" not in r["hooks"]


def test_an_admin_gets_the_restart_where_it_works(driven) -> None:
    assert "Restart the core" in driven["sat_down_admin"]["buttons"]
    cannot = driven["sat_down_admin_cannot"]
    assert "Restart the core" not in cannot["buttons"]
    assert "Restart=no" in cannot["text"]
    assert "| sudo systemctl restart domovoi-core |" in cannot["text"]   # the command, on its own
    starting = driven["sat_down_admin_starting"]
    assert "Restart the core" not in starting["buttons"]
    assert "the core is starting" in starting["text"]


def test_a_press_restarts_and_waits_for_the_core(driven) -> None:
    r = driven["sat_down_press"]
    assert "POST /api/config/core/restart" in r["calls"]
    assert "the core is back" in r["text"]


def test_with_the_core_up_an_offline_room_reads_when_it_was_heard(driven) -> None:
    r = driven["sat_up_offline"]
    assert r["banner"] is None
    for card in r["cards"]:
        assert "offline" in card and "last heard 12m ago" in card and "19d" not in card
    assert "0 online · 4 offline" in r["text"]


def test_the_version_card_offers_the_restart_when_the_core_is_down(driven) -> None:
    admin = driven["version_down_admin"]
    assert "The Domovoi core isn’t answering" in admin["text"]
    assert "Restart the core" in admin["buttons"]
    household = driven["version_down_household"]
    assert "The Domovoi core isn’t answering" in household["text"]
    assert "Restart the core" not in household["buttons"]


def test_the_version_card_shows_the_last_freeze(driven) -> None:
    text = driven["version_last_freeze"]["text"]
    assert "last freeze" in text
    assert "restarted · the event loop has not run for 31 s" in text
    assert "isn’t answering" not in text


def test_home_s_row_carries_the_restart_for_an_admin(driven) -> None:
    assert "Restart the core" in driven["home_admin"]["buttons"]
    assert "Restart the core" not in driven["home_household"]["buttons"]
    assert "isn't answering" in driven["home_household"]["text"]


def test_a_database_that_is_down_is_named_and_offers_no_restart(driven) -> None:
    r = driven["sat_db_down_admin"]
    assert r["banner"] and "The database isn’t answering, so satellites can’t connect" in r["banner"]
    assert "Restarting the core won’t help" in r["banner"]
    assert "core isn’t answering" not in r["banner"]
    assert "Restart the core" not in r["buttons"]


def test_a_core_that_runs_but_cannot_answer_offers_no_restart(driven) -> None:
    r = driven["sat_sick_admin"]
    assert r["banner"] and "running but can’t answer" in r["banner"]
    assert "database" in r["banner"]
    assert "Restart the core" not in r["buttons"]
