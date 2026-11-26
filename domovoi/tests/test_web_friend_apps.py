"""The dashboard pieces for apps with friends (web/static/friend_apps.jsx,
design FRIEND-HOUSES-PLUGINS §9, build slice 6): wired to the plugin mail
service's routes (security tier), drawn in a friend's row and the notices
card of the Friend Houses page's admin zone and as an admin's rows on Home;
no preview flag left. Plus the trust screen's "apps with friends" lines
(plugins.jsx).

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
# friend_houses.jsx leans on these page helpers from other files.
FH_SETUP = ("globalThis.DeleteConfirmDialog = () => null;"
            "globalThis.adminReloadState = () => ({ live: false });")

# GET /api/friend-houses/friends/{id}/apps, as plugin_mail.apps_view answers.
VIEW = {
    "friend_id": "f1",
    "apps_on": True,
    "apps": [
        {"slug": "games", "name": "Games", "allowed": True, "needs_confirm": False, "both_have_it": True,
         "paused": False, "paused_for_sec": 0, "pause_cause": None},
        {"slug": "garden", "name": "Garden", "allowed": False, "needs_confirm": True, "both_have_it": False,
         "paused": True, "paused_for_sec": 5400, "pause_cause": "faults"},
    ],
    "notices": [],
}
FRIEND = {"id": "f1", "name": "The Smiths", "house": "ab" * 32, "verified": True,
          "fingerprint": "x", "added_at": "2026-11-01T00:00:00Z"}
PAUSED = {"kind": "paused", "friend_id": "f1", "friend": "Sam's house", "slug": "games", "app": "Games",
          "cause": "restart", "paused_for_sec": 3000,
          "text": "Mail from Sam's house to Games is paused: a mail from Sam's house made Domovoi restart "
                  "while Games handled it."}
MANY = {"kind": "plugin", "friend_id": None, "slug": "games", "app": "Games",
        "text": "Games has failed on mail from 3 friends; it may need an update."}
CONFIRMS = [{"kind": "confirm", "friend_id": f"f{i}", "friend": n, "slug": "ranch", "app": "Ranch",
             "text": f"Ranch can use Apps with friends. Allow it with {n}?"}
            for i, n in enumerate(["The Smiths", "Nan's"])]
DOCKER = {"kind": "docker", "friend_id": None, "slug": None, "app": None,
          "text": "Docker restarted; no friend was charged."}


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


# ─── live, no flag ───────────────────────────────────────────────────────


def test_no_preview_flag_is_left() -> None:
    for path in list(STATIC.glob("*.jsx")) + list(STATIC.glob("*.js")) + [STATIC / "index.html"]:
        src = path.read_text(encoding="utf-8")
        assert "DOMOVOI_FH_APPS_PREVIEW" not in src and "fhAppsShown" not in src, path.name
        assert "FH_APPS_FIXTURE" not in src, path.name


def test_the_pieces_call_only_the_apps_routes() -> None:
    src = (STATIC / "friend_apps.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    assert "fetch(" not in src
    assert "const FH_APPS_API = '/api/friend-houses';" in src
    urls = set(re.findall(r"`\$\{(?:FH_APPS_API|friendPath\(id\))\}([^`]*)`", src))
    assert urls == {"/friends/${encodeURIComponent(id)}/apps", "/apps/notices",
                    "/${encodeURIComponent(slug)}", "/${encodeURIComponent(slug)}/resume",
                    "/friends/${encodeURIComponent(id)}/apps/${encodeURIComponent(slug)}"}, urls


def test_a_friend_row_draws_the_live_apps_section() -> None:
    got = _render({"row": {"file": FH, "component": "FhFriend", "preload": [APPS],
                           "props": {"friend": FRIEND, "quiet": None},
                           "fnProps": ["fire", "onChanged", "onMessage", "onShare"],
                           "apiObject": {"data": VIEW}, "setup": FH_SETUP}})
    text = _text(got["row"])
    assert "Apps with this friend" in text and "both houses have it" in text
    assert "waiting for you to allow it" in text and "paused 2 hours" in text


def test_the_page_puts_the_notices_card_in_the_admin_zone() -> None:
    fh = (STATIC / "friend_houses.jsx").read_text(encoding="utf-8")
    zone = fh[fh.index("<FhAdminZone open="):]
    assert zone.index("<FhAppsNoticesCard") < zone.index("<FhFriendsCard")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="friend_apps.jsx"') < html.index('src="friend_houses.jsx"')
    assert "'/friend_apps.jsx'" in (STATIC / "sw.js").read_text(encoding="utf-8")


# ─── the pieces ──────────────────────────────────────────────────────────

SECTION = {"file": APPS, "component": "FhAppsSection",
           "fnProps": ["onAsk", "onConfirm", "onCancel", "onTurnOff", "onAllow"]}


def test_the_apps_list_says_which_both_houses_have() -> None:
    got = _render({"on": {**SECTION, "props": {"friend": FRIEND, "data": VIEW, "busy": False,
                                                "confirming": False}}})
    text = _text(got["on"])
    assert "Apps with this friend" in text
    assert "both houses have it" in text and "their house hasn’t said it has Garden" in text
    boxes = [n for n in got["on"] if n["type"] == "input"]
    assert [b["props"]["checked"] for b in boxes] == [True, False]
    assert all("onChange" in b["on"] for b in boxes)
    assert "Turn off" in text and "deletes the mail waiting" in text


def test_turning_it_on_asks_first_with_every_app_ticked() -> None:
    off = {**VIEW, "apps_on": False}
    got = _render({
        "asking": {**SECTION, "props": {"friend": FRIEND, "data": off, "busy": False, "confirming": True}},
        "none": {**SECTION, "props": {"friend": FRIEND, "data": {"apps_on": False, "apps": []}, "busy": False,
                                      "confirming": False}},
    })
    text = _text(got["asking"])
    assert "Turn on apps with The Smiths?" in text
    assert "will see which of these apps this house has" in text and "reads what that friend sends" in text
    assert [n["props"]["checked"] for n in got["asking"] if n["type"] == "input"] == [True, True]
    assert "no installed app can be used with friends" in _text(got["none"])
    # No misleading controls: no "Turn on" at all without an app to turn on.
    assert not [n for n in got["none"] if n["type"] == "Button" and n["text"] == "Turn on"]


def test_the_reconfirm_prompt_names_the_app_and_ticks_every_friend() -> None:
    got = _render({"r": {"file": APPS, "component": "FhAppsReconfirm",
                         "props": {"app": {"slug": "games", "name": "Games"}, "busy": False,
                                   "friends": [{"id": "f1", "name": "The Smiths"},
                                               {"id": "f2", "name": "Sam's house"}]},
                         "fnProps": ["onAllow", "onLater"]}})
    text = _text(got["r"])
    assert "Games can use Apps with friends. Allow it with:" in text
    assert [n["props"]["checked"] for n in got["r"] if n["type"] == "input"] == [True, True]


def test_the_paused_notice_uses_the_servers_words_with_resume_and_turn_off() -> None:
    base = {"file": APPS, "component": "FhAppsPaused", "fnProps": ["onResume", "onTurnOff"]}
    got = _render({
        "one": {**base, "props": {"paused": [PAUSED], "busy": False}},
        "many": {**base, "props": {"paused": [PAUSED], "many": [MANY], "busy": False}},
        "none": {**base, "props": {"paused": [], "busy": False}},
    })
    one = _text(got["one"])
    assert PAUSED["text"] in one
    assert "Resume now" in one and "Turn off apps with Sam's house" in one
    assert "may need an update" not in one
    assert MANY["text"] in _text(got["many"])
    assert got["none"] == []


def test_the_notices_card_draws_every_kind_and_nothing_when_quiet() -> None:
    base = {"file": APPS, "component": "FhAppsNoticesCard", "fnProps": ["fire", "onChanged"]}
    got = _render({
        "all": {**base, "apiObject": {"data": {"notices": [PAUSED, MANY, DOCKER] + CONFIRMS}}},
        "quiet": {**base, "apiObject": {"data": {"notices": []}}},
    })
    text = _text(got["all"])
    for n in (PAUSED, MANY, DOCKER):
        assert n["text"] in text
    # One re-confirm prompt for the app, every friend it waits for ticked.
    assert text.count("Ranch can use Apps with friends. Allow it with:") == 1
    assert [n["props"]["checked"] for n in got["all"] if n["type"] == "input"] == [True, True]
    assert got["quiet"] == []


def test_home_gets_one_row_per_notice_and_one_per_app_waiting() -> None:
    data = json.dumps({"notices": [PAUSED, DOCKER] + CONFIRMS})
    probe = ("() => React.createElement('div', null, fhAppsHomeRows(" + data + ")"
             ".map((r) => [r.scope, r.href, r.text].join('~')).join('|'))")
    got = _render({"rows": {"file": APPS, "component": probe}})
    rows = _text(got["rows"]).split("|")
    assert len(rows) == 3
    assert all(r.startswith("admin~#settings/friend-houses~") for r in rows)
    assert rows[-1].endswith("Ranch can use Apps with friends: allow it with 2 friends?")
    home = (STATIC / "home.jsx").read_text(encoding="utf-8")
    # Asked for by an admin only, never on a shared screen.
    assert "useApiObject(admin && !shared ? HOME_FH_APPS : null" in home


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
