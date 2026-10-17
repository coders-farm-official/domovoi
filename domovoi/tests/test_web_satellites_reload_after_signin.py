"""Settings -> Satellites loads its rooms once an admin signs in.

Batch-15 integration (2026-10-17): open Settings -> Satellites on a browser
that is signed out and holds no household token. ``GET /api/satellites``
is a device read and ``GET /api/satellites/approvals`` an admin read, so
both are refused and a sign-in prompt opens. After the admin signed in,
the page kept "0 known" until a reload. The Files page had the same bug
(F-052, ``test_web_files_reload_after_pairing.py``); this is its twin.

Driven through ``jsx_interact_harness.js`` on top of the REAL auth.js and
data.js (only ``fetch`` is scripted: a small server that answers by the
credential a request carried) and the real satellites.jsx. No DB; needs
node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/auth.js", "web/static/data.js", "web/static/components.jsx",
         "web/static/satellite_media.jsx", "web/static/satellites.jsx"]

SETUP = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
setInterval = () => 0; clearInterval = () => {};
const __store = new Map();
localStorage = {
  getItem: (k) => (__store.has(k) ? __store.get(k) : null),
  setItem: (k, v) => { __store.set(k, String(v)); },
  removeItem: (k) => { __store.delete(k); },
};
document.hidden = false;
WebSocket = function () { this.l = {}; };
WebSocket.prototype.addEventListener = function () {};
WebSocket.prototype.send = function () {};
IntersectionObserver = class { observe() {} disconnect() {} };
SpokenAudio = { clientId: () => 'dev-1', getPerson: () => null };

// The server: one admin password, the household token; the roster and
// the USB adoptions on the device tier, the approvals on the admin tier
// (the words web/backend/admin_auth.py refuses them with).
window.__srv = { sessions: new Set(), device: 'house-token', n: 0 };
window.__fetches = [];
const __answer = (status, body) => {
  const text = body == null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
const __room = { room_id: 'kitchen', status: 'online', room_label: null, now_playing: null,
                 last_seen: null, volume: 40 };
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
  const hd = o.headers || {};
  const srv = window.__srv;
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') && srv.sessions.has(auth.slice(7));
  const sentDevice = hd['X-Device-Token'];
  const household = bearer || sentDevice === srv.device;
  window.__fetches.push({ method, path, household, bearer, device: sentDevice || null });
  // A wrong household token (an old install's, left in this browser): the
  // per-address lockout answers 429 while it lasts, then the refusal.
  if (!bearer && sentDevice && sentDevice !== srv.device) {
    if (srv.throttled) {
      return __answer(429, { detail: 'too many wrong X-Device-Token values from this address — '
        + 'retry in 1s. A device or browser tab at this address may still be sending an old '
        + 'household token (after a reinstall or a token change): pair it again or close it, '
        + 'or sign in as an admin' });
    }
    return __answer(401, { detail: 'X-Device-Token or admin session required' });
  }
  if (method === 'POST' && path === '/api/auth/login') {
    srv.n += 1; const token = `session-${srv.n}`; srv.sessions.add(token);
    return __answer(200, { ok: true, token });
  }
  if (path === '/api/auth/status') return __answer(200, { setup_complete: true, authenticated: bearer });
  if (path === '/api/auth/device-token') {
    return bearer ? __answer(200, { token: srv.device, header: 'X-Device-Token' })
                  : __answer(401, { detail: 'admin session required' });
  }
  if (path === '/api/satellites' || path === '/api/satellites/pending') {
    if (!household) return __answer(401, { detail: 'X-Device-Token or admin session required' });
    return __answer(200, path === '/api/satellites' ? [__room] : []);
  }
  if (path === '/api/satellites/approvals') {
    if (!bearer) return __answer(401, { detail: 'admin session required' });
    return __answer(200, []);
  }
  if (path === '/api/health') return __answer(200, { domovoi_reachable: true, db_reachable: true });
  if (path === '/api/capabilities') return __answer(200, { host_kind: 'linux' });
  if (method !== 'GET') return __answer(200, {});
  return __answer(200, []);
};
window.__flush = async (h, n = 10) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__gets = (p) => window.__fetches.filter((f) => f.method === 'GET' && f.path === p);
window.__later = (fn, ms) => __st(fn, ms);
window.__known = (h) => h.text().filter((t) => / known$/.test(t));
"""

# This browser still holds an old install's household token, and the
# server is making this address wait for it (429).
STALE_TOKEN = r"""
__store.set('domovoi-device-token', 'old-install-token');
window.__srv.throttled = true;
"""

SCENARIOS = {
    "sign_in": {
        "files": FILES, "component": "(window.__Auth = Auth, SatellitesPanel)", "props": {},
        "setup": SETUP,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const sats = () => w.__gets('/api/satellites');
          h.render(); await step();
          const refused = { reads: sats().length, known: w.__known(h),
                            prompt: A.modalOpen || A.pairModalOpen };
          await A.login('the-admin-password'); await step();
          return { refused,
                   after: { reads: sats().length, household: sats().map((f) => f.household),
                            known: w.__known(h), loggedIn: A.isLoggedIn(),
                            room: h.text().some((t) => t.includes('kitchen')) } };
        """,
    },
    "throttled": {
        "files": FILES, "component": "(window.__Auth = Auth, SatellitesPanel)", "props": {},
        "setup": SETUP + STALE_TOKEN,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const sats = () => w.__gets('/api/satellites');
          h.render(); await step();
          const refused = { reads: sats().length, known: w.__known(h), prompt: A.modalOpen };
          await A.login('the-admin-password'); await step();
          return { refused,
                   after: { reads: sats().length, known: w.__known(h),
                            room: h.text().some((t) => t.includes('kitchen')) } };
        """,
    },
    "wait_then_sign_in": {
        # The wait runs out before anyone signs in: the old token goes out
        # once more (that retry is refused for the token itself), and the
        # admin sign-in after it must still reload the page.
        "files": FILES, "component": "(window.__Auth = Auth, SatellitesPanel)", "props": {},
        "setup": SETUP + STALE_TOKEN,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const sats = () => w.__gets('/api/satellites');
          h.render(); await step();
          w.__srv.throttled = false;
          await new Promise((r) => w.__later(r, 1300)); await step();
          const spent = { reads: sats().length, devices: sats().map((f) => f.device),
                          known: w.__known(h) };
          await A.login('the-admin-password'); await step();
          return { spent, after: { reads: sats().length, known: w.__known(h),
                                   room: h.text().some((t) => t.includes('kitchen')) } };
        """,
    },
    "sign_in_instead": {
        "files": FILES, "component": "(window.__Auth = Auth, SatellitesPanel)", "props": {},
        "setup": SETUP,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const sats = () => w.__gets('/api/satellites');
          h.render(); await step();
          A.signInInstead(); await step();
          await A.login('the-admin-password'); await step();
          return { reads: sats().length, known: w.__known(h), paired: A.isPaired(),
                   room: h.text().some((t) => t.includes('kitchen')) };
        """,
    },
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    f = tmp_path_factory.mktemp("satellites-signin") / "scenarios.json"
    f.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{f}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


KNOWN_EMPTY = ["0 online · 0 offline · 0 known"]
KNOWN_ONE = ["1 online · 0 offline · 1 known"]


def test_the_rooms_appear_once_an_admin_signs_in(outcomes) -> None:
    o = outcomes["sign_in"]
    assert o["refused"] == {"reads": 1, "known": KNOWN_EMPTY, "prompt": True}
    after = o["after"]
    assert after["loggedIn"] is True
    assert after["reads"] == 2, "the roster was not read again after the sign-in"
    assert after["household"] == [False, True]
    assert after["known"] == KNOWN_ONE and after["room"] is True


def test_signing_in_from_the_pair_prompt_loads_them_too(outcomes) -> None:
    o = outcomes["sign_in_instead"]
    assert o["paired"] is True
    assert o["reads"] == 2 and o["known"] == KNOWN_ONE and o["room"] is True


def test_a_sign_in_lifts_the_wait_an_old_household_token_caused(outcomes) -> None:
    """The batch-15 case: this browser still held an old install's
    household token, so the server made the address wait (429). Its words
    say "sign in as an admin"; the sign-in has to reload the roster."""
    o = outcomes["throttled"]
    assert o["refused"]["reads"] == 1 and o["refused"]["known"] == KNOWN_EMPTY
    after = o["after"]
    assert after["reads"] == 2, "a 429 for the old token was never retried after the sign-in"
    assert after["known"] == KNOWN_ONE and after["room"] is True


def test_the_sign_in_after_a_spent_retry_still_reloads(outcomes) -> None:
    """The wait ran out first, so the retry went out under the old token and
    was refused for it. The hooks used to allow ONE retry per refusal: the
    sign-in that followed left "0 known" on screen until a reload."""
    o = outcomes["wait_then_sign_in"]
    spent = o["spent"]
    assert spent["reads"] == 2 and spent["devices"] == ["old-install-token", "old-install-token"]
    assert spent["known"] == KNOWN_EMPTY
    after = o["after"]
    assert after["reads"] == 3
    assert after["known"] == KNOWN_ONE and after["room"] is True
