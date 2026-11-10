"""The first-run welcome and "you're all set" (web/static/components.jsx).

Owner, 2026-11-04, after a real Mac install: a new owner opened the
dashboard, everything looked usable, and nothing said "start here"; the
only way to claim the house was a small Home row. What the dashboard does
now, and what these tests pin:

* while the house is unclaimed, every page load (AuthModalHost, rendered
  by the shell on every page, the plain root address included) opens a
  welcome that HOLDS the claim: setup code, admin password, confirm;
* "where's my code?" names the place for this kind of server (the Mac's
  Setup window and its menu-bar route, the Windows installer's last page,
  the file on a Linux server) and never a menu item that doesn't exist;
* "Not now" puts it away for this browser (localStorage), the Home row's
  "claim it" brings it back;
* it NEVER shows on a claimed house: not to a guest, not to an admin, not
  when the status read failed;
* after the claim, "you're all set" shows the household token with copy,
  says it is not the admin password, and offers "Want an easier one?"
  (POST /api/auth/device-token, refused server-side under 12 characters)
  or "Keep this one"; then the internet question, as before;
* the admin setup dialog's claim (the Windows installer's address) gets
  the same "you're all set";
* neither secret ever rides in an address or an error line.

Driven through domovoi/tests/jsx_interact_harness.js on top of the REAL
auth.js and data.js, as the Home tests are (their PRELUDE: a scripted
fetch). No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_home_page import (
    NOW,
    PRELUDE,
    STATUS_ADMIN,
    STATUS_CLAIMED,
    STATUS_UNCLAIMED,
    cfg,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = REPO_ROOT / "web" / "static" / "components.jsx"

FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx"]
COMPONENT = ("(window.__Auth = Auth, window.__FR = FirstRun, window.__CA = ConnectAsk,"
             " window.__shows = welcomeShows, AuthModalHost)")

CODE = "apple-brick-cloud-drum-eagle-flute-grape-house"
PASSWORD = "correct horse battery"
TOKEN = "tall-green-fern-river-stone"
MINE = "maple-river-lamp"
DISMISS_KEY = "domovoi-welcome-dismissed"


def table(status: dict | None = None, **over) -> dict:
    t = {
        "GET /api/auth/status": status if status is not None else STATUS_UNCLAIMED,
        "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/auth/setup": {"token": "session-bearer", "setup_complete": True},
        "GET /api/auth/device-token": {"token": TOKEN, "header": "X-Device-Token"},
        "GET /api/config": {**cfg(), "internet_access": ""},
    }
    t.update(over)
    return t


# What a scenario returns: the dialog on screen (its aria-label), its text,
# every request made, and what this browser stored.
SNAP = r"""
const snap = () => {
  const dlg = h.find((e) => e.props && e.props.role === 'dialog');
  const inet = h.find((e) => w.__cls(e).includes('internet-choice'));
  const tok = h.find((e) => e.props && e.props['data-testid'] === 'first-run-token');
  return {
    dialog: dlg ? dlg.props['aria-label'] : (inet ? 'internet' : null),
    text: h.text().join(' '),
    token: tok ? w.__deepText(tok) : null,
    fetches: w.__fetches.map((f) => `${f.method} ${f.path}`),
    bodies: w.__fetches.filter((f) => f.body).map((f) => ({ path: f.path, body: f.body })),
    dismissed: h.global('localStorage').getItem('domovoi-welcome-dismissed'),
    paired: h.global('localStorage').getItem('domovoi-device-token'),
    buttons: h.findAll({ type: 'button' }).map((e) => ({ text: w.__deepText(e), disabled: !!e.props.disabled })),
  };
};
const empty = (e) => e.type === 'input' && e.props.type === 'password' && e.props.value === '';
const claim = async (code) => {
  await h.type({ placeholder: 'eight-words-separated-by-dashes' }, code);
  await h.type(empty, __PASSWORD);
  await h.type(empty, __PASSWORD);
  await h.click({ type: 'button', text: 'set password' });
  await w.__flush(h, 10);
};
"""


def scenario(tbl: dict, script: str, *, ls: dict | None = None) -> dict:
    head = (f"const __NOW0 = {NOW}; const __TABLE = {json.dumps(tbl)};"
            f" const __LS = {json.dumps(ls or {})}; const __PHONE = null;"
            " const __MANIFEST = null;\n")
    return {"files": FILES, "component": COMPONENT, "props": {},
            "setup": head + PRELUDE,
            "script": (f"const __PASSWORD = {json.dumps(PASSWORD)};\n"
                       "const w = h.global('window'); h.render(); await w.__flush(h);\n"
                       + SNAP + script)}


SCENARIOS: dict[str, dict] = {}

# ── when it shows ────────────────────────────────────────────────────────
SCENARIOS["unclaimed"] = scenario(table(), "return snap();")
SCENARIOS["claimed_guest"] = scenario(table(STATUS_CLAIMED), "return snap();")
SCENARIOS["claimed_admin"] = scenario(table(STATUS_ADMIN), "return snap();",
                                      ls={"domovoi-device-token": "house-token"})
# A claimed house where this browser once said "Not now", and someone
# presses a stale "claim it": still nothing.
SCENARIOS["claimed_asked_anyway"] = scenario(
    table(STATUS_CLAIMED), "w.__FR.open(); await w.__flush(h); return snap();",
    ls={DISMISS_KEY: "1"})
SCENARIOS["status_unreadable"] = scenario(
    table(**{"GET /api/auth/status": {"__status": 502, "__body": {"detail": "bad gateway"}}}),
    "return snap();")
SCENARIOS["dismissed_before"] = scenario(
    table(),
    "const before = snap(); w.__FR.open(); await w.__flush(h); return { before, after: snap() };",
    ls={DISMISS_KEY: "1"})
SCENARIOS["not_now"] = scenario(
    table(), "await h.click({ type: 'button', text: 'Not now' }); await w.__flush(h); return snap();")

# ── where's my code ──────────────────────────────────────────────────────
for _kind in ("mac", "wsl", "linux", "darwin", None):
    caps = {"host_kind": _kind} if _kind else {"plugins": []}
    SCENARIOS[f"where_{_kind or 'unknown'}"] = scenario(
        table(**{"GET /api/capabilities": caps}),
        "const before = snap();"
        "await h.click({ type: 'button', text: 'where’s my code?' }); await w.__flush(h);"
        "const hint = h.find((e) => w.__cls(e).includes('first-run-where'));"
        "const text = hint ? h.tree().filter((e) => e === hint || h.ancestors(e).includes(hint))"
        "  .map((e) => e.text).filter(Boolean).join(' ') : null;"
        "return { before: before.text, hint: text };")

# ── the claim, and "you're all set" ──────────────────────────────────────
SCENARIOS["claim_then_keep"] = scenario(
    table(),
    f"await claim({json.dumps(CODE)}); const allSet = snap();"
    "await h.click({ type: 'button', text: 'Keep this one' }); await w.__flush(h, 10);"
    "return { allSet, after: snap() };")
SCENARIOS["claim_then_easier"] = scenario(
    # autoPair's read, "you're all set"'s, then the read-back after the save.
    table(**{"POST /api/auth/device-token": {"token": MINE, "header": "X-Device-Token"},
             "GET /api/auth/device-token": {"__seq": [
                 {"token": TOKEN, "header": "X-Device-Token"}, {"token": TOKEN, "header": "X-Device-Token"},
                 {"token": MINE, "header": "X-Device-Token"}]}}),
    f"await claim({json.dumps(CODE)});"
    "await h.click({ type: 'button', text: 'Want an easier one?' }); await w.__flush(h);"
    "const field = (e) => e.type === 'input' && e.props.placeholder === 'maple-river-lamp';"
    "await h.type(field, 'too-short'); const short = snap();"
    f"await h.type(field, {json.dumps(MINE)});"
    "await h.click({ type: 'button', text: 'Use this' }); await w.__flush(h, 10);"
    "return { short, saved: snap() };")
SCENARIOS["claim_easier_refused"] = scenario(
    table(**{"POST /api/auth/device-token": {
        "__status": 400, "__body": {"detail": "letters, digits, punctuation and spaces only"}}}),
    f"await claim({json.dumps(CODE)});"
    "await h.click({ type: 'button', text: 'Want an easier one?' }); await w.__flush(h);"
    "await h.type((e) => e.type === 'input' && e.props.placeholder === 'maple-river-lamp', 'twelve-chars-or-more');"
    "await h.click({ type: 'button', text: 'Use this' }); await w.__flush(h, 10);"
    "return snap();")
SCENARIOS["wrong_code"] = scenario(
    table(**{"POST /api/auth/setup": {"__status": 403, "__body": {
        "detail": "setup code required — read it from ~/.domovoi/setup-code.txt on the Domovoi server"}}}),
    "await claim('not-the-code'); return snap();")
SCENARIOS["password_mismatch"] = scenario(
    table(),
    f"await h.type({{ placeholder: 'eight-words-separated-by-dashes' }}, {json.dumps(CODE)});"
    "await h.type(empty, 'first password here'); await h.type(empty, 'second password!!');"
    "await h.click({ type: 'button', text: 'set password' }); await w.__flush(h);"
    "return snap();")
# The admin setup dialog (Settings, the Windows installer's address) claims
# too, and lands on the same "you're all set".
SCENARIOS["dialog_claim"] = scenario(
    table(),
    "w.__Auth.openModal(); await w.__flush(h);"
    "const dialog = snap();"
    f"await claim({json.dumps(CODE)});"
    "return { dialog, after: snap() };")


# ── review round 1 (2026-11-04) ──────────────────────────────────────────
# A welcome standing on a house claimed since (another tab, or this claim's
# answer lost): the 409 re-reads the status, the welcome closes and the
# admin sign-in opens in its place.
SCENARIOS["stale_409"] = scenario(
    table(**{"GET /api/auth/status": {"__seq": [STATUS_UNCLAIMED, STATUS_CLAIMED]},
             "POST /api/auth/setup": {"__status": 409, "__body": {"detail": "admin setup already completed"}}}),
    f"const before = snap(); await claim({json.dumps(CODE)});"
    "return { before, after: snap(), login: w.__Auth.modalOpen };")
# Claimed elsewhere while it stood open: coming back to the tab closes it.
SCENARIOS["focus_rereads"] = scenario(
    table(**{"GET /api/auth/status": {"__seq": [STATUS_UNCLAIMED, STATUS_CLAIMED]}}),
    "const before = snap(); w.__fire('focus'); await w.__flush(h, 8); return { before, after: snap() };")
# "Not now" is per server.
_SERVER = "http://192.168.1.20:6369"
SCENARIOS["dismissed_other_server"] = scenario(
    table(), "return snap();", ls={"domovoi-server": _SERVER, DISMISS_KEY: "1"})
SCENARIOS["dismissed_this_server"] = scenario(
    table(), "return snap();", ls={"domovoi-server": _SERVER, f"{DISMISS_KEY}@{_SERVER}": "1"})
SCENARIOS["not_now_keyed"] = scenario(
    table(), "await h.click({ type: 'button', text: 'Not now' }); await w.__flush(h);"
    f"return {{ bare: h.global('localStorage').getItem({json.dumps(DISMISS_KEY)}),"
    f" keyed: h.global('localStorage').getItem({json.dumps(DISMISS_KEY + '@' + _SERVER)}) }};",
    ls={"domovoi-server": _SERVER})
# The token didn't load: no "Keep this one" over nothing.
SCENARIOS["token_unloaded"] = scenario(
    table(**{"GET /api/auth/device-token": {"__status": 500, "__body": {"detail": "no"}}}),
    f"await claim({json.dumps(CODE)}); return snap();")
# Plain http on the LAN: no navigator.clipboard; the textarea copy works.
_COPY_STUBS = (
    "window.isSecureContext = false; window.__copied = [];"
    "document.body = { appendChild: (n) => { window.__node = n; }, removeChild: () => {} };"
    "document.createElement = () => ({ setAttribute() {}, style: {}, select() {} });"
    "document.execCommand = (c) => { window.__copied.push(c + ':' + window.__node.value); return true; };"
)
SCENARIOS["copy_on_plain_http"] = scenario(
    table(),
    _COPY_STUBS.replace("window.", "w.").replace("document.", "h.global('document').")
    + f"await claim({json.dumps(CODE)});"
    "await h.click({ type: 'button', text: 'copy' }); return { s: snap(), copied: w.__copied };")

# ── the connect prompt: a claimed house, an unpaired browser ─────────────
REFUSAL = {"__status": 401, "__body": {"detail": "X-Device-Token or admin session required"}}
WRONG = "not-the-household-token"
SCENARIOS["connect_guest"] = scenario(table(STATUS_CLAIMED), "return snap();")
SCENARIOS["connect_paired"] = scenario(table(STATUS_CLAIMED), "return snap();",
                                       ls={"domovoi-device-token": "house-token"})
SCENARIOS["connect_admin"] = scenario(table(STATUS_ADMIN), "return snap();")
SCENARIOS["connect_unclaimed"] = scenario(table(), "return snap();")
SCENARIOS["connect_wrong_token"] = scenario(
    table(STATUS_CLAIMED, **{"GET /api/satellites": REFUSAL}),
    f"await h.type((e) => e.type === 'input' && e.props.autoComplete === 'off', {json.dumps(WRONG)});"
    "await h.click({ type: 'button', text: 'Connect' }); await w.__flush(h); return snap();")
SCENARIOS["connect_right_token"] = scenario(
    table(STATUS_CLAIMED, **{"GET /api/satellites": []}),
    f"await h.type((e) => e.type === 'input' && e.props.autoComplete === 'off', {json.dumps(TOKEN)});"
    "await h.click({ type: 'button', text: 'Connect' }); await w.__flush(h); return snap();")
SCENARIOS["connect_where"] = scenario(
    table(STATUS_CLAIMED),
    "await h.click({ type: 'button', text: 'Where do I find it?' }); await w.__flush(h); return snap();")
SCENARIOS["connect_not_now"] = scenario(
    table(STATUS_CLAIMED),
    "await h.click({ type: 'button', text: 'Not now' }); await w.__flush(h); const after = snap();"
    "w.__CA.open(); await w.__flush(h);"
    "return { after, key: h.global('localStorage').getItem('domovoi-connect-dismissed'), again: snap() };")
SCENARIOS["connect_dismissed_before"] = scenario(
    table(STATUS_CLAIMED), "return snap();", ls={"domovoi-connect-dismissed": "1"})


# ── review round 2 ───────────────────────────────────────────────────────
# A save that settles while another "you're all set" is mounted (the
# login prompt unmounted the first, data.js replayed the save later):
# FirstRun.tokenChanged makes the mounted one read the token again.
SCENARIOS["token_changed_notice"] = scenario(
    table(**{"GET /api/auth/device-token": {"__seq": [
        {"token": TOKEN, "header": "X-Device-Token"}, {"token": TOKEN, "header": "X-Device-Token"},
        {"token": MINE, "header": "X-Device-Token"}]}}),
    f"await claim({json.dumps(CODE)}); const before = snap();"
    "w.__FR.tokenChanged(); await w.__flush(h, 8);"
    "return { before, after: snap() };")
# Closing the older pair prompt is this page's answer: the connect prompt
# doesn't ask again behind it.
SCENARIOS["pair_modal_closed"] = scenario(
    table(STATUS_CLAIMED),
    "const first = snap(); w.__Auth.requestPairing(); await w.__flush(h);"
    "const pairing = { open: w.__Auth.pairModalOpen, text: snap().text };"
    "await h.click((e) => w.__cls(e).includes('cal-modal-bg')); await w.__flush(h);"
    "return { first, pairing, after: snap(), stored: h.global('localStorage').getItem('domovoi-connect-dismissed') };")

@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("first-run") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _no_secret_in_an_address(snap: dict, *secrets: str) -> None:
    for f in snap["fetches"]:
        for s in secrets:
            assert s not in f, f


# ── when it shows ────────────────────────────────────────────────────────

def test_an_unclaimed_house_is_welcomed_with_the_claim_form_itself(driven) -> None:
    s = driven["unclaimed"]
    assert s["dialog"] == "Welcome to Domovoi"
    assert "Welcome to Domovoi!" in s["text"]
    # Suits someone who is not the owner too.
    assert "Setting up Domovoi? Enter the setup code from the computer it’s installed on." in s["text"]
    # The form, not a link to it.
    for label in ("setup code", "admin password (min 10 chars)", "confirm password"):
        assert label in s["text"]
    texts = [b["text"] for b in s["buttons"]]
    assert "Not now" in texts and "set password" in texts and "where’s my code?" in texts


@pytest.mark.parametrize("name", ["claimed_guest", "claimed_admin", "claimed_asked_anyway",
                                  "status_unreadable"])
def test_it_never_shows_on_a_claimed_house_or_without_a_status(driven, name) -> None:
    s = driven[name]
    # A guest on a claimed house may get the connect prompt instead
    # (below); never the welcome.
    assert s["dialog"] in (None, "Connect this browser"), s["text"]
    assert "Welcome to Domovoi" not in s["text"]
    assert "POST /api/auth/setup" not in s["fetches"]


def test_not_now_puts_it_away_for_this_browser(driven) -> None:
    s = driven["not_now"]
    assert s["dialog"] is None
    assert s["dismissed"] == "1"
    before = driven["dismissed_before"]
    assert before["before"]["dialog"] is None
    # The Home row's "claim it" (FirstRun.open) brings it back.
    assert before["after"]["dialog"] == "Welcome to Domovoi"


def test_the_rule_alone() -> None:
    """welcomeShows, read from the source: the claim gate is setup_complete
    === false and nothing looser."""
    src = COMPONENTS.read_text(encoding="utf-8")
    start = src.index("const welcomeShows")
    body = src[start:src.index("};", start)]
    assert "status.setup_complete !== false) return false" in body


# ── where's my code ──────────────────────────────────────────────────────

def test_wheres_my_code_on_a_mac_names_setup_and_the_real_menu_route(driven) -> None:
    s = driven["where_mac"]
    assert s["hint"] is not None
    assert "Domovoi Setup window" in s["hint"]
    # mac/Sources/DomovoiMacKit/MenuItems.swift + SetupViews.swift: the menu
    # has "Repair or Uninstall Domovoi..." which opens Setup's installed page
    # with "Show Setup Code..."; there is no setup-code item in the menu.
    assert "Repair or Uninstall Domovoi…, then Show Setup Code…" in s["hint"]
    assert "~/.domovoi" not in s["hint"] and "Windows" not in s["hint"]
    # Folded until asked.
    assert "Domovoi Setup window" not in s["before"]


def test_wheres_my_code_on_windows_and_linux(driven) -> None:
    win = driven["where_wsl"]["hint"]
    assert "last page of Domovoi’s Windows installer" in win and "~/.domovoi" not in win
    for kind in ("linux", "darwin"):
        hint = driven[f"where_{kind}"]["hint"]
        assert "~/.domovoi/setup-code.txt" in hint and "installer" not in hint, kind
    unknown = driven["where_unknown"]["hint"]
    assert "Windows" in unknown and "Mac" in unknown and "~/.domovoi/setup-code.txt" in unknown


# ── the claim, and "you're all set" ──────────────────────────────────────

def test_claiming_shows_the_household_token_and_what_it_is(driven) -> None:
    out = driven["claim_then_keep"]
    s = out["allSet"]
    assert s["dialog"] == "You're all set"
    assert "You’re all set!" in s["text"]
    assert s["token"] == TOKEN
    assert "This is not your admin password." in s["text"]
    assert "every phone, tablet and browser asks for it once" in s["text"]
    assert "admin password is only for changing settings" in s["text"]
    texts = [b["text"] for b in s["buttons"]]
    assert "copy" in texts and "Want an easier one?" in texts and "Keep this one" in texts
    # The claim went in the body, never in an address.
    setup = [b for b in s["bodies"] if b["path"] == "/api/auth/setup"]
    assert len(setup) == 1 and json.loads(setup[0]["body"]) == {"setup_code": CODE, "password": PASSWORD}
    _no_secret_in_an_address(s, CODE, PASSWORD, TOKEN)
    # Signed in and paired by the claim (auth.js autoPair).
    assert s["paired"] == TOKEN


def test_keep_this_one_is_one_click_and_the_internet_question_follows(driven) -> None:
    after = driven["claim_then_keep"]["after"]
    assert after["dialog"] == "internet"
    assert "POST /api/auth/device-token" not in after["fetches"]


def test_an_easier_token_is_checked_by_the_server_and_takes_over(driven) -> None:
    out = driven["claim_then_easier"]
    short = out["short"]
    use = [b for b in short["buttons"] if b["text"] == "Use this"]
    assert use and use[0]["disabled"] is True          # under 12 characters
    assert "At least 12 characters" in short["text"] and "maple-river-lamp" in short["text"]
    saved = out["saved"]
    posted = [b for b in saved["bodies"] if b["path"] == "/api/auth/device-token"]
    assert len(posted) == 1 and json.loads(posted[0]["body"]) == {"token": MINE}
    assert saved["token"] == MINE and saved["paired"] == MINE
    assert "Done" in [b["text"] for b in saved["buttons"]]
    _no_secret_in_an_address(saved, MINE, TOKEN)


def test_a_refused_easier_token_says_why_without_repeating_it(driven) -> None:
    s = driven["claim_easier_refused"]
    assert s["dialog"] == "You're all set"
    assert "letters, digits, punctuation and spaces only" in s["text"]
    assert s["token"] == TOKEN and s["paired"] == TOKEN
    # The typed value is in its field, not in any error line.
    assert s["text"].count("twelve-chars-or-more") == 0


def test_a_wrong_code_keeps_the_welcome_and_says_where_the_right_one_is(driven) -> None:
    s = driven["wrong_code"]
    assert s["dialog"] == "Welcome to Domovoi"
    assert "setup code required" in s["text"]
    assert "not-the-code" not in s["text"]
    _no_secret_in_an_address(s, "not-the-code", PASSWORD)


def test_the_form_checks_the_passwords_before_sending(driven) -> None:
    s = driven["password_mismatch"]
    assert "passwords do not match" in s["text"]
    assert "POST /api/auth/setup" not in s["fetches"]


def test_the_admin_setup_dialog_claim_lands_on_all_set_too(driven) -> None:
    out = driven["dialog_claim"]
    # The installer's labels (test_web_settings_routes) are unchanged.
    assert "first-run admin setup" in out["dialog"]["text"]
    assert out["after"]["dialog"] == "You're all set"
    assert out["after"]["token"] == TOKEN


# ── review round 1 ───────────────────────────────────────────────────────

def test_a_stale_welcome_on_a_house_claimed_since_closes_and_offers_the_sign_in(driven) -> None:
    out = driven["stale_409"]
    assert out["before"]["dialog"] == "Welcome to Domovoi"
    after = out["after"]
    assert after["dialog"] is None
    assert out["login"] is True
    assert "admin login" in after["text"]                  # the sign-in, not the setup form
    assert "Welcome to Domovoi!" not in after["text"]


def test_coming_back_to_the_tab_rereads_the_status(driven) -> None:
    out = driven["focus_rereads"]
    assert out["before"]["dialog"] == "Welcome to Domovoi"
    # Now a claimed house this browser isn't paired with: the connect
    # prompt, never the welcome.
    assert out["after"]["dialog"] == "Connect this browser"
    assert out["after"]["fetches"].count("GET /api/auth/status") >= 2


def test_not_now_is_per_server(driven) -> None:
    assert driven["dismissed_other_server"]["dialog"] == "Welcome to Domovoi"
    assert driven["dismissed_this_server"]["dialog"] is None
    keyed = driven["not_now_keyed"]
    assert keyed["bare"] is None and keyed["keyed"] == "1"


def test_no_keep_this_one_without_a_token(driven) -> None:
    s = driven["token_unloaded"]
    assert s["dialog"] == "You're all set" and s["token"] is None
    texts = [b["text"] for b in s["buttons"]]
    assert "Done" in texts and "Keep this one" not in texts
    assert "Settings → Household → Phones & browsers → Household token" in s["text"]


def test_copy_works_on_plain_http(driven) -> None:
    out = driven["copy_on_plain_http"]
    assert out["copied"] == [f"copy:{TOKEN}"]
    assert "copied" in out["s"]["text"] and "copy failed" not in out["s"]["text"]


# ── the connect prompt ───────────────────────────────────────────────────

def test_an_unpaired_browser_on_a_claimed_house_is_asked_to_connect(driven) -> None:
    s = driven["connect_guest"]
    assert s["dialog"] == "Connect this browser"
    assert "Welcome!" in s["text"]
    assert "Enter this home’s household token to connect this browser" in s["text"]
    assert "household token" in s["text"] and "not the admin password" in s["text"]
    texts = [b["text"] for b in s["buttons"]]
    assert "Connect" in texts and "Not now" in texts and "Where do I find it?" in texts
    assert "phone" not in " ".join(texts)


@pytest.mark.parametrize("name", ["connect_paired", "connect_admin"])
def test_never_to_a_paired_or_signed_in_browser(driven, name) -> None:
    assert driven[name]["dialog"] is None


def test_never_on_an_unclaimed_house(driven) -> None:
    # The welcome's job.
    assert driven["connect_unclaimed"]["dialog"] == "Welcome to Domovoi"


def test_a_wrong_token_says_so_without_repeating_it(driven) -> None:
    s = driven["connect_wrong_token"]
    assert s["dialog"] == "Connect this browser"
    assert "that isn't this home's household token" in s["text"]
    assert WRONG not in s["text"]
    assert s["paired"] is None
    # In a header, never an address.
    _no_secret_in_an_address(s, WRONG)


def test_the_right_token_connects_and_the_prompt_goes(driven) -> None:
    s = driven["connect_right_token"]
    assert s["dialog"] is None
    assert s["paired"] == TOKEN
    _no_secret_in_an_address(s, TOKEN)


def test_where_do_i_find_it(driven) -> None:
    s = driven["connect_where"]
    assert ("Ask whoever set up Domovoi — it’s in Settings → Household → Phones & browsers"
            " → Household token.") in s["text"]


def test_connect_not_now_and_back(driven) -> None:
    out = driven["connect_not_now"]
    assert out["after"]["dialog"] is None and out["key"] == "1"
    assert out["again"]["dialog"] == "Connect this browser"
    assert driven["connect_dismissed_before"]["dialog"] is None


# ── review round 2 ───────────────────────────────────────────────────────

def test_a_settled_save_makes_the_mounted_step_read_the_token_again(driven) -> None:
    out = driven["token_changed_notice"]
    assert out["before"]["token"] == TOKEN
    assert out["after"]["token"] == MINE
    assert "Done" in [b["text"] for b in out["after"]["buttons"]]


def test_one_token_prompt_at_a_time(driven) -> None:
    out = driven["pair_modal_closed"]
    assert out["first"]["dialog"] == "Connect this browser"
    assert out["pairing"]["open"] is True
    assert "Welcome!" not in out["pairing"]["text"]          # the pair prompt alone
    assert out["after"]["dialog"] is None                    # not asked again behind it
    assert out["stored"] is None                             # for this page only
