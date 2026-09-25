"""A device an admin BLOCKED must not be asked for the admin password.

An admin blocks one tablet in Settings → Devices. The server then refuses
that device's writes ``403`` with the block's own reason as the body
(``web/backend/api/files.py`` ``_assert_can_write`` / ``_blocked_message``).
``403`` is also what "sign in" looks like, so ``data.js``'s generic auth
retry treated it as a credential refusal: it called ``Auth.ensureLoggedIn``,
opened the admin-password modal over the editor and replayed the save.

That is worse than a bad error message. The block is on the DEVICE, so no
password lifts it — the replay is refused again — and being ASKED implies
that typing the household's admin password would help. Somebody whose
tablet was deliberately blocked is invited to try the admin password, and
if they cancel, the editor tells them "Save cancelled — not signed in",
which is not the reason.

HOW THIS IS FIXED WITHOUT PINNING COPY. The obvious fix — match words in
the refusal — puts a sentence in a conditional, and sentences move. The
server is asked instead: ``GET /api/files/browse`` answers ``writable``
and ``blocked_reason`` for a named device on the READ tier (reading is
never blocked), and the SAME server function renders that field and the
write refusal. So a refusal whose body IS this device's ``blocked_reason``
is the block, however anybody rewords it later. ``test_a_reworded_block_
message_is_still_recognised`` is that property, executed.

It also has to stay NARROW, which is the second half of every test here:
a delete refused for want of an admin bearer — on a device that happens
to be blocked as well — still gets the sign-in it needs.

Three layers, none of them a source-string match:

1. the server, DB-free: the sentence ``browse`` reports and the sentence
   the write refusal carries are the same one, which is the premise the
   whole client comparison rests on;
2. ``web/static/data.js`` itself, in a Node ``vm`` with a scripted
   ``fetch`` — no modal, no replay, the reason on the error;
3. the real ``TextEditorOverlay`` from ``web/static/files.jsx``, loaded
   beside the real ``data.js`` and driven through
   ``jsx_interact_harness.js`` — type, press Save, and assert what the
   person sees: a refusal at the button, Save off, the typing still
   there, and no password prompt anywhere.

No DB, no ``requires_db`` — this must never skip. Layers 2 and 3 need
``node`` (the runtime the JSX compile check already relies on) and fail
rather than skip without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
DATA_JS = STATIC / "data.js"
INTERACT = Path(__file__).with_name("jsx_interact_harness.js")

# One blocked device, as the devices roster holds it.
BLOCK_ROW = {"id": 1, "device_id": "browser-abc123",
             "device_name": "Chrome on Windows", "note": None}
BLOCK_ROW_WITH_NOTE = {**BLOCK_ROW, "note": "kids' tablet"}

# An admin refusal that is NOT the block — verbatim from
# domovoi/admin_auth.py. A blocked device pressing Delete gets this, and
# it must still reach the sign-in.
ADMIN_COOKIE_ONLY = (
    "mutations require Authorization: Bearer — "
    "the dashboard cookie only renders GET state"
)


@pytest.fixture(scope="module")
def node_bin() -> str:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static (see jsxcheck)"
    return node


# ─── layer 1: the server says the same thing twice ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [BLOCK_ROW, BLOCK_ROW_WITH_NOTE])
async def test_the_browse_answer_and_the_write_refusal_are_one_sentence(
    monkeypatch, row: dict
) -> None:
    """The premise the dashboard's whole detection rests on.

    ``browse`` hands a client ``blocked_reason`` so it can grey its own
    buttons out; ``_assert_can_write`` refuses the write. If those two
    ever stop being the same sentence, the dashboard can no longer tell
    a block from a missing credential by asking the server — so this is
    the test that fails FIRST if somebody prefixes one of them.
    """
    from web.backend.api import files as files_api

    said = files_api._blocked_message(row)
    assert said, "a block with no words is a blank refusal"

    async def _fake_block_status(device_id):
        return said if device_id == row["device_id"] else None

    monkeypatch.setattr(files_api, "_block_status", _fake_block_status)

    with pytest.raises(HTTPException) as caught:
        await files_api._assert_can_write(row["device_id"])
    assert caught.value.status_code == 403
    assert caught.value.detail == said, (
        "the write refusal no longer carries the browse answer verbatim — "
        "web/static/data.js identifies a device block by comparing them"
    )
    # ...and an unblocked device is not refused at all.
    await files_api._assert_can_write("browser-somebody-else")


def test_the_block_message_names_the_device() -> None:
    """Not the wording, the CONTENT: the sentence has to say whose device
    it is, or an operator holding two tablets cannot act on it."""
    from web.backend.api import files as files_api

    said = files_api._blocked_message(BLOCK_ROW)
    assert BLOCK_ROW["device_name"] in said, said
    # A block carrying an admin's note must surface it — that note is
    # the only place the reason for the block can be written down.
    with_note = files_api._blocked_message(BLOCK_ROW_WITH_NOTE)
    assert BLOCK_ROW_WITH_NOTE["note"] in with_note, with_note


# ─── layer 2: the real data.js decides what to do with the 403 ───────

DATA_JS_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const resp = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : 'Forbidden',
  headers: { get: () => null },
  text: async () => body,
  json: async () => JSON.parse(body),
});

const run = async (sc) => {
  // sc: { method, path, refusalStatus, refusal, browse, signIn }
  const calls = [];
  let requestLoginCalls = 0, requestPairingCalls = 0;
  let ensureCalls = 0, pairCalls = 0;
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
      ensureCalls += 1;
      if (!sc.signIn) return Promise.resolve(false);
      this.token = 'fresh-bearer';
      return Promise.resolve(true);
    },
    ensurePaired() { pairCalls += 1; return Promise.resolve(false); },
  };
  const fetch = async (url, opts) => {
    const o = opts || {};
    const method = (o.method || 'GET').toUpperCase();
    const u = String(url);
    calls.push({ method, url: u, auth: (o.headers || {}).Authorization || null });
    if (u.indexOf('/api/files/browse') === 0 || u.indexOf('/api/files/browse') > 0) {
      if (sc.browse === null) return resp(500, '{"detail":"boom"}');
      return resp(200, JSON.stringify(sc.browse));
    }
    // The bearer the replay carries does not lift a per-device block,
    // so the second attempt is refused exactly like the first.
    return resp(sc.refusalStatus, JSON.stringify({ detail: sc.refusal }));
  };
  const sandbox = { window: {}, console, fetch, Auth: auth, setTimeout, clearTimeout,
                    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
                    navigator: { userAgent: 'harness' } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const w = sandbox.window;

  const opts = sc.method === 'GET' ? {} : { method: sc.method, body: '{"text":"x"}' };
  try {
    await w.apiFetch(sc.path, opts);
    return { threw: false, calls };
  } catch (e) {
    return {
      threw: true,
      status: e.status,
      deviceBlocked: !!e.deviceBlocked,
      blockedReason: e.blockedReason || null,
      reasonHelper: w.deviceBlockReason(e),
      loginPrompted: !!e.loginPrompted,
      authCancelled: !!e.authCancelled,
      isAuthFailure: w.isAuthFailure(e),
      said: w.mutationErrorText(e, 'Save'),
      saidNoBuffer: w.mutationErrorText(e, 'Upload', { kept: false }),
      calls, requestLoginCalls, requestPairingCalls, ensureCalls, pairCalls,
      writes: calls.filter((c) => c.method !== 'GET').length,
      probes: calls.filter((c) => c.url.indexOf('/api/files/browse') >= 0).length,
    };
  }
};

(async () => {
  const out = {};
  for (const [name, sc] of Object.entries(JSON.parse(process.argv[3]))) out[name] = await run(sc);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(2); });
"""

BLOCKED = "Chrome on Windows isn't allowed to change files"
REWORDED = "Files are read-only on Chrome on Windows right now (an admin turned that on)"
SAVE = "/api/documents/text/shopping.txt"

DATA_JS_SCENARIOS = {
    # The finding, exactly: a blocked tablet saves a document.
    "blocked_save": {
        "method": "PUT", "path": SAVE, "refusalStatus": 403, "refusal": BLOCKED,
        "browse": {"writable": False, "blocked_reason": BLOCKED}, "signIn": True,
    },
    # The same block with the sentence rewritten. Nothing in the client
    # knows these words, so it must behave identically.
    "blocked_save_reworded": {
        "method": "PUT", "path": SAVE, "refusalStatus": 403, "refusal": REWORDED,
        "browse": {"writable": False, "blocked_reason": REWORDED}, "signIn": True,
    },
    # NOT blocked: the same status and a body about something else. The
    # sign-in must still be offered, or this fix has eaten the feature.
    "really_signed_out": {
        "method": "PUT", "path": SAVE, "refusalStatus": 403,
        "refusal": ADMIN_COOKIE_ONLY,
        "browse": {"writable": True, "blocked_reason": None}, "signIn": True,
    },
    # A BLOCKED device pressing Delete: admin-gated, and delete does not
    # consult the block, so the refusal is a different sentence and the
    # password prompt is the right answer even though the device is
    # blocked. This is the narrowness test.
    "blocked_device_needs_admin_for_something_else": {
        "method": "POST", "path": "/api/files/delete", "refusalStatus": 403,
        "refusal": ADMIN_COOKIE_ONLY,
        "browse": {"writable": False, "blocked_reason": BLOCKED}, "signIn": True,
    },
    # A refused READ. Reads are never blocked, so no probe may be spent.
    "refused_read": {
        "method": "GET", "path": "/api/documents/list", "refusalStatus": 403,
        "refusal": ADMIN_COOKIE_ONLY,
        "browse": {"writable": False, "blocked_reason": BLOCKED}, "signIn": True,
    },
    # The probe itself fails (an old core, a dropped LAN). Falling back
    # to the old behaviour is the only safe answer: never a silent
    # "blocked" for a refusal that might really want a sign-in.
    "probe_unavailable": {
        "method": "PUT", "path": SAVE, "refusalStatus": 403, "refusal": BLOCKED,
        "browse": None, "signIn": True,
    },
}


@pytest.fixture(scope="module")
def data_js(node_bin, tmp_path_factory) -> dict:
    harness = tmp_path_factory.mktemp("f051") / "harness.js"
    harness.write_text(DATA_JS_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [node_bin, str(harness), str(DATA_JS), json.dumps(DATA_JS_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case", ["blocked_save", "blocked_save_reworded"])
def test_a_blocked_device_is_never_offered_the_admin_password(data_js, case: str):
    o = data_js[case]
    assert o["threw"] is True
    assert o["ensureCalls"] == 0, "the admin-password modal was opened"
    assert o["requestLoginCalls"] == 0, "the admin-password modal was opened"
    assert o["pairCalls"] == 0 and o["requestPairingCalls"] == 0
    assert o["writes"] == 1, "the save was replayed against a block no bearer lifts"
    assert o["isAuthFailure"] is False, (
        "a device block reported as an auth failure is a refusal the editor "
        "swallows, because it believes a modal is saying it"
    )
    assert o["authCancelled"] is False


@pytest.mark.parametrize("case", ["blocked_save", "blocked_save_reworded"])
def test_the_refusal_carries_the_reason_to_the_button(data_js, case: str):
    o = data_js[case]
    reason = DATA_JS_SCENARIOS[case]["refusal"]
    assert o["deviceBlocked"] is True
    assert o["blockedReason"] == reason
    assert o["reasonHelper"] == reason, "deviceBlockReason() is what the editors read"
    # The sentence a person gets: the server's own reason, and who can
    # lift it. Not a status line, not "sign in", not "cancelled".
    assert reason in o["said"], o["said"]
    assert "sign" not in o["said"].lower(), o["said"]
    assert "cancelled" not in o["said"].lower(), o["said"]
    assert "403" not in o["said"], o["said"]
    # An editor keeps the typing; a page action with no buffer must not
    # promise one.
    assert "still here" in o["said"]
    assert "still here" not in o["saidNoBuffer"]


def test_a_reworded_block_message_is_still_recognised(data_js):
    """The reason this is not a substring match.

    ``blocked_save_reworded`` shares not one distinctive word with the
    message the server ships today. If this passes, the next person to
    improve that sentence does not silently reopen the finding.
    """
    plain, reworded = data_js["blocked_save"], data_js["blocked_save_reworded"]
    assert reworded["deviceBlocked"] is plain["deviceBlocked"] is True
    assert reworded["ensureCalls"] == plain["ensureCalls"] == 0
    assert REWORDED != BLOCKED
    assert not set(REWORDED.lower().split()) >= {"isn't", "allowed"}


def test_a_genuine_credential_refusal_still_opens_the_sign_in(data_js):
    """The other half of the rule. Keeping the block out of the modal
    must not stop anything else from reaching it."""
    o = data_js["really_signed_out"]
    assert o["deviceBlocked"] is False
    assert o["ensureCalls"] == 1, "no sign-in was offered for a real refusal"
    assert o["writes"] == 2, "the request was not replayed after signing in"


def test_a_blocked_device_still_gets_the_sign_in_for_an_admin_action(data_js):
    """Delete is admin-gated and does not consult the block, so its
    refusal is about the credential even on a blocked device. Answering
    "you are blocked" there would be a lie that hides the real fix."""
    o = data_js["blocked_device_needs_admin_for_something_else"]
    assert o["probes"] == 1, "the server was not asked"
    assert o["deviceBlocked"] is False
    assert o["ensureCalls"] == 1
    assert o["writes"] == 2


def test_a_refused_read_costs_no_probe(data_js):
    """Reads are never blocked, so probing on one would be a request per
    empty panel on a dashboard that is merely signed out."""
    o = data_js["refused_read"]
    assert o["probes"] == 0
    assert o["deviceBlocked"] is False


def test_an_unavailable_probe_falls_back_to_the_old_behaviour(data_js):
    """Fail towards the prompt, never towards a silent wrong reason: an
    old core, or a LAN blip, must not turn a real sign-in into "your
    device is blocked"."""
    o = data_js["probe_unavailable"]
    assert o["deviceBlocked"] is False
    assert o["ensureCalls"] == 1


# ─── layer 3: the real editor, driven ────────────────────────────────

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
    this.token = 'fresh-bearer';
    return Promise.resolve(true);
  },
  ensurePaired() { return Promise.resolve(false); },
};
globalThis.fetch = async (url, opts) => {
  const o = opts || {};
  const method = (o.method || 'GET').toUpperCase();
  const u = String(url);
  globalThis.__net.push({ method, url: u,
                          auth: (o.headers || {}).Authorization || null,
                          body: typeof o.body === 'string' ? o.body : null });
  if (u.indexOf('/api/files/browse') >= 0) {
    return __resp(200, JSON.stringify({ writable: globalThis.__WRITABLE,
                                        blocked_reason: globalThis.__REASON }));
  }
  if (method === 'GET') {
    return __resp(200, JSON.stringify({ text: 'shopping list\n- milk\n- oats\n' }));
  }
  return __resp(403, JSON.stringify({ detail: globalThis.__REFUSAL }));
};
"""

EDITOR_SCRIPT = r"""
  h.render();
  await h.settle();
  h.rerender();
  await h.change({ type: 'textarea' }, 'shopping list\n- milk\n- oats\nTXT-EDIT');
  // A browser does not deliver a click to a disabled button; this
  // harness dispatches onClick regardless, so the check a real press
  // has to pass is made here instead.
  const before = h.find({ type: 'button', text: 'Save' });
  const pressable = !!before && before.props.disabled !== true;
  if (pressable) await h.click({ type: 'button', text: 'Save' });
  await h.settle();
  h.rerender();
  const ta = h.find({ type: 'textarea' });
  const save = h.find({ type: 'button', text: 'Save' });
  return {
    alerts: h.findAll((el) => el.props && el.props.role === 'alert').map((el) => el.text),
    texts: h.text().map(String),
    net: h.global('__net').map((c) => ({ method: c.method, url: c.url })),
    prompts: h.global('__prompts'),
    toasts: h.fnCalls.filter((c) => c.name === 'fire').map((c) => c.args[0]),
    pressable,
    saveDisabled: save ? save.props.disabled === true : null,
    saveTitle: save ? (save.props.title || null) : null,
    bufferAfter: ta ? ta.props.value : null,
    unsavedAfter: h.text().some((t) => String(t).includes('unsaved')),
  };
"""


def _editor(writable: bool, reason, refusal: str, props: dict | None = None) -> dict:
    setup = (
        f"globalThis.__WRITABLE = {json.dumps(writable)};\n"
        f"globalThis.__REASON = {json.dumps(reason)};\n"
        f"globalThis.__REFUSAL = {json.dumps(refusal)};\n" + EDITOR_SETUP
    )
    return {
        "files": ["web/static/data.js", "web/static/components.jsx",
                  "web/static/files.jsx"],
        "component": "TextEditorOverlay",
        "props": {"rel_path": "shopping.txt", **(props or {})},
        "fnProps": ["onClose", "fire"],
        "setup": setup,
        "script": EDITOR_SCRIPT,
    }


EDITOR_SCENARIOS = {
    # The finding: nobody told this editor anything, the save is refused.
    "refused_at_save": _editor(False, BLOCKED, BLOCKED),
    # The page DID know (browse told it) and passed it in, so Save is off
    # before the person types for ten minutes.
    "told_up_front": _editor(False, BLOCKED, BLOCKED, {"blockedReason": BLOCKED}),
    # Not blocked, refused for want of a bearer: the old story, intact.
    "really_signed_out": _editor(True, None, ADMIN_COOKIE_ONLY),
}


@pytest.fixture(scope="module")
def editor(node_bin) -> dict:
    proc = subprocess.run(
        [node_bin, str(INTERACT), str(REPO_ROOT), json.dumps(EDITOR_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert "__harness_error" not in res, f"{name}: {res.get('__harness_error')}"
    return out


def test_the_refusal_lands_at_the_button_not_in_a_password_prompt(editor):
    o = editor["refused_at_save"]
    assert o["pressable"] is True, "nothing told this editor anything — Save works"
    assert o["prompts"] == {"login": 0, "pair": 0, "ensure": 0}, (
        "the editor asked for the admin password over a block no password lifts")
    writes = [c for c in o["net"] if c["method"] == "PUT"]
    assert len(writes) == 1, "the save was replayed"
    # Said where the button is, in a live region, in the server's words.
    assert any(BLOCKED in a for a in o["alerts"]), o["alerts"]
    # ...and not as a toast that sits behind the overlay's own story.
    assert o["toasts"] == [], o["toasts"]


def test_the_refusal_says_who_can_lift_it(editor):
    """The person holding the tablet cannot act on "not allowed" alone —
    the next move is to find an admin, so the sentence says so."""
    alert = " ".join(editor["refused_at_save"]["alerts"])
    assert "admin" in alert.lower(), alert
    assert "Settings" in alert and "Devices" in alert, alert


def test_the_typing_survives_the_refusal(editor):
    o = editor["refused_at_save"]
    assert "TXT-EDIT" in (o["bufferAfter"] or ""), "the edit was thrown away"
    assert o["unsavedAfter"] is True, "the editor stopped saying the work is unsaved"


def test_save_goes_off_once_the_block_is_known(editor):
    """Pressing a button that cannot work is the second half of the
    complaint. After the refusal — and, when the page already knew,
    before it — Save is disabled and says why on hover."""
    for case in ("refused_at_save", "told_up_front"):
        o = editor[case]
        assert o["saveDisabled"] is True, case
        assert o["saveTitle"] and BLOCKED in o["saveTitle"], (case, o["saveTitle"])


def test_a_page_that_already_knew_never_sends_the_save(editor):
    """Save is not pressable at all, so nothing reaches the server and
    nothing has to be explained afterwards. (A browser does not deliver
    a click to a disabled button; the harness would, so the scenario
    checks `disabled` before pressing, exactly as a browser does.)"""
    o = editor["told_up_front"]
    assert o["pressable"] is False
    assert [c for c in o["net"] if c["method"] == "PUT"] == [], (
        "the editor let a save go that it already knew would be refused")
    assert any(BLOCKED in a for a in o["alerts"]), o["alerts"]


def test_an_ordinary_signed_out_save_still_prompts_and_replays(editor):
    """Nothing above may cost the editor its sign-in-and-replay."""
    o = editor["really_signed_out"]
    assert o["prompts"]["ensure"] == 1
    assert len([c for c in o["net"] if c["method"] == "PUT"]) == 2
    assert o["alerts"] == [], o["alerts"]
