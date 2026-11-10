"""The household's web menu (Settings → System → App layout → Menu).

* The API (``web/backend/api/nav_layout.py``): ``GET /api/nav-layout`` is
  open, like the plugin manifest the shell reads beside it; ``PUT`` and
  ``DELETE`` are admin (Bearer; the cookie alone never writes). The
  validator refuses HTML in a name, long names, duplicate group ids, bad
  routes, Home, a page placed twice, oversized documents, and an order
  naming what isn't there. Save → read back → reset against the test DB
  (V045 ``household_settings``), and the realtime snapshot follows.
* The resolver's ``order`` (``resolveNavTree`` in components.jsx): the top
  level follows the household's order; raw mode keeps empty and one-page
  groups for the editor; ``navLayoutEdit`` moves, renames, adds, deletes.
* The editor (settings.jsx ``NavLayoutCard``), driven through the vendored
  Babel in node (``jsx_interact_harness.js``): it renders folded with the
  household wording, the moves produce the expected PUT body, a saved
  layout is what the sidebar draws (and the shared-screen hiding still
  applies on top), and reset sends the DELETE.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_client
from domovoi.tests.conftest import requires_db
from web.backend import realtime
from web.backend.api import nav_layout as nl

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
HOME = "web/static/home.jsx"
SETTINGS = "web/static/settings.jsx"
ADMIN = "admin-session-token"
DEVICE = "lantern-river-kettle-harbor"

GOOD = {
    "version": 1,
    "groups": [{"id": "media", "label": "Watch & listen", "items": ["music", "videos"]},
               {"id": "kids", "label": "Kids", "items": ["podcasts"]}],
    "top": ["chat", "plugins", "files"],
    "order": ["group:kids", "chat", "group:media", "files", "plugins"],
}


# ─── the validator (pure) ─────────────────────────────────────────────────


def test_a_good_layout_is_kept_as_sent():
    assert nl.validate_layout(GOOD) == GOOD


def test_names_are_trimmed_and_spaces_folded():
    out = nl.validate_layout({"groups": [{"id": "a", "label": "  Media   room ", "items": []}]})
    assert out["groups"][0]["label"] == "Media room"
    assert out["top"] == [] and out["order"] == [] and out["version"] == 1


def _bad(**patch):
    body = json.loads(json.dumps(GOOD))
    body.update(patch)
    return body


@pytest.mark.parametrize("body,why", [
    ([], "must be an object"),
    (_bad(extra=1), "unknown field"),
    (_bad(version=2), "version"),
    (_bad(groups=[{"id": "a", "label": "<b>x</b>", "items": []}]), "< or >"),
    (_bad(groups=[{"id": "a", "label": "x" * (nl.LABEL_MAX + 1), "items": []}]), "longer"),
    (_bad(groups=[{"id": "a", "label": "   ", "items": []}]), "empty"),
    (_bad(groups=[{"id": "a", "label": "tab\u0007bell", "items": []}]), "can't be shown"),
    (_bad(groups=[{"id": "a", "label": 5, "items": []}]), "text"),
    (_bad(groups=[{"id": "Media", "label": "M", "items": []}]), "lowercase"),
    (_bad(groups=[{"id": "a", "label": "A", "items": []}, {"id": "a", "label": "B", "items": []}]),
     "share the id"),
    (_bad(groups=[{"id": "a", "label": "A", "items": [], "icon": "x"}]), "unknown field"),
    (_bad(top=["../etc"]), "not a page address"),
    (_bad(top=["Music"]), "not a page address"),
    (_bad(top=["home"]), "Home stays"),
    (_bad(top=["chat", "chat"], order=[]), "twice"),
    (_bad(top=["music"], order=[]), "twice"),           # also in media
    (_bad(groups=[{"id": f"g{i}", "label": "G", "items": []} for i in range(nl.MAX_GROUPS + 1)],
          order=[]), "at most"),
    (_bad(order=["group:nope"]), "no group"),
    (_bad(order=["news"]), "not a top-level page"),
    (_bad(order=["chat", "chat"]), "listed twice"),
    (_bad(order=[3]), "text"),
    (_bad(top="chat"), "must be a list"),
])
def test_what_the_validator_refuses(body, why):
    with pytest.raises(nl.LayoutError) as e:
        nl.validate_layout(body)
    assert why in str(e.value)


def test_an_oversized_layout_is_refused():
    big = {"groups": [{"id": f"g{i}", "label": "G" * nl.LABEL_MAX,
                       "items": [f"p{i}-{j}-{'x' * 50}" for j in range(nl.MAX_GROUP_ITEMS)]}
                      for i in range(nl.MAX_GROUPS)]}
    with pytest.raises(nl.LayoutError, match="too large"):
        nl.validate_layout(big)


def test_the_route_rule_is_the_manifests():
    from domovoi.plugins_runtime.manifest import WEB_ROUTE_RE
    assert nl.WEB_ROUTE_RE is WEB_ROUTE_RE


# ─── tiers (DB-free) ──────────────────────────────────────────────────────


@pytest.fixture
def auth(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=DEVICE)


@pytest.fixture
def no_db(monkeypatch):
    """The handlers' storage, stood in for: a refused request must never
    reach it."""
    reached = []

    async def read():
        reached.append("read")
        return None, None

    monkeypatch.setattr(nl, "read_layout", read)
    return reached


async def test_the_read_is_open(auth, no_db):
    async with web_client() as c:
        r = await c.get("/api/nav-layout")
    assert r.status_code == 200
    body = r.json()
    assert body["layout"] is None and body["updated_at"] is None
    assert body["limits"]["label_max"] == nl.LABEL_MAX


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_writes_are_admin_bearer_only(auth, no_db, method):
    kw = {"json": GOOD} if method == "PUT" else {}
    async with web_client() as c:
        nobody = await c.request(method, "/api/nav-layout", **kw)
        device = await c.request(method, "/api/nav-layout",
                                 headers={HEADER: DEVICE, "X-Requested-With": "t"}, **kw)
    async with web_client(cookies={COOKIE: ADMIN}) as c:
        cookie = await c.request(method, "/api/nav-layout", **kw)
    assert nobody.status_code == 401
    assert device.status_code in (401, 403)
    assert cookie.status_code == 403
    assert no_db == []


async def test_a_bad_layout_is_422_before_anything_is_written(auth, monkeypatch):
    def boom():
        raise AssertionError("the database was reached")

    monkeypatch.setattr(nl, "session_scope", boom)
    async with web_client() as c:
        r = await c.put("/api/nav-layout", json=_bad(top=["home"]),
                        headers={**bearer(ADMIN), "X-Requested-With": "t"})
    assert r.status_code == 422
    assert "Home" in r.json()["detail"]


def test_the_route_tiers_are_declared():
    from domovoi import admin_auth
    from web.backend.main import app

    tiers = {}
    for route in app.routes:
        if getattr(route, "path", None) == "/api/nav-layout":
            for m in route.methods:
                tiers[m] = [d.call for d in route.dependant.dependencies]
    assert tiers["GET"] == []
    assert admin_auth.require_admin_mutation in tiers["PUT"]
    assert admin_auth.require_admin_mutation in tiers["DELETE"]


def test_the_change_rides_the_state_socket():
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME[nl.NOTIFY_CHANNEL] == "nav_layout"
    assert "nav_layout" in realtime.CORE_REALTIME_CHANNELS


# ─── save, read, reset (DB) ───────────────────────────────────────────────


@pytest.fixture
async def clean_table():
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM household_settings WHERE key = 'nav_layout'"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM household_settings WHERE key = 'nav_layout'"))


@requires_db
async def test_save_read_back_and_reset(auth, clean_table):
    hdrs = {**bearer(ADMIN), "X-Requested-With": "t"}
    async with web_client() as c:
        saved = await c.put("/api/nav-layout", json=GOOD, headers=hdrs)
        assert saved.status_code == 200, saved.text
        assert saved.json()["layout"] == GOOD and saved.json()["updated_at"]
        # Anyone reads it, no credential at all.
        got = (await c.get("/api/nav-layout")).json()
        assert got["layout"] == GOOD
        assert await realtime._snapshot_nav_layout() == GOOD
        # A second save replaces it.
        again = await c.put("/api/nav-layout", json={**GOOD, "order": []}, headers=hdrs)
        assert again.json()["layout"]["order"] == []
        assert (await c.get("/api/nav-layout")).json()["layout"]["order"] == []
        # A refused save leaves the last good one.
        bad = await c.put("/api/nav-layout", json=_bad(groups="x"), headers=hdrs)
        assert bad.status_code == 422
        assert (await c.get("/api/nav-layout")).json()["layout"]["order"] == []
        reset = await c.delete("/api/nav-layout", headers=hdrs)
        assert reset.status_code == 200 and reset.json()["layout"] is None
        assert (await c.get("/api/nav-layout")).json()["layout"] is None
    assert await realtime._snapshot_nav_layout() is None


@requires_db
async def test_a_stored_layout_that_no_longer_validates_reads_as_the_default(clean_table):
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO household_settings (key, value) VALUES ('nav_layout', CAST(:v AS jsonb))"),
            {"v": json.dumps({"groups": [{"id": "a", "label": "<i>", "items": []}]})})
    assert await nl.read_layout() == (None, None)


@requires_db
async def test_the_table_refuses_a_value_that_is_not_an_object(clean_table):
    from domovoi.db.session import engine

    with pytest.raises(Exception):
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO household_settings (key, value) VALUES ('nav_layout', '[]'::jsonb)"))


# ─── the browser side (vendored Babel in node) ────────────────────────────

_STATIONS = {"slug": "radio", "pages": [
    {"route": "stations", "page": "StationsPage", "nav_label": "Stations", "nav_order": 50,
     "nav_group": "media"}]}

_SETUP = r"""
ServerStore = { current: () => null, currentLabel: () => 'box:6369' };
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
DeviceIdentity = { id: () => 'dev-1', sharedScreen: () => __SHARED, canRegister: () => true,
                   subscribeShared: () => () => {} };
mutationErrorText = (e) => String((e && e.message) || e);
window.DomovoiPluginManifest = __MANIFEST;
"""


def _setup(shared=False, manifest=None):
    return (f"const __SHARED = {json.dumps(shared)};"
            f" const __MANIFEST = {json.dumps(manifest or {'plugins': [_STATIONS]})};" + _SETUP)


def _resolve(override, *, raw=False):
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": {"plugins": []}, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(),
        "script": (
            "const w = h.global('window');"
            f" const items = w.navItemsFor({json.dumps({'plugins': [_STATIONS]})}, {{ shared: false, ai: true }});"
            f" return w.resolveNavTree(items, {json.dumps(override)}, w.NAV_GROUPS, {{ raw: {json.dumps(raw)} }})"
            ".map((e) => e.kind === 'item' ? e.item.route : { group: e.id, label: e.label, items: e.items.map((i) => i.route) });"
        ),
    }


def _edits(ops):
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": {"plugins": []}, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(),
        "script": (
            "const w = h.global('window');"
            " const items = w.buildNavItems({ plugins: [] });"
            " let L = w.navLayoutFull(items, null); const start = JSON.parse(JSON.stringify(L));"
            f" for (const [op, a] of {json.dumps(ops)}) L = w.navLayoutEdit(L, op, a);"
            " return { start, end: L, id: w.navGroupId(L, 'Kids & teens'), id2: w.navGroupId({ groups: [{ id: 'kids-teens' }] }, 'Kids & teens'),"
            " id3: w.navGroupId(L, '123') };"
        ),
    }


_READ_CARD = r"""
const cls = (e) => String((e.props && e.props.className) || '');
const preview = h.find((e) => cls(e) === 'nav-layout-preview-box');
const rows = preview ? h.findAll((e) => h.ancestors(e).includes(preview) && cls(e).startsWith('nav-layout-preview-row')) : [];
return { text: h.text(), calls: h.calls, fires: h.fnCalls,
         preview: rows.map((r) => cls(r).replace('nav-layout-preview-row', '').trim() + ':' + (h.findAll((x) => h.ancestors(x).includes(r) && x.type === 'span').map((s) => s.text).join(''))),
         editing: !!h.find((e) => cls(e) === 'nav-layout-edit') };
"""


def _card(script, *, layout=None, put=None, shared=False):
    api = {"GET /api/nav-layout": {"layout": layout, "updated_at": None,
                                   "limits": {"label_max": 24, "max_groups": 12}}}
    if put is not None:
        api["PUT /api/nav-layout"] = put
    api["DELETE /api/nav-layout"] = {"layout": None, "updated_at": None}
    return {
        "files": [COMPONENTS, SETTINGS], "component": "NavLayoutCard", "fnProps": ["fire"],
        "setup": _setup(shared=shared), "api": api,
        "script": "h.render(); await h.settle(); h.rerender();" + script + _READ_CARD,
    }


_SEL = "(label) => (e) => e.type === 'select' && e.props['aria-label'] === label"
_BTN = "(label) => (e) => e.type === 'button' && e.props['aria-label'] === label"

_MOVES = f"""
const sel = {_SEL}; const btn = {_BTN};
await h.click({{ type: 'button', text: 'change the menu' }}); h.rerender();
// Chat into Media, News to the top level, Tools renamed, a new group with Files in it.
await h.change(sel('move Chat to'), 'media'); h.rerender();
await h.change(sel('move News to'), '__top'); h.rerender();
await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'name of the group Tools', 'Office'); h.rerender();
await h.click({{ type: 'button', text: 'new group' }}); h.rerender();
await h.change(sel('move Files to'), 'new-group'); h.rerender();
await h.change((e) => e.type === 'input' && e.props.id === 'nav-layout-name-new-group', 'Kids'); h.rerender();
// Plugins up one along the top level; Podcasts down one inside Media.
await h.click(btn('move Plugins up')); h.rerender();
await h.click(btn('move Podcasts down')); h.rerender();
await h.click({{ type: 'button', text: 'save for everyone' }}); await h.settle(); h.rerender();
"""

SCENARIOS = {
    "r_order": _resolve({
        "groups": [{"id": "media", "label": "Media", "items": []}, {"id": "tools", "label": "Tools", "items": []},
                   {"id": "kids", "label": "Kids", "items": []}],
        "top": ["plugins", "chat"],
        "order": ["plugins", "group:tools", "chat", "group:media"]}),
    "r_order_raw": _resolve({
        "groups": [{"id": "media", "label": "Media", "items": []}, {"id": "tools", "label": "Tools", "items": ["files"]},
                   {"id": "kids", "label": "Kids", "items": []}],
        "top": ["news", "calendar"],
        "order": ["group:kids", "group:tools"]}, raw=True),
    "edits": _edits([
        ["move", {"route": "chat", "to": "media"}],
        ["rename", {"id": "tools", "label": "Office"}],
        ["add-group", {"id": "kids", "label": "Kids"}],
        ["move", {"route": "files", "to": "kids"}],
        ["up", {"token": "group:kids"}],
        ["down", {"token": "music"}],
        ["delete-group", {"id": "tools"}],
        ["move", {"route": "nope", "to": "nowhere"}],
    ]),
    "card_default": _card(""),
    "card_moves": _card(_MOVES, put={"layout": None, "updated_at": "2026-11-10T12:00:00+00:00"}),
    "card_saved": _card("", layout={
        "version": 1, "groups": [{"id": "media", "label": "Watch", "items": ["music", "videos"]}],
        "top": ["chat", "plugins"], "order": ["plugins", "group:media", "chat"]}),
    "card_reset": _card(
        "await h.click({ type: 'button', text: 'reset to default' }); h.rerender();"
        " await h.click({ type: 'button', text: 'Reset' }); await h.settle(); h.rerender();",
        layout={"version": 1, "groups": [], "top": ["chat"], "order": ["chat"]}),
    "card_bad_name": _card(
        "await h.click({ type: 'button', text: 'change the menu' }); h.rerender();"
        " await h.change((e) => e.type === 'input' && e.props['aria-label'] === 'name of the group Media', '<b>');"
        " h.rerender(); await h.click({ type: 'button', text: 'save for everyone' }); h.rerender();"),
    "sidebar_saved": {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": {"plugins": [_STATIONS]}, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(),
        "api": {"GET /api/nav-layout": {"layout": {
            "version": 1,
            "groups": [{"id": "media", "label": "Watch", "items": ["videos", "music"]},
                       {"id": "kids", "label": "Kids", "items": ["podcasts", "files"]}],
            "top": ["chat", "news"], "order": ["news", "group:kids", "chat", "group:media"]}}},
        "script": r"""
          h.render(); await h.settle(); h.rerender();
          const cls = (e) => String((e.props && e.props.className) || '').split(' ');
          return {
            rows: h.findAll((e) => cls(e).includes('nav-item') && !cls(e).includes('brand-link')).map((e) => {
              const box = h.ancestors(e).find((a) => cls(a).includes('nav-group-items'));
              return [e.props.key, box ? box.props.id.replace('nav-group-', '') : null]; }),
            heads: h.findAll((e) => cls(e).includes('nav-group-head')).map((e) =>
              (h.findAll((x) => h.ancestors(x).includes(e) && x.type === 'span')[0] || {}).text),
          };
        """,
    },
    "sidebar_saved_shared": {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "home", "counts": {}, "manifest": {"plugins": []}, "badges": {}},
        "fnProps": ["setRoute"], "setup": _setup(shared=True),
        "api": {"GET /api/nav-layout": {"layout": {
            "version": 1, "groups": [{"id": "kids", "label": "Kids", "items": ["files", "news", "calendar"]}],
            "top": ["chat"], "order": ["chat", "group:kids"]}}},
        "script": r"""
          h.render(); await h.settle(); h.rerender();
          const cls = (e) => String((e.props && e.props.className) || '').split(' ');
          return h.findAll((e) => cls(e).includes('nav-item') && !cls(e).includes('brand-link')).map((e) => e.props.key);
        """,
    },
    "grid_saved": {
        "files": [COMPONENTS, HOME], "component": "HomeEverything",
        "props": {"counts": {}, "badges": {}},
        "setup": _setup(),
        "api": {"GET /api/nav-layout": {"layout": {
            "version": 1, "groups": [{"id": "kids", "label": "Kids", "items": ["podcasts", "files"]}],
            "top": [], "order": ["group:kids"]}}},
        "script": (
            "h.render(); await h.settle(); h.rerender(); const cls = (e) => String((e.props && e.props.className) || '');"
            " return h.findAll((e) => cls(e) === 'home-grid-head').map((x) => x.text);"
        ),
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    spec = tmp_path_factory.mktemp("nav-layout") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_household_order_sets_the_top_level(driven):
    media = {"group": "media", "label": "Media", "items": ["music", "podcasts", "audiobooks", "videos", "stations"]}
    tools = {"group": "tools", "label": "Tools", "items": ["news", "calendar", "files"]}
    # Kids is empty, so not drawn; the rest in the household's order.
    assert driven["r_order"] == ["plugins", tools, "chat", media]


def test_raw_mode_keeps_empty_and_one_page_groups_for_the_editor(driven):
    out = driven["r_order_raw"]
    assert out[0] == {"group": "kids", "label": "Kids", "items": []}
    assert out[1] == {"group": "tools", "label": "Tools", "items": ["files"]}
    # What the order doesn't name follows in nav order.
    assert [e if isinstance(e, str) else e["group"] for e in out[2:]] == ["chat", "media", "news", "calendar", "plugins"]


def test_the_edit_operations(driven):
    o = driven["edits"]
    assert o["start"]["order"] == ["chat", "group:media", "group:tools", "plugins"]
    end = o["end"]
    groups = {g["id"]: g for g in end["groups"]}
    assert set(groups) == {"media", "kids"}                      # tools deleted
    assert groups["media"]["items"] == ["podcasts", "music", "audiobooks", "videos", "chat"]
    assert groups["kids"] == {"id": "kids", "label": "Kids", "items": ["files"]}
    # Tools' pages went to the top level where Tools was; the unknown move did nothing.
    assert end["order"] == ["group:media", "news", "calendar", "group:kids", "plugins"]
    assert set(end["top"]) == {"news", "calendar", "plugins"}
    assert o["id"] == "kids-teens" and o["id2"] == "kids-teens-2" and o["id3"] == "group-123"


def test_the_card_starts_folded_with_the_household_wording(driven):
    o = driven["card_default"]
    assert not o["editing"]
    text = " ".join(o["text"])
    assert ("This changes the menu for everyone using this Domovoi in a web browser. "
            "The phone app has its own layout in the app.") in text
    assert "default menu" in text and "change the menu" in text
    assert "Only an admin can change it." in text
    assert o["preview"][:3] == ["brand:domovoi", ":Chat", "head:Media"]
    assert [c for c in o["calls"] if c["method"] != "GET"] == []


def test_the_moves_produce_the_expected_layout(driven):
    o = driven["card_moves"]
    put = [c for c in o["calls"] if c["method"] == "PUT"]
    assert len(put) == 1 and put[0]["path"] == "/api/nav-layout"
    body = put[0]["body"]
    assert body["version"] == 1
    groups = {g["id"]: g for g in body["groups"]}
    assert groups["media"]["items"] == ["music", "audiobooks", "podcasts", "videos", "stations", "chat"]
    assert groups["tools"] == {"id": "tools", "label": "Office", "items": ["calendar"]}
    assert groups["new-group"] == {"id": "new-group", "label": "Kids", "items": ["files"]}
    assert body["top"] == ["plugins", "news"]
    assert body["order"] == ["group:media", "plugins", "group:tools", "news", "group:new-group"]
    assert {"name": "fire", "args": ["menu saved for every browser on this Domovoi"]} in o["fires"]
    assert not o["editing"]


def test_a_saved_layout_shows_in_the_card_preview(driven):
    o = driven["card_saved"]
    assert "custom menu" in o["text"] and "reset to default" in o["text"]
    assert o["preview"][:4] == ["brand:domovoi", ":Plugins", "head:Watch", "sub:Music"]


def test_reset_asks_then_deletes(driven):
    o = driven["card_reset"]
    assert [c["path"] for c in o["calls"] if c["method"] == "DELETE"] == ["/api/nav-layout"]
    assert {"name": "fire", "args": ["menu back to the default for every browser"]} in o["fires"]


def test_a_bad_name_holds_the_save(driven):
    o = driven["card_bad_name"]
    assert "no < or > in a name" in o["text"]
    assert not [c for c in o["calls"] if c["method"] == "PUT"]


def test_the_sidebar_draws_the_saved_layout(driven):
    o = driven["sidebar_saved"]
    assert o["rows"] == [["news", None], ["podcasts", "kids"], ["files", "kids"], ["chat", None],
                         ["videos", "media"], ["music", "media"], ["audiobooks", "media"],
                         ["stations", "media"], ["calendar", None], ["plugins", None]]
    assert o["heads"] == ["Kids", "Watch"]


def test_shared_screen_hiding_still_applies_on_top(driven):
    # Chat, Files and News are hidden on a shared screen wherever the layout
    # puts them: Kids is left with Calendar alone, a plain row.
    assert driven["sidebar_saved_shared"] == [
        "calendar", "music", "podcasts", "audiobooks", "videos", "plugins"]


def test_homes_grid_uses_the_saved_layout(driven):
    assert driven["grid_saved"] == ["kids"]
