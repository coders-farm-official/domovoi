"""A mistyped approval code must not ask for the admin password (F-050).

Kamron stands at a new satellite, reads six digits off it, and types them
into the Satellites page. One wrong digit used to cost him a re-login:

* the core answered a mismatch with **403**;
* the approve route is admin-gated, so ``data.js``'s generic auth retry
  treats every 401/403 on a mutation as "sign in again" — it calls
  ``Auth.ensureLoggedIn`` and, failing that, opens the admin-password
  modal and replays the request once a bearer exists;
* so a wrong code opened the password prompt, and the refusal itself
  arrived as ``approve failed: 403 Forbidden: {"detail":"…"}`` — a status
  line and a JSON blob — in a toast.

He has five tries per room per five minutes. Spending them on re-logins
is how a hardware setup session is lost.

THE RULE THIS MODULE PINS: on that route, 401 and 403 are about WHO is
asking. Everything about WHAT was sent answers on some other status, and
the dashboard puts it beside the code box in the server's own words.

Three layers, none of them a source-string match:

1. the core route, driven through its real ASGI app — no refusal about
   the code is 401 or 403;
2. ``web/static/data.js`` itself, in a Node ``vm`` with a scripted
   ``fetch`` — a 422 opens no modal, replays nothing, and hands the
   caller the parsed detail (the sibling ``test_web_auth_failure_toast``
   pins the other half: a real 401 still does open it);
3. the real ``SatellitesPage`` from ``web/static/satellites.jsx``, driven
   through ``jsx_interact_harness.js`` — the refusal lands at the field,
   the toast stays empty, the digits survive, and a 429 reads as a wait
   rather than a failure.

Layer 3 asserts BEHAVIOUR, never wording: every sentence it checks for is
one the scenario itself handed the component, so rewording the copy
cannot break it — but rendering ``e.message`` (the status line and the
raw body, which is the second half of the finding) can, because the
scenario's ``message`` is exactly that string and the assertions refuse
it.

No DB, no ``requires_db`` — this must never skip. Layers 2 and 3 need
``node``, the runtime the JSX compile check already relies on, and fail
rather than skip without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import main as core_main
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import bearer, install_fake_db

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
DATA_JS = STATIC / "data.js"
INTERACT = Path(__file__).with_name("jsx_interact_harness.js")

ADMIN_TOKEN = "admin-token"
ROOM = "ft-attic"
APPROVE_URL = f"/v1/admin/satellites/approvals/{ROOM}/approve"

# The two statuses the dashboard reads as "your credential, not your
# input". Nothing about the code may land on either.
CREDENTIAL_STATUSES = (401, 403)


# ── layer 1: the core route ──────────────────────────────────────────


class _FakeApprovals:
    """Answers whatever the class attribute says, so every branch of the
    handler can be reached without Postgres."""

    answer = "mismatch"

    def __init__(self, session: Any) -> None:
        pass

    async def approve(self, room_id: str, code: str) -> str:
        return type(self).answer


@pytest.fixture
def faked(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(core_main, "session_scope", fake_scope)
    monkeypatch.setattr(core_main, "SatelliteApprovalRepository", _FakeApprovals)
    core_main.APPROVAL_CODE_LIMITER.reset()
    yield _FakeApprovals
    _FakeApprovals.answer = "mismatch"
    core_main.APPROVAL_CODE_LIMITER.reset()


async def _post(body: dict) -> Any:
    transport = ASGITransport(app=core_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(APPROVE_URL, json=body, headers=bearer(ADMIN_TOKEN))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer,body",
    [
        ("mismatch", {"code": "000001"}),
        ("not_pending", {"code": "000001"}),
        ("no_code", {"code": "000001"}),
        ("mismatch", {"code": ""}),
        ("mismatch", {"code": "not-digits"}),
        ("mismatch", {}),
    ],
)
async def test_no_refusal_about_the_code_is_a_credential_refusal(
    monkeypatch, faked, answer: str, body: dict
) -> None:
    """The whole finding in one assertion: an admin who IS signed in must
    never be told to sign in because of what they typed."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    faked.answer = answer
    r = await _post(body)
    assert r.status_code >= 400, "a bad code must still be refused"
    assert r.status_code not in CREDENTIAL_STATUSES, (
        f"{answer}/{body} answered {r.status_code}: the dashboard reads that as "
        "'sign in again' and opens the admin-password modal"
    )
    assert r.json()["detail"], "a refusal with no words is a blank toast"


@pytest.mark.asyncio
async def test_the_wrong_code_says_what_to_do_about_it(monkeypatch, faked) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    faked.answer = "mismatch"
    r = await _post({"code": "000001"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "does not match" in detail, detail
    # It sends the operator back to the device, which is the only place
    # the real digits are.
    assert "satellite" in detail and "try again" in detail, detail


@pytest.mark.asyncio
async def test_the_throttle_reads_as_a_wait_not_as_a_failure(
    monkeypatch, faked
) -> None:
    """A 429 here means "not just yet". Nothing was lost, nothing is
    banned, and the satellite is still asking — the sentence has to say
    so, because five wrong digits at a satellite is an ordinary evening,
    not an attack."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    for _ in range(core_main.APPROVAL_CODE_MAX_ATTEMPTS):
        await _post({"code": "000001"})
    r = await _post({"code": "000001"})
    assert r.status_code == 429
    detail = r.json()["detail"].lower()
    assert "wait" in detail, detail
    assert "nothing is lost" in detail and "nothing is banned" in detail, detail
    assert "clears itself" in detail, detail
    # …and it is still a 4xx that is not a credential refusal.
    assert r.status_code not in CREDENTIAL_STATUSES


# ── layer 2: data.js decides which refusals open the modal ───────────

DATA_JS_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

const run = async ({ status, detail }) => {
  let requestLoginCalls = 0, ensureCalls = 0, pairCalls = 0, fetchCalls = 0;
  const auth = {
    token: 'live-bearer',
    headers() { return { Authorization: 'Bearer ' + this.token }; },
    requestLogin() { requestLoginCalls += 1; },
    requestPairing() { pairCalls += 1; },
    ensureLoggedIn() { ensureCalls += 1; return Promise.resolve(true); },
    ensurePaired() { pairCalls += 1; return Promise.resolve(true); },
    deviceToken() { return 'device'; },
  };
  const fetch = async () => {
    fetchCalls += 1;
    const body = JSON.stringify({ detail });
    return { ok: false, status, statusText: 'Refused',
             text: async () => body, json: async () => JSON.parse(body) };
  };
  const sandbox = { window: {}, console, fetch, Auth: auth, setTimeout, clearTimeout };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const w = sandbox.window;
  try {
    await w.apiPost('/api/satellites/approvals/room/approve', { code: '000001' });
    return { threw: false };
  } catch (e) {
    return { threw: true, status: e.status, nested: e.detail && e.detail.detail,
             loginPrompted: !!e.loginPrompted, isAuthFailure: w.isAuthFailure(e),
             said: w.mutationErrorText(e, 'approve', { kept: false }),
             onlyDetail: w.apiErrorText(e), message: e.message,
             requestLoginCalls, ensureCalls, pairCalls, fetchCalls };
  }
};

(async () => {
  const out = {};
  for (const [name, sc] of Object.entries(JSON.parse(process.argv[3]))) out[name] = await run(sc);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(2); });
"""

WRONG_CODE_DETAIL = "that code does not match — check the six digits"

DATA_JS_SCENARIOS = {
    "wrong_code_422": {"status": 422, "detail": WRONG_CODE_DETAIL},
    "throttled_429": {"status": 429, "detail": "that room has had its 5 tries"},
    "really_signed_out_401": {"status": 401, "detail": "admin session required"},
}


@pytest.fixture(scope="module")
def data_js(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static/data.js (see jsxcheck)"
    harness = tmp_path_factory.mktemp("f050") / "harness.js"
    harness.write_text(DATA_JS_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(DATA_JS), json.dumps(DATA_JS_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case", ["wrong_code_422", "throttled_429"])
def test_a_refusal_about_the_code_never_opens_the_sign_in(data_js, case: str):
    o = data_js[case]
    assert o["threw"] is True
    assert o["requestLoginCalls"] == 0, "the admin-password modal was opened"
    assert o["ensureCalls"] == 0, "data.js tried to sign the operator in again"
    assert o["pairCalls"] == 0
    assert o["fetchCalls"] == 1, "the code was sent twice, spending two of five tries"
    assert o["isAuthFailure"] is False
    assert o["nested"], "the server's own sentence never reached the caller"


def test_a_real_sign_in_problem_still_opens_the_sign_in(data_js):
    """The other half of the rule: keeping a business refusal out of the
    modal must not stop a genuine one from reaching it. This scenario's
    Auth signs in, so the sign-in ran and the request was replayed."""
    o = data_js["really_signed_out_401"]
    assert o["ensureCalls"] == 1, "no sign-in was offered for a real 401"
    assert o["fetchCalls"] == 2, "the request was not replayed after signing in"


def test_the_detail_is_reachable_without_the_status_line(data_js):
    """``e.message`` is ``"<status> <statusText>: <raw body>"`` by
    construction — that string is the toast the finding complained
    about. The helpers hand back the sentence alone."""
    o = data_js["wrong_code_422"]
    assert o["onlyDetail"] == WRONG_CODE_DETAIL
    assert "422" in o["message"] and "{" in o["message"]
    assert "422" not in o["onlyDetail"] and "{" not in o["onlyDetail"]


# ── layer 3: the real Satellites page, driven ────────────────────────

# `mutationErrorText` and `apiErrorText` live in data.js, which cannot be
# loaded beside the JSX here: its top-level `const apiPost` would shadow
# the harness's scripted data layer and every call would try to reach the
# network. They are restated with their real precedence — nested detail
# first, message second — and their real contract is pinned for real in
# layer 2 and in test_web_auth_failure_toast.py.
SETUP = r"""
globalThis.apiErrorText = (e, max) => {
  const nested = e && e.detail && e.detail.detail;
  const text = (typeof nested === 'string' && nested) || (e && e.message) || String(e);
  return String(text).slice(0, max || 160);
};
globalThis.mutationErrorText = (e, verb, o) => {
  if (e && e.authCancelled) return (verb || 'Save') + ' cancelled — not signed in.';
  if (e && e.loginPrompted) return null;
  return (verb || 'Save') + ' failed: ' + apiErrorText(e, 120);
};
"""

# One satellite in the room, asking. `attempts` is the device's reconnect
# count, not a budget (F-049).
PENDING = [{"room_id": ROOM, "board": "pi5", "sat_type": "voice",
            "attempts": 3, "has_code": True}]

# What the page loads besides satellites.jsx: components.jsx for Card /
# Button / useToast, satellite_media.jsx for the PrepareMediaCard the
# page renders at the bottom.
FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx",
         "web/static/satellites.jsx"]

TYPED = "000001"
SAID = {
    422: "that code does not match — check the six digits",
    429: "that room has had its 5 tries for the moment — wait up to 300 seconds",
    409: "nothing pending for that room",
    502: "domovoi unreachable",
    401: "admin session required",
}


def _refusal(status: int, **extra) -> dict:
    """What data.js leaves in a rejected apiPost: the parsed body on
    `.detail`, and a `.message` that is the status line plus the raw
    body — the very string the finding saw in the toast."""
    detail = SAID[status]
    return {"__error": {
        "status": status,
        "message": f"{status} Refused: " + json.dumps({"detail": detail}),
        "detail": {"detail": detail},
        **extra,
    }}


SNAP = r"""
  const box = () => h.find({ type: 'input', placeholder: '000000' }) || { props: { style: {} } };
  const snap = () => ({
    alerts: h.findAll((el) => el.props && el.props.role === 'alert')
             .map((el) => ({ text: el.text, color: (el.props.style || {}).color })),
    texts: h.text().map(String),
    calls: h.calls.map((c) => c.method + ' ' + c.path),
    code: box().props.value,
    marked: box().props['aria-invalid'] === true,
  });
"""

TRY = r"""
  h.render(); await h.settle(); h.rerender();
  await h.type({ type: 'input', placeholder: '000000' }, '%s');
  await h.click({ type: 'button', text: 'approve' });
  const after = snap();
""" % TYPED


def _scenario(approve_answer: object, script_tail: str = "return { after };") -> dict:
    return {
        "files": FILES,
        "component": "SatellitesPage",
        "api": {
            "GET /api/satellites": [],
            "GET /api/satellites/approvals": PENDING,
            "GET /api/satellites/pending": [],
            f"POST /api/satellites/approvals/{ROOM}/approve": approve_answer,
        },
        "setup": SETUP,
        "script": SNAP + TRY + script_tail,
    }


JSX_SCENARIOS = {
    "wrong_code": _scenario(_refusal(422), """
      await h.type({ type: 'input', placeholder: '000000' }, '000002');
      const afterRetype = snap();
      return { after, afterRetype };"""),
    "throttled": _scenario(_refusal(429)),
    "nothing_pending": _scenario(_refusal(409)),
    "core_unreachable": _scenario(_refusal(502)),
    "sign_in_dismissed": _scenario(_refusal(401, authCancelled=True, loginPrompted=True)),
    "modal_on_screen": _scenario(_refusal(401, loginPrompted=True)),
    "right_code": _scenario({"approved": True, "room_id": ROOM}),
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(INTERACT), str(REPO_ROOT), json.dumps(JSX_SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _toasts(snapshot: dict) -> list[str]:
    """Whatever the page said outside the approval card. The card's own
    two sentences (the alert, and the standing "type the digits" hint)
    are excluded by their text so what is left is the toast."""
    alerts = {a["text"] for a in snapshot["alerts"]}
    return [t for t in snapshot["texts"]
            if t not in alerts and ("failed" in t or "cancelled" in t
                                    or t.endswith("approved") or t.endswith("rejected"))]


CASE_STATUS = {"wrong_code": 422, "throttled": 429, "nothing_pending": 409}


@pytest.mark.parametrize("case", ["wrong_code", "throttled", "nothing_pending"])
def test_a_refusal_about_the_code_lands_at_the_field(driven, case: str):
    after = driven[case]["after"]
    assert len(after["alerts"]) == 1, f"{case}: {after['alerts']}"
    said = after["alerts"][0]["text"]
    # The server's own words, whatever they are — this test does not own
    # the copy, it owns the plumbing.
    assert said == SAID[CASE_STATUS[case]], said
    assert _toasts(after) == [], f"{case}: also shouted in a toast: {_toasts(after)}"


@pytest.mark.parametrize("case", ["wrong_code", "throttled", "nothing_pending"])
def test_the_refusal_is_not_a_status_line_and_a_json_blob(driven, case: str):
    """``approve failed: 403 Forbidden: {"detail":"…"}`` is the toast the
    finding photographed. The scenario's `message` IS that string, so a
    component that renders it fails here."""
    said = driven[case]["after"]["alerts"][0]["text"]
    for junk in ("Refused", "{", "}", '"detail"', "failed:"):
        assert junk not in said, f"{case}: raw {junk!r} shown to the operator: {said}"
    assert str(CASE_STATUS[case]) not in said, f"{case}: bare HTTP status: {said}"


def test_the_digits_survive_a_wrong_code(driven):
    """Five tries per room. Clearing the box would make fixing one digit
    a full retype, and the operator is holding a Pi."""
    after = driven["wrong_code"]["after"]
    assert after["code"] == TYPED
    assert after["calls"] == [f"POST /api/satellites/approvals/{ROOM}/approve"], (
        "the page sent the code more than once for one press")


def test_a_wrong_code_marks_the_code_box(driven):
    assert driven["wrong_code"]["after"]["marked"] is True


@pytest.mark.parametrize("case", ["throttled", "nothing_pending"])
def test_a_wait_or_a_vanished_request_does_not_accuse_the_digits(driven, case: str):
    """A 429 is "not yet" and a 409 is "that request is gone" — neither
    is a typo, so neither reddens the box."""
    assert driven[case]["after"]["marked"] is False


def test_the_throttle_is_toned_as_a_wait_not_as_an_error(driven):
    tones = {c: driven[c]["after"]["alerts"][0]["color"]
             for c in ("wrong_code", "throttled")}
    assert tones["throttled"] != tones["wrong_code"], tones
    assert tones["wrong_code"] == "var(--err)", tones
    assert tones["throttled"] == "var(--warn)", tones


def test_typing_again_clears_the_refusal(driven):
    after = driven["wrong_code"]["afterRetype"]
    assert after["alerts"] == []
    assert after["marked"] is False
    assert after["code"] == "000002"


def test_a_page_level_failure_still_uses_the_toast(driven):
    """An unreachable core is not this field's business."""
    after = driven["core_unreachable"]["after"]
    assert after["alerts"] == []
    assert _toasts(after) == ["approve failed: " + SAID[502]], after["texts"]


def test_a_dismissed_sign_in_says_cancelled_and_not_at_the_field(driven):
    after = driven["sign_in_dismissed"]["after"]
    assert after["alerts"] == []
    assert _toasts(after) == ["approve cancelled — not signed in."], after["texts"]


def test_the_page_stays_quiet_while_the_password_modal_owns_the_story(driven):
    """F-006: a toast behind the password prompt reads as a crash."""
    after = driven["modal_on_screen"]["after"]
    assert after["alerts"] == []
    assert _toasts(after) == [], after["texts"]


def test_the_right_code_still_approves(driven):
    after = driven["right_code"]["after"]
    assert after["alerts"] == []
    assert _toasts(after) == [f"{ROOM} approved"], after["texts"]
