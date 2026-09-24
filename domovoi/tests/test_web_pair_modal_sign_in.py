"""F-048 — the "pair this browser" modal is not a dead end.

A browser with cleared storage 401s on its first device-tier request, so
the pairing prompt is the FIRST thing a person sees — before they have
had any chance to sign in, and asking for a household token most people
have never heard of. The way out is an admin login, which fetches the
token itself (``auth.js`` ``autoPair``), so the modal offers it as an
action instead of describing it in a hint.

Acceptance:

1. A device-tier 401 opens the pair modal (not the login modal).
2. The pair modal offers "sign in as an admin instead"; taking it opens
   the login modal, and the pairing prompt stays open UNDERNEATH — never
   both on screen, and cancelling the login comes back to it.
3. A successful login pairs the browser without the token being typed
   and replays the refused request exactly once (the replay-once
   semantics of ``_sendWithAuthRetry`` are unchanged).
4. Cancelling the login and then the pairing still reports a cancelled
   pairing — nothing is trapped.
5. The paste-the-token path is untouched and still works.

Two harnesses, both DB-free (never ``requires_db``), both needing
``node``: the store half runs the real ``auth.js`` + ``data.js`` in a
Node ``vm`` with a scripted ``fetch`` (``HARNESS_JS``, shared with
``test_web_device_token_pairing``), and the component half drives the
real ``PairModal``/``LoginModal`` through ``<AuthModalHost/>`` with
``jsx_interact_harness.js``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_device_token_pairing import (
    DEVICE_401,
    HARNESS_JS,
    OK,
    STATIC,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"

LOGIN_OK = {"status": 200, "body": {"ok": True, "token": "bearer-1"}}
DEVICE_TOKEN_OK = {"status": 200, "body": {"token": "household-from-login", "header": "X-Device-Token"}}


# ── half 1: the Auth store + the 401 replay ─────────────────────────────

STORE_SCENARIOS = {
    # The whole journey Kamron hit: a device-tier refusal, a prompt for a
    # token this browser has never heard of, and out through the login.
    "sign_in_instead_pairs_and_replays_once": {
        "responses": [DEVICE_401, LOGIN_OK, DEVICE_TOKEN_OK, OK],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/music/queue', { a: 1 }));
          await h.settle();
          const refused = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen };
          h.Auth.signInInstead();              // the modal's second action
          await h.settle();
          const offered = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen };
          await h.Auth.login('correct horse battery');   // LoginModal.submit
          h.Auth.closeModal();                            // ... then its onClose
          const first = await pending;
          return { refused, offered, first,
                   after: { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen },
                   paired: h.Auth.isPaired(), device: h.Auth.deviceToken(),
                   calls: h.calls.map((c) => ({ url: c.url, method: c.method,
                                                device: c.headers['X-Device-Token'] || null })),
                   stored: h.storage.dump() };
        """,
    },
    # Dismissing the login is not a dead end: the pairing prompt is still
    # standing, the refused request is still waiting, and the token still
    # answers it.
    "cancelling_the_login_returns_to_the_pair_modal": {
        "responses": [DEVICE_401, OK],
        "script": r"""
          let done = false;
          const pending = h.attempt(() => h.w.apiPost('/api/x', {})).then((v) => { done = true; return v; });
          await h.settle();
          h.Auth.signInInstead();
          await h.settle();
          const duringLogin = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen };
          h.Auth.closeModal();                            // cancel the login
          await h.settle();
          const afterCancel = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen,
                                calls: h.calls.length, done };
          h.Auth.pair('household-typed');                 // the other way in
          const out = await pending;
          return { duringLogin, afterCancel, out,
                   headers: h.calls.map((c) => c.headers['X-Device-Token'] || null),
                   paired: h.Auth.isPaired() };
        """,
    },
    # Cancelling both still gives up cleanly — no replay, no loop.
    "cancelling_both_reports_a_cancelled_pairing": {
        "responses": [DEVICE_401],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/x', {}));
          await h.settle();
          h.Auth.signInInstead();
          await h.settle();
          h.Auth.closeModal();
          await h.settle();
          h.Auth.closePairModal();
          const out = await pending;
          return { out, calls: h.calls.length, paired: h.Auth.isPaired(),
                   after: { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen } };
        """,
    },
    # Asking twice does not stack a second login modal.
    "sign_in_instead_is_idempotent": {
        "responses": [DEVICE_401],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/x', {}));
          await h.settle();
          h.Auth.signInInstead();
          h.Auth.signInInstead();
          const both = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen };
          h.Auth.closeModal();
          h.Auth.closePairModal();
          await pending;
          return { both };
        """,
    },
}


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static/auth.js + data.js (see jsxcheck)"
    harness = tmp_path_factory.mktemp("f048_store") / "harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(STATIC), json.dumps(STORE_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


def test_a_device_refusal_opens_the_pair_modal_and_the_sign_in_opens_the_login(store):
    o = store["sign_in_instead_pairs_and_replays_once"]
    assert o["refused"] == {"pair": True, "login": False}
    # The pairing prompt stays open underneath — that is what makes the
    # login dismissable without stranding the refused request.
    assert o["offered"] == {"pair": True, "login": True}


def test_signing_in_pairs_the_browser_without_the_token_being_typed(store):
    o = store["sign_in_instead_pairs_and_replays_once"]
    assert o["paired"] is True
    assert o["device"] == "household-from-login"
    assert o["stored"]["domovoi-device-token"] == "household-from-login"
    # Nothing was pasted: the only place the token came from is the
    # admin-session read.
    assert [c["url"] for c in o["calls"]][1:3] == ["/api/auth/login", "/api/auth/device-token"]


def test_the_refused_request_is_replayed_exactly_once_after_the_sign_in(store):
    o = store["sign_in_instead_pairs_and_replays_once"]
    assert o["first"]["resolved"] is True
    # 4 calls: the refusal, the login, the token read, and ONE replay.
    assert [(c["url"], c["device"]) for c in o["calls"]] == [
        ("/api/music/queue", None),
        ("/api/auth/login", None),
        ("/api/auth/device-token", None),
        ("/api/music/queue", "household-from-login"),
    ]
    # Both prompts are down once the browser is paired.
    assert o["after"] == {"pair": False, "login": False}


def test_cancelling_the_login_comes_back_to_the_pair_modal(store):
    o = store["cancelling_the_login_returns_to_the_pair_modal"]
    assert o["duringLogin"] == {"pair": True, "login": True}
    # Back to the pairing prompt, not to nothing — and still waiting.
    assert o["afterCancel"] == {"pair": True, "login": False, "calls": 1, "done": False}
    # The token still answers it, and the replay carries it.
    assert o["out"]["resolved"] is True
    assert o["headers"] == [None, "household-typed"]
    assert o["paired"] is True


def test_cancelling_both_prompts_reports_a_cancelled_pairing(store):
    o = store["cancelling_both_reports_a_cancelled_pairing"]
    assert o["out"]["resolved"] is False
    assert o["out"]["deviceTokenRequired"] is True
    assert o["out"]["authCancelled"] is True
    assert o["calls"] == 1                       # nothing replayed
    assert o["paired"] is False
    assert o["after"] == {"pair": False, "login": False}


def test_asking_for_the_login_twice_does_not_stack_modals(store):
    assert store["sign_in_instead_is_idempotent"]["both"] == {"pair": True, "login": True}


# ── half 2: the modals themselves ───────────────────────────────────────

# An Auth stub with the real modal-precedence semantics, recording what
# the modals asked for. `login` stands in for the real one, which pairs
# the browser through autoPair and takes the pairing prompt down.
HOST_SETUP = r"""
window.__auth = { signIn: 0, paired: null, loginPw: null, pairOpen: true, loginOpen: false };
Auth = {
  status: { setup_complete: true, authenticated: false },
  subscribe: () => () => {},
  isLoggedIn: () => false,
  headers: () => ({}),
  get modalOpen() { return window.__auth.loginOpen; },
  get pairModalOpen() { return window.__auth.pairOpen; },
  refreshStatus: () => Promise.resolve(Auth.status),
  deviceToken: () => window.__auth.paired,
  pair: (t) => { window.__auth.paired = String(t).trim(); window.__auth.pairOpen = false; return true; },
  signInInstead: () => { window.__auth.signIn += 1; window.__auth.loginOpen = true; },
  closeModal: () => { window.__auth.loginOpen = false; },
  closePairModal: () => { window.__auth.pairOpen = false; },
  login: (pw) => {
    window.__auth.loginPw = pw;
    window.__auth.paired = 'household-from-login';   // autoPair
    window.__auth.pairOpen = false;                  // ... which answers the prompt
    return Promise.resolve({ ok: true });
  },
};
"""

MODAL_SCENARIOS = {
    "the_pair_modal_offers_the_sign_in": {
        "files": [COMPONENTS], "component": "AuthModalHost", "setup": HOST_SETUP,
        "script": r"""
          h.render();
          const modals = () => h.findAll((el) => (el.props.className || '') === 'cal-modal').length;
          const titles = () => h.findAll({ type: 'div' })
                                .filter((d) => (d.props.className || '') === 'ttl').map((d) => d.text);
          const start = { titles: titles(), modals: modals(), texts: h.text(),
                          signIn: !!h.find({ type: 'button', text: 'sign in as an admin instead' }) };
          await h.click({ type: 'button', text: 'sign in as an admin instead' });
          const afterClick = { titles: titles(), modals: modals(),
                               signInCalls: h.global('window').__auth.signIn,
                               pairStillOpen: h.global('window').__auth.pairOpen };
          await h.click({ type: 'button', text: 'cancel' });     // dismiss the login
          const afterCancel = { titles: titles(), modals: modals(),
                                pairStillOpen: h.global('window').__auth.pairOpen };
          return { start, afterClick, afterCancel };
        """,
    },
    "signing_in_from_the_pair_modal_pairs_without_typing_the_token": {
        "files": [COMPONENTS], "component": "AuthModalHost", "setup": HOST_SETUP,
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'sign in as an admin instead' });
          await h.type({ type: 'input' }, 'correct horse battery');
          await h.click({ type: 'button', text: 'log in' });
          const a = h.global('window').__auth;
          return { tree: h.tree().length, paired: a.paired, pw: a.loginPw,
                   pairOpen: a.pairOpen, loginOpen: a.loginOpen };
        """,
    },
    "the_token_path_still_works": {
        "files": [COMPONENTS], "component": "AuthModalHost", "setup": HOST_SETUP,
        "script": r"""
          h.render();
          // The modal has exactly ONE input, so select it by type rather than by
          // its placeholder: the placeholder is copy and it has already changed
          // once (it shows the word-phrase shape now), which broke this scenario
          // the moment the two branches met.
          await h.type({ type: 'input' }, '  household-typed  ');
          await h.click({ type: 'button', text: 'pair' });
          const a = h.global('window').__auth;
          return { tree: h.tree().length, paired: a.paired, signIn: a.signIn, pairOpen: a.pairOpen };
        """,
    },
}


@pytest.fixture(scope="module")
def modals() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the pair/login modals (see jsxcheck)"
    proc = subprocess.run(
        [node, str(INTERACT_HARNESS), str(REPO_ROOT), json.dumps(MODAL_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


def test_the_pair_modal_offers_a_sign_in_and_only_one_modal_is_ever_shown(modals):
    o = modals["the_pair_modal_offers_the_sign_in"]
    assert o["start"]["titles"] == ["pair this browser"]
    assert o["start"]["modals"] == 1
    assert o["start"]["signIn"] is True
    # Taking it swaps the pair modal for the login — one on screen, and
    # the pairing prompt is still open behind it.
    assert o["afterClick"]["titles"] == ["admin login"]
    assert o["afterClick"]["modals"] == 1
    assert o["afterClick"]["signInCalls"] == 1
    assert o["afterClick"]["pairStillOpen"] is True
    # Cancelling the login is not a dead end.
    assert o["afterCancel"]["titles"] == ["pair this browser"]
    assert o["afterCancel"]["modals"] == 1
    assert o["afterCancel"]["pairStillOpen"] is True


def test_the_pair_modal_says_where_the_token_lives(modals):
    texts = modals["the_pair_modal_offers_the_sign_in"]["start"]["texts"]
    assert any("Settings → Devices → Household token" in t for t in texts)
    assert any("~/.domovoi/device-token.txt" in t for t in texts)


def test_signing_in_from_the_pair_modal_pairs_the_browser(modals):
    o = modals["signing_in_from_the_pair_modal_pairs_without_typing_the_token"]
    assert o["pw"] == "correct horse battery"
    assert o["paired"] == "household-from-login"    # never typed
    assert o["pairOpen"] is False and o["loginOpen"] is False
    assert o["tree"] == 0                           # both modals gone


def test_pasting_the_household_token_still_pairs_this_browser(modals):
    o = modals["the_token_path_still_works"]
        # Trimmed by the stub Auth this scenario installs. The REAL
    # canonicalisation Auth.pair does now — lowercasing, and collapsing
    # spaces and underscores to hyphens so a typed word phrase pairs — is
    # driven against the real auth.js in test_web_device_token_pairing.py;
    # this scenario is about the modal, not about the token format.
    assert o["paired"] == "household-typed"
    assert o["signIn"] == 0
    assert o["pairOpen"] is False
    assert o["tree"] == 0
