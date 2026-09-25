"""The document editors must survive the refresh that drops the bearer.

The admin Bearer lives in the dashboard's memory only (web/backend/api/
auth.py); the ``HttpOnly`` cookie survives a reload and renders GET state.
So an admin who refreshes is silently read-only until the first write, and
every document write is ADMIN tier (``web/backend/api/documents.py`` —
``PUT /text``, ``PUT /sheet``, ``/create``, ``/delete``, ``/download-zip``,
``/upload``, ``/drawings/write``).

``data.js`` ``_sendWithAuthRetry`` exists for exactly that: a 401/403 on a
mutation with a replayable body opens the sign-in and REPLAYS the request
once a bearer exists. The text editor never reached it. ``.txt`` does not
open the markdown editor — ``files.jsx`` routes ``.md``/``.markdown`` to
'doc' and everything else to 'text' — and ``TextEditorOverlay``'s Save was
a bare ``fetch()``. One red PUT, no prompt, no replay, the typing lost.

What this module pins:

* the transport — no ``web/static`` mutation may hand-build its request;
  apiFetch / apiUpload / apiFetchRaw are the only ways out (the exact
  root cause, as a grep that cannot be argued with);
* the retry — a cookie-only 403 on a PUT with a JSON body opens the LOGIN
  (not the pairing modal), replays ONCE, and the replay carries the fresh
  bearer and the identical body;
* the words — a dismissed sign-in is CANCELLED with the work still in the
  buffer, a prompt still on screen gets silence, and a refusal that
  survived a fresh bearer is a visible failure carrying the detail;
* the editors — ``TextEditorOverlay`` driven for real: type, press Save,
  sign in at the prompt, and the save completes with the typed text;
* the layer — a toast fired from inside an editor has to be ABOVE the
  opaque full-screen overlay that fired it, or the editor says nothing at
  all (which is what "pressing Save did nothing" actually was).

No DB, no ``requires_db`` — this must never skip. The behavioural halves
need ``node`` (the runtime the JSX compile check already relies on) and
fail, not skip, without it.
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
INTERACT_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"

# The ADMIN tier's cookie-only refusal, verbatim from
# domovoi/admin_auth.py require_admin_mutation — and verbatim from the
# body the fullstack harness returned for PUT /api/documents/text/notes.txt.
ADMIN_COOKIE_ONLY = (
    "mutations require Authorization: Bearer — "
    "the dashboard cookie only renders GET state"
)
# The DEVICE tier's, which must route to the PAIRING modal instead.
DEVICE_COOKIE_ONLY = (
    "X-Device-Token required — the dashboard cookie does not "
    "authorize device-tier actions"
)


# ─── Part 1: the store, with the real data.js in a vm ────────────────

STORE_HARNESS_JS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const resp = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : (status === 403 ? 'Forbidden' : 'Error'),
  headers: { get: () => null },
  body: { getReader: () => ({ read: async () => ({ done: true }) }) },
  text: async () => body,
  json: async () => JSON.parse(body),
});

const run = async (scenario) => {
  const { call, sequence, signIn, refusal } = scenario;
  const calls = [];
  let requestLoginCalls = 0;
  let requestPairingCalls = 0;
  let ensureLoggedInCalls = 0;
  let ensurePairedCalls = 0;
  const auth = {
    token: null,
    status: { setup_complete: true, authenticated: false },
    headers() {
      const h = { 'X-Device-Token': 'household-token' };
      if (this.token) h.Authorization = 'Bearer ' + this.token;
      return h;
    },
    deviceToken() { return 'household-token'; },
    requestLogin() { requestLoginCalls += 1; },
    requestPairing() { requestPairingCalls += 1; },
    ensureLoggedIn() {
      ensureLoggedInCalls += 1;
      if (!signIn) return Promise.resolve(false);
      this.token = 'fresh-bearer';
      return Promise.resolve(true);
    },
    ensurePaired() { ensurePairedCalls += 1; return Promise.resolve(false); },
  };
  const fetch = async (url, opts) => {
    const o = opts || {};
    const headers = o.headers || {};
    const status = sequence[Math.min(calls.length, sequence.length - 1)];
    calls.push({
      url: String(url),
      method: (o.method || 'GET').toUpperCase(),
      auth: headers.Authorization || null,
      deviceToken: headers['X-Device-Token'] || null,
      requestedWith: headers['X-Requested-With'] || null,
      contentType: headers['Content-Type'] || null,
      body: typeof o.body === 'string' ? o.body : null,
    });
    return resp(status, status === 200
      ? '{"rel_path":"notes.txt","category":"text"}'
      : JSON.stringify({ detail: refusal }));
  };
  const sandbox = { window: {}, console, fetch, Auth: auth, setTimeout, clearTimeout,
                    localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const w = sandbox.window;

  const body = JSON.stringify({ text: 'shopping list\n- milk\n- oats\n' });
  const invoke = call === 'raw'
    ? () => w.apiFetchRaw('/api/documents/download-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rel_paths: ['notes.txt'] }),
      })
    : () => w.apiFetch('/api/documents/text/notes.txt', { method: 'PUT', body });

  let outcome;
  try {
    const value = await invoke();
    outcome = {
      resolved: true,
      // apiFetchRaw must hand back the Response itself, unread.
      isResponse: !!(value && typeof value.text === 'function' && 'status' in value),
      value: (value && value.status) || null,
    };
  } catch (e) {
    outcome = {
      resolved: false,
      status: e.status,
      authCancelled: !!e.authCancelled,
      loginPrompted: !!e.loginPrompted,
      deviceTokenRequired: !!e.deviceTokenRequired,
      isAuthFailure: w.isAuthFailure(e),
      toast: w.mutationErrorText(e),
      // A page action with no buffer to keep must not promise one.
      toastNoBuffer: w.mutationErrorText(e, 'upload', { kept: false }),
      message: e.message,
    };
  }
  return { ...outcome, calls, requestLoginCalls, requestPairingCalls,
           ensureLoggedInCalls, ensurePairedCalls,
           exports: { apiFetchRaw: typeof w.apiFetchRaw,
                      mutationErrorText: typeof w.mutationErrorText } };
};

(async () => {
  const scenarios = JSON.parse(process.argv[3]);
  const out = {};
  for (const [name, sc] of Object.entries(scenarios)) out[name] = await run(sc);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(2); });
"""

STORE_SCENARIOS = {
    # Kamron's state: cookie, no bearer. Sign in at the prompt.
    "put_signed_in": {"call": "put", "sequence": [403, 200], "signIn": True,
                      "refusal": ADMIN_COOKIE_ONLY},
    # Same, but the operator changes their mind at the password prompt.
    "put_dismissed": {"call": "put", "sequence": [403], "signIn": False,
                      "refusal": ADMIN_COOKIE_ONLY},
    # The replay is refused too — a real bug, and the toast is all there is.
    "put_refused_twice": {"call": "put", "sequence": [403, 403], "signIn": True,
                          "refusal": ADMIN_COOKIE_ONLY},
    # The streamed twin: same prompt, same replay, raw Response back.
    "raw_signed_in": {"call": "raw", "sequence": [403, 200], "signIn": True,
                      "refusal": ADMIN_COOKIE_ONLY},
    # A DEVICE-tier refusal must open the pairing modal, not the login.
    "put_device_refusal": {"call": "put", "sequence": [403], "signIn": True,
                           "refusal": DEVICE_COOKIE_ONLY},
}


@pytest.fixture(scope="module")
def node_bin() -> str:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static (see jsxcheck)"
    return node


@pytest.fixture(scope="module")
def store(node_bin, tmp_path_factory) -> dict:
    harness = tmp_path_factory.mktemp("editor-save") / "store.js"
    harness.write_text(STORE_HARNESS_JS, encoding="utf-8")
    proc = subprocess.run(
        [node_bin, str(harness), str(DATA_JS), json.dumps(STORE_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_helpers_the_editors_need_are_exported(store):
    for name, o in store.items():
        assert o["exports"] == {"apiFetchRaw": "function",
                                "mutationErrorText": "function"}, name


def test_a_cookie_only_403_on_a_put_opens_the_login_and_replays(store):
    o = store["put_signed_in"]
    assert o["resolved"] is True
    assert o["ensureLoggedInCalls"] == 1        # the sign-in prompt, once
    assert o["ensurePairedCalls"] == 0          # not the pairing modal
    assert o["requestLoginCalls"] == 0          # ...and never re-opened
    assert len(o["calls"]) == 2                 # refused, then replayed


def test_the_replay_carries_the_fresh_bearer_and_the_same_body(store):
    first, second = store["put_signed_in"]["calls"]
    assert first["method"] == second["method"] == "PUT"
    assert first["auth"] is None                            # the refusal
    assert second["auth"] == "Bearer fresh-bearer"          # the replay
    assert first["body"] == second["body"]                  # nothing retyped
    assert json.loads(second["body"])["text"].startswith("shopping list")
    # Both attempts keep the CSRF backstop and the household token.
    for call in (first, second):
        assert call["requestedWith"] == "XMLHttpRequest"
        assert call["deviceToken"] == "household-token"


def test_a_dismissed_sign_in_reports_cancelled_not_failed(store):
    o = store["put_dismissed"]
    assert o["resolved"] is False
    assert len(o["calls"]) == 1                 # nothing replayed
    assert o["authCancelled"] is True
    assert o["isAuthFailure"] is True
    toast = o["toast"]
    assert "cancelled" in toast
    assert "still here" in toast                # the work was not lost
    assert "failed" not in toast.lower()
    assert "403" not in toast                   # no raw status line
    assert "Authorization: Bearer" not in toast  # nor the server's plumbing
    # The Files page's own actions have no buffer, so they promise none.
    assert o["toastNoBuffer"] == "upload cancelled — not signed in."


def test_a_prompt_that_is_still_on_screen_gets_no_toast(store):
    # loginPrompted without authCancelled: the modal owns the story (F-006).
    o = store["put_device_refusal"]
    assert o["deviceTokenRequired"] is True
    assert o["ensurePairedCalls"] == 1          # the PAIR modal...
    assert o["ensureLoggedInCalls"] == 0        # ...not the admin login
    assert o["authCancelled"] is True           # ensurePaired resolved false
    assert "not paired" in o["toast"]


def test_a_refusal_that_survives_a_fresh_bearer_is_a_visible_failure(store):
    o = store["put_refused_twice"]
    assert o["resolved"] is False
    assert len(o["calls"]) == 2                 # one replay, never a loop
    assert o["calls"][1]["auth"] == "Bearer fresh-bearer"
    assert o["authCancelled"] is False
    assert o["isAuthFailure"] is False          # no modal was re-opened
    toast = o["toast"]
    assert toast.startswith("Save failed:")
    assert ADMIN_COOKIE_ONLY[:30] in toast      # the server's own detail


def test_the_streamed_call_prompts_replays_and_returns_the_response(store):
    o = store["raw_signed_in"]
    assert o["resolved"] is True
    assert o["isResponse"] is True              # NOT parsed JSON
    assert len(o["calls"]) == 2
    assert o["calls"][1]["auth"] == "Bearer fresh-bearer"
    assert o["calls"][0]["contentType"] == "application/json"


# ─── Part 2: the editor itself, driven ───────────────────────────────
#
# The real data.js is loaded alongside the real JSX, so the component
# goes through the actual retry machinery rather than a stub of it.

EDITOR_SETUP = r"""
globalThis.__net = [];
globalThis.__prompts = { login: 0, pair: 0, ensure: 0 };
const __resp = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : 'Forbidden',
  headers: { get: () => null },
  text: async () => body,
  json: async () => JSON.parse(body),
});
globalThis.Auth = {
  token: null,
  status: { setup_complete: true, authenticated: false },
  headers() {
    const h = { 'X-Device-Token': 'household-token' };
    if (this.token) h.Authorization = 'Bearer ' + this.token;
    return h;
  },
  deviceToken() { return 'household-token'; },
  requestLogin() { globalThis.__prompts.login += 1; },
  requestPairing() { globalThis.__prompts.pair += 1; },
  ensureLoggedIn() {
    globalThis.__prompts.ensure += 1;
    if (!globalThis.__SIGN_IN) return Promise.resolve(false);
    this.token = 'fresh-bearer';
    return Promise.resolve(true);
  },
  ensurePaired() { return Promise.resolve(false); },
};
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  const method = (o.method || 'GET').toUpperCase();
  const headers = o.headers || {};
  globalThis.__net.push({ method, url: String(url),
                          auth: headers.Authorization || null,
                          body: typeof o.body === 'string' ? o.body : null });
  if (method === 'GET') {
    return __resp(200, JSON.stringify({ text: 'shopping list\n- milk\n- oats\n' }));
  }
  if (!headers.Authorization) {
    return __resp(403, JSON.stringify({ detail: globalThis.__REFUSAL }));
  }
  return __resp(200, '{"rel_path":"notes.txt","category":"text"}');
};
"""

# Open the file, type, press Save. The result says what went over the
# wire, what the editor said, and whether the typing survived.
EDITOR_SCRIPT = r"""
  h.render();
  await h.settle();
  h.rerender();
  const loaded = h.find({ type: 'textarea' });
  await h.change({ type: 'textarea' }, 'shopping list\n- milk\n- oats\nTXT-EDIT');
  const beforeSave = { unsaved: h.text().some((t) => String(t).includes('unsaved')) };
  await h.click({ type: 'button', text: 'Save' });
  await h.settle();
  h.rerender();
  const ta = h.find({ type: 'textarea' });
  return {
    loadedText: loaded ? loaded.props.value : null,
    beforeSave,
    net: h.global('__net').map((c) => ({ method: c.method, auth: c.auth, body: c.body })),
    prompts: h.global('__prompts'),
    toasts: h.fnCalls.filter((c) => c.name === 'fire').map((c) => c.args[0]),
    unsavedAfter: h.text().some((t) => String(t).includes('unsaved')),
    bufferAfter: ta ? ta.props.value : null,
  };
"""


def _editor_scenario(component: str, page: str, sign_in: bool,
                     refusal: str = ADMIN_COOKIE_ONLY) -> dict:
    setup = (f"globalThis.__SIGN_IN = {json.dumps(sign_in)};\n"
             f"globalThis.__REFUSAL = {json.dumps(refusal)};\n" + EDITOR_SETUP)
    return {
        "files": ["web/static/data.js", COMPONENTS, page],
        "component": component,
        "props": {"rel_path": "notes.txt"},
        "fnProps": ["onClose", "fire"],
        "setup": setup,
        "script": EDITOR_SCRIPT,
    }


EDITOR_SCENARIOS = {
    "text_signed_in": _editor_scenario("TextEditorOverlay", "web/static/files.jsx", True),
    "text_dismissed": _editor_scenario("TextEditorOverlay", "web/static/files.jsx", False),
    "doc_signed_in": _editor_scenario("DocEditorOverlay", "web/static/doc_editor.jsx", True),
    "doc_dismissed": _editor_scenario("DocEditorOverlay", "web/static/doc_editor.jsx", False),
}


@pytest.fixture(scope="module")
def editors(node_bin) -> dict:
    proc = subprocess.run(
        [node_bin, str(INTERACT_HARNESS), str(REPO_ROOT), json.dumps(EDITOR_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


@pytest.mark.parametrize("name", ["text_signed_in", "doc_signed_in"])
def test_the_editor_reads_the_file_on_the_cookie_alone(editors, name):
    # Reads are DAILY tier: the drifted admin can still open the file,
    # which is why the failure only shows up on Save.
    o = editors[name]
    assert o["loadedText"].startswith("shopping list")
    assert o["net"][0]["method"] == "GET"
    assert o["beforeSave"]["unsaved"] is True


@pytest.mark.parametrize("name", ["text_signed_in", "doc_signed_in"])
def test_pressing_save_prompts_and_then_completes_the_save(editors, name):
    o = editors[name]
    writes = [c for c in o["net"] if c["method"] == "PUT"]
    assert len(writes) == 2, o["net"]              # refused, then replayed
    assert writes[0]["auth"] is None
    assert writes[1]["auth"] == "Bearer fresh-bearer"
    assert o["prompts"]["ensure"] == 1             # the password prompt
    assert o["prompts"]["pair"] == 0
    # The typed text is what was saved, and the editor stops saying unsaved.
    assert "TXT-EDIT" in json.loads(writes[1]["body"])["text"]
    assert o["toasts"] == ["Saved"]
    assert o["unsavedAfter"] is False


@pytest.mark.parametrize("name", ["text_dismissed", "doc_dismissed"])
def test_dismissing_the_prompt_keeps_the_text_and_says_cancelled(editors, name):
    o = editors[name]
    writes = [c for c in o["net"] if c["method"] == "PUT"]
    assert len(writes) == 1                        # nothing replayed
    assert o["prompts"]["ensure"] == 1
    assert len(o["toasts"]) == 1
    toast = o["toasts"][0]
    assert "cancelled" in toast and "failed" not in toast.lower()
    assert "TXT-EDIT" in o["bufferAfter"]          # the typing is still there
    assert o["unsavedAfter"] is True               # ...and still flagged


# ─── Part 3: the invariants, as source facts ─────────────────────────

PAGE_SCRIPTS = sorted(p for p in STATIC.glob("*.jsx"))

# `fetch(` that is not apiFetch/apiFetchRaw, with the options object that
# follows it on the same call. A mutating method in there is the bug.
RAW_FETCH = re.compile(r"(?<![\w.])fetch\(", re.MULTILINE)
MUTATING_METHOD = re.compile(r"method:\s*'(POST|PUT|PATCH|DELETE)'")


def _without_comments(src: str) -> str:
    """Blank out comment lines, keeping the line count so offenders can
    still be reported as file:line. Prose about `fetch()` is not a call."""
    out = []
    for line in src.split("\n"):
        stripped = line.lstrip()
        out.append("" if stripped.startswith(("//", "/*", "*")) else line)
    return "\n".join(out)


def test_no_page_script_hand_builds_a_mutation():
    """The root cause, as a rule: every mutation goes through the helpers.

    A raw ``fetch`` sends the right headers and skips the retry, so the
    refusal is the end of the story — no sign-in prompt, no replay, and
    whatever the operator had typed is gone.
    """
    offenders = []
    for path in PAGE_SCRIPTS:
        src = _without_comments(path.read_text(encoding="utf-8"))
        for m in RAW_FETCH.finditer(src):
            window = src[m.end():m.end() + 400]
            # stop at the end of this call's options object
            head = window.split("});")[0]
            if MUTATING_METHOD.search(head):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.name}:{line}")
    assert offenders == [], (
        "raw fetch() mutation(s) — use apiFetch / apiUpload / apiFetchRaw: "
        + ", ".join(offenders))


# Every editor that holds unsent work behind a Save button.
EDITOR_SAVES = {
    "files.jsx": "TextEditorOverlay",     # .txt and every unknown type
    "doc_editor.jsx": "DocEditorOverlay",  # .md
    "sheet_editor.jsx": "SheetEditorOverlay",  # .xlsx / .csv
    "drawings.jsx": "DrawingOverlay",     # .excalidraw
}


@pytest.mark.parametrize("name", sorted(EDITOR_SAVES))
def test_every_editor_save_branches_on_the_auth_outcome(name: str):
    src = (STATIC / name).read_text(encoding="utf-8")
    # In the editor's OWN body, not merely somewhere in the file.
    start = src.index(f"const {EDITOR_SAVES[name]} = ")
    body = src[start:start + 4000]
    assert "mutationErrorText" in body, f"{name}: save catch ignores the auth outcome"
    # The words are the helper's to choose now; a literal "Save failed"
    # here is a catch that decided before it looked.
    assert "Save failed" not in src, f"{name}: unconditional Save-failed toast"


@pytest.mark.parametrize("name", ["doc_editor.jsx", "sheet_editor.jsx"])
def test_export_stops_when_the_save_did_not_happen(name: str):
    """Export used to download the server's copy either way, handing back
    the OLD text after a refused save — the one shape here that loses
    work without saying so."""
    src = (STATIC / name).read_text(encoding="utf-8")
    assert re.search(r"if \(dirty && !\(await onSave\(\)\)\) return;", src), name
    assert "if (dirty) await onSave();" not in src, name


def test_a_toast_outranks_every_overlay_it_can_be_fired_from():
    """z-index 60 under an opaque z-80 takeover is why pressing Save
    looked like it did nothing: the editor's own message was painted
    behind the editor."""
    toast = re.search(r"gap: 8, zIndex: (\d+), maxWidth",
                      (STATIC / "components.jsx").read_text(encoding="utf-8"))
    assert toast, "the toast host's zIndex moved — update this test"
    toast_z = int(toast.group(1))
    overlays = []
    for path in PAGE_SCRIPTS:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"position: 'fixed', inset: 0, zIndex: (\d+)", src):
            overlays.append((path.name, int(m.group(1))))
    assert overlays, "no full-screen overlays found — update this test"
    assert toast_z > max(z for _, z in overlays), overlays
    # The blocking sign-in prompt lives at 100 (styles.css .cal-modal-bg);
    # a toast must clear that too, since it reports what the prompt did.
    assert toast_z > 100
