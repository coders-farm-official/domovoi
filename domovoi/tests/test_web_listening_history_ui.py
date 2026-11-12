"""The dashboard side of "this is me" and clearing listening history
(Up next P1, 2026-11-12; the server half is test_music_listens.py).

* data.js ``DeviceIdentity`` learns whose this browser is from its own
  registration and remembers it for a reload; ``setPerson`` PUTs the claim;
* People → a person → "listening history · clear": a browser that is that
  person's, or a signed-in admin, goes straight to the confirm; anyone else
  is asked first — "this is me" (then the confirm) or "sign in as admin" —
  and a shared screen is never offered "this is me";
* the "this browser" line: "this is me" / "not me" send the right claim;
* player.jsx's "listening as" follows the device until something is
  picked, and the player logs library songs (ListenLog) in the code.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React) and a vm sandbox for data.js. No DB; needs
``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"
PLAYER = REPO_ROOT / "web" / "static" / "player.jsx"
FILES = ["web/static/components.jsx", "web/static/people.jsx"]

ALEX = {"id": 3, "name": "Alex", "device_count": 0, "created_at": "2026-11-01T00:00:00Z",
        "voice_profile_count": 1, "presence_tier": "high", "namesakes": 0}
SUMMARY = {"keep": "365", "keep_label": "1 year", "keep_days": 365, "choices": [],
           "total": 5, "oldest_at": "2026-11-01T00:00:00Z", "household": 1,
           "people": [{"person_id": 3, "name": "Alex", "listens": 4}]}


def _setup(owner: dict | None, *, admin: bool = False, shared: bool = False) -> str:
    return (
        f"DeviceIdentity.owner = () => ({json.dumps(owner)});"
        "DeviceIdentity.subscribeOwner = () => () => {};"
        f"DeviceIdentity.sharedScreen = () => {json.dumps(shared)};"
        "DeviceIdentity.canRegister = () => true;"
        "DeviceIdentity.setPerson = (pid) => { window.__claims = (window.__claims || []).concat([pid]);"
        " return Promise.resolve({}); };"
        f"Auth.isLoggedIn = () => {json.dumps(admin)};"
        "Auth.status = { setup_complete: true };"
        "Auth.ensureLoggedIn = () => { window.__signIn = true; return Promise.resolve(true); };"
        "useToast = () => [(m) => { window.__toasts = (window.__toasts || []).concat([m]); }, null];"
    )


_LISTEN_SCRIPT = r"""
  h.render();
  const out = { first: h.text().join(' | ') };
  await h.click({ type: 'button', text: 'clear' });
  await h.settle();
  out.asked = h.text().join(' | ');
  out.offersClaim = !!h.find({ type: 'button', text: 'this is me' });
  out.offersAdmin = !!h.find({ type: 'button', text: 'sign in as admin' });
  if (out.offersClaim) {
    await h.click({ type: 'button', text: 'this is me' });
    await h.settle();
  } else if (out.offersAdmin) {
    await h.click({ type: 'button', text: 'sign in as admin' });
    await h.settle();
  }
  out.confirm = h.text().join(' | ');
  const go = h.find({ type: 'button', text: 'Clear' });
  if (go) { await h.click({ type: 'button', text: 'Clear' }); await h.settle(); }
  out.calls = h.calls;
  out.claims = h.global('window').__claims || [];
  out.signIn = !!h.global('window').__signIn;
  return out;
"""


def _listen_scenario(owner, *, admin=False, shared=False, person=ALEX):
    return {
        "files": FILES,
        "component": "PersonListening",
        "props": {"person": person,
                  "listens": {"data": SUMMARY}},
        "fnProps": ["fire", "onChanged"],
        "setup": _setup(owner, admin=admin, shared=shared),
        "api": {"DELETE /api/music/listens/people/3": {"deleted": 4}},
        "script": _LISTEN_SCRIPT,
    }


_THIS_BROWSER_SCRIPT = r"""
  h.render();
  const out = { text: h.text().join(' | ') };
  const btn = h.find({ type: 'button', text: 'this is me' }) || h.find({ type: 'button', text: 'not me' })
           || h.find({ type: 'button', text: 'instead' });
  out.button = btn ? h.plain(btn).text : null;
  if (btn) { await h.click(btn); await h.settle(); }
  out.claims = h.global('window').__claims || [];
  return out;
"""


def _browser_scenario(owner, *, shared=False, person=ALEX):
    return {
        "files": FILES, "component": "PersonThisBrowser",
        "props": {"person": person}, "fnProps": ["fire", "onChanged"],
        "setup": _setup(owner, shared=shared), "api": {}, "script": _THIS_BROWSER_SCRIPT,
    }


SCENARIOS = {
    "nobodys_browser": _listen_scenario(None),
    "alexs_browser": _listen_scenario({"id": 3, "name": "Alex"}),
    "sams_browser": _listen_scenario({"id": 4, "name": "Sam"}),
    "admin": _listen_scenario(None, admin=True),
    "shared_screen": _listen_scenario(None, shared=True),
    "claim_unclaimed": _browser_scenario(None),
    "release_own": _browser_scenario({"id": 3, "name": "Alex"}),
    "switch": _browser_scenario({"id": 4, "name": "Sam"}),
    "shared_line": _browser_scenario(None, shared=True),
    "taken_elsewhere": _browser_scenario(None, person={**ALEX, "device_count": 1}),
}


@pytest.fixture(scope="module")
def ui(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the dashboard's JSX"
    spec = tmp_path_factory.mktemp("lh") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _deletes(sc) -> list:
    return [c["path"] for c in sc["calls"] if c["method"] == "DELETE"]


def test_a_browser_that_is_nobodys_is_asked_first(ui) -> None:
    sc = ui["nobodys_browser"]
    assert "4 songs played for Alex" in sc["first"]
    assert "Only Alex’s own phone or browser, or an admin, can clear it." in sc["asked"]
    assert sc["offersClaim"] and sc["offersAdmin"]
    # "this is me" claims, then confirms, then clears.
    assert sc["claims"] == [3]
    assert "This can’t be undone." in sc["confirm"]
    assert _deletes(sc) == ["/api/music/listens/people/3"]


def test_the_persons_own_browser_and_an_admin_go_straight_to_the_confirm(ui) -> None:
    for name in ("alexs_browser", "admin"):
        sc = ui[name]
        assert not sc["offersClaim"] and not sc["offersAdmin"], name
        assert "Clear Alex’s listening history?" in sc["asked"], name
        assert sc["claims"] == [] and _deletes(sc) == ["/api/music/listens/people/3"], name


def test_someone_elses_browser_and_a_shared_screen_only_get_the_admin(ui) -> None:
    for name, why in (("sams_browser", "This browser is Sam’s."),
                      ("shared_screen", "This is a shared screen, so it can’t be anyone’s.")):
        sc = ui[name]
        assert not sc["offersClaim"] and sc["offersAdmin"], name
        assert why in sc["asked"], name
        assert sc["signIn"] is True and sc["claims"] == [], name
        assert _deletes(sc) == ["/api/music/listens/people/3"], name


def test_the_this_browser_line(ui) -> None:
    assert ui["claim_unclaimed"]["button"] == "this is me" and ui["claim_unclaimed"]["claims"] == [3]
    assert ui["release_own"]["button"] == "not me" and ui["release_own"]["claims"] == [None]
    assert "This browser is Sam’s." in ui["switch"]["text"] and ui["switch"]["claims"] == [3]
    assert ui["shared_line"]["button"] is None and "shared screen" in ui["shared_line"]["text"]
    assert "an admin has to say yes" in ui["taken_elsewhere"]["text"]


# ─── data.js: whose this browser is ──────────────────────────────────────

OWNER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
(async () => {
  const store = { 'domovoi-device-owner': '{"id":9,"name":"Old"}', 'domovoi-client-id': 'browser-x' };
  const sent = [];
  const answers = [
    { device_id: 'browser-x', name: 'x', shared_screen: false, owner_person_id: 3, owner_person_name: 'Alex' },
    { device_id: 'browser-x', name: 'x', shared_screen: false, owner_person_id: null, owner_person_name: null },
  ];
  const fetch = async (url, opts) => {
    sent.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body });
    const body = JSON.stringify(answers.shift());
    return { ok: true, status: 200, statusText: 'OK', text: async () => body, json: async () => JSON.parse(body) };
  };
  const Auth = { headers: () => ({}), isPaired: () => true, isLoggedIn: () => false,
                 status: { setup_complete: true }, requestPairing() {}, deviceToken: () => 't' };
  const localStorage = { getItem: (k) => (k in store ? store[k] : null),
                         setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
  const sandbox = { window: {}, console: { warn() {}, log() {} }, fetch, Auth, localStorage,
                    navigator: { userAgent: 'harness' }, setTimeout, clearTimeout };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const D = sandbox.window.DeviceIdentity;
  const heard = [];
  D.subscribeOwner((o) => heard.push(o));
  const out = { remembered: D.owner() };
  await D.register();
  out.registered = D.owner();
  await D.setPerson(null);
  out.released = D.owner();
  out.put = sent[1];
  out.heard = heard;
  out.stored = store['domovoi-device-owner'];
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(2); });
"""


def test_device_identity_learns_and_remembers_whose_it_is(tmp_path) -> None:
    node = shutil.which("node")
    assert node
    harness = tmp_path / "owner.js"
    harness.write_text(OWNER_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), str(DATA_JS)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["remembered"] == {"id": 9, "name": "Old"}        # a reload paints the last answer
    assert out["registered"] == {"id": 3, "name": "Alex"}
    assert out["released"] is None and out["stored"] == "null"
    assert out["put"]["method"] == "PUT" and out["put"]["url"].endswith("/api/devices/browser-x/person")
    assert json.loads(out["put"]["body"]) == {"person_id": None}
    assert out["heard"] == [{"id": 3, "name": "Alex"}, None]


def test_the_player_follows_the_device_and_logs_library_songs() -> None:
    src = PLAYER.read_text(encoding="utf-8")
    # "listening as" starts from the device's person, never on a shared screen.
    assert "DeviceIdentity.owner ? DeviceIdentity.owner() : null" in src
    assert "DeviceIdentity.sharedScreen() === true) return null" in src
    # The log: library songs only, the server's endpoint, keepalive on pagehide
    # (sendBeacon can't carry the household token), and the outcome rule.
    assert "item.kind === 'library' && item.trackId != null" in src
    assert "apiFetch('/api/music/listens', { method: 'POST', body, noPrompt: true, keepalive })" in src
    assert "flush({ keepalive: true })" in src
    assert "sendBeacon(" not in src
    assert "played >= FINISHED_SHARE * duration" in src and "FINISHED_SHARE = 0.9" in src
    assert "no server-side play logging" not in src
