"""Settings → Configuration → Version: the release label, the update
channel, and "update available" in the channel's own terms (design-notes
V1-RELEASE-PLAN 1.3; docs/LINUX_HOST.md, "Release channels").

* the card names the code by its release label ("1.0.0", "1.0.0 + 14
  (abc1234)"), the SHA beside it only when the label doesn't hold it;
* "updates" says which channel the server follows; an admin can switch
  it (a confirm, then the switch, then a check), a household member only
  reads it, and a channel root's update unit doesn't allow is not offered;
* on stable a check offers the next release by name, or says this server
  runs newer code than the latest release; on main it counts commits and
  names the latest release.

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
FILES = ["web/static/components.jsx", "web/static/settings.jsx"]

_SETUP = r"""
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__cls = (e) => String((e && e.props && e.props.className) || '').split(' ');
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__confirms = [];
window.confirm = (m) => { window.__confirms.push(String(m)); return window.__answer !== false; };
"""
_HOUSEHOLD = _SETUP + r"""
Auth = { status: { authenticated: false, setup_complete: true }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false };
"""

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const view = () => {
  const one = (cls) => { const e = h.find((x) => w.__cls(x).includes(cls)); return e ? w.__deep(e).trim() : null; };
  const sel = h.find({ type: 'select' });
  return {
    label: one('version-label'), channel: one('version-channel'), result: one('version-check-result'),
    latest: one('version-latest-release'), ahead: one('version-ahead-of-release'),
    select: sel ? { value: sel.props.value, disabled: !!sel.props.disabled,
                    options: h.findAll((x) => x.type === 'option' && h.inside(x, { type: 'select' }))
                      .map((o) => w.__deep(o) + (o.props.disabled ? ' [disabled]' : '')) } : null,
    buttons: h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean),
    text: h.text().join(' | '),
  };
};
const before = view();
__ACT__
await w.__settle(h, 6);
return { before, after: view(), confirms: w.__confirms,
         calls: h.calls.map((c) => `${c.method} ${c.path}${c.body && Object.keys(c.body).length ? ' ' + JSON.stringify(c.body) : ''}`) };
"""
CHECK = "await h.click({ type: 'button', text: 'Check for updates' });"
SWITCH_TO_MAIN = "await h.change({ type: 'select' }, 'main');"
NOTHING = ""


def _version(**over) -> dict:
    v = {"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
         "restart_required": False, "restart_capable": True, "restart_mode": "update",
         "restart_in_progress": False, "version": "1.0.0", "checkout_version": "1.0.0",
         "channel": "stable", "channels": ["stable", "main"], "channel_pin": None}
    v.update(over)
    return v


def _check(**over) -> dict:
    c = {"behind": 0, "ahead": 0, "upstream": True, "upstream_sha": "f" * 40, "error": None,
         "channel": "stable", "upstream_version": "1.0.0", "latest_release": "1.0.0"}
    c.update(over)
    return c


def _scenario(version: dict, check: dict | None = None, act: str = NOTHING, setup: str = _SETUP,
              switch: dict | None = None) -> dict:
    api = {"GET /api/config": {"web_version": "1.0.0"}, "GET /api/config/version": version,
           "GET /api/config/core/restart": {"answering": True}}
    if check is not None:
        api["POST /api/config/version/check"] = check
    if switch is not None:
        api["POST /api/config/version/channel"] = switch
    return {"files": FILES, "component": "VersionSection", "setup": setup, "api": api,
            "script": _SCRIPT.replace("__ACT__", act)}


SCENARIOS = {
    "stable_latest": _scenario(_version(), _check(), CHECK),
    "stable_release_available": _scenario(_version(), _check(behind=3, upstream_version="1.0.1",
                                                             latest_release="1.0.1"), CHECK),
    "stable_ahead": _scenario(_version(version="1.0.0 + 23 (abc1234)", sha="abc1234",
                                       checkout_sha="abc1234"),
                              _check(ahead=23), CHECK),
    "main_behind": _scenario(_version(channel="main", version="1.0.0 + 4 (4d6b414)"),
                             _check(channel="main", behind=3, upstream_version="1.0.0 + 7 (0a1b2c3)"), CHECK),
    "switch_to_main": _scenario(_version(), _check(channel="main"), SWITCH_TO_MAIN,
                                switch={"ok": True, "changed": True, "channel": "main", "error": None}),
    "switch_refused": _scenario(_version(), _check(), SWITCH_TO_MAIN,
                                switch={"ok": False, "changed": False, "channel": None,
                                        "error": "a restart or update is under way; switch once it has finished"}),
    "switch_declined": _scenario(_version(), _check(), "w.__answer = false; " + SWITCH_TO_MAIN,
                                 switch={"ok": True, "changed": True, "channel": "main", "error": None}),
    "household": _scenario(_version(), _check(), NOTHING, setup=_HOUSEHOLD),
    "pinned_to_main": _scenario(_version(channel="main", channel_pin=["main"]), _check(channel="main")),
    "pinned_to_both": _scenario(_version(channel="main", channel_pin=["stable", "main"]), _check(channel="main")),
    "tag_moved": _scenario(_version(), _check(behind=1, upstream_version="1.0.1", tag_warning=(
        "a published release tag moved upstream (v1.0.0); this server keeps the copy it had, so a "
        "version label may be off. Updates are not affected. Published tags never move: tell "
        "Domovoi's maintainers")), CHECK),
    "stable_waiting": _scenario(_version(channel="main", channel_intended="stable", latest_release="1.0.0")),
    "stable_not_yet": _scenario(_version(channel="main", channel_intended="stable", latest_release=None)),
    "stable_waiting_household": _scenario(_version(channel="main", channel_intended="stable", latest_release="1.0.0"),
                                          setup=_HOUSEHOLD),
    "pin_unknown": _scenario(_version(channel="main", channel_pin_problem="unreadable"), _check(channel="main")),
    "dev_branch": _scenario(_version(channel="wf/x", version="1.0.0 + 2 (4d6b414)")),
    "older_core": _scenario({"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
                             "restart_required": False, "restart_capable": True, "restart_mode": "restart",
                             "restart_in_progress": False},
                            {"behind": 2, "ahead": 0, "upstream": True, "upstream_sha": None, "error": None}, CHECK),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("version-channels") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_card_names_the_release_and_the_channel(driven) -> None:
    b = driven["stable_latest"]["before"]
    assert b["label"] == "1.0.0"
    assert "4d6b414" in b["text"]                    # the SHA beside a bare release label
    assert b["select"]["value"] == "stable"
    assert b["select"]["options"] == ["stable: releases only", "main: every change, as it lands"]


def test_on_stable_the_check_says_it_is_the_latest_release(driven) -> None:
    a = driven["stable_latest"]["after"]
    assert a["result"] == "the latest release"
    assert "Pull the latest" not in a["buttons"]
    assert a["latest"] is None                       # stable names its release in the result itself


def test_on_stable_a_newer_release_is_offered_by_name(driven) -> None:
    a = driven["stable_release_available"]["after"]
    assert a["result"] == "Domovoi 1.0.1 is available"
    assert "Pull the latest" in a["buttons"]


def test_on_stable_past_the_latest_release_updates_wait_for_the_next(driven) -> None:
    o = driven["stable_ahead"]
    assert o["before"]["label"] == "1.0.0 + 23 (abc1234)"
    assert "abc1234 |" not in o["before"]["text"] + " |"   # the label holds the SHA: not repeated
    a = o["after"]
    assert a["result"] == "newer than the latest release"
    assert a["ahead"] == ("This server runs newer code than the latest release (1.0.0 + 23 (abc1234)); "
                          "updates resume with the next release that includes it.")
    assert "Pull the latest" not in a["buttons"]


def test_on_main_the_check_counts_commits_and_names_the_latest_release(driven) -> None:
    a = driven["main_behind"]["after"]
    assert a["result"] == "3 commits behind"
    assert a["latest"] == "latest release: 1.0.0"
    assert "Pull the latest" in a["buttons"]


def test_switching_asks_switches_and_checks(driven) -> None:
    o = driven["switch_to_main"]
    assert len(o["confirms"]) == 1 and o["confirms"][0].startswith("Follow the main channel")
    assert "Nothing changes now." in o["confirms"][0]
    assert o["calls"] == ['POST /api/config/version/channel {"channel":"main"}',
                          "POST /api/config/version/check"]
    assert "now following main" in o["after"]["text"] or o["after"]["result"] == "up to date"


def test_a_refused_switch_says_why_and_checks_nothing(driven) -> None:
    o = driven["switch_refused"]
    assert o["calls"] == ['POST /api/config/version/channel {"channel":"main"}']
    assert "channel not changed: a restart or update is under way" in o["after"]["text"]


def test_a_declined_confirm_sends_nothing(driven) -> None:
    o = driven["switch_declined"]
    assert len(o["confirms"]) == 1 and o["calls"] == []


def test_a_household_member_reads_the_channel_and_cannot_switch(driven) -> None:
    b = driven["household"]["before"]
    assert b["select"] is None
    assert b["channel"] == "stable: releases only"


def test_a_channel_root_does_not_allow_is_not_offered(driven) -> None:
    """No dead controls: pinned to main alone, there is nowhere to switch."""
    b = driven["pinned_to_main"]["before"]
    assert b["select"] is None
    assert b["channel"].startswith("main: every change, as it lands")
    assert b["channel"].endswith("set on the server")
    both = driven["pinned_to_both"]["before"]
    assert both["select"]["options"] == ["stable: releases only", "main: every change, as it lands"]


def test_a_moved_tag_is_a_warning_beside_a_working_update(driven) -> None:
    """rc2 review C1: the check still offers the pull; the moved tag is a
    line about the labels."""
    a = driven["tag_moved"]["after"]
    assert "Pull the latest" in a["buttons"]
    assert "a published release tag moved upstream (v1.0.0)" in a["text"]


def test_an_install_from_before_the_first_release_is_told_when_it_is_out(driven) -> None:
    """provision.sh follows main when origin has no stable yet, and
    remembers stable was asked for; the card says so once a release is
    out. It never switches by itself."""
    def note(name):
        e = driven[name]["before"]["text"]
        return "set up to follow stable" in e, e
    shown, text = note("stable_waiting")
    assert shown and "Domovoi 1.0.0 is out: switch to stable above" in text
    assert driven["stable_waiting"]["calls"] == []
    assert note("stable_not_yet")[0] is False
    shown, text = note("stable_waiting_household")
    assert shown and "an admin can switch it to stable here" in text


def test_a_settings_file_the_server_cannot_read_offers_no_switch(driven) -> None:
    """REV-20: the server can't tell what root pins, so no channel is offered."""
    b = driven["pin_unknown"]["before"]
    assert b["select"] is None
    assert "can’t switch: the server can’t read its update settings" in b["channel"]


def test_a_box_on_another_branch_can_move_to_a_channel(driven) -> None:
    s = driven["dev_branch"]["before"]["select"]
    assert s["value"] == "wf/x"
    assert s["options"] == ["wf/x (not a channel) [disabled]", "stable: releases only",
                            "main: every change, as it lands"]


def test_a_core_from_before_the_channels_reads_as_before(driven) -> None:
    o = driven["older_core"]
    assert o["before"]["label"] is None and o["before"]["channel"] is None
    assert "4d6b414" in o["before"]["text"]
    assert o["after"]["result"] == "2 commits behind" and o["after"]["latest"] is None
