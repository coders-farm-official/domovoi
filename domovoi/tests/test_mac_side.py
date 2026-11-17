"""A Mac install's host side, beside the Windows one (mac-installer PLAN
4.8 and §6 item 6; domovoi/windows_side.py ``read_host_side``).

* the Mac helper's ``/var/lib/domovoi-mac/side.json`` (``"kind": "mac"``)
  is believed only from a root-owned file, like DomovoiHost's;
* a Windows install reads exactly as before: its own file wins, and
  ``read_side`` keeps its shape;
* for a Mac side from before the updater (no ``"updates": 1``; the Mac
  side's updates are test_mac_update.py) a newer release is ``why_not:
  mac_by_hand``: no step, nothing for the Windows tray's routes, and the
  dashboard's request is refused with words;
* the Version card shows "Linux side" and "Mac side", says the Mac side is
  behind, and how to update it, with no Windows text and no button for it.

DB-free; the page half needs node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from domovoi import egress, git_version, self_restart, windows_side

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
C = "c0ffee0123456789abcdef0123456789abcdef01"
MAC = {"schema": 1, "kind": "mac", "version": "1.1.0", "build_commit": C, "written_at": "2026-11-02T10:00:00Z"}
WIN = {"schema": 1, "version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMOVOI_WINDOWS_UPDATE_DIR", str(tmp_path / "wu"))
    monkeypatch.setenv("DOMOVOI_WINDOWS_SIDE_FILE", str(tmp_path / "win" / "side.json"))
    monkeypatch.setenv("DOMOVOI_HOST_SIDE_FILE", str(tmp_path / "host" / "side.json"))
    monkeypatch.setattr(windows_side, "_root_only", lambda st: True)
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()
    yield
    windows_side._TRAY.clear()
    windows_side._TARGET_CACHE.clear()


def _write(tmp_path: Path, which: str, doc: Any) -> None:
    p = tmp_path / which / "side.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


def test_the_default_path_is_the_host_sides():
    assert windows_side.HOST_SIDE_FILE == "/var/lib/domovoi-mac/side.json"


def test_no_file_at_all_is_a_linux_only_install():
    assert windows_side.read_host_side() is None


def test_a_mac_report_is_read_with_its_kind(tmp_path):
    _write(tmp_path, "host", MAC)
    assert windows_side.read_host_side() == {"version": "1.1.0", "build_commit": C,
                                             "written_at": "2026-11-02T10:00:00Z", "kind": "mac",
                                             "updates": False}
    assert windows_side.read_side() is None, "the Windows reader still reads only DomovoiHost's file"


def test_a_windows_install_reads_as_before(tmp_path):
    _write(tmp_path, "win", WIN)
    _write(tmp_path, "host", MAC)
    assert windows_side.read_side() == {"version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z",
                                         "setup": None}
    assert windows_side.read_host_side()["kind"] == "windows", "DomovoiHost's own file wins"


@pytest.mark.parametrize("doc", [
    {**MAC, "kind": "windows"},       # Windows has its own file
    {**MAC, "kind": "linux"},
    {k: v for k, v in MAC.items() if k != "kind"},
    {**MAC, "schema": 2},
    {**MAC, "version": "1.1; reboot"},
    "not json",
])
def test_a_host_report_that_is_not_a_macs_is_ignored(tmp_path, doc):
    _write(tmp_path, "host", doc)
    assert windows_side.read_host_side() is None


def test_a_host_report_anyone_but_root_could_write_is_not_believed(tmp_path, monkeypatch):
    _write(tmp_path, "host", MAC)
    monkeypatch.setattr(windows_side, "_root_only", lambda st: False)
    assert windows_side.read_host_side() is None


@pytest.fixture
def mac_server(tmp_path, monkeypatch):
    _write(tmp_path, "host", MAC)
    s: dict[str, Any] = {"target": {"tag": "v1.2.0", "version": "1.2.0", "commit": C}, "internet": "always"}
    monkeypatch.setattr(windows_side, "target_release", lambda sha, ch: s["target"])
    monkeypatch.setattr(git_version, "boot_sha", lambda: "4d6b414")
    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": "stable"})
    monkeypatch.setattr(git_version, "read_last_update", lambda: None)
    monkeypatch.setattr(self_restart, "underway", lambda last=None, mode=None: False)
    monkeypatch.setattr(egress, "policy", lambda: s["internet"])
    return s


@pytest.mark.asyncio
async def test_a_newer_release_is_for_the_mac_by_hand(mac_server):
    card = await windows_side.card_state()
    assert card["present"] and card["kind"] == "mac" and card["version"] == "1.1.0"
    assert card["step_needed"] is False and card["why_not"] == "mac_by_hand"
    assert card["target"]["version"] == "1.2.0" and card["follows"] == "releases"


@pytest.mark.asyncio
@pytest.mark.parametrize("change,why", [
    ({"target": {"tag": "v1.1.0", "version": "1.1.0", "commit": C}}, "up_to_date"),
    ({"internet": "never"}, "internet_off"),
    ({"target": None}, "no_release"),
])
async def test_the_other_reasons_are_the_windows_sides(mac_server, change, why):
    mac_server.update(change)
    card = await windows_side.card_state()
    assert card["kind"] == "mac" and card["why_not"] == why and card["step_needed"] is False


@pytest.mark.asyncio
async def test_the_windows_trays_routes_see_no_windows_side(mac_server):
    st = await windows_side.tray_state()
    assert st["windows"] is None and st["step_needed"] is False and st["why_not"] == "no_windows_side"


@pytest.mark.asyncio
async def test_a_windows_card_says_its_kind(tmp_path, mac_server):
    _write(tmp_path, "win", WIN)
    card = await windows_side.card_state()
    assert card["kind"] == "windows" and card["step_needed"] is True
    st = await windows_side.tray_state()
    assert st["windows"] == {"version": "1.0.0", "build_commit": C, "written_at": "2026-10-22T21:30:00Z",
                            "setup": None}


def test_the_dashboards_request_is_refused_in_words_on_a_mac(mac_server, monkeypatch):
    """POST /v1/admin/windows/request: nothing on the Mac takes it."""
    import asyncio

    from fastapi import HTTPException

    from domovoi import main
    from domovoi.container_guard import freeze as container_freeze

    async def unfrozen(_what):
        return None

    monkeypatch.setattr(container_freeze, "require_unfrozen", unfrozen)
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_windows_request(main._WindowsRequestBody(on_this_pc=False)))
    assert e.value.status_code == 409
    assert "DomovoiSetup-mac.pkg" in e.value.detail
    assert not (windows_side.state_dir() / "request.json").exists()


# ─── the Version card ───────────────────────────────────────────────────

_SETUP = r"""
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.confirm = () => true;
"""
_SCRIPT = r"""
const w = h.global('window'); h.render();
for (let i = 0; i < 4; i++) { await h.settle(); h.rerender(); }
const one = (cls) => { const e = h.find((x) => w.__cls(x).includes(cls)); return e ? w.__deep(e).trim() : null; };
return { mac: one('version-mac'), behind: one('version-mac-behind'), notes: one('version-mac-notes'),
         windows: one('version-windows'),
         buttons: h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean),
         text: h.text().join(' | ') };
"""


def _card(**over) -> dict:
    c = {"present": True, "kind": "mac", "version": "1.1.0", "build_commit": C, "reported_at": "x",
         "target": {"tag": "v1.2.0", "version": "1.2.0", "commit": C}, "step_needed": False,
         "why_not": "mac_by_hand", "follows": "releases", "request": None, "result": None,
         "tray": {"running": False, "seen_at": None, "version": None}}
    c.update(over)
    return c


def _scenario(card: dict) -> dict:
    version = {"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
               "restart_required": False, "restart_capable": True, "restart_mode": "update",
               "restart_in_progress": False, "version": "1.2.0", "checkout_version": "1.2.0",
               "channel": "stable", "channels": ["stable", "main"], "channel_pin": None, "windows_side": card}
    return {"files": ["web/static/components.jsx", "web/static/settings.jsx"], "component": "VersionSection",
            "setup": _SETUP, "script": _SCRIPT,
            "api": {"GET /api/config": {"web_version": "1.2.0"}, "GET /api/config/version": version,
                    "GET /api/config/core/restart": {"answering": True}}}


SCENARIOS = {
    "behind": _scenario(_card()),
    "current": _scenario(_card(why_not="up_to_date", version="1.2.0")),
    "internet_never": _scenario(_card(why_not="internet_off")),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("version-mac") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_a_mac_install_shows_both_halves(driven):
    o = driven["current"]
    assert "Linux side" in o["text"] and "Mac side" in o["text"]
    assert o["mac"].startswith("1.2.0") and "(c0ffee0)" in o["mac"] and "updates on releases" in o["mac"]
    assert o["windows"] is None and "Windows" not in o["text"]
    assert o["behind"] is None and o["notes"] is None


def test_a_behind_mac_side_says_what_to_do_with_no_button(driven):
    o = driven["behind"]
    assert o["behind"] == "Mac side is behind: 1.2.0 is out"
    assert "DomovoiSetup-mac.pkg" in o["notes"] and "on the Mac" in o["notes"]
    # Only "when": this server can't see whether the release carries the package.
    assert "When that release includes DomovoiSetup-mac.pkg" in o["notes"] and "download" not in o["notes"].lower()
    assert "Windows" not in o["text"]
    assert "Update Domovoi" not in o["buttons"], "no button for a step the dashboard can't run"


def test_internet_off_is_said_for_the_mac(driven):
    assert "internet access is turned off" in driven["internet_never"]["notes"]
