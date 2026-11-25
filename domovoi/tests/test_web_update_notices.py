"""Settings → System → About & updates, with update notices (1.5.0; design
note UPDATE-NOTICES §7.2).

* (a) "Check for updates by itself": every state of the box and its one
  line; greyed (and unticked) whenever the internet answer keeps it off;
  greyed for a household member, whose line says only an admin can change
  it; unticking writes the setting and says so;
* (b) a newer signed release: "Domovoi 1.5.0 is available" from the
  stored notice without pressing anything, its highlights as plain text
  (a ``<b>`` in a bullet stays text), and the main-house line;
* (c) on main, the line that replaces the "not a signed release" badge
  and the "latest release" words: no duplicate lines;
* Home's Update (``#settings/about/update-now``) runs the card's own one
  update action, or says it is already up to date.

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
HL = ["Friend Houses is open to everyone in the household, by voice too.",
      "Voice ID learns better from short samples.",
      "Music keeps playing after you ask a question."]

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
window.confirm = (m) => { window.__confirms.push(String(m)); return true; };
"""
_HOUSEHOLD = _SETUP + r"""
Auth = { status: { authenticated: false, setup_complete: true }, subscribe: () => () => {},
         isLoggedIn: () => false, headers: () => ({}), modalOpen: false };
"""
_UPDATE_NOW = _SETUP + "window.location.hash = '#settings/about/update-now';\n"

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const view = () => {
  const one = (cls) => { const e = h.find((x) => w.__cls(x).includes(cls)); return e ? w.__deep(e).trim() : null; };
  const box = h.find((x) => x.type === 'input' && x.props.type === 'checkbox');
  const items = h.findAll((x) => x.type === 'li').map((li) => ({
    text: w.__deep(li), kids: [].concat(li.props.children).map((c) => typeof c) }));
  return {
    box: box ? { checked: !!box.props.checked, disabled: !!box.props.disabled } : null,
    autoLine: one('version-auto-check-line'), notice: one('version-notice'), mainLine: one('version-main-line'),
    result: one('version-check-result'), latest: one('version-latest-release'),
    badge: one('version-signature'), items,
    buttons: h.findAll({ type: 'button' }).map((b) => w.__deep(b).trim()).filter(Boolean),
    text: h.text().join(' | '),
  };
};
const before = view();
__ACT__
await w.__settle(h, 6);
return { before, after: view(), confirms: w.__confirms, hash: w.location.hash,
         calls: h.calls.map((c) => `${c.method} ${c.path}${c.body && Object.keys(c.body).length ? ' ' + JSON.stringify(c.body) : ''}`) };
"""


def _auto(why=None, last_at=None, last_ok=None, enabled=True) -> dict:
    return {"enabled": enabled, "active": why is None, "why_off": why, "last_at": last_at, "last_ok": last_ok}


def _updates(*, why=None, last_at=None, last_ok=None, relation="unknown", changes=None, latest=None,
             notice=None, enabled=True) -> dict:
    return {"auto_check": _auto(why, last_at, last_ok, enabled), "latest_release": latest,
            "relation": relation, "changes_since": changes,
            "notice": notice or {"available": False, "updating": False}}


def _latest(version="1.4.1", highlights=HL) -> dict:
    return {"version": version, "tag": f"v{version}", "commit": "a" * 40, "signed": True, "highlights": highlights}


def _version(updates: dict | None, **over) -> dict:
    v = {"sha": "4d6b414", "running_sha": "4d6b414", "checkout_sha": "4d6b414",
         "restart_required": False, "restart_capable": True, "restart_mode": "update",
         "restart_in_progress": False, "version": "1.4.1", "checkout_version": "1.4.1",
         "channel": "stable", "channels": ["stable", "main"], "channel_pin": None,
         "signature": {"status": "unsigned", "enforced": False, "allowed_signers": "/etc/domovoi/allowed_signers"},
         "updates": updates}
    v.update(over)
    return v


def _scenario(version: dict, act: str = "", setup: str = _SETUP, check: dict | None = None) -> dict:
    api = {"GET /api/config": {"web_version": "1.4.1"}, "GET /api/config/version": version,
           "GET /api/config/core/restart": {"answering": True},
           "PATCH /api/config/editable": {"applied": {"update_auto_check": False}, "rejected": {}},
           "POST /api/config/version/pull": {"pulled": True, "new_sha": "b" * 7},
           "POST /api/config/version/restart": {"ok": True, "mode": "update", "delay_sec": 1}}
    if check is not None:
        api["POST /api/config/version/check"] = check
    return {"files": FILES, "component": "VersionSection", "setup": setup, "api": api,
            "script": _SCRIPT.replace("__ACT__", act)}


NOTICE = {"available": True, "version": "1.5.0", "highlights": HL, "found_by": "auto",
          "found_at": "2026-11-25T08:02:11Z", "updating": False}
CHECK = {"behind": 3, "ahead": 0, "upstream": True, "upstream_sha": "f" * 40, "error": None,
         "channel": "stable", "upstream_version": "1.5.0", "latest_release": "1.5.0"}
UNTICK = "await h.change((x) => x.type === 'input' && x.props.type === 'checkbox', false);"

SCENARIOS = {
    "active": _scenario(_version(_updates(latest=_latest(), relation="same"))),
    "active_looked": _scenario(_version(_updates(last_at="2000-01-01T00:00:00Z", last_ok=True))),
    "active_failed": _scenario(_version(_updates(last_at="2000-01-01T00:00:00Z", last_ok=False))),
    "setting_off": _scenario(_version(_updates(why="setting_off", enabled=False))),
    "sometimes": _scenario(_version(_updates(why="internet_sometimes"))),
    "never": _scenario(_version(_updates(why="internet_never"))),
    "unanswered": _scenario(_version(_updates(why="internet_unanswered"))),
    "no_git": _scenario(_version(_updates(why="no_git"))),
    "older_core": _scenario(_version(None)),
    "member": _scenario(_version(_updates()), setup=_HOUSEHOLD),
    "member_never": _scenario(_version(_updates(why="internet_never")), setup=_HOUSEHOLD),
    "untick": _scenario(_version(_updates()), UNTICK),
    "stable_notice": _scenario(_version(_updates(latest=_latest("1.5.0"), relation="behind", notice=NOTICE))),
    "stable_notice_checked": _scenario(_version(_updates(latest=_latest("1.5.0"), relation="behind", notice=NOTICE)),
                                       "await h.click({ type: 'button', text: 'Check for updates' });",
                                       check={**CHECK, "updates": _updates(latest=_latest("1.5.0"), relation="behind",
                                                                           notice=NOTICE)}),
    "html_bullet": _scenario(_version(_updates(notice={**NOTICE, "highlights": ["Music <b>keeps</b> playing.",
                                                                                 HL[1], HL[2]]}))),
    "notice_no_highlights": _scenario(_version(_updates(notice={**NOTICE, "highlights": []}))),
    "main_behind": _scenario(_version(_updates(latest=_latest("1.5.0"), relation="behind", notice=NOTICE),
                                      channel="main", version="1.4.1 + 12 (4d6b414)")),
    "main_ahead": _scenario(_version(_updates(latest=_latest(), relation="ahead", changes=12),
                                     channel="main", version="1.4.1 + 30 (4d6b414)"),
                            "await h.click({ type: 'button', text: 'Check for updates' });",
                            check={**CHECK, "behind": 3, "channel": "main", "upstream_version": "1.4.1 + 33 (0a1b2c3)",
                                   "latest_release": "1.4.1",
                                   "updates": _updates(latest=_latest(), relation="ahead", changes=12)}),
    "main_ahead_one": _scenario(_version(_updates(latest=_latest(), relation="ahead", changes=1), channel="main")),
    "main_same": _scenario(_version(_updates(latest=_latest(), relation="same"), channel="main")),
    "main_unknown": _scenario(_version(_updates(), channel="main"),
                              "await h.click({ type: 'button', text: 'Check for updates' });",
                              check={**CHECK, "channel": "main", "upstream_version": "1.4.1 + 3 (0a1b2c3)",
                                     "latest_release": "1.4.1", "updates": _updates()}),
    "main_enforced": _scenario(_version(_updates(latest=_latest(), relation="ahead", changes=12), channel="main",
                                        signature={"status": "unsigned", "enforced": True,
                                                   "allowed_signers": "/etc/domovoi/allowed_signers"})),
    # A host without the update unit: the card's action is the pull (the
    # restart is its own step there), so the harness doesn't wait on one.
    "update_now": _scenario(_version(_updates(latest=_latest("1.5.0"), relation="behind", notice=NOTICE),
                                     restart_mode="restart", restart_capable=False),
                            setup=_UPDATE_NOW, check={**CHECK, "updates": _updates(notice=NOTICE)}),
    "update_now_nothing": _scenario(_version(_updates(latest=_latest(), relation="same")), setup=_UPDATE_NOW,
                                    check={**CHECK, "behind": 0, "upstream_version": "1.4.1",
                                           "updates": _updates()}),
    "update_now_member": _scenario(_version(_updates(notice=NOTICE)),
                                   setup=_HOUSEHOLD + "window.location.hash = '#settings/about/update-now';\n",
                                   check=CHECK),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("update-notices") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


ACTIVE = "Once a day and when Domovoi starts. It never installs anything by itself."


# ─── (a) the checkbox ────────────────────────────────────────────────────


def test_active_is_ticked_and_editable(driven):
    b = driven["active"]["before"]
    assert b["box"] == {"checked": True, "disabled": False}
    assert b["autoLine"] == ACTIVE
    assert "Check for updates by itself" in b["text"]


def test_active_says_when_it_last_looked_or_that_it_failed(driven):
    looked = driven["active_looked"]["before"]["autoLine"]
    assert looked.startswith(ACTIVE + " Last looked ") and looked.endswith(" ago.")
    assert "days ago" in looked
    failed = driven["active_failed"]["before"]["autoLine"]
    assert failed == ACTIVE + " The last look didn’t get through. It tries again within the hour."


@pytest.mark.parametrize(("name", "box", "line"), [
    ("setting_off", {"checked": False, "disabled": False}, "Off. Domovoi looks only when you press Check for updates."),
    ("sometimes", {"checked": False, "disabled": True}, "Off while the house’s internet setting is Sometimes."),
    ("never", {"checked": False, "disabled": True}, "Off while the house’s internet setting is No."),
    ("unanswered", {"checked": False, "disabled": True}, "Off until the house’s internet setting is Yes."),
])
def test_every_off_state(driven, name, box, line):
    b = driven[name]["before"]
    assert b["box"] == box and b["autoLine"] == line


def test_no_git_and_an_older_core_show_no_box(driven):
    for name in ("no_git", "older_core"):
        b = driven[name]["before"]
        assert b["box"] is None and "Check for updates by itself" not in b["text"], name


def test_a_household_member_reads_it_greyed(driven):
    b = driven["member"]["before"]
    assert b["box"] == {"checked": True, "disabled": True}
    assert b["autoLine"] == ACTIVE + " Only an admin can change this."
    n = driven["member_never"]["before"]
    assert n["box"] == {"checked": False, "disabled": True}
    assert n["autoLine"] == "Off while the house’s internet setting is No. Only an admin can change this."


def test_unticking_writes_the_setting_and_says_so(driven):
    out = driven["untick"]
    assert 'PATCH /api/config/editable {"changes":{"update_auto_check":false}}' in out["calls"]
    assert "Domovoi won’t look by itself now." in out["after"]["text"]


# ─── (b) the notice and its highlights ───────────────────────────────────


def test_a_stored_notice_shows_without_pressing_with_its_highlights(driven):
    b = driven["stable_notice"]["before"]
    assert b["notice"].startswith("Domovoi 1.5.0 is available")
    assert [i["text"] for i in b["items"]] == HL
    assert "Highlights" in b["text"]
    assert "newest change on main" not in b["notice"]


def test_after_a_check_the_words_are_not_said_twice(driven):
    a = driven["stable_notice_checked"]["after"]
    # The card's block, plus the check's own toast (gone in a moment): no
    # second line in the card.
    assert a["notice"].startswith("Domovoi 1.5.0 is available")
    assert a["result"] is None
    assert a["text"].count("Domovoi 1.5.0 is available") == 2 and a["text"].endswith("Domovoi 1.5.0 is available")
    assert "Update Domovoi" in a["buttons"]


def test_highlights_are_plain_text(driven):
    b = driven["html_bullet"]["before"]
    first = b["items"][0]
    assert first["text"] == "Music <b>keeps</b> playing."
    assert first["kids"] == ["string"], "a text node, never markup"


def test_no_highlights_no_list(driven):
    b = driven["notice_no_highlights"]["before"]
    assert b["notice"] == "Domovoi 1.5.0 is available" and b["items"] == []


def test_a_main_house_behind_is_told_where_updating_brings_it(driven):
    b = driven["main_behind"]["before"]
    assert b["notice"].endswith("Updating brings this house to the newest change on main, which includes 1.5.0.")
    assert b["mainLine"] is None, "behind: (b) only, no line (c)"
    assert b["badge"] is None


# ─── (c) the main-channel line ───────────────────────────────────────────


def test_ahead_on_main_replaces_the_badge_and_the_latest_release_words(driven):
    out = driven["main_ahead"]
    for v in (out["before"], out["after"]):
        assert v["mainLine"].startswith("12 changes since 1.4.1You are ahead of the latest release on unsigned changes.")
        assert "Highlights of 1.4.1" in v["text"] and [i["text"] for i in v["items"]] == HL
        assert v["badge"] is None
        assert "not a signed release" not in v["text"]
        assert v["latest"] is None
        assert v["text"].count("1.4.1") >= 1
    a = out["after"]
    assert a["result"] == "3 commits behind", "the manual check result behaves as before"


def test_one_change_is_singular(driven):
    assert driven["main_ahead_one"]["before"]["mainLine"].startswith("1 change since 1.4.1You")


def test_same_on_main(driven):
    b = driven["main_same"]["before"]
    assert b["mainLine"] == "This is 1.4.1, the latest release."
    assert [i["text"] for i in b["items"]] == HL
    assert b["badge"] is None


def test_unknown_keeps_the_old_badge_once(driven):
    out = driven["main_unknown"]
    for v in (out["before"], out["after"]):
        assert v["mainLine"] is None
        assert v["badge"] == "main channel: not a signed release (expected)"
        assert v["text"].count("not a signed release") == 1
    assert out["after"]["latest"] == "latest release: 1.4.1"


def test_enforced_signing_keeps_its_red_badge(driven):
    b = driven["main_enforced"]["before"]
    assert b["badge"] == "unsigned" and b["mainLine"].startswith("12 changes since 1.4.1")


# ─── Home's Update ───────────────────────────────────────────────────────


def test_update_now_checks_then_runs_the_card_s_own_update(driven):
    out = driven["update_now"]
    assert "POST /api/config/version/check" in out["calls"]
    assert "POST /api/config/version/pull" in out["calls"]
    assert out["confirms"] and out["confirms"][0].startswith("Update Domovoi?")
    assert out["hash"] == "#settings/about/update-now" or out["hash"].endswith("updates")


def test_update_now_with_nothing_to_press_says_so(driven):
    out = driven["update_now_nothing"]
    assert "POST /api/config/version/pull" not in out["calls"]
    assert "Already up to date." in out["after"]["text"]


def test_update_now_does_nothing_for_a_household_member(driven):
    out = driven["update_now_member"]
    assert not any(c.startswith("POST") for c in out["calls"])
