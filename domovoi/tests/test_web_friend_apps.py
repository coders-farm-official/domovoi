"""The dashboard pieces for apps with friends (web/static/friend_apps.jsx,
design FRIEND-HOUSES-PLUGINS §9), built before the plugin mail service:
rendered against fixtures, and kept out of every page the household can
open by a flag that nothing in production sets. Plus the trust screen's
"apps with friends" lines (plugins.jsx).

Rendered with the vendored Babel in node (domovoi/tests/jsx_render_harness.js)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
STATIC = REPO_ROOT / "web" / "static"
APPS = "web/static/friend_apps.jsx"
FH = "web/static/friend_houses.jsx"
PLUGINS = "web/static/plugins.jsx"
FLAG_ON = "window.DOMOVOI_FH_APPS_PREVIEW = true;"
# friend_houses.jsx leans on these page helpers from other files.
FH_SETUP = ("globalThis.DeleteConfirmDialog = () => null;"
            "globalThis.adminReloadState = () => ({ live: false });")

FIXTURE = {
    "apps_on": True,
    "apps": [
        {"slug": "games", "name": "Games", "allowed": True, "theirs": True, "paused_until": None},
        {"slug": "garden", "name": "Garden", "allowed": False, "theirs": False, "paused_until": None},
    ],
}
FRIEND = {"id": "f1", "name": "The Smiths", "house": "ab" * 32, "verified": True,
          "fingerprint": "x", "added_at": "2026-11-01T00:00:00Z"}


def _render(scenarios: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to render the dashboard")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scenarios)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _text(nodes: list[dict]) -> str:
    return " ".join(n["text"] for n in nodes if n["text"])


# ─── the hidden flag ─────────────────────────────────────────────────────


def test_nothing_in_the_dashboard_turns_the_flag_on() -> None:
    for path in list(STATIC.glob("*.jsx")) + list(STATIC.glob("*.js")) + [STATIC / "index.html"]:
        src = path.read_text(encoding="utf-8")
        if "DOMOVOI_FH_APPS_PREVIEW" in src:
            assert path.name == "friend_apps.jsx", path.name
            assert not re.search(r"DOMOVOI_FH_APPS_PREVIEW\s*=[^=]", src), "the page sets its own flag"
    assert "DOMOVOI_FH_APPS_PREVIEW" not in (REPO_ROOT / "web" / "backend").joinpath(
        "main.py").read_text(encoding="utf-8")


def test_the_pieces_are_in_no_nav_section_or_home_card() -> None:
    src = (STATIC / "friend_apps.jsx").read_text(encoding="utf-8")
    # It registers nothing: no page, no settings section, no Home card.
    assert "window.Domovoi" not in src and "window.__" not in src
    for name in ("components.jsx", "settings.jsx", "home.jsx"):
        other = (STATIC / name).read_text(encoding="utf-8")
        assert "FhApps" not in other and "friend_apps" not in other and "fhAppsShown" not in other, name
    # friend_houses.jsx draws it only behind the flag.
    fh = (STATIC / "friend_houses.jsx").read_text(encoding="utf-8")
    uses = [line for line in fh.splitlines() if "FhFriendApps" in line or "FhApps" in line]
    assert uses and all("fhAppsShown()" in line for line in uses)


def test_the_shell_loads_it_before_friend_houses() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="friend_apps.jsx"') < html.index('src="friend_houses.jsx"')
    assert "'/friend_apps.jsx'" in (STATIC / "sw.js").read_text(encoding="utf-8")


def test_a_friend_row_has_no_apps_section_with_the_flag_off_and_one_with_it_on() -> None:
    base = {"file": FH, "component": "FhFriend", "preload": [APPS],
            "props": {"friend": FRIEND, "quiet": None},
            "fnProps": ["fire", "onChanged", "onMessage", "onShare"]}
    got = _render({
        "off": {**base, "setup": FH_SETUP},
        "on": {**base, "setup": FH_SETUP + FLAG_ON},
    })
    assert "Apps with this friend" not in _text(got["off"])
    assert not [n for n in got["off"] if "fh-apps" in str(n["props"].get("className", ""))]
    assert "Apps with this friend" in _text(got["on"])


def test_the_preview_wrapper_draws_nothing_with_the_flag_off() -> None:
    got = _render({"off": {"file": APPS, "component": "FhFriendApps", "props": {"friend": FRIEND}}})
    assert got["off"] == []


# ─── the pieces, against fixtures ────────────────────────────────────────


def test_the_apps_list_says_which_both_houses_have() -> None:
    got = _render({"on": {"file": APPS, "component": "FhAppsSection",
                          "props": {"friend": FRIEND, "data": FIXTURE, "busy": False, "confirming": False},
                          "fnProps": ["onAsk", "onConfirm", "onCancel", "onTurnOff", "onAllow"]}})
    text = _text(got["on"])
    assert "Apps with this friend" in text
    assert "both houses have it" in text and "needs the Garden plugin" in text
    boxes = [n for n in got["on"] if n["type"] == "input"]
    assert [b["props"]["checked"] for b in boxes] == [True, False]
    assert all("onChange" in b["on"] for b in boxes)
    assert "Turn off" in text and "deletes the mail waiting" in text


def test_turning_it_on_asks_first_with_every_app_ticked() -> None:
    off = {**FIXTURE, "apps_on": False}
    got = _render({
        "asking": {"file": APPS, "component": "FhAppsSection",
                   "props": {"friend": FRIEND, "data": off, "busy": False, "confirming": True},
                   "fnProps": ["onAsk", "onConfirm", "onCancel", "onTurnOff", "onAllow"]},
        "none": {"file": APPS, "component": "FhAppsSection",
                 "props": {"friend": FRIEND, "data": {"apps_on": False, "apps": []}, "busy": False,
                           "confirming": False},
                 "fnProps": ["onAsk", "onConfirm", "onCancel", "onTurnOff", "onAllow"]},
    })
    text = _text(got["asking"])
    assert "Turn on apps with The Smiths?" in text
    assert "will see which of these apps this house has" in text and "reads what that friend sends" in text
    assert [n["props"]["checked"] for n in got["asking"] if n["type"] == "input"] == [True, True]
    assert "no installed app can be used with friends" in _text(got["none"])
    # No working "Turn on" without an app to turn on.
    on_buttons = [n for n in got["none"] if n["type"] == "Button" and n["text"] == "Turn on"]
    assert all(b["props"].get("disabled") for b in on_buttons)


def test_the_reconfirm_prompt_names_the_app_and_ticks_every_friend() -> None:
    got = _render({"r": {"file": APPS, "component": "FhAppsReconfirm",
                         "props": {"app": {"slug": "games", "name": "Games"}, "busy": False,
                                   "friends": [{"id": "f1", "name": "The Smiths"},
                                               {"id": "f2", "name": "Sam's house"}]},
                         "fnProps": ["onAllow", "onLater"]}})
    text = _text(got["r"])
    assert "Games can use Apps with friends. Allow it with:" in text
    assert [n["props"]["checked"] for n in got["r"] if n["type"] == "input"] == [True, True]


def test_the_paused_notice_offers_resume_and_turn_off_and_names_a_plugin_bug() -> None:
    paused = [{"friend_id": f"f{i}", "friend": name, "slug": "games", "app": "Games",
               "paused_until": "2999-01-01T00:00:00Z"}
              for i, name in enumerate(["Sam's house", "The Smiths", "Nan's"])]
    got = _render({
        "one": {"file": APPS, "component": "FhAppsPaused", "props": {"paused": paused[:1], "busy": False},
                "fnProps": ["onResume", "onTurnOff"]},
        "many": {"file": APPS, "component": "FhAppsPaused", "props": {"paused": paused, "busy": False},
                 "fnProps": ["onResume", "onTurnOff"]},
        "none": {"file": APPS, "component": "FhAppsPaused", "props": {"paused": [], "busy": False},
                 "fnProps": ["onResume", "onTurnOff"]},
    })
    one = _text(got["one"])
    assert "Mail from Sam's house to Games is paused for" in one
    assert "it kept failing. Other friends aren’t affected." in one
    assert "Resume now" in one and "Turn off apps with Sam's house" in one
    assert "may need an update" not in one
    assert "Games has failed on mail from 3 friends; it may need an update." in _text(got["many"])
    assert got["none"] == []


def test_strings_about_a_friend_are_plain_text() -> None:
    src = (STATIC / "friend_apps.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    # It calls no route: slice 4 wires the actions.
    assert not re.search(r"\bapi(Get|Post|Put|Patch|Delete)\b|fetch\(|useApi", src)


# ─── the trust screen ────────────────────────────────────────────────────


def _preview(perms: dict, flag: bool) -> dict:
    return {"slug": "cards", "name": "Cards", "version": "1.0.0", "publisher": "tests",
            "permissions": {**perms, "warnings": ["Exchanges card moves with friend houses"]},
            "open_endpoints": [{"method": "GET", "path": "/board", "process": "core"}] if flag else [],
            "device_endpoints": [], "friend_houses_open_routes": flag}


def test_the_trust_screen_says_what_apps_with_friends_means() -> None:
    base = {"file": PLUGINS, "component": "TrustConfirmModal", "fnProps": ["onDone", "onCancel", "fire"]}
    got = _render({
        "fh": {**base, "props": {"stagedId": "s", "verb": "Install",
                                 "preview": _preview({"friend_houses": True}, False)}},
        "fh_open": {**base, "props": {"stagedId": "s", "verb": "Install",
                                      "preview": _preview({"friend_houses": True}, True)}},
        "plain": {**base, "props": {"stagedId": "s", "verb": "Install", "preview": _preview({}, False)}},
    })
    text = _text(got["fh"])
    assert "talks to friend houses (apps with friends)" in text
    assert "only with friend houses an admin turns" in text
    assert "see that this app is installed here" in text
    assert "filtered before a room says them; the chat history never keeps them" in text
    assert "Those must never reach friend houses" not in text
    assert "Those must never reach friend houses" in _text(got["fh_open"])
    assert "apps with friends" not in _text(got["plain"])
