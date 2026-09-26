"""F-006 — a 401 the login modal already owns must not also raise a raw toast.

When an admin-gated mutation is refused for want of a sign-in, data.js
opens the login modal (and replays the request once the operator signs
in). The pages' catch blocks then ALSO fired a toast built from the raw
exception — ``delete failed: 401 Unauthorized: {"detail":"admin session
required"}`` — behind the password prompt, which reads as a crash rather
than "please sign in" (finding F-006, card SH-03).

Two invariants:

* ``isAuthFailure(e)`` (data.js) is true exactly when the login modal was
  shown for that refusal — a dismissed sign-in, or a plain 401/403 GET —
  and FALSE for a 401 that survived a fresh sign-in, because no modal was
  re-opened for that one and the caller's toast is the only thing the
  operator will see. Exercised for real: data.js is loaded into a Node
  ``vm`` sandbox with a scripted ``fetch`` and ``Auth``.
* every delete handler SH-03 audited guards its toast with it.

No DB, no ``requires_db`` — this must never skip. The behavioural half
needs ``node`` (the same runtime the JSX compile check uses); it fails,
not skips, when node is missing so a broken toolchain is visible.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
DATA_JS = STATIC / "data.js"

# The harness scripts fetch responses per call and records what Auth saw.
# `sequence` is the list of HTTP statuses fetch returns, in order.
HARNESS_JS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const run = async (scenario) => {
  const { method, sequence, signIn } = scenario;
  const calls = [];
  let probeCalls = 0;
  let requestLoginCalls = 0;
  let ensureCalls = 0;
  const auth = {
    token: null,
    headers() { return this.token ? { Authorization: 'Bearer ' + this.token } : {}; },
    requestLogin() { requestLoginCalls += 1; },
    ensureLoggedIn(refused) {
      ensureCalls += 1;
      if (!signIn) return Promise.resolve(false);
      this.token = 'fresh-' + ensureCalls;
      return Promise.resolve(true);
    },
  };
  const fetch = async (url, opts) => {
    // The device-block probe (a read-tier GET /api/files/browse that
    // data.js makes on a refused MUTATION, to tell an admin's per-device
    // block from a missing credential) is answered apart and does not
    // consume a step of `sequence`: this module is about the request
    // under test, and the probe is counted on its own as `probeCalls`.
    if (String(url).indexOf('/api/files/browse') >= 0) {
      probeCalls += 1;
      const pb = '{"writable":true,"blocked_reason":null}';
      return { ok: true, status: 200, statusText: 'OK',
               text: async () => pb, json: async () => JSON.parse(pb) };
    }
    const status = sequence[Math.min(calls.length, sequence.length - 1)];
    calls.push({ url, method: (opts && opts.method) || 'GET',
                 auth: (opts && opts.headers && opts.headers.Authorization) || null });
    const ok = status >= 200 && status < 300;
    const body = ok ? '{"ok":true}' : '{"detail":"admin session required"}';
    return { ok, status, statusText: ok ? 'OK' : (status === 401 ? 'Unauthorized' : 'Error'),
             text: async () => body, json: async () => JSON.parse(body) };
  };
  const sandbox = { window: {}, console, fetch, Auth: auth, setTimeout, clearTimeout };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const w = sandbox.window;
  const call = method === 'GET' ? () => w.apiGet('/api/x') : () => w.apiPost('/api/x', { a: 1 });
  let outcome;
  try {
    outcome = { resolved: true, value: await call() };
  } catch (e) {
    outcome = { resolved: false, status: e.status, authCancelled: !!e.authCancelled,
                loginPrompted: !!e.loginPrompted, isAuthFailure: w.isAuthFailure(e),
                message: e.message };
  }
  return { ...outcome, fetchCalls: calls.length, probeCalls, requestLoginCalls, ensureCalls,
           exported: typeof w.isAuthFailure === 'function' };
};

(async () => {
  const scenarios = JSON.parse(process.argv[3]);
  const out = {};
  for (const [name, sc] of Object.entries(scenarios)) out[name] = await run(sc);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(2); });
"""

SCENARIOS = {
    # SH-03: signed out, delete, cancel the modal.
    "mutation_dismissed": {"method": "POST", "sequence": [401], "signIn": False},
    # Sign in at the prompt: the request is replayed with the new bearer.
    "mutation_signed_in": {"method": "POST", "sequence": [401, 200], "signIn": True},
    # The fresh bearer is refused too — a real bug, no modal re-opened.
    "mutation_persistent_401": {"method": "POST", "sequence": [401, 401], "signIn": True},
    # A GET that 401s pops the modal (fire-and-forget) and rejects.
    "get_401": {"method": "GET", "sequence": [401], "signIn": False},
    # An ordinary server error is not the modal's business.
    "mutation_500": {"method": "POST", "sequence": [500], "signIn": True},
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static/data.js (see jsxcheck)"
    harness = tmp_path_factory.mktemp("f006") / "harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(DATA_JS), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_is_auth_failure_is_exported(outcomes):
    assert all(o["exported"] for o in outcomes.values())


def test_dismissed_sign_in_is_owned_by_the_login_modal(outcomes):
    o = outcomes["mutation_dismissed"]
    assert o["resolved"] is False
    assert o["status"] == 401
    assert o["ensureCalls"] == 1          # the modal was shown for it
    assert o["requestLoginCalls"] == 0    # ...and not opened a second time
    assert o["fetchCalls"] == 1           # nothing to replay without a bearer
    assert o["authCancelled"] is True
    assert o["isAuthFailure"] is True     # => the page's catch stays quiet


def test_sign_in_replays_the_mutation_without_an_error(outcomes):
    o = outcomes["mutation_signed_in"]
    assert o["resolved"] is True
    assert o["fetchCalls"] == 2
    # The cost of telling an admin's per-device file block from a
    # missing credential, stated rather than hidden: one read-tier GET,
    # on the refusal path of a mutation only, never more than one.
    assert o["probeCalls"] == 1


def test_a_401_that_survives_a_fresh_bearer_is_a_visible_error(outcomes):
    o = outcomes["mutation_persistent_401"]
    assert o["resolved"] is False
    assert o["status"] == 401
    assert o["fetchCalls"] == 2
    assert o["requestLoginCalls"] == 0    # never re-open the modal we came from
    assert o["authCancelled"] is False
    assert o["isAuthFailure"] is False    # => the toast is the only signal left


def test_get_401_pops_the_modal_and_counts_as_auth_failure(outcomes):
    o = outcomes["get_401"]
    assert o["resolved"] is False
    assert o["fetchCalls"] == 1           # GETs are never replayed here
    assert o["ensureCalls"] == 0
    assert o["requestLoginCalls"] == 1
    assert o["isAuthFailure"] is True


def test_server_error_is_not_an_auth_failure(outcomes):
    o = outcomes["mutation_500"]
    assert o["resolved"] is False
    assert o["status"] == 500
    assert o["ensureCalls"] == 0
    assert o["requestLoginCalls"] == 0
    assert o["isAuthFailure"] is False


# The delete handlers SH-03 audited (finding F-006 "Where"): each toast must
# sit behind the guard — `if (!isAuthFailure(e))`, or data.js's
# reportMutationFailure, whose mutationErrorText stays quiet on the same
# condition. Counting occurrences, not just presence, so a new unguarded
# `delete failed:` in these files is caught too.
GUARDED_DELETE_TOASTS = {
    "files.jsx": 1,      # performDelete
    "music.jsx": 2,      # onDeleteTrack, onDeletePlaylist (reportMutationFailure)
    "people.jsx": 2,     # deleteMemory, deleteFavorite
    "calendar.jsx": 1,   # del
}


@pytest.mark.parametrize("name,expected", sorted(GUARDED_DELETE_TOASTS.items()))
def test_delete_toasts_are_guarded_by_is_auth_failure(name: str, expected: int):
    src = (STATIC / name).read_text(encoding="utf-8")
    guarded = re.findall(r"if \(!isAuthFailure\(\w+\)\) fire\(`delete failed:", src)
    guarded += re.findall(r"reportMutationFailure\(fire, 'delete', \w+", src)
    unguarded = re.findall(r"^\s*fire\(`delete failed:", src, re.MULTILINE)
    assert len(guarded) == expected, f"{name}: {len(guarded)} guarded delete toasts"
    assert unguarded == [], f"{name}: unguarded delete toast(s): {unguarded}"
