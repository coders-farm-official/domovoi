"""B1 — the dashboard pairs with the household device token, and trusts a
server before it talks to it.

Acceptance (security batch B1, FE-2 + the device-tier client work):

1. A request answered 401 "device token required" opens the pairing
   modal; once the token is entered every request carries
   ``X-Device-Token`` and the refused mutation is replayed and succeeds.
   An admin login pairs the browser without a prompt (it fetches
   ``GET /api/auth/device-token`` itself).
3. The server switcher persists a newly discovered server, loads its
   plugin JS or sends a login only after the trust confirmation:
   ``ServerStore.select()`` refuses an untrusted address (nothing written,
   no reload), ``Auth.login()`` refuses to post the password to it, and
   ``trust()`` is what turns both on.

The real ``auth.js`` + ``data.js`` run in a Node ``vm`` with a scripted
``fetch``, ``localStorage`` and ``WebSocket`` — no browser, no server, no
DB (never ``requires_db``). Needs ``node`` (the runtime the JSX compile
check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"

# One scenario = one fresh sandbox (fresh localStorage, fresh Auth). The
# scripted fetch answers from `responses`, a list consumed in order; each
# entry is {status, body} and the last one repeats. Every call is recorded
# with its headers so the test can see what rode along.
HARNESS_JS = r"""
const fs = require('fs');
const vm = require('vm');
const staticDir = process.argv[2];
const scenarios = JSON.parse(process.argv[3]);
const authSrc = fs.readFileSync(staticDir + '/auth.js', 'utf8');
const dataSrc = fs.readFileSync(staticDir + '/data.js', 'utf8');

const makeStorage = (seed) => {
  const m = new Map(Object.entries(seed || {}));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    dump: () => Object.fromEntries(m),
  };
};

const run = async ({ storage: seed, responses, script }) => {
  const calls = [];
  const storage = makeStorage(seed);
  let reloads = 0;
  const fetch = async (url, opts = {}) => {
    const i = Math.min(calls.length, responses.length - 1);
    const { status, body } = responses[i];
    calls.push({ url, method: (opts.method || 'GET').toUpperCase(), headers: opts.headers || {} });
    const ok = status >= 200 && status < 300;
    const text = typeof body === 'string' ? body : JSON.stringify(body);
    return { ok, status, statusText: ok ? 'OK' : (status === 401 ? 'Unauthorized' : 'Refused'),
             text: async () => text, json: async () => JSON.parse(text) };
  };
  const sockets = [];
  // RFC 9110 tchar. A real browser validates EVERY element of `protocols`
  // and throws a SyntaxError before a byte is sent; without this the stub
  // happily accepts a spaced subprotocol and the test proves nothing.
  const TCHAR = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
  class WebSocket {
    constructor(url, protocols) {
      const offered = protocols === undefined ? []
        : (Array.isArray(protocols) ? protocols : [protocols]);
      for (const p of offered) {
        if (!TCHAR.test(String(p))) {
          const e = new Error(
            `Failed to construct 'WebSocket': The subprotocol '${p}' is invalid.`);
          e.name = 'SyntaxError';
          throw e;
        }
      }
      this.url = url; this.protocols = offered; this.sent = [];
      this.handlers = {}; sockets.push(this);
    }
    addEventListener(ev, fn) { this.handlers[ev] = fn; }
    send(frame) { this.sent.push(JSON.parse(frame)); }
    open() { this.handlers.open && this.handlers.open(); }
  }
  const window = {
    location: { host: 'domovoi.lan:6369', hostname: 'domovoi.lan', protocol: 'http:', reload: () => { reloads += 1; } },
    addEventListener() {}, removeEventListener() {},
  };
  const sandbox = { window, console, fetch, localStorage: storage, WebSocket, setTimeout, clearTimeout,
                    URL, AbortController, navigator: { userAgent: 'harness' } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(authSrc, sandbox, { filename: 'auth.js' });
  vm.runInContext(dataSrc, sandbox, { filename: 'data.js' });
  const w = sandbox.window;
  // `const Auth` is a lexical binding of the context, not a property of
  // the sandbox object — evaluate it inside the context to reach it.
  const Auth = vm.runInContext('Auth', sandbox);
  const h = {
    w, Auth, calls, sockets, storage,
    reloads: () => reloads,
    async attempt(fn) {
      try { return { resolved: true, value: await fn() }; }
      catch (e) {
        return { resolved: false, status: e.status, message: e.message, untrusted: !!e.untrusted,
                 deviceTokenRequired: !!e.deviceTokenRequired, authCancelled: !!e.authCancelled,
                 isAuthFailure: w.isAuthFailure(e) };
      }
    },
    settle: async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); },
  };
  const fn = new Function('h', `return (async () => { ${script} })();`);
  return await fn(h);
};

(async () => {
  const out = {};
  for (const [name, sc] of Object.entries(scenarios)) {
    try { out[name] = await run(sc); }
    catch (e) { out[name] = { __harness_error: String((e && e.stack) || e) }; }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(2); });
"""

DEVICE_401 = {"status": 401, "body": {"detail": "X-Device-Token or admin session required"}}
COOKIE_403 = {"status": 403, "body": {"detail": "X-Device-Token required — the dashboard cookie does not authorize device-tier actions"}}
ADMIN_401 = {"status": 401, "body": {"detail": "admin session required"}}
OK = {"status": 200, "body": {"ok": True}}

SCENARIOS = {
    # 1. A refused mutation opens the pair modal; entering the token
    #    replays it with the header, and later requests carry it too.
    "mutation_pairs_and_replays": {
        "responses": [DEVICE_401, OK, OK],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/music/queue', { a: 1 }));
          await h.settle();
          const modalWhilePending = h.Auth.pairModalOpen;
          const loginModalWhilePending = h.Auth.modalOpen;
          h.Auth.pair('  household-abc  ');       // what the modal does on "pair"
          const first = await pending;
          const second = await h.attempt(() => h.w.apiGet('/api/music/library'));
          return { modalWhilePending, loginModalWhilePending, first, second,
                   headers: h.calls.map((c) => c.headers['X-Device-Token'] || null),
                   modalAfter: h.Auth.pairModalOpen, paired: h.Auth.isPaired(),
                   stored: h.storage.dump() };
        """,
    },
    # The cookie-only 403 wording is a device refusal too.
    "cookie_only_403_is_a_device_refusal": {
        "responses": [COOKIE_403, OK],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/x', {}));
          await h.settle();
          const modal = h.Auth.pairModalOpen;
          h.Auth.pair('tok');
          const out = await pending;
          return { modal, out, calls: h.calls.length };
        """,
    },
    # Dismissing the pair modal: no replay, the error says why.
    "pair_modal_dismissed": {
        "responses": [DEVICE_401],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/x', {}));
          await h.settle();
          h.Auth.closePairModal();
          const out = await pending;
          return { out, calls: h.calls.length, paired: h.Auth.isPaired() };
        """,
    },
    # A GET refused for the device token prompts (no replay) and rejects.
    "get_prompts_pairing": {
        "responses": [DEVICE_401],
        "script": r"""
          const out = await h.attempt(() => h.w.apiGet('/api/x'));
          return { out, pairModal: h.Auth.pairModalOpen, loginModal: h.Auth.modalOpen, calls: h.calls.length };
        """,
    },
    # An admin-tier refusal is still the login modal's business.
    "admin_401_opens_login_not_pairing": {
        "responses": [ADMIN_401],
        "script": r"""
          const pending = h.attempt(() => h.w.apiPost('/api/x', {}));
          await h.settle();
          const which = { pair: h.Auth.pairModalOpen, login: h.Auth.modalOpen };
          h.Auth.closeModal();
          const out = await pending;
          return { which, out };
        """,
    },
    # A stored token rides on every request from the first one.
    "stored_token_is_sent": {
        "storage": {"domovoi-device-token": "already-paired"},
        "responses": [OK],
        "script": r"""
          await h.w.apiGet('/api/x');
          await h.w.apiPost('/api/y', {});
          return { headers: h.calls.map((c) => c.headers['X-Device-Token']), paired: h.Auth.isPaired() };
        """,
    },
    # Admin login fetches the household token itself — no modal.
    "admin_login_auto_pairs": {
        "responses": [
            {"status": 200, "body": {"ok": True, "token": "bearer-1"}},
            {"status": 200, "body": {"token": "household-from-login", "header": "X-Device-Token"}},
            OK,
        ],
        "script": r"""
          await h.Auth.login('correct horse battery');
          await h.w.apiPost('/api/x', {});
          return { calls: h.calls.map((c) => ({ url: c.url, method: c.method,
                                                auth: c.headers.Authorization || null,
                                                device: c.headers['X-Device-Token'] || null })),
                   pairModal: h.Auth.pairModalOpen, paired: h.Auth.isPaired(),
                   version: h.Auth.credentialVersion };
        """,
    },
    # Setup rotates the household token: the fresh one is fetched after.
    "setup_auto_pairs_with_the_rotated_token": {
        "storage": {"domovoi-device-token": "pre-setup-token"},
        "responses": [
            {"status": 200, "body": {"ok": True, "token": "bearer-1"}},
            {"status": 200, "body": {"token": "post-setup-token", "header": "X-Device-Token"}},
        ],
        "script": r"""
          await h.Auth.setup('eight-words', 'correct horse battery');
          return { device: h.Auth.deviceToken(), calls: h.calls.map((c) => c.url) };
        """,
    },
    # Logging out keeps the household token — it is the household's.
    "logout_keeps_the_device_token": {
        "storage": {"domovoi-device-token": "household"},
        "responses": [
            {"status": 200, "body": {"ok": True, "token": "bearer-1"}},
            {"status": 200, "body": {"token": "household", "header": "X-Device-Token"}},
            OK,
        ],
        "script": r"""
          await h.Auth.login('pw');
          await h.Auth.logout();
          return { paired: h.Auth.isPaired(), loggedIn: h.Auth.isLoggedIn() };
        """,
    },
    # The token is stored per server: a selected server has its own.
    "token_is_per_server": {
        "storage": {"domovoi-server": "http://10.0.0.7:6369", "domovoi-trusted-servers": '["http://10.0.0.7:6369"]',
                    "domovoi-device-token": "same-origin-token"},
        "responses": [OK],
        "script": r"""
          const before = h.Auth.isPaired();
          h.Auth.pair('other-house');
          await h.w.apiGet('/api/x');
          return { before, url: h.calls[0].url, header: h.calls[0].headers['X-Device-Token'], stored: h.storage.dump() };
        """,
    },
    # A token is stored EXACTLY as it was typed, bar the outer whitespace:
    # an admin may set the household token to any printable ASCII, so
    # lowercasing it here would pair the browser to a token that does not
    # exist. Offered on the socket base64url-encoded.
    "a_typed_token_is_stored_verbatim": {
        "responses": [OK],
        "script": r"""
          h.Auth.pair('  MyT0ken!!going  ');
          await h.w.apiGet('/api/x');
          h.w.stateBus.subscribe(() => {});
          return { stored: h.storage.dump(), header: h.calls[0].headers['X-Device-Token'],
                   protocols: h.sockets[0].protocols, url: h.sockets[0].url };
        """,
    },
    # ...including one that is ILLEGAL in a raw subprotocol. A real browser
    # throws on `new WebSocket(url, ['domovoi.device-token.a b c'])`, so the
    # b64 element has to be the only one offered for this token.
    "a_token_with_spaces_and_a_comma_still_opens_a_socket": {
        "responses": [OK],
        "script": r"""
          h.Auth.pair('Maple Street, 1984!');
          await h.w.apiGet('/api/x');
          h.w.stateBus.subscribe(() => {});
          return { stored: h.Auth.deviceToken(), header: h.calls[0].headers['X-Device-Token'],
                   protocols: h.sockets[0].protocols, sockets: h.sockets.length };
        """,
    },
    # A phrase is a legal RFC 9110 token, so BOTH elements are offered —
    # b64 first, legacy second — and a server that has not been restarted
    # yet still has something it recognises to echo back.
    "a_phrase_offers_both_the_b64_and_the_legacy_element": {
        "responses": [OK],
        "script": r"""
          h.Auth.pair('acorn-maple-river-thistle-harbor-quartz-willow-ember');
          h.w.stateBus.subscribe(() => {});
          return { protocols: h.sockets[0].protocols };
        """,
    },
    # A 64-hex token from an install that predates the phrase survives the
    # same canonicalisation untouched, so an upgraded browser stays paired.
    "a_legacy_hex_token_is_stored_unchanged": {
        "responses": [OK],
        "script": r"""
          const hex = 'a3f0'.repeat(16);
          h.Auth.pair(hex);
          await h.w.apiGet('/api/x');
          return { stored: h.Auth.deviceToken(), header: h.calls[0].headers['X-Device-Token'], hex };
        """,
    },
    # The WebSocket hello carries the token (browsers cannot set headers).
    "ws_hello_carries_the_token": {
        "storage": {"domovoi-device-token": "household"},
        "responses": [OK],
        "script": r"""
          h.w.stateBus.subscribe(() => {});
          h.sockets[0].open();
          return { url: h.sockets[0].url, sent: h.sockets[0].sent };
        """,
    },
    "ws_hello_without_a_token": {
        "responses": [OK],
        "script": r"""
          h.w.stateBus.subscribe(() => {});
          h.sockets[0].open();
          return { sent: h.sockets[0].sent };
        """,
    },
    # 3. Trust before select: nothing persisted, no reload, no password.
    "untrusted_server_is_not_selected_or_logged_into": {
        "storage": {"domovoi-trusted-servers": "[]"},
        "responses": [OK],
        "script": r"""
          const S = h.w.ServerStore;
          const url = 'http://10.0.0.9:6369';
          const selected = S.select(url);
          const afterRefusal = { stored: h.storage.dump(), reloads: h.reloads(), trusted: S.isTrusted(url) };
          // The switcher only persists after the confirmation; auth.js checks
          // the same list before posting the password anywhere.
          h.storage.setItem('domovoi-server', url);
          const login = await h.attempt(() => h.Auth.login('pw'));
          const loginCalls = h.calls.length;
          S.trust(url);
          const selectedAfterTrust = S.select(url);
          return { selected, afterRefusal, login, loginCalls, selectedAfterTrust,
                   host: S.hostOf(url), reloads: h.reloads(), stored: h.storage.dump(),
                   sameOriginTrusted: S.isTrusted('') };
        """,
    },
    # A dashboard from before the trust list existed: its saved servers
    # (only ever added by explicit "use" clicks) and the current selection
    # are trusted once, without re-asking.
    "legacy_saved_servers_seed_the_trusted_list": {
        "storage": {"domovoi-server": "http://10.0.0.2:6369",
                    "domovoi-servers": '[{"url":"http://10.0.0.2:6369","name":"a"},{"url":"http://10.0.0.3:6369","name":"b"}]'},
        "responses": [OK],
        "script": r"""
          const S = h.w.ServerStore;
          return { two: S.isTrusted('http://10.0.0.2:6369'), three: S.isTrusted('http://10.0.0.3:6369'),
                   four: S.isTrusted('http://10.0.0.4:6369'), stored: h.storage.dump() };
        """,
    },
    "forgetting_a_server_untrusts_it": {
        "storage": {"domovoi-trusted-servers": '["http://10.0.0.5:6369"]',
                    "domovoi-servers": '[{"url":"http://10.0.0.5:6369","name":null}]'},
        "responses": [OK],
        "script": r"""
          const S = h.w.ServerStore;
          S.remove('http://10.0.0.5:6369');
          return { trusted: S.isTrusted('http://10.0.0.5:6369'), list: S.list() };
        """,
    },
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static/auth.js + data.js (see jsxcheck)"
    harness = tmp_path_factory.mktemp("b1_pairing") / "harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(STATIC), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, o in out.items():
        assert "__harness_error" not in o, f"{name}: {o.get('__harness_error')}"
    return out


# ── 1. pairing ──────────────────────────────────────────────────────────


def test_device_token_refusal_opens_the_pair_modal_and_the_replay_carries_the_token(outcomes):
    o = outcomes["mutation_pairs_and_replays"]
    assert o["modalWhilePending"] is True          # the pair modal, ...
    assert o["loginModalWhilePending"] is False    # ... not the admin login
    assert o["first"]["resolved"] is True          # replayed after pairing
    assert o["second"]["resolved"] is True
    # attempt 1: no token; the replay and every later request: the token
    assert o["headers"] == [None, "household-abc", "household-abc"]
    assert o["modalAfter"] is False
    assert o["paired"] is True
    assert o["stored"]["domovoi-device-token"] == "household-abc"   # trimmed


def test_cookie_only_403_counts_as_a_device_refusal(outcomes):
    o = outcomes["cookie_only_403_is_a_device_refusal"]
    assert o["modal"] is True
    assert o["out"]["resolved"] is True
    assert o["calls"] == 2


def test_dismissing_the_pair_modal_reports_a_cancelled_pairing(outcomes):
    o = outcomes["pair_modal_dismissed"]
    assert o["out"]["resolved"] is False
    assert o["out"]["status"] == 401
    assert o["out"]["deviceTokenRequired"] is True
    assert o["out"]["authCancelled"] is True
    assert o["out"]["isAuthFailure"] is True       # the page's toast stays quiet
    assert o["calls"] == 1                         # nothing to replay
    assert o["paired"] is False


def test_a_refused_get_prompts_pairing_without_a_replay(outcomes):
    o = outcomes["get_prompts_pairing"]
    assert o["out"]["resolved"] is False
    assert o["out"]["deviceTokenRequired"] is True
    assert o["pairModal"] is True
    assert o["loginModal"] is False
    assert o["calls"] == 1


def test_an_admin_refusal_still_opens_the_login_modal(outcomes):
    o = outcomes["admin_401_opens_login_not_pairing"]
    assert o["which"] == {"pair": False, "login": True}
    assert o["out"]["resolved"] is False
    assert o["out"]["deviceTokenRequired"] is False


def test_a_stored_token_rides_on_every_request(outcomes):
    o = outcomes["stored_token_is_sent"]
    assert o["headers"] == ["already-paired", "already-paired"]
    assert o["paired"] is True


def test_admin_login_pairs_the_browser_without_a_prompt(outcomes):
    o = outcomes["admin_login_auto_pairs"]
    urls = [c["url"] for c in o["calls"]]
    assert urls == ["/api/auth/login", "/api/auth/device-token", "/api/x"]
    assert o["calls"][1]["auth"] == "Bearer bearer-1"       # the read needs the session
    assert o["calls"][2] == {"url": "/api/x", "method": "POST", "auth": "Bearer bearer-1",
                             "device": "household-from-login"}
    assert o["pairModal"] is False
    assert o["paired"] is True
    assert o["version"] >= 2                                  # login + pair both counted


def test_setup_stores_the_rotated_token_not_the_pre_setup_one(outcomes):
    o = outcomes["setup_auto_pairs_with_the_rotated_token"]
    assert o["device"] == "post-setup-token"
    assert o["calls"] == ["/api/auth/setup", "/api/auth/device-token"]


def test_logout_keeps_the_household_token(outcomes):
    o = outcomes["logout_keeps_the_device_token"]
    assert o == {"paired": True, "loggedIn": False}


def test_the_token_is_stored_per_server(outcomes):
    o = outcomes["token_is_per_server"]
    assert o["before"] is False                    # the same-origin token is not this server's
    assert o["url"] == "http://10.0.0.7:6369/api/x"
    assert o["header"] == "other-house"
    assert o["stored"]["domovoi-device-token@http://10.0.0.7:6369"] == "other-house"
    assert o["stored"]["domovoi-device-token"] == "same-origin-token"


def test_the_websocket_hello_carries_the_token(outcomes):
    o = outcomes["ws_hello_carries_the_token"]
    assert o["url"] == "ws://domovoi.lan:6369/ws/state"
    assert o["sent"] == [{"subscribe": [], "device_token": "household"}]
    assert outcomes["ws_hello_without_a_token"]["sent"] == [{"subscribe": []}]


# ── 3. trust before select ──────────────────────────────────────────────


def test_an_untrusted_server_is_neither_persisted_nor_sent_the_password(outcomes):
    o = outcomes["untrusted_server_is_not_selected_or_logged_into"]
    assert o["selected"] is False
    assert "domovoi-server" not in o["afterRefusal"]["stored"]
    assert o["afterRefusal"]["reloads"] == 0
    assert o["afterRefusal"]["trusted"] is False
    assert o["login"]["resolved"] is False
    assert o["login"]["untrusted"] is True
    assert o["loginCalls"] == 0                    # the password never left the browser
    assert o["selectedAfterTrust"] is True
    assert o["reloads"] == 1
    assert o["stored"]["domovoi-server"] == "http://10.0.0.9:6369"
    assert json.loads(o["stored"]["domovoi-trusted-servers"]) == ["http://10.0.0.9:6369"]
    assert o["host"] == "10.0.0.9:6369"            # what the prompt shows
    assert o["sameOriginTrusted"] is True


def test_legacy_saved_servers_are_trusted_once_without_re_asking(outcomes):
    o = outcomes["legacy_saved_servers_seed_the_trusted_list"]
    assert o["two"] is True and o["three"] is True and o["four"] is False
    assert set(json.loads(o["stored"]["domovoi-trusted-servers"])) == {
        "http://10.0.0.2:6369", "http://10.0.0.3:6369"}


def test_forgetting_a_server_untrusts_it(outcomes):
    o = outcomes["forgetting_a_server_untrusts_it"]
    assert o["trusted"] is False
    assert o["list"] == []


# ── static: the bootstrap only runs plugin JS from a trusted server ─────


def test_index_bootstrap_skips_plugin_scripts_for_an_untrusted_server():
    src = (STATIC / "index.html").read_text(encoding="utf-8")
    loader = src[src.index("const loadPluginScripts"):src.index("const bootstrap")]
    assert re.search(r"if \(!ServerStore\.isTrusted\(ServerStore\.current\(\)\)\)", loader)
    # The guard sits before the fetch/transform/execute loop.
    assert loader.index("isTrusted") < loader.index("Babel.transform")


def test_the_pair_modal_names_where_an_admin_finds_the_token():
    src = (STATIC / "components.jsx").read_text(encoding="utf-8")
    modal = src[src.index("const PairModal"):src.index("const AuthModalHost")]
    assert "pair this browser" in modal
    assert "Settings → Devices → Household token" in modal
    host = src[src.index("const AuthModalHost"):src.index("/* expose to other Babel scripts */")]
    assert "Auth.pairModalOpen" in host and "PairModal" in host


# ── the token format ────────────────────────────────────────────────────

CANONICAL_PHRASE = "acorn-maple-river-thistle-harbor-quartz-willow-ember"


def _b64url(token: str) -> str:
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")


def test_a_typed_token_is_stored_verbatim(outcomes):
    r"""auth.js used to canonicalise before storing — trim, LOWERCASE,
    collapse every run of [\s_-] to one hyphen. An admin may now set the
    household token to any printable ASCII, so that would have paired this
    browser to a token that does not exist: `MyT0ken!!going` stored as
    `myt0ken!!going`, 401 on everything, and the settings card reporting
    "not paired" right after a successful save."""
    o = outcomes["a_typed_token_is_stored_verbatim"]
    assert o["stored"]["domovoi-device-token"] == "MyT0ken!!going"
    assert o["header"] == "MyT0ken!!going"
    # It is itself a legal RFC 9110 token, so both elements are offered.
    assert o["protocols"][0] == f"domovoi.device-token-b64.{_b64url('MyT0ken!!going')}"
    assert o["protocols"][1] == "domovoi.device-token.MyT0ken!!going"


def test_a_token_with_spaces_and_a_comma_still_opens_a_socket(outcomes):
    """The reason the whole transport changed. This token is illegal in a
    raw subprotocol — the stub now throws exactly as Chromium does — and
    the comma would be truncated by the server's own header split even if
    it got through. base64url carries it, and it is the ONLY element
    offered, because the constructor validates every element."""
    o = outcomes["a_token_with_spaces_and_a_comma_still_opens_a_socket"]
    token = "Maple Street, 1984!"
    assert o["stored"] == token
    assert o["header"] == token
    assert o["sockets"] == 1                       # constructed, not thrown
    assert o["protocols"] == [f"domovoi.device-token-b64.{_b64url(token)}"]
    assert " " not in o["protocols"][0] and "," not in o["protocols"][0]


def test_a_phrase_offers_both_the_b64_and_the_legacy_element(outcomes):
    """Mid-rollout safety in both directions. A browser whose offers are
    ALL unrecognised gets no subprotocol echoed back and drops the socket,
    so a new data.js against a not-yet-restarted server must still offer
    something that server knows — and it can, because a phrase is a legal
    token."""
    o = outcomes["a_phrase_offers_both_the_b64_and_the_legacy_element"]
    assert o["protocols"] == [
        f"domovoi.device-token-b64.{_b64url(CANONICAL_PHRASE)}",
        f"domovoi.device-token.{CANONICAL_PHRASE}",
    ]


def test_a_legacy_hex_token_is_stored_unchanged(outcomes):
    o = outcomes["a_legacy_hex_token_is_stored_unchanged"]
    assert o["stored"] == o["hex"]
    assert o["header"] == o["hex"]
