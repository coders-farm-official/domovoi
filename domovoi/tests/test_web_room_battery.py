"""A phone room's battery on the web hop and in the dashboard (V040).

* The routes: ``GET /api/satellites/battery-warnings`` (the open warnings
  the phones notify about) and ``GET /api/satellites/{room}/battery-alerts``
  are household reads; ``PUT …/battery-alerts`` (the two switches) is device
  tier, refused before any write without a household credential. A missing
  V040 answers "none" on the reads and 503 on the write.
* The roster row's ``battery``: phone rooms only; ``low`` while a warning
  is open, the room online, off its charger and notifying;
  ``probably_out`` once it is offline after a last report at 5 % or less
  off its charger.
* The live socket's ``room_battery`` channel carries the warnings and each
  phone room's last level.
* The dashboard (driven through jsx_interact_harness.js): the card's
  battery icon, % and "charging"; nothing on a Pi; the offline line
  "probably out of battery"; Health → Rooms' battery row (only in a house
  with a phone room) and the offline row; the room's Settings tab switches
  (phone rooms only), and the spare-device phone card's charger line.

No DB (the routes run on a fake seam), never ``requires_db``; the JSX
part needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi import room_battery as rb
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_app
from domovoi.tests.route_walk import iter_route_contexts
from web.backend import realtime
from web.backend.api import captures as captures_api
from web.backend.api import satellites as sat_api

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"
BROWSER = {"X-Requested-With": "XMLHttpRequest"}
T0 = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)


# ─── the routes' tiers ───────────────────────────────────────────────────


def _route(method: str, path: str):
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
            return rc
    raise AssertionError(f"{method} {path} is not a web route")


def _gates(rc) -> set:
    out, stack = set(), [getattr(rc, "dependant", None)]
    while stack:
        d = stack.pop()
        for dep in getattr(d, "dependencies", ()) if d is not None else ():
            if dep.call is not None:
                out.add(dep.call)
            stack.append(dep)
    return out


_GATES = {admin_auth.require_device, admin_auth.require_device_read, admin_auth.require_admin_read,
          admin_auth.require_admin_mutation, admin_auth.require_admin_security}


def test_the_reads_are_household_reads_and_the_write_is_device_tier() -> None:
    for path in ("/api/satellites/battery-warnings", "/api/satellites/{room_id}/battery-alerts"):
        assert _gates(_route("GET", path)) & _GATES == {admin_auth.require_device_read}, path
    put = _gates(_route("PUT", "/api/satellites/{room_id}/battery-alerts"))
    assert put & _GATES == {admin_auth.require_device}


def test_battery_warnings_is_not_read_as_a_room() -> None:
    paths = [getattr(rc, "path", "") for rc in iter_route_contexts(web_app.routes)]
    assert paths.index("/api/satellites/battery-warnings") < paths.index("/api/satellites/{room_id}")


# ─── the routes, on a fake seam ──────────────────────────────────────────


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)


@pytest.fixture
def seam(monkeypatch):
    rec: dict[str, Any] = {"known": {"pako", "kitchen"}, "switches": {}, "set": [], "missing": False,
                           "warnings": [{"id": 3, "room_id": "pako", "threshold": 20, "percent": 15,
                                         "spoken": True, "created_at": T0, "room_label": None, "notify": True}]}

    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def room_known(s, room_id):
        return room_id in rec["known"]

    async def switches(s, room_id):
        if rec["missing"]:
            raise RuntimeError('column "battery_say" does not exist')
        return rec["switches"].get(room_id, rb.Switches())

    async def set_switches(s, room_id, *, say=None, notify=None):
        if rec["missing"]:
            raise RuntimeError('column "battery_say" does not exist')
        rec["set"].append((room_id, say, notify))
        cur = rec["switches"].get(room_id, rb.Switches())
        new = rb.Switches(cur.say if say is None else say, cur.notify if notify is None else notify)
        rec["switches"][room_id] = new
        return new

    async def open_warnings(s, room_id=None, *, notify_only=False):
        if rec["missing"]:
            raise RuntimeError('relation "room_battery_warnings" does not exist')
        return [dict(w) for w in rec["warnings"]]

    monkeypatch.setattr(sat_api, "session_scope", fake_scope)
    monkeypatch.setattr(captures_api, "_room_known", room_known)
    monkeypatch.setattr(rb, "switches", switches)
    monkeypatch.setattr(rb, "set_switches", set_switches)
    monkeypatch.setattr(rb, "open_warnings", open_warnings)
    return rec


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers=headers or {}, **kw)


@pytest.mark.asyncio
async def test_the_reads_need_a_household_credential(claimed, seam) -> None:
    async with _client() as c:
        assert (await c.get("/api/satellites/battery-warnings")).status_code == 401
        assert (await c.get("/api/satellites/pako/battery-alerts")).status_code == 401
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get("/api/satellites/battery-warnings")
        assert r.status_code == 200
        assert r.json()["warnings"] == [{
            "id": 3, "room_id": "pako", "name": "Pako", "threshold": 20, "percent": 15,
            "text": "Pako is at 15%. Plug it in.", "created_at": "2026-10-25T15:00:00Z",
        }]
        r = await c.get("/api/satellites/pako/battery-alerts")
        assert r.json() == {"room_id": "pako", "say": True, "notify": True}
        assert (await c.get("/api/satellites/nowhere/battery-alerts")).status_code == 404


@pytest.mark.asyncio
async def test_the_switches_take_a_household_credential(claimed, seam) -> None:
    path = "/api/satellites/pako/battery-alerts"
    async with _client(BROWSER) as c:
        assert (await c.put(path, json={"say": False})).status_code == 401
    async with _client(BROWSER, cookies={COOKIE: ADMIN_TOKEN}) as c:
        assert (await c.put(path, json={"say": False})).status_code == 403   # the cookie alone
    assert seam["set"] == []
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        r = await c.put(path, json={"say": False})
        assert r.status_code == 200 and r.json() == {"room_id": "pako", "say": False, "notify": True}
        assert (await c.get(path)).json()["say"] is False
        assert (await c.put(path, json={"say": "no"})).status_code == 422
        assert (await c.put(path, json={"loud": True})).status_code == 422
        assert (await c.put("/api/satellites/nowhere/battery-alerts", json={"say": True})).status_code == 404
    async with _client({**BROWSER, **bearer(ADMIN_TOKEN)}) as c:
        r = await c.put(path, json={"notify": False})
        assert r.json() == {"room_id": "pako", "say": False, "notify": False}
    assert seam["set"] == [("pako", False, None), ("pako", None, False)]


@pytest.mark.asyncio
async def test_without_v041_the_reads_say_none_and_the_write_says_so(claimed, seam) -> None:
    seam["missing"] = True
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        assert (await c.get("/api/satellites/battery-warnings")).json() == {"warnings": []}
        assert (await c.get("/api/satellites/pako/battery-alerts")).json()["say"] is True
        r = await c.put("/api/satellites/pako/battery-alerts", json={"say": False})
        assert r.status_code == 503 and "V040" in r.text


# ─── the roster row ──────────────────────────────────────────────────────


def _bats(pct, charging=False, on_power=False, low=True, room="pako"):
    return {"reports": {room: {"percent": pct, "charging": charging, "on_power": on_power, "reported_at": T0}},
            "low": {room} if low else set()}


def test_only_a_phone_room_has_a_battery() -> None:
    assert sat_api._battery_for("pako", "linux_pc", True, _bats(15)) is None
    assert sat_api._battery_for("pako", None, True, _bats(15)) is None
    assert sat_api._battery_for("pako", "android_room", True, {}) is None
    b = sat_api._battery_for("pako", "android_room", True, _bats(15))
    assert (b.percent, b.charging, b.low, b.probably_out) == (15, False, True, False)


def test_low_needs_the_room_online_off_its_charger_and_notifying() -> None:
    assert not sat_api._battery_for("pako", "android_room", False, _bats(15)).low
    assert not sat_api._battery_for("pako", "android_room", True, _bats(15, charging=True)).low
    assert not sat_api._battery_for("pako", "android_room", True, _bats(15, low=False)).low


@pytest.mark.parametrize(("online", "pct", "charging", "out"), [
    (False, 5, False, True), (False, 2, False, True), (False, 6, False, False),
    (False, 3, True, False), (True, 3, False, False),
])
def test_the_offline_row_says_probably_out_of_battery(online, pct, charging, out) -> None:
    b = sat_api._battery_for("pako", "android_room", online, _bats(pct, charging=charging))
    assert b.probably_out is out


@pytest.mark.asyncio
async def test_the_live_channel_carries_the_warnings_and_the_levels(monkeypatch) -> None:
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME[rb.NOTIFY_CHANNEL] == "room_battery"
    assert realtime.StatePollLoop._CHANNEL_HELPERS["room_battery"] is realtime._snapshot_room_battery

    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def open_warnings(s, room_id=None, *, notify_only=False):
        assert notify_only
        return [{"id": 1, "room_id": "pako", "threshold": 10, "percent": 9, "created_at": T0, "room_label": None}]

    async def last_reports(s, room_id=None):
        return {"pako": {"percent": 9, "charging": False, "on_power": False, "reported_at": T0}}

    monkeypatch.setattr(realtime, "session_scope", fake_scope)
    monkeypatch.setattr(rb, "open_warnings", open_warnings)
    monkeypatch.setattr(rb, "last_reports", last_reports)
    snap = await realtime._snapshot_room_battery()
    assert snap["warnings"][0]["text"] == "Pako is at 9%. Plug it in."
    assert snap["rooms"] == [{"room_id": "pako", "percent": 9, "charging": False, "on_power": False}]


# ─── the dashboard ───────────────────────────────────────────────────────

FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx", "web/static/satellites.jsx"]
HEALTH_FILES = ["web/static/components.jsx", "web/static/health.jsx", "web/static/home.jsx"]


def room(rid: str, status: str = "online", **kw) -> dict:
    r = {"room_id": rid, "status": status, "last_connected_at": "2026-10-25T08:00:00Z", "wifi": None,
         "now_playing": None, "mpd_ports": {"control": 6650, "http": 8050}, "version": None,
         "full_duplex": False, "in_call_with": None, "voice": None, "volume": None,
         "pairing": {"paired": True}, "sat_type": "voice", "mic_enabled": True,
         "capture_commands": False, "timers_own_only": False, "board": None, "supports": None,
         "battery": None}
    r.update(kw)
    return r


def phone(rid="pako", status="online", pct=15, charging=False, on_power=False, low=False, out=False):
    return room(rid, status, board="android_room",
                supports=["announce_after_session_end", "capture_control", "sounds", "speech_pause", "wake_models"],
                battery={"percent": pct, "charging": charging, "on_power": on_power,
                         "reported_at": "2026-10-25T08:00:00Z", "low": low, "probably_out": out})


SETUP = r"""
globalThis.__writes = [];
apiFetch = (path, opts) => {
  __writes.push({ path, method: (opts && opts.method) || 'GET', body: (opts && opts.body) || null });
  return Promise.resolve(Object.assign({ room_id: 'pako', say: true, notify: true }, JSON.parse(opts.body)));
};
reportMutationFailure = (fire, verb, e) => fire(`${verb} failed`);
"""

_CARD = r"""
  h.render(); await h.settle(); h.rerender();
  const cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
  const bat = h.find((e) => cls(e).includes('sat-battery'));
  return { text: h.text().join(' | '), battery: bat ? bat.props['data-tone'] : null,
           icons: h.findAll((e) => e.type === 'Icon').map((e) => e.props.name) };
"""

_CHECKS = r"""
  const w = h.global('window');
  const out = {};
  for (const [k, c] of Object.entries(w.__CASES)) {
    out[k] = w.healthChecks(c).filter((x) => x.part === 'rooms')
      .map((x) => ({ key: x.key, tone: x.tone, text: x.text }));
  }
  return out;
"""

_SETTINGS = r"""
  h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const boxes = () => h.findAll((e) => e.type === 'input' && e.props.type === 'checkbox');
  const before = boxes().map((b) => !!b.props.checked);
  await h.change(boxes()[0], false); await h.settle(); h.rerender();
  const after = boxes().map((b) => !!b.props.checked);
  return { text: h.text().join(' | '), before, after, writes: h.global('__writes').slice(),
           fired: h.fnCalls.map((c) => [c.name].concat(c.args)) };
"""

_DRAWER = r"""
  h.render(); await h.settle(); h.rerender();
  await h.click((e) => e.type === 'button' && String(e.props.title || e.props['aria-label'] || '').includes('Settings'));
  await h.settle(); h.rerender();
  const cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
  return { text: h.text().join(' | '), switches: h.findAll((e) => cls(e).includes('sat-battery-settings')).length };
"""

ROOMS_HOUSE = {"viewer": {"known": True, "unclaimed": False, "paired": True, "isAdmin": True},
               "health": {"status": "ok", "db_reachable": True, "domovoi_reachable": True, "stt": "ready"},
               "plugins": {"plugins": []}, "pluginErrors": {}, "acq": {"core_reachable": True, "acquisitions": []},
               "approvals": [], "pending": [], "version": None, "hardware": None, "config": None,
               "internet": None, "guard": {"loaded": False}}

SCENARIOS = {
    "card_low": {"files": FILES, "component": "SatCard", "props": {"s": phone(pct=15, low=True), "tick": 0},
                 "fnProps": ["onOpen"], "script": _CARD},
    "card_charging": {"files": FILES, "component": "SatCard",
                      "props": {"s": phone(pct=64, charging=True, on_power=True), "tick": 0},
                      "fnProps": ["onOpen"], "script": _CARD},
    "card_pi": {"files": FILES, "component": "SatCard", "props": {"s": room("kitchen"), "tick": 0},
                "fnProps": ["onOpen"], "script": _CARD},
    "card_out": {"files": FILES, "component": "SatCard",
                 "props": {"s": phone(status="offline", pct=3, out=True), "tick": 0},
                 "fnProps": ["onOpen"], "script": _CARD},
    "checks": {
        "files": HEALTH_FILES, "component": "(window.HomeAttentionRows = HomeAttentionRows, HomePage)",
        "setup": "window.__CASES = " + json.dumps({
            "pis_only": {**ROOMS_HOUSE, "rooms": [room("kitchen"), room("den", "offline")]},
            "phone_fine": {**ROOMS_HOUSE, "rooms": [room("kitchen"), phone(pct=80)]},
            "phone_low": {**ROOMS_HOUSE, "rooms": [room("kitchen"), phone(pct=15, low=True)]},
            "phone_very_low": {**ROOMS_HOUSE, "rooms": [phone(pct=9, low=True)]},
            "phone_out": {**ROOMS_HOUSE, "rooms": [room("kitchen"), phone(status="offline", pct=2, out=True)]},
            "phone_offline": {**ROOMS_HOUSE, "rooms": [phone(status="offline", pct=40)]},
        }) + ";",
        "script": _CHECKS,
    },
    "settings": {"files": FILES, "component": "RoomBatterySettings", "props": {"room": "pako"},
                 "fnProps": ["fire"], "setup": SETUP,
                 "api": {"GET /api/satellites/pako/battery-alerts": {"room_id": "pako", "say": True, "notify": True}},
                 "script": _SETTINGS},
    "drawer_phone": {"files": FILES, "component": "SatDrawer", "props": {"s": phone(), "sats": [phone()]},
                     "fnProps": ["onClose", "fire", "refresh"], "setup": SETUP,
                     "api": {"GET /api/satellites/pako/battery-alerts": {"room_id": "pako", "say": True, "notify": True}},
                     "script": _DRAWER},
    "drawer_laptop": {"files": FILES, "component": "SatDrawer",
                      "props": {"s": room("study", board="linux_pc", supports=["sounds"]),
                                "sats": [room("study", board="linux_pc", supports=["sounds"])]},
                      "fnProps": ["onClose", "fire", "refresh"], "setup": SETUP, "script": _DRAWER},
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("room-battery") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_card_shows_the_battery_and_charging(driven) -> None:
    low = driven["card_low"]
    assert low["battery"] == "warn" and "15%" in low["text"] and "battery-low" in low["icons"]
    charging = driven["card_charging"]
    assert "64% · charging" in charging["text"] and "battery-charging" in charging["icons"]
    assert charging["battery"] == "ok"


def test_a_pi_shows_no_battery(driven) -> None:
    pi = driven["card_pi"]
    assert pi["battery"] is None
    assert not any(i.startswith("battery") for i in pi["icons"])


def test_the_offline_card_says_probably_out_of_battery(driven) -> None:
    out = driven["card_out"]
    assert "probably out of battery · last heard" in out["text"]
    assert out["battery"] == "idle" and "3%" in out["text"]


def test_health_rooms_battery_row_and_offline_line(driven) -> None:
    c = driven["checks"]
    keys = lambda k: [x["key"] for x in c[k]]  # noqa: E731
    assert "battery" not in keys("pis_only"), "a house of Pis never sees a battery row"
    fine = next(x for x in c["phone_fine"] if x["key"] == "battery")
    assert fine["tone"] == "ok"
    low = next(x for x in c["phone_low"] if x["key"] == "battery")
    assert (low["tone"], low["text"]) == ("warn", "pako's battery is low (15%) · plug it in")
    assert next(x for x in c["phone_very_low"] if x["key"] == "battery")["tone"] == "err"
    out = next(x for x in c["phone_out"] if x["key"] == "offline")
    assert out["text"] == "pako is offline · probably out of battery"
    plain = next(x for x in c["phone_offline"] if x["key"] == "offline")
    assert plain["text"] == "pako is offline · it can't hear anyone right now"
    # Offline, the battery row is not the story (the offline row is).
    assert next(x for x in c["phone_out"] if x["key"] == "battery")["tone"] == "ok"


def test_the_settings_switches(driven) -> None:
    s = driven["settings"]
    assert "Say when the battery is low" in s["text"] and "Notify when the battery is low" in s["text"]
    assert s["before"] == [True, True] and s["after"] == [False, True]
    assert s["writes"] == [{"path": "/api/satellites/pako/battery-alerts", "method": "PUT",
                            "body": json.dumps({"say": False}, separators=(",", ":"))}]
    assert ["fire", "pako won't say when its battery is low"] in s["fired"]


def test_only_a_phone_rooms_settings_tab_has_the_switches(driven) -> None:
    assert driven["drawer_phone"]["switches"] == 1
    assert "Say when the battery is low" in driven["drawer_phone"]["text"]
    assert driven["drawer_laptop"]["switches"] == 0


def test_the_phone_card_and_the_app_say_keep_it_on_its_charger() -> None:
    line = "Keep the phone on its charger: rooms are always on."
    assert line in (REPO_ROOT / "web/static/satellites.jsx").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "android/app/src/main/java/com/domovoi/app/roommode/RoomSetupActivity.kt")
    assert f'CHARGER_TIP = "{line}"' in setup.read_text(encoding="utf-8")
