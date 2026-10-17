"""A dashboard tab that holds a household token the server no longer takes
(2026-10-17).

The owner's phone app was locked out of a reinstalled Domovoi by a browser
tab on the same phone. The tab still held the old install's household token,
sent it on every poll and every state-socket reconnect, and so kept the
server's per-address wrong-token wait (429) running for every device at that
address, the app included.

Acceptance:

1. A device-tier 401/403 to a request that CARRIED the stored token marks it
   refused. It is not sent again: not as the header, not on the state socket,
   not in a media URL, and not after a reload. A read refused for it opens no
   modal (the notice says it once); a mutation still offers pairing, and the
   replay carries the new token.
2. A new token (pasted, or fetched by an admin sign-in) is sent at once.
3. A 429 for wrong household tokens sets the server's wait, read from
   Retry-After or, when a cross-origin page cannot see that header, from the
   detail's "retry in Ns". Nothing carrying the token goes out until it ends,
   and no prompt opens. A 429 about anything else does not hold the token.
4. With no token stored, nothing changes: the pair modal, as before.
5. The notice: "doesn't recognise this browser any more" with pair again /
   sign in as admin / try the old token again, and the wait counted down,
   in the page's words, not the server's.

Both halves are DB-free and need ``node``. The store runs the real
``auth.js`` + ``data.js`` with the scripted ``fetch`` of
``test_web_device_token_pairing`` (given response headers here). The notice
is the real component in ``jsx_interact_harness.js``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_device_token_pairing import DEVICE_401, HARNESS_JS, OK, STATIC

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"

# The scripted fetch, with response headers (Retry-After) on top.
_STUB_TAIL = "text: async () => text, json: async () => JSON.parse(text) };"
assert _STUB_TAIL in HARNESS_JS, "the shared harness's fetch stub changed shape"
_STUB_CALL = "calls.push({ url, method: (opts.method || 'GET').toUpperCase(), headers: opts.headers || {} });"
assert _STUB_CALL in HARNESS_JS, "the shared harness's fetch stub changed shape"
STORE_HARNESS = HARNESS_JS.replace(
    _STUB_CALL,
    _STUB_CALL + "\n    if (responses[i].throws) throw new TypeError('Failed to fetch');",
).replace(
    _STUB_TAIL,
    "text: async () => text, json: async () => JSON.parse(text),\n"
    "             headers: { get: (k) => { const hs = responses[i].headers || {};\n"
    "               const f = Object.keys(hs).find((x) => x.toLowerCase() === String(k).toLowerCase());\n"
    "               return f ? String(hs[f]) : null; } } };",
)

THROTTLED = {
    "status": 429,
    "headers": {"Retry-After": "42"},
    "body": {"detail": "too many wrong X-Device-Token values from this address — retry in 41s. A device or "
                       "browser tab at this address may still be sending an old household token (after a "
                       "reinstall or a token change): pair it again or close it, or sign in as an admin"},
}
# The same answer as a cross-origin page sees it: no readable Retry-After.
THROTTLED_CROSS_ORIGIN = {"status": 429, "body": {"detail": "too many wrong X-Device-Token values — retry in 77s, or sign in as an admin"}}
LOGIN_THROTTLED = {"status": 429, "headers": {"Retry-After": "30"},
                   "body": {"detail": "too many failed attempts — retry in 30s"}}

TOKEN = {"domovoi-device-token": "old-household"}
DB_BLIP = {"status": 503, "headers": {"Retry-After": "5"},
           "body": {"detail": "auth check unavailable — the server could not check credentials just now; try again shortly"}}
PLUGIN_401 = {"status": 401, "body": {"detail": "this plugin wants a fresh X-Device-Token"}}
NO_ANSWER = {"status": 0, "throws": True, "body": ""}
HDR = "(c) => c.headers['X-Device-Token'] || null"

STORE_SCENARIOS = {
    "a_refused_token_stops_at_once": {
        "storage": TOKEN,
        "responses": [DEVICE_401],
        "script": rf"""
          const first = await h.attempt(() => h.w.apiGet('/api/rooms'));
          for (let i = 0; i < 3; i++) await h.attempt(() => h.w.apiGet('/api/rooms'));
          h.w.stateBus.subscribe(() => {{}});
          return {{ first, headers: h.calls.map({HDR}),
                   trouble: h.Auth.deviceTrouble(), pairModal: h.Auth.pairModalOpen,
                   loginModal: h.Auth.modalOpen, paired: h.Auth.isPaired(),
                   stored: h.Auth.storedDeviceToken(),
                   socketProtocols: h.sockets.map((s) => s.protocols),
                   media: h.w.withDeviceToken('/api/images/1/raw') }};
        """,
    },
    "a_refused_token_stays_refused_after_a_reload": {
        "storage": {**TOKEN, "domovoi-device-token-refused": "old-household"},
        "responses": [OK],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ headers: h.calls.map({HDR}), trouble: h.Auth.deviceTrouble() }};
        """,
    },
    "a_new_token_is_sent_at_once": {
        "storage": TOKEN,
        "responses": [DEVICE_401, OK],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          const before = h.Auth.deviceTrouble();
          h.Auth.pair('new-household');
          const out = await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ before, after: h.Auth.deviceTrouble(), out, headers: h.calls.map({HDR}),
                   stored: h.storage.dump() }};
        """,
    },
    "a_pressed_action_still_offers_pairing_and_replays": {
        "storage": TOKEN,
        "responses": [DEVICE_401, OK],
        "script": rf"""
          const pending = h.attempt(() => h.w.apiPost('/api/music/queue', {{ a: 1 }}));
          await h.settle();
          const modal = h.Auth.pairModalOpen;
          h.Auth.pair('new-household');
          const out = await pending;
          return {{ modal, out, headers: h.calls.map({HDR}) }};
        """,
    },
    "try_the_old_token_again": {
        "storage": TOKEN,
        "responses": [DEVICE_401, OK],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          const v = h.Auth.credentialVersion;
          const probe = await h.Auth.retryRefusedDeviceToken();
          const out = await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ probe, out, headers: h.calls.map({HDR}), urls: h.calls.map((c) => c.url),
                   bumped: h.Auth.credentialVersion > v, trouble: h.Auth.deviceTrouble() }};
        """,
    },
    "try_again_refused_again_is_one_request": {
        "storage": TOKEN,
        "responses": [DEVICE_401],
        "script": r"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          const v = h.Auth.credentialVersion;
          const probe = await h.Auth.retryRefusedDeviceToken();
          return { probe, calls: h.calls.length, bumped: h.Auth.credentialVersion > v,
                   trouble: h.Auth.deviceTrouble() };
        """,
    },
    "a_db_blip_is_not_a_refusal": {
        "storage": TOKEN,
        "responses": [DB_BLIP, OK],
        "script": rf"""
          const first = await h.attempt(() => h.w.apiGet('/api/rooms'));
          const trouble = h.Auth.deviceTrouble();
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ first, trouble, headers: h.calls.map({HDR}), pairModal: h.Auth.pairModalOpen }};
        """,
    },
    "a_plugin_401_is_not_a_refusal": {
        "storage": TOKEN,
        "responses": [PLUGIN_401, OK],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/plugins/x/thing'));
          const trouble = h.Auth.deviceTrouble();
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ trouble, headers: h.calls.map({HDR}) }};
        """,
    },
    "paste_accepted": {
        "responses": [OK],
        "script": r"""
          const out = await h.Auth.checkAndPair('  current-household  ');
          return { out, stored: h.Auth.storedDeviceToken(), calls: h.calls.map((c) => ({ url: c.url,
                   device: c.headers['X-Device-Token'] || null })) };
        """,
    },
    "paste_refused": {
        "storage": {"domovoi-device-token": "kept-household"},
        "responses": [DEVICE_401],
        "script": r"""
          const out = await h.Auth.checkAndPair('my-old-custom-token');
          return { out, stored: h.Auth.storedDeviceToken(), calls: h.calls.length,
                   trouble: h.Auth.deviceTrouble() };
        """,
    },
    "paste_throttled": {
        "responses": [THROTTLED],
        "script": r"""
          const out = await h.Auth.checkAndPair('current-household');
          const again = await h.Auth.checkAndPair('current-household');
          return { out, again, stored: h.Auth.storedDeviceToken(), calls: h.calls.length };
        """,
    },
    "paste_unchecked": {
        "responses": [DB_BLIP, NO_ANSWER],
        "script": r"""
          const blip = await h.Auth.checkAndPair('current-household');
          const gone = await h.Auth.checkAndPair('current-household');
          return { blip, gone, stored: h.Auth.storedDeviceToken(), calls: h.calls.length };
        """,
    },
    "an_admin_sign_in_fetches_the_current_token": {
        "storage": TOKEN,
        "responses": [
            DEVICE_401,
            {"status": 200, "body": {"ok": True, "token": "bearer-1"}},
            {"status": 200, "body": {"token": "current-household", "header": "X-Device-Token"}},
            OK,
        ],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          await h.Auth.login('correct horse battery');
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ trouble: h.Auth.deviceTrouble(), last: h.calls[h.calls.length - 1].headers['X-Device-Token'] }};
        """,
    },
    "a_wait_holds_the_token_until_it_ends": {
        "storage": TOKEN,
        "responses": [THROTTLED, OK],
        "script": rf"""
          const first = await h.attempt(() => h.w.apiGet('/api/rooms'));
          const trouble = h.Auth.deviceTrouble();
          const during = await h.attempt(() => h.w.apiPost('/api/music/queue', {{ a: 1 }}));
          const modals = {{ pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen }};
          // The wait runs out (as if 42 s passed).
          h.storage.setItem('domovoi-device-token-wait', String(Date.now() - 1));
          const after = await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ first, trouble, during, modals, after, headers: h.calls.map({HDR}),
                   endTrouble: h.Auth.deviceTrouble() }};
        """,
    },
    "a_cross_origin_wait_is_read_from_the_words": {
        "storage": TOKEN,
        "responses": [THROTTLED_CROSS_ORIGIN, OK],
        "script": r"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return { trouble: h.Auth.deviceTrouble() };
        """,
    },
    "a_login_throttle_does_not_hold_the_token": {
        "storage": TOKEN,
        "responses": [LOGIN_THROTTLED, OK],
        "script": rf"""
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          await h.attempt(() => h.w.apiGet('/api/rooms'));
          return {{ trouble: h.Auth.deviceTrouble(), headers: h.calls.map({HDR}) }};
        """,
    },
    "no_token_is_still_the_pair_modal": {
        "responses": [DEVICE_401],
        "script": r"""
          const out = await h.attempt(() => h.w.apiGet('/api/rooms'));
          return { out, pairModal: h.Auth.pairModalOpen, trouble: h.Auth.deviceTrouble() };
        """,
    },
}


def _run(harness_src: str, argv_tail: list[str], tmp: Path) -> dict:
    node = shutil.which("node")
    assert node, "node is required (see jsxcheck)"
    harness = tmp / "harness.js"
    harness.write_text(harness_src, encoding="utf-8")
    proc = subprocess.run([node, str(harness), *argv_tail], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> dict:
    return _run(STORE_HARNESS, [str(STATIC), json.dumps(STORE_SCENARIOS)],
                tmp_path_factory.mktemp("stale_token_store"))


# ── 1. a refused token stops at once ────────────────────────────────────


def test_a_refused_token_is_sent_once_and_then_never_again(store):
    o = store["a_refused_token_stops_at_once"]
    assert o["first"]["resolved"] is False and o["first"]["status"] == 401
    assert o["headers"] == ["old-household", None, None, None]
    assert o["trouble"] == {"kind": "refused", "waitMs": 0}
    assert o["paired"] is False
    assert o["stored"] == "old-household"            # kept, for "try it again"
    assert o["socketProtocols"] == [[]]              # the state socket offers none
    assert o["media"] == "/api/images/1/raw"         # no ?device_token= either


def test_a_read_refused_for_the_stored_token_opens_no_modal(store):
    # The notice says it once; every panel of the page used to ask.
    o = store["a_refused_token_stops_at_once"]
    assert o["pairModal"] is False and o["loginModal"] is False


def test_a_refusal_survives_a_reload(store):
    o = store["a_refused_token_stays_refused_after_a_reload"]
    assert o["headers"] == [None]
    assert o["trouble"]["kind"] == "refused"


def test_a_new_token_is_sent_at_once(store):
    o = store["a_new_token_is_sent_at_once"]
    assert o["before"]["kind"] == "refused"
    assert o["after"] is None
    assert o["out"]["resolved"] is True
    assert o["headers"] == ["old-household", "new-household"]
    assert "domovoi-device-token-refused" not in o["stored"]


def test_a_pressed_action_still_offers_pairing_and_the_replay_carries_the_new_token(store):
    o = store["a_pressed_action_still_offers_pairing_and_replays"]
    assert o["modal"] is True
    assert o["out"]["resolved"] is True
    assert o["headers"] == ["old-household", "new-household"]


def test_trying_the_old_token_again_sends_it_once_more(store):
    o = store["try_the_old_token_again"]
    assert o["probe"] == {"kind": "accepted"}
    # One probe first (the satellites read), and only then the panels.
    assert o["urls"][1].endswith("/api/satellites")
    assert o["headers"] == ["old-household", "old-household", "old-household"]
    assert o["bumped"] is True                       # the refused reads are asked again
    assert o["out"]["resolved"] is True and o["trouble"] is None


def test_trying_again_when_still_refused_is_one_request_not_a_burst(store):
    # Review N-T1: it used to release every open panel at once.
    o = store["try_again_refused_again_is_one_request"]
    assert o["probe"] == {"kind": "refused"}
    assert o["calls"] == 2
    assert o["bumped"] is False
    assert o["trouble"]["kind"] == "refused"


# ── review fixes: a 503 and a plugin's 401 say nothing about the token ──


def test_a_database_blip_is_not_a_refusal(store):
    o = store["a_db_blip_is_not_a_refusal"]
    assert o["first"]["status"] == 503
    assert o["trouble"] is None
    assert o["headers"] == ["old-household", "old-household"]
    assert o["pairModal"] is False


def test_a_plugins_own_401_is_not_a_refusal(store):
    o = store["a_plugin_401_is_not_a_refusal"]
    assert o["trouble"] is None
    assert o["headers"] == ["old-household", "old-household"]


# ── a pasted token is checked before it is stored ───────────────────────


def test_a_pasted_token_the_server_takes_is_stored(store):
    o = store["paste_accepted"]
    assert o["out"] == {"kind": "accepted"}
    assert o["stored"] == "current-household"
    assert o["calls"] == [{"url": "/api/satellites", "device": "current-household"}]


def test_a_wrong_paste_is_not_stored_and_is_asked_once(store):
    o = store["paste_refused"]
    assert o["out"] == {"kind": "refused"}
    assert o["stored"] == "kept-household"           # nothing replaced
    assert o["calls"] == 1                           # one wrong token, no retry
    assert o["trouble"] is None                      # the stored token is not blamed


def test_a_paste_during_the_wait_says_how_long_and_sends_nothing_more(store):
    o = store["paste_throttled"]
    assert o["out"]["kind"] == "waiting" and 40_000 <= o["out"]["waitMs"] <= 42_000
    assert o["again"]["kind"] == "waiting"
    assert o["calls"] == 1
    assert o["stored"] is None


def test_a_paste_the_server_could_not_check_is_not_stored(store):
    o = store["paste_unchecked"]
    assert o["blip"] == {"kind": "unavailable"}
    assert o["gone"] == {"kind": "unavailable"}
    assert o["stored"] is None
    assert o["calls"] == 2


def test_an_admin_sign_in_replaces_a_refused_token(store):
    o = store["an_admin_sign_in_fetches_the_current_token"]
    assert o["trouble"] is None
    assert o["last"] == "current-household"


# ── 3. the server's wait ────────────────────────────────────────────────


def test_a_wait_holds_the_token_and_opens_no_prompt(store):
    o = store["a_wait_holds_the_token_until_it_ends"]
    assert o["first"]["status"] == 429
    assert o["trouble"]["kind"] == "waiting"
    assert 40_000 <= o["trouble"]["waitMs"] <= 42_000
    # During the wait: the request goes out WITHOUT the token, which the
    # server does not count, and a pressed action opens no modal.
    assert o["modals"] == {"pair": False, "login": False}
    assert o["during"]["resolved"] is True
    # Over: the token goes out again.
    assert o["after"]["resolved"] is True
    assert o["headers"] == ["old-household", None, "old-household"]
    assert o["endTrouble"] is None


def test_a_cross_origin_page_reads_the_wait_from_the_detail(store):
    t = store["a_cross_origin_wait_is_read_from_the_words"]["trouble"]
    assert t["kind"] == "waiting"
    assert 75_000 <= t["waitMs"] <= 77_000


def test_a_throttle_about_something_else_does_not_hold_the_token(store):
    o = store["a_login_throttle_does_not_hold_the_token"]
    assert o["trouble"] is None
    assert o["headers"] == ["old-household", "old-household"]


def test_a_browser_with_no_token_still_gets_the_pair_modal(store):
    o = store["no_token_is_still_the_pair_modal"]
    assert o["pairModal"] is True
    assert o["trouble"] is None


# ── 5. the notice ───────────────────────────────────────────────────────

NOTICE_SETUP = r"""
window.__auth = { trouble: { kind: 'refused', waitMs: 0 }, pairAsked: 0, signIn: 0, retried: 0 };
Auth = {
  status: { setup_complete: true, authenticated: false },
  subscribe: () => () => {},
  isLoggedIn: () => false,
  headers: () => ({}),
  modalOpen: false, pairModalOpen: false,
  deviceToken: () => null,
  deviceTrouble: () => window.__auth.trouble,
  requestPairing: () => { window.__auth.pairAsked += 1; },
  openModal: () => { window.__auth.signIn += 1; },
  retryRefusedDeviceToken: () => { window.__auth.retried += 1; window.__auth.trouble = null; },
};
"""

NOTICE_SCENARIOS = {
    "refused": {
        "files": [COMPONENTS], "component": "DeviceTokenNotice", "setup": NOTICE_SETUP,
        "script": r"""
          h.render();
          const texts = h.text();
          const buttons = h.findAll({ type: 'button' }).map((b) => b.text);
          await h.click({ type: 'button', text: 'pair again' });
          await h.click({ type: 'button', text: 'sign in as admin' });
          await h.click({ type: 'button', text: 'try the old token again' });
          const a = h.global('window').__auth;
          h.rerender();
          return { texts, buttons, pairAsked: a.pairAsked, signIn: a.signIn, retried: a.retried,
                   goneAfterRetry: h.tree().length === 0 };
        """,
    },
    "waiting": {
        "files": [COMPONENTS], "component": "DeviceTokenNotice",
        "setup": NOTICE_SETUP.replace("{ kind: 'refused', waitMs: 0 }", "{ kind: 'waiting', waitMs: 245000 }"),
        "script": r"""
          h.render();
          return { texts: h.text(), buttons: h.findAll({ type: 'button' }).map((b) => b.text) };
        """,
    },
    "refused_during_a_wait": {
        "files": [COMPONENTS], "component": "DeviceTokenNotice",
        "setup": NOTICE_SETUP.replace("{ kind: 'refused', waitMs: 0 }", "{ kind: 'refused', waitMs: 61000 }"),
        "script": r"""
          h.render();
          return { texts: h.text(), buttons: h.findAll({ type: 'button' }).map((b) => b.text) };
        """,
    },
    "nothing_wrong": {
        "files": [COMPONENTS], "component": "DeviceTokenNotice",
        "setup": NOTICE_SETUP.replace("{ kind: 'refused', waitMs: 0 }", "null"),
        "script": r"""
          h.render();
          return { size: h.tree().length };
        """,
    },
}


PAIR_SETUP = r"""
window.__auth = { outcome: OUTCOME, asked: [], closed: 0 };
Auth = {
  status: { setup_complete: true, authenticated: false },
  subscribe: () => () => {},
  isLoggedIn: () => false,
  headers: () => ({}),
  modalOpen: false, pairModalOpen: true,
  deviceToken: () => null,
  checkAndPair: (v) => { window.__auth.asked.push(v); return Promise.resolve(window.__auth.outcome); },
  pair: () => true,
  signInInstead: () => {},
  closePairModal: () => { window.__auth.closed += 1; },
};
"""

PAIR_SCRIPT = r"""
  h.render();
  await h.type({ type: 'input' }, 'my-old-custom-token');
  await h.click({ type: 'button', text: 'pair' });
  await h.settle();
  const a = h.global('window').__auth;
  const pairButton = h.findAll({ type: 'button' }).filter((b) => /pair|checking/.test(b.text)).pop();
  return { asked: a.asked, closed: a.closed, texts: h.text(),
           disabled: !!(pairButton && pairButton.props.disabled) };
"""


def _pair(outcome: str) -> dict:
    return {"files": [COMPONENTS], "component": "AuthModalHost",
            "setup": PAIR_SETUP.replace("OUTCOME", outcome), "script": PAIR_SCRIPT}


NOTICE_SCENARIOS.update({
    "pair_accepted": _pair("{ kind: 'accepted' }"),
    "pair_refused": _pair("{ kind: 'refused' }"),
    "pair_waiting": _pair("{ kind: 'waiting', waitMs: 53000 }"),
    "pair_unchecked": _pair("{ kind: 'unavailable' }"),
})


@pytest.fixture(scope="module")
def notice() -> dict:
    node = shutil.which("node")
    assert node, "node is required (see jsxcheck)"
    proc = subprocess.run([node, str(INTERACT_HARNESS), str(REPO_ROOT), json.dumps(NOTICE_SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


def _joined(o) -> str:
    return " ".join(o["texts"])


def test_the_notice_says_it_in_household_words_with_the_ways_back(notice):
    o = notice["refused"]
    text = _joined(o)
    assert "doesn't recognise this browser any more" in text
    assert "stopped sending it" in text
    assert "X-Device-Token" not in text and "429" not in text
    assert o["buttons"] == ["pair again", "sign in as admin", "try the old token again"]
    assert (o["pairAsked"], o["signIn"], o["retried"]) == (1, 1, 1)
    assert o["goneAfterRetry"] is True


def test_the_notice_counts_the_wait_and_offers_no_retry_during_it(notice):
    w = notice["waiting"]
    assert "4:05" in _joined(w)
    assert "tab or a device still holding an old one" in _joined(w)
    assert w["buttons"] == ["pair again", "sign in as admin"]
    r = notice["refused_during_a_wait"]
    assert "1:01" in _joined(r)
    assert "try the old token again" not in r["buttons"]   # nothing would be sent


def test_no_notice_when_nothing_is_wrong(notice):
    assert notice["nothing_wrong"]["size"] == 0


def test_the_shell_shows_the_notice_on_every_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<DeviceTokenNotice/>" in html


def test_the_pair_prompt_closes_only_when_the_server_took_the_token(notice):
    ok = notice["pair_accepted"]
    assert ok["asked"] == ["my-old-custom-token"]
    assert ok["closed"] == 1


def test_the_pair_prompt_says_a_wrong_token_does_not_match_and_stays(notice):
    o = notice["pair_refused"]
    assert o["closed"] == 0
    assert any("that token doesn't match this server" in t for t in o["texts"])
    assert any("Settings → Devices → Household token on this server's dashboard" in t for t in o["texts"])


def test_the_pair_prompt_counts_a_wait_down_and_holds_the_button(notice):
    o = notice["pair_waiting"]
    assert o["closed"] == 0
    assert any("too many tries — wait 0:53" in t for t in o["texts"])
    assert o["disabled"] is True


def test_the_pair_prompt_says_it_could_not_check(notice):
    o = notice["pair_unchecked"]
    assert o["closed"] == 0
    assert any("couldn't check it with the server — try again" in t for t in o["texts"])
