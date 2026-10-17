"""The Files page loads its libraries again once a credential arrives (F-052).

rc2 smoke (2026-10-16): admin claimed, the browser signed out and holding
no household token, open #files. ``GET /api/files/libraries`` is a device
read, so it is refused 401 and the "pair this browser" modal opens. Its
"sign in as an admin instead" pairs the browser silently (the household
token comes with the sign-in), and every hook-driven read on screen
refetched, but the Files page fetches its libraries by hand, once, on
mount: it kept "no libraries available" until you left the page and came
back.

Driven through ``jsx_interact_harness.js`` on top of the REAL auth.js and
data.js (only ``fetch`` is scripted: a small server that answers by the
credential a request carried) and the real files.jsx. No DB; needs node.
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
         "web/static/files.jsx"]

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

// The server: one admin password, the household token, and the Files
// routes on the device tier (a refusal that names the device token, as
// web/backend/admin_auth.py words it).
window.__srv = { sessions: new Set(), device: 'house-token', n: 0 };
window.__fetches = [];
const __answer = (status, body) => {
  const text = body == null ? '' : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => text, json: async () => JSON.parse(text) };
};
fetch = async (url, opts) => {
  const o = opts || {};
  const method = String(o.method || 'GET').toUpperCase();
  const path = String(url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
  const hd = o.headers || {};
  const srv = window.__srv;
  const auth = String(hd.Authorization || '');
  const bearer = auth.startsWith('Bearer ') && srv.sessions.has(auth.slice(7));
  const household = bearer || hd['X-Device-Token'] === srv.device;
  window.__fetches.push({ method, path, household });
  // An old install's household token while the address is made to wait.
  if (!bearer && hd['X-Device-Token'] && hd['X-Device-Token'] !== srv.device && srv.throttled) {
    return __answer(429, { detail: 'too many wrong X-Device-Token values from this address — '
      + 'retry in 60s. A device or browser tab at this address may still be sending an old '
      + 'household token (after a reinstall or a token change): pair it again or close it, '
      + 'or sign in as an admin' });
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
  if (path === '/api/files/libraries') {
    if (!household) return __answer(401, { detail: 'X-Device-Token required: pair this browser' });
    return __answer(200, { libraries: [{ id: 'core:documents', label: 'Documents', kind: 'core',
      icon: 'file-text', kind_icon: 'folder', editable: true, importable: true,
      doc_editing: true, reindex_kind: 'documents', present: true }] });
  }
  if (path === '/api/files/browse') {
    if (!household) return __answer(401, { detail: 'X-Device-Token required: pair this browser' });
    return __answer(200, { library_id: 'core:documents', path: '', editable: true, importable: true,
      doc_editing: true, breadcrumb: [], writable: true, blocked_reason: null,
      entries: [], total: 0, has_more: false, next_cursor: null });
  }
  if (method !== 'GET') return __answer(200, {});
  return __answer(200, []);
};
window.__flush = async (h, n = 10) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__gets = (p) => window.__fetches.filter((f) => f.method === 'GET' && f.path === p);
"""

SCENARIOS = {
    "sign_in_instead": {
        "files": FILES, "component": "(window.__Auth = Auth, FilesPage)", "props": {},
        "setup": SETUP, "refs": True,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const libs = () => w.__gets('/api/files/libraries');
          h.render(); await step();
          const refused = { reads: libs().length, pairModal: A.pairModalOpen,
                            empty: h.text().some((t) => t.includes('no libraries available')) };
          // The pair modal's "sign in as an admin instead", then the password.
          A.signInInstead(); await step();
          await A.login('the-admin-password'); await step();
          return { refused,
                   after: { reads: libs().length, household: libs().map((f) => f.household),
                            paired: A.isPaired(), pairModal: A.pairModalOpen,
                            shows: h.text().some((t) => t.includes('Documents')),
                            empty: h.text().some((t) => t.includes('no libraries available')),
                            toasts: h.text().filter((t) => t.startsWith("Couldn't load libraries")) } };
        """,
    },
    "paired_by_token": {
        "files": FILES, "component": "(window.__Auth = Auth, FilesPage)", "props": {},
        "setup": SETUP, "refs": True,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const libs = () => w.__gets('/api/files/libraries');
          h.render(); await step();
          // A notify that changes no credential (the modal closing) is no retry.
          A.closePairModal(); await step();
          const modalOnly = libs().length;
          A.pair('house-token'); await step();
          return { modalOnly, reads: libs().length,
                   shows: h.text().some((t) => t.includes('Documents')) };
        """,
    },
    # The browser still holds an old install's household token and the
    # server makes this address wait (429); its words say "sign in as an
    # admin". The sign-in has to load the libraries and the folder.
    "throttled": {
        "files": FILES, "component": "(window.__Auth = Auth, FilesPage)", "props": {},
        "setup": SETUP + r"""
__store.set('domovoi-device-token', 'old-install-token');
window.__srv.throttled = true;
""",
        "refs": True,
        "script": r"""
          const w = h.global('window'); const A = w.__Auth;
          const step = () => w.__flush(h);
          const libs = () => w.__gets('/api/files/libraries');
          h.render(); await step();
          const refused = { reads: libs().length,
                            empty: h.text().some((t) => t.includes('no libraries available')) };
          await A.login('the-admin-password'); await step();
          return { refused, reads: libs().length,
                   browsed: w.__gets('/api/files/browse').filter((f) => f.household).length,
                   shows: h.text().some((t) => t.includes('Documents')) };
        """,
    },
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    f = tmp_path_factory.mktemp("files-pairing") / "scenarios.json"
    f.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{f}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


def test_the_refused_page_loads_once_an_admin_signs_in_instead(outcomes) -> None:
    o = outcomes["sign_in_instead"]
    assert o["refused"] == {"reads": 1, "pairModal": True, "empty": True}
    after = o["after"]
    assert after["paired"] is True and after["pairModal"] is False
    assert after["reads"] == 2, "the libraries were not read again after the sign-in"
    assert after["household"] == [False, True]
    assert after["shows"] is True and after["empty"] is False
    # The pair modal told the story; no "couldn't load" toast on top of it.
    assert after["toasts"] == []


def test_pasting_the_household_token_loads_it_too(outcomes) -> None:
    o = outcomes["paired_by_token"]
    assert o["modalOnly"] == 1
    assert o["reads"] == 2 and o["shows"] is True


def test_a_sign_in_lifts_the_wait_an_old_household_token_caused(outcomes) -> None:
    """Batch 15: a 429 (an old token, the address made to wait) was never
    retried, so the sign-in its own words suggest left the page empty."""
    o = outcomes["throttled"]
    assert o["refused"] == {"reads": 1, "empty": True}
    assert o["reads"] == 2 and o["shows"] is True
    assert o["browsed"] >= 1
