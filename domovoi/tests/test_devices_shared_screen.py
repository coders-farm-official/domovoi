"""A device an admin marks as a shared screen (V014, HOME-PLAN "way 2").

Owner decision 2026-09-26: the kitchen tablet's Home leaves personal
content off, and the flag that says so lives on the device's own
``devices`` row, set by an ADMIN:

* ``PATCH /api/devices/{device_id}/shared-screen`` takes an admin Bearer
  (``require_admin_mutation``), never the household token the tablet
  holds — a device must not be able to un-share itself. The rename beside
  it stays device tier.
* every device answer carries ``shared_screen`` — the admin roster, the
  rename, and the device's OWN registration, which is how the dashboard
  learns its mode (``DeviceIdentity.sharedScreen()`` in data.js).
* a web process started against a database without V014 keeps
  registering devices (every dashboard load does), reads the flag as
  false, and answers the admin toggle with 503 naming the migration.

DB-backed tests carry ``requires_db`` (lane DB with V014 applied); the
tiers, the migration file, data.js's DeviceIdentity and the Settings
toggle are checked without one.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.api import devices as devices_api
from web.backend.main import app as web_app

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "domovoi" / "db" / "migrations"
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")


# ─── the migration and the tiers, no database ────────────────────────────


def test_v014_adds_the_column_off_by_default() -> None:
    versions = sorted(int(p.name[1:4]) for p in MIGRATIONS.glob("V*__*.sql"))
    assert versions == list(range(1, versions[-1] + 1)), "a gap in the Flyway versions"
    (v14,) = MIGRATIONS.glob("V014__*.sql")
    sql = " ".join(
        line for line in v14.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    assert re.search(
        r"ALTER TABLE devices\s+ADD COLUMN IF NOT EXISTS shared_screen BOOLEAN NOT NULL DEFAULT FALSE",
        sql,
    ), sql


def test_the_docs_say_how_thin_the_mask_is() -> None:
    """The flag is presentational, and the docs must not oversell it: what
    Home hides is an open read, an unpaired browser (a private window on the
    tablet) is never masked, and the flag belongs to a self-asserted id the
    browser keeps in its own storage — clearing that un-marks the tablet."""
    (v14,) = MIGRATIONS.glob("V014__*.sql")
    v14_text = " ".join(v14.read_text(encoding="utf-8").split())
    assert "survives the browser's storage being cleared" not in v14_text
    assert "SELF-ASSERTED" in v14_text and "private window" in v14_text
    security = (REPO_ROOT / "docs" / "SECURITY_PRIVACY.md").read_text(encoding="utf-8")
    row = next(line for line in security.splitlines() if line.startswith("| **Shared screens**"))
    for phrase in ("open read", "private window on the tablet itself", "self-asserted device id",
                   "Clearing the tablet's site data"):
        assert phrase in row, phrase
    assert "renders several of them to an unpaired browser" in security
    api = (REPO_ROOT / "docs" / "API_REFERENCE.md").read_text(encoding="utf-8")
    patch = next(line for line in api.splitlines() if line.startswith("| `PATCH /api/devices/{device_id}/shared-screen`"))
    assert "readable with no credential at all" in patch and "self-asserted device id" in patch
    module = " ".join((devices_api.__doc__ or "").split())
    assert "NO credential" in module and "SELF-ASSERTED" in module


def _gates(method: str, path: str) -> set:
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
            out, stack = set(), [rc.dependant]
            while stack:
                for dep in stack.pop().dependencies:
                    if dep.call is not None:
                        out.add(dep.call)
                    stack.append(dep)
            return out
    raise AssertionError(f"{method} {path} is not a web route")


def test_only_an_admin_sets_it_and_the_device_tier_does_not() -> None:
    toggle = _gates("PATCH", "/api/devices/{device_id}/shared-screen")
    assert admin_auth.require_admin_mutation in toggle
    assert admin_auth.require_device not in toggle
    # The rename beside it is still the device's own business.
    assert admin_auth.require_device in _gates("PATCH", "/api/devices/{device_id}")
    assert admin_auth.require_admin_read in _gates("GET", "/api/devices")


# ─── against a real database ─────────────────────────────────────────────


@pytest.fixture
async def clean_devices():
    from domovoi.db.session import engine

    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE queue_device_blocks, room_queue_items, devices RESTART IDENTITY CASCADE"))

    await _truncate()
    yield
    await _truncate()


def _client(headers: dict[str, str] | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests", **(headers or {})},
    )


async def _register(c: AsyncClient, device_id: str = "browser-kitchen1") -> dict:
    r = await c.post("/api/devices/register",
                     json={"device_id": device_id, "name": "Kitchen tablet", "platform": "browser"})
    assert r.status_code == 200, r.text
    return r.json()


@requires_db
@pytest.mark.asyncio
async def test_an_admin_marks_the_tablet_and_the_tablet_learns_it(_db, clean_devices) -> None:
    async with _client() as setup:
        admin = await claim_admin(setup)
    token = await db_device_token()
    tablet = _client({HEADER: token})
    admin_c = _client(bearer(admin))
    async with tablet, admin_c:
        assert (await _register(tablet))["shared_screen"] is False

        # The tablet holds the household token: that is not enough.
        refused = await tablet.patch("/api/devices/browser-kitchen1/shared-screen",
                                     json={"shared_screen": False})
        assert refused.status_code in (401, 403), refused.text
        async with _client() as anon:
            assert (await anon.patch("/api/devices/browser-kitchen1/shared-screen",
                                     json={"shared_screen": True})).status_code == 401

        on = await admin_c.patch("/api/devices/browser-kitchen1/shared-screen",
                                 json={"shared_screen": True})
        assert on.status_code == 200, on.text
        assert on.json()["shared_screen"] is True and on.json()["name"] == "Kitchen tablet"

        # The roster shows it; the device's OWN record says so — its next
        # registration (every load) and its rename both carry it, and
        # neither resets it.
        roster = (await admin_c.get("/api/devices")).json()
        assert [d["shared_screen"] for d in roster] == [True]
        assert (await _register(tablet))["shared_screen"] is True
        renamed = await tablet.patch("/api/devices/browser-kitchen1", json={"name": "Hall tablet"})
        assert renamed.status_code == 200 and renamed.json()["shared_screen"] is True

        # ...and the tablet still cannot switch itself back.
        again = await tablet.patch("/api/devices/browser-kitchen1/shared-screen",
                                   json={"shared_screen": False})
        assert again.status_code in (401, 403)
        off = await admin_c.patch("/api/devices/browser-kitchen1/shared-screen",
                                  json={"shared_screen": False})
        assert off.json()["shared_screen"] is False
        assert (await _register(tablet))["shared_screen"] is False


@requires_db
@pytest.mark.asyncio
async def test_the_toggle_names_a_missing_or_malformed_device(_db, clean_devices) -> None:
    async with _client() as c:   # pre-setup grace: no credential needed yet
        missing = await c.patch("/api/devices/browser-nobody/shared-screen",
                                json={"shared_screen": True})
        assert missing.status_code == 404 and "browser-nobody" in missing.json()["detail"]
        bad = await c.patch("/api/devices/a%20b/shared-screen", json={"shared_screen": True})
        assert bad.status_code == 400
        await _register(c)
        # The admin's toggle is not the device being seen.
        before = (await c.get("/api/devices")).json()[0]["last_seen_at"]
        after = (await c.patch("/api/devices/browser-kitchen1/shared-screen",
                               json={"shared_screen": True})).json()
        assert after["last_seen_at"] == before


@requires_db
@pytest.mark.asyncio
async def test_a_database_without_v014_still_registers(_db, clean_devices, monkeypatch) -> None:
    """The probe says "no column": registration, rename and the roster run
    their fallback SQL for real and answer shared_screen false; only the
    admin toggle refuses, naming the migration."""
    async def _no_column(_s) -> bool:
        return False

    monkeypatch.setattr(devices_api, "_has_shared_screen", _no_column)
    async with _client() as c:
        assert (await _register(c))["shared_screen"] is False
        r = await c.patch("/api/devices/browser-kitchen1", json={"name": "x"})
        assert r.status_code == 200 and r.json()["shared_screen"] is False
        assert (await c.get("/api/devices")).json()[0]["shared_screen"] is False
        refused = await c.patch("/api/devices/browser-kitchen1/shared-screen",
                                json={"shared_screen": True})
        assert refused.status_code == 503 and "V014" in refused.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_the_column_probe_caches_only_a_yes(monkeypatch) -> None:
    from domovoi.db.session import session_scope

    monkeypatch.setattr(devices_api, "_HAS_SHARED_SCREEN", False)
    async with session_scope() as s:
        assert await devices_api._has_shared_screen(s) is True
    assert devices_api._HAS_SHARED_SCREEN is True


# ─── data.js: how the page learns its mode ───────────────────────────────

DEVICE_IDENTITY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const run = async (sc) => {
  const store = Object.assign({}, sc.stored || {});
  const posts = [];
  const answers = sc.answers.slice();
  const fetch = async (url, opts) => {
    posts.push(String(url));
    const status = sc.refuse ? 401 : 200;
    const row = answers.length > 1 ? answers.shift() : answers[0];
    const body = sc.refuse ? '{"detail":"X-Device-Token required"}' : JSON.stringify(row);
    return { ok: status === 200, status, statusText: status === 200 ? 'OK' : 'Unauthorized',
             text: async () => body, json: async () => JSON.parse(body) };
  };
  const Auth = { headers: () => ({}), isPaired: () => !!sc.paired, isLoggedIn: () => false,
                 status: { setup_complete: sc.setupComplete !== false },
                 requestPairing() {}, ensurePaired: () => Promise.resolve(false),
                 deviceToken: () => (sc.paired ? 't' : null) };
  const localStorage = { getItem: (k) => (k in store ? store[k] : null),
                         setItem: (k, v) => { store[k] = String(v); },
                         removeItem: (k) => { delete store[k]; } };
  const sandbox = { window: {}, console: { warn() {}, log() {} }, fetch, Auth, localStorage,
                    navigator: { userAgent: 'harness' }, setTimeout, clearTimeout };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'data.js' });
  const D = sandbox.window.DeviceIdentity;
  const heard = [];
  D.subscribeShared((v) => heard.push(v));
  const out = { before: D.sharedScreen() };
  await D.register();
  out.afterRegister = D.sharedScreen();
  out.refreshed = await D.refresh();
  out.afterRefresh = D.sharedScreen();
  out.heard = heard;
  out.posts = posts.length;
  out.stored = store['domovoi-shared-screen'] || null;
  return out;
};
(async () => {
  const scenarios = JSON.parse(process.argv[3]);
  const out = {};
  for (const [k, sc] of Object.entries(scenarios)) out[k] = await run(sc);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(2); });
"""

ROW = {"device_id": "browser-x", "name": "Kitchen tablet"}
DI_SCENARIOS = {
    # A paired tablet an admin marked shared, then un-marked between loads.
    "paired_marked_then_cleared": {
        "paired": True, "answers": [{**ROW, "shared_screen": True}, {**ROW, "shared_screen": False}]},
    # A reload: the last answer paints first, before register() returns.
    "reload_remembers": {
        "paired": True, "stored": {"domovoi-shared-screen": "1"},
        "answers": [{**ROW, "shared_screen": True}]},
    # An unpaired browser on a claimed box: refresh() must not POST (a
    # refused write opens the pair prompt) and nothing is known.
    "unpaired": {"paired": False, "refuse": True, "answers": [{}]},
    # An older server that answers without the field: nothing is guessed.
    "older_server": {"paired": True, "answers": [ROW]},
}


@pytest.fixture(scope="module")
def identity(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to exercise web/static/data.js"
    harness = tmp_path_factory.mktemp("di") / "harness.js"
    harness.write_text(DEVICE_IDENTITY_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), str(DATA_JS), json.dumps(DI_SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_page_learns_the_mode_from_its_own_registration(identity) -> None:
    sc = identity["paired_marked_then_cleared"]
    assert sc["before"] is None          # nothing known, nothing remembered
    assert sc["afterRegister"] is True
    assert sc["afterRefresh"] is False and sc["refreshed"]["shared_screen"] is False
    assert sc["heard"] == [True, False]  # subscribers hear each change once
    assert sc["posts"] == 2 and sc["stored"] == "0"


def test_a_reload_paints_the_remembered_mode_first(identity) -> None:
    sc = identity["reload_remembers"]
    assert sc["before"] is True and sc["afterRegister"] is True
    assert sc["heard"] == []             # no change, no event


def test_an_unpaired_browser_is_never_prompted_by_a_refresh(identity) -> None:
    sc = identity["unpaired"]
    assert sc["posts"] == 1              # the boot register only; refresh() sent nothing
    assert sc["refreshed"] is None
    assert sc["before"] is None and sc["afterRefresh"] is None


def test_an_answer_without_the_field_guesses_nothing(identity) -> None:
    sc = identity["older_server"]
    assert sc["afterRegister"] is None and sc["stored"] is None


# ─── Settings → Devices: the toggle ──────────────────────────────────────

_ADMIN = r"""
Auth = { status: { setup_complete: true, authenticated: true }, subscribe: () => () => {},
         isLoggedIn: () => true, isPaired: () => true, headers: () => ({}), modalOpen: false,
         refreshStatus: () => Promise.resolve(Auth.status), deviceToken: () => null };
window.__refreshed = 0;
DeviceIdentity = { id: () => 'browser-kitchen1', name: () => 'Kitchen tablet',
                   suggestedName: () => 'Chrome on Android',
                   register: () => Promise.resolve({ device_id: 'browser-kitchen1', name: 'Kitchen tablet' }),
                   refresh: () => { window.__refreshed += 1; return Promise.resolve(null); },
                   rename: (name) => Promise.resolve({ device_id: 'browser-kitchen1', name }) };
reportMutationFailure = (fire, verb, e) => { fire(`${verb} failed: ${e.message}`); };
"""
_DEVICES = [
    {"device_id": "browser-kitchen1", "name": "Kitchen tablet", "platform": "browser",
     "last_seen_at": None, "shared_screen": False},
    {"device_id": "android-pixel", "name": "Pixel", "platform": "android",
     "last_seen_at": None, "shared_screen": True},
]
_API = {"GET /api/devices": _DEVICES, "GET /api/music/queue-blocks": [],
        "GET /api/music/now-playing": [], "GET /api/files/device-blocks": [],
        "GET /api/auth/device-token": {"token": "t"},
        "PATCH /api/devices/browser-kitchen1/shared-screen": {**_DEVICES[0], "shared_screen": True},
        "PATCH /api/devices/android-pixel/shared-screen": {"__error": {"status": 401, "message": "401 Unauthorized"}}}
_TOGGLES = "h.findAll((e) => e.type === 'input' && e.props.name === 'shared-screen')"
SETTINGS_SCENARIOS = {
    "toggle": {
        "files": ["web/static/components.jsx", "web/static/settings.jsx"], "component": "DevicesPanel",
        "setup": _ADMIN, "api": _API,
        "script": f"""
          h.render();
          const boxes = {_TOGGLES}.map((e) => ({{ checked: e.props.checked, label: e.props['aria-label'] }}));
          await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'Kitchen tablet: shared screen', true);
          const on = {{ calls: h.calls.map((c) => [c.method, c.path, c.body]),
                       texts: h.text(), refreshed: h.global('window').__refreshed }};
          await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'Pixel: shared screen', false);
          return {{ boxes, on, off: {{ calls: h.calls.map((c) => [c.method, c.path, c.body]),
                                      texts: h.text(), refreshed: h.global('window').__refreshed }} }};
        """,
    },
}


@pytest.fixture(scope="module")
def settings_driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(SETTINGS_SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "__harness_error" not in out["toggle"], out["toggle"]
    return out["toggle"]


def test_the_known_devices_table_shows_each_devices_flag(settings_driven) -> None:
    assert settings_driven["boxes"] == [
        {"checked": False, "label": "Kitchen tablet: shared screen"},
        {"checked": True, "label": "Pixel: shared screen"},
    ]


def test_ticking_the_box_patches_the_admin_route_and_says_so(settings_driven) -> None:
    on = settings_driven["on"]
    assert ["PATCH", "/api/devices/browser-kitchen1/shared-screen", {"shared_screen": True}] in on["calls"]
    assert any("Kitchen tablet is now a shared screen" in t for t in on["texts"])
    # This browser's own row: it re-reads its record so its Home follows.
    assert on["refreshed"] == 1


def test_a_refused_toggle_is_reported_and_changes_nothing_here(settings_driven) -> None:
    off = settings_driven["off"]
    assert ["PATCH", "/api/devices/android-pixel/shared-screen", {"shared_screen": False}] in off["calls"]
    assert any(t.startswith("shared screen failed") for t in off["texts"])
    assert off["refreshed"] == 1          # another device's row: no self re-read
