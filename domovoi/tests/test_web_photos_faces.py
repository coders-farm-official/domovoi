"""Media ▸ Photos and Media ▸ Faces (1.5.0, media understanding slice 5;
design MEDIA-UNDERSTANDING §6, §8, §9; web/static/photos.jsx).

Driven in ``jsx_interact_harness.js`` (components.jsx + photos.jsx, the
data layer scripted). What is pinned:

* a caption, tag or text found in a picture stays text: an injected
  ``<img onerror>`` caption is never an element, and nothing is rendered
  as HTML; a caption always reads "Might show: …";
* the honest empty states: understanding off, nothing looked at yet (both
  link to Settings → Chat & AI → Photos and videos), a guest;
* a video hit opens the player at the moment the search matched;
* Faces is on no launcher while faces are off, for a guest, or on a shared
  screen, and the page asks the server for nothing there;
* admin controls (merge, split, move, show a hidden group) only for an
  admin; nothing asks "who is this?"; strangers' small groups behind
  "show more";
* Forget my face: for the person's own claimed browser or an admin,
  behind a confirm that says what is deleted and what stays;
* the phone layout (no fixed widths; grid columns that fit 375px).

DB-free; needs ``node``.
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
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/photos.jsx"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

EVIL = "<img src=x onerror=alert(1)>"


def _setup(*, guest=False, shared=False, admin=True, owner=None, hash_="") -> str:
    return f"""
globalThis.API_BASE = '';
globalThis.withDeviceToken = (u) => u;
globalThis.isGuestViewer = () => {str(guest).lower()};
globalThis.useGuestViewer = () => {str(guest).lower()};
globalThis.__overlay = null;
globalThis.VideoOverlay = (p) => {{ globalThis.__overlay = {{ rel: p.video.rel, at: p.resumeSec }}; return null; }};
globalThis.DeviceIdentity = {{ id: () => 'dev-1', name: () => 'harness',
  sharedScreen: () => {str(shared).lower()}, canRegister: () => true,
  owner: () => ({json.dumps(owner) if owner else "null"}) }};
globalThis.Auth = {{ status: {{ authenticated: {str(admin).lower()} }}, subscribe: () => () => {{}},
  isLoggedIn: () => {str(admin).lower()}, headers: () => ({{}}), modalOpen: false }};
window.location.hash = {json.dumps(hash_)};
"""


def _run(scenarios: dict, tmp: Path) -> dict:
    # The file form: the scenarios are past Windows' command-line limit.
    arg = tmp / "scenarios.json"
    arg.write_text(json.dumps(scenarios), encoding="utf-8")
    out = subprocess.run(["node", str(HARNESS), str(REPO_ROOT), f"@{arg}"],
                         capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    broken = {n: str(r["__harness_error"])[:300] for n, r in res.items()
              if isinstance(r, dict) and "__harness_error" in r}
    assert not broken, broken
    return res


def _sc(component: str, api: dict, script: str, **setup) -> dict:
    return {"files": FILES, "component": component, "api": api, "setup": _setup(**setup), "script": script}


SNAP = r"""
const blob = h.text().join(' \n ');
const els = h.tree();
return Object.assign({
  blob,
  html: els.filter((e) => e.props && e.props.dangerouslySetInnerHTML).length,
  onerror: els.filter((e) => e.props && (e.props.onerror || e.props.onError) && e.type === 'img').length,
  imgs: els.filter((e) => e.type === 'img').length,
  hrefs: els.filter((e) => e.type === 'a').map((e) => e.props.href),
  inputs: els.filter((e) => e.type === 'input').map((e) => e.props['aria-label'] || e.props.type),
  checkboxes: els.filter((e) => e.type === 'input' && e.props.type === 'checkbox').length,
  selects: els.filter((e) => e.type === 'select').length,
  buttons: els.filter((e) => e.type === 'button').map((e) => String(e.text).trim()),
  hooks: h.hookCalls.slice(),
  calls: h.calls.map((c) => ({ method: c.method, path: c.path, body: c.body })),
  overlay: h.global('__overlay'),
}, extra || {});
"""


def _snap(before: str = "", extra: str = "null") -> str:
    return f"h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();\n{before}\nconst extra = {extra};\n{SNAP}"


MU_ON = {"settings": {"enabled": True, "faces": True}, "progress": {"photos": {"done": 3, "total": 5},
                                                                    "videos": {"done": 1, "total": 1}}}
HITS = {"items": [
    {"id": 1, "library_id": "core:pictures", "path": "2024/a.jpg", "name": "a.jpg", "kind": "image",
     "analysed": True, "taken_at": "2024-07-01T10:00:00+00:00", "date_from": "photo",
     "might_show": EVIL, "tags": ["<b>beach</b>", "dog"], "has_text": True,
     "text": "<script>alert(2)</script>", "score": 1.0},
    {"id": 2, "library_id": "core:videos", "path": "clips/b.mp4", "name": "b.mp4", "kind": "video",
     "analysed": True, "taken_at": None, "date_from": "file", "might_show": None, "tags": [],
     "has_text": False, "text": None, "score": 0.5, "at_s": 83.0},
], "has_more": False, "more": False, "next_cursor": None}

OVERVIEW = {"people": [{"id": 7, "name": "Sarah", "faces": 5, "confirmed": 3, "group_id": 11}],
            "groups": [{"group_id": 12, "hidden": False, "faces": 4}], "shown_min_faces": 3}
FACE = {"face_id": 21, "item_id": 1, "library_id": "core:pictures", "rel_path": "2024/a.jpg", "kind": "image",
        "at_s": None, "box": [0.4, 0.3, 0.1, 0.12], "quality": 0.9, "group_id": 11, "person_id": 7,
        "confirmed": False, "suggested": True}
FACE_OK = {**FACE, "face_id": 22, "confirmed": True, "suggested": False}
UNNAMED = {**FACE, "face_id": 31, "group_id": 12, "person_id": None, "suggested": False}
FACES_API = {
    "GET /api/faces/status": {"enabled": True, "models_ready": True, "fetch_error": None, "faces": 9, "people": 1},
    "GET /api/faces/people": OVERVIEW,
    "GET /api/faces/groups/11": {"group_id": 11, "person_id": 7, "hidden": False, "faces": [FACE_OK, FACE]},
    "GET /api/faces/groups/12": {"group_id": 12, "person_id": None, "hidden": False, "faces": [UNNAMED]},
    "GET /api/faces/people/7/items": {"person_id": 7, "items": [
        {"item_id": 1, "library_id": "core:pictures", "rel_path": "2024/a.jpg", "kind": "image",
         "taken_at": None, "moments": [], "confirmed": True, "faces": 1}], "has_more": False},
    "GET /api/people": [{"id": 7, "name": "Sarah"}, {"id": 8, "name": "Alex"}],
    "POST /api/faces/groups/12/name": {"group_id": 12, "person_id": 7, "faces": 4},
}

NAV = r"""
const W = h.global('window');
const items = (opts) => W.navItemsFor({ plugins: [] }, opts).map((it) => it.route);
const store = W.FacesPageStore;
const before = items({});
await store.ask(); await h.settle();
return { before, after: items({}), shared: items({ shared: true }),
         asked: h.calls.filter((c) => c.path === '/api/faces/status').length };
"""


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    return _run({
        "photos_evil": _sc("PhotosPage", {"GET /api/media-understanding": MU_ON, "GET /api/media/search": HITS,
                                          "GET /api/faces/status": {"enabled": False}},
                           _snap("await h.click((el) => el.type === 'button' && String(el.props['aria-label'] || '').includes('open a.jpg'));")),
        "photos_video": _sc("PhotosPage", {"GET /api/media-understanding": MU_ON, "GET /api/media/search": HITS},
                            _snap("await h.click((el) => el.type === 'button' && String(el.props['aria-label'] || '').includes('play b.mp4'));")),
        "photos_search": _sc("PhotosPage", {"GET /api/media-understanding": MU_ON, "GET /api/media/search": HITS},
                             _snap("await h.type({ type: 'input' }, 'beach 2024'); await h.submit({ type: 'form' });"
                                   " await h.click({ type: 'button', text: 'videos' });")),
        "photos_off": _sc("PhotosPage", {"GET /api/media-understanding": {"settings": {"enabled": False}, "progress": {}}},
                          _snap()),
        "photos_off_admin_words": _sc("PhotosPage", {"GET /api/media-understanding": {"settings": {"enabled": False}, "progress": {}}},
                                      _snap(), admin=False),
        "photos_nothing_yet": _sc("PhotosPage", {"GET /api/media-understanding": {
            "settings": {"enabled": True}, "progress": {"photos": {"done": 0, "total": 10}, "videos": {"done": 0, "total": 2}}}},
            _snap()),
        "photos_no_hits": _sc("PhotosPage", {"GET /api/media-understanding": MU_ON,
                                             "GET /api/media/search": {"items": [], "has_more": False}},
                              _snap("await h.type({ type: 'input' }, 'zebra'); await h.submit({ type: 'form' });")),
        "photos_guest": _sc("PhotosPage", {"GET /api/media-understanding": MU_ON, "GET /api/media/search": HITS},
                            _snap(), guest=True),
        "nav_off": _sc("Empty", {"GET /api/faces/status": {"enabled": False}}, NAV),
        "nav_on": _sc("Empty", {"GET /api/faces/status": {"enabled": True}}, NAV),
        "nav_refused": _sc("Empty", {"GET /api/faces/status": {"__error": {"status": 403, "message": "shared"}}}, NAV),
        "nav_guest": _sc("Empty", {"GET /api/faces/status": {"enabled": True}}, NAV, guest=True),
        "faces_shared": _sc("FacesPage", FACES_API, _snap(), shared=True),
        "faces_guest": _sc("FacesPage", FACES_API, _snap(), guest=True),
        "faces_off": _sc("FacesPage", {**FACES_API, "GET /api/faces/status": {"enabled": False}}, _snap(), admin=False),
        "faces_home": _sc("FacesPage", FACES_API, _snap(), admin=False),
        "faces_home_more": _sc("FacesPage", FACES_API, _snap("await h.click({ type: 'button', text: 'show more' });"), admin=False),
        "faces_home_admin": _sc("FacesPage", FACES_API, _snap()),
        "person_household": _sc("FacesPage", FACES_API, _snap(
            "await h.click({ type: 'button', text: 'not this person' });"), admin=False, hash_="faces/person/7"),
        "person_confirm": _sc("FacesPage", FACES_API, _snap(
            "await h.click({ type: 'button', text: 'yes, Sarah' });"), admin=False, hash_="faces/person/7"),
        "person_own": _sc("FacesPage", FACES_API, _snap(
            "await h.click({ type: 'button', text: 'Forget my face' });",
            "{ dialog: h.text().join(' \\n '), postsBefore: h.calls.filter((c) => c.method === 'POST').length }"),
            admin=False, owner={"id": 7, "name": "Sarah"}, hash_="faces/person/7"),
        "person_own_confirmed": _sc("FacesPage", FACES_API, _snap(
            "await h.click({ type: 'button', text: 'Forget my face' });"
            " await h.click((el) => el.type === 'button' && String(el.text).trim() === 'Forget face');"),
            admin=False, owner={"id": 7, "name": "Sarah"}, hash_="faces/person/7"),
        "person_someone_else": _sc("FacesPage", FACES_API, _snap(), admin=False, owner={"id": 8, "name": "Alex"},
                                   hash_="faces/person/7"),
        "person_admin": _sc("FacesPage", FACES_API, _snap(), hash_="faces/person/7"),
        "group_household": _sc("FacesPage", FACES_API, _snap(
            "await h.type({ type: 'input' }, 'sarah'); await h.submit({ type: 'form' });"),
            admin=False, hash_="faces/group/12"),
        "group_view": _sc("FacesPage", FACES_API, _snap(), admin=False, hash_="faces/group/12"),
        "group_admin": _sc("FacesPage", FACES_API, _snap(), hash_="faces/group/12"),
        "faces_off_confirm": {"files": ["web/static/components.jsx", "web/static/media_understanding.jsx"],
                              "component": "window.DomovoiSettingsSections['photos-and-videos'].Panel",
                              "api": {"GET /api/media-understanding": {**MU_ON, "face_count": 9, "ai_on": True}},
                              "setup": _setup(),
                              "script": _snap(
                                  "const sw = h.findAll((el) => el.type === 'input' && el.props.type === 'checkbox')[1];"
                                  " await h.type((el) => el === sw, false);",
                                  "{ putsBefore: h.calls.filter((c) => c.method === 'PUT').length }")},
        "hidden_household": _sc("FacesPage", {**FACES_API, "GET /api/faces/hidden": {"groups": [{"group_id": 40, "faces": 2}]}},
                                _snap(), admin=False, hash_="faces/hidden"),
    }, tmp_path_factory.mktemp("photos"))


# ─── Photos ───────────────────────────────────────────────────────────────

def test_a_caption_with_html_in_it_stays_text(driven) -> None:
    o = driven["photos_evil"]
    assert f"Might show: {EVIL}" in o["blob"]
    assert "<b>beach</b>, dog" in o["blob"] and "Text in it: <script>alert(2)</script>" in o["blob"]
    assert o["html"] == 0 and o["onerror"] == 0
    # the tile and the details' pictures, and nothing a caption made
    assert o["imgs"] == 3


def test_the_source_never_renders_html() -> None:
    src = (STATIC / "photos.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    assert "Might show: {item.might_show}" in src
    assert "window.confirm" not in src
    assert "window.confirm" not in (STATIC / "media_understanding.jsx").read_text(encoding="utf-8")


def test_a_video_hit_opens_the_player_at_the_moment(driven) -> None:
    assert driven["photos_video"]["overlay"] == {"rel": "clips/b.mp4", "at": 83.0}


def test_the_search_asks_the_server_with_the_words_and_the_kind(driven) -> None:
    hooks = driven["photos_search"]["hooks"]
    assert "/api/media/search?q=" in hooks
    assert "/api/media/search?q=beach%202024" in hooks
    assert "/api/media/search?q=beach%202024&kind=video" in hooks


def test_understanding_off_says_so_and_links_to_settings(driven) -> None:
    o = driven["photos_off"]
    assert "understanding photos and videos is off" in o["blob"] and "Turn it on" in o["blob"]
    assert "#settings/photos-and-videos" in o["hrefs"]
    assert not [h for h in o["hooks"] if h.startswith("/api/media/search")]
    assert "Search photos and videos" not in o["inputs"]
    assert "An admin turns it on" in driven["photos_off_admin_words"]["blob"]


def test_nothing_looked_at_yet_says_so(driven) -> None:
    o = driven["photos_nothing_yet"]
    assert "nothing looked at yet" in o["blob"] and "12 photos and videos are waiting" in o["blob"]
    assert "#settings/photos-and-videos" in o["hrefs"]
    assert not [h for h in o["hooks"] if h.startswith("/api/media/search")]
    assert "nothing found" in driven["photos_no_hits"]["blob"]


def test_a_guest_sees_no_photos_and_asks_for_nothing(driven) -> None:
    o = driven["photos_guest"]
    assert "paired devices only" in o["blob"]
    assert o["hooks"] == [] and "Might show" not in o["blob"]


# ─── Faces on the launchers ───────────────────────────────────────────────

def test_faces_is_on_no_launcher_until_the_server_says_faces_are_on(driven) -> None:
    for name in ("nav_off", "nav_on", "nav_refused", "nav_guest"):
        assert "faces" not in driven[name]["before"], name
        assert "photos" in driven[name]["before"], name
    assert "faces" not in driven["nav_off"]["after"]
    assert "faces" in driven["nav_on"]["after"]
    assert "faces" not in driven["nav_on"]["shared"]
    assert "faces" not in driven["nav_refused"]["after"]
    assert "faces" not in driven["nav_guest"]["after"] and driven["nav_guest"]["asked"] == 0


def test_photos_and_faces_are_core_routes_in_the_media_group() -> None:
    comps = (STATIC / "components.jsx").read_text(encoding="utf-8")
    assert "routes: ['music', 'podcasts', 'audiobooks', 'videos', 'photos', 'faces'] }," in comps
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "photos: () => window.PhotosPage," in html and "faces: () => window.FacesPage," in html
    assert html.index('src="videos.jsx"') < html.index('src="photos.jsx"')


def test_a_shared_screen_and_a_guest_get_no_faces_page(driven) -> None:
    for name in ("faces_shared", "faces_guest"):
        o = driven[name]
        assert not [h for h in o["hooks"] if h.startswith("/api/faces")], name
        assert "Sarah" not in o["blob"], name
    assert "not on a shared screen" in driven["faces_shared"]["blob"]
    assert "paired devices only" in driven["faces_guest"]["blob"]


def test_faces_off_says_so(driven) -> None:
    o = driven["faces_off"]
    assert "recognizing faces is off" in o["blob"] and "An admin turns it on" in o["blob"]
    assert "#settings/photos-and-videos" in o["hrefs"]
    assert not [h for h in o["hooks"] if h.startswith("/api/faces/people")]


# ─── Faces: the household and the admin ──────────────────────────────────

def test_the_overview_names_people_and_folds_strangers_away(driven) -> None:
    o = driven["faces_home"]
    assert "Sarah" in o["blob"] and "2 to check" in o["blob"] and "4 faces" in o["blob"]
    assert "show more" in o["buttons"] and "hidden groups" not in o["blob"]
    assert "/api/faces/people?more=true" in driven["faces_home_more"]["hooks"]
    assert "hidden groups" in driven["faces_home_admin"]["blob"]
    src = (STATIC / "photos.jsx").read_text(encoding="utf-8").lower()
    for name in ("faces_home", "faces_home_more", "group_view"):
        assert "who is this" not in driven[name]["blob"].lower(), name
    assert "who is this?" not in src.replace('"who is this?"', "")


def test_the_household_confirms_and_says_not_this_person(driven) -> None:
    o = driven["person_household"]
    assert "yes, Sarah" in o["buttons"] and "not this person" in o["buttons"]
    assert {"method": "POST", "path": "/api/faces/face/21/not-this-person", "body": {}} in o["calls"]
    c = driven["person_confirm"]["calls"]
    assert {"method": "POST", "path": "/api/faces/face/21/confirm", "body": {"person_id": 7}} in c
    for word in ("merge", "split selected", "move these faces to someone else"):
        assert word not in o["buttons"], word
    assert o["checkboxes"] == 0 and o["selects"] == 0
    assert "Forget" not in o["blob"]
    assert "Forget" not in driven["person_someone_else"]["blob"]


def test_admin_controls_only_for_an_admin(driven) -> None:
    o = driven["person_admin"]
    assert "merge" in o["buttons"] and "move these faces to someone else" in o["buttons"]
    assert o["checkboxes"] >= 2 and o["selects"] == 1
    assert "Forget Sarah’s face" in o["buttons"]
    g = driven["group_admin"]
    assert g["checkboxes"] == 1 and g["selects"] == 1
    gh = driven["group_view"]
    assert gh["checkboxes"] == 0 and gh["selects"] == 0
    assert "hide this group" in gh["buttons"] and "not the same person" in gh["buttons"]
    assert "only an admin sees hidden groups" in driven["hidden_household"]["blob"]
    assert not [h for h in driven["hidden_household"]["hooks"] if h == "/api/faces/hidden"]


def test_naming_a_group_picks_the_existing_person(driven) -> None:
    calls = driven["group_household"]["calls"]
    # ... and the page then opens that person
    assert "yes, Sarah" in driven["group_household"]["buttons"]
    assert {"method": "POST", "path": "/api/faces/groups/12/name", "body": {"person_id": 7}} in calls


def test_forget_my_face_asks_first_and_says_what_goes(driven) -> None:
    o = driven["person_own"]
    assert o["postsBefore"] == 0
    d = o["dialog"]
    assert "Forget my face?" in d
    assert "deletes every saved face of Sarah" in d
    assert "Their photos and videos stay" in d and "can’t be undone" in d
    # Permanent, even across faces off and on (owner, 2026-11-22).
    assert "It’s permanent: Sarah stays forgotten even if faces are later turned off and on again" in d
    assert "keeps the forget list" in d
    done = driven["person_own_confirmed"]["calls"]
    assert {"method": "POST", "path": "/api/faces/people/7/forget", "body": {}} in done


# ─── the phone ────────────────────────────────────────────────────────────

def test_the_phone_layout_has_nothing_wider_than_the_screen() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    part = css[css.index("Media ▸ Photos and Faces (photos.jsx"):]
    for grid in (".photos-grid", ".faces-grid", ".faces-cards"):
        rule = re.search(re.escape(grid) + r" \{[^}]*\}", part).group(0)
        assert re.search(r"minmax\(min\(\d+px, 100%\), 1fr\)", rule), grid
    assert ".photos-wrap { overflow-wrap: anywhere; min-width: 0; }" in part
    assert "min-width: 0; max-width: 100%;" in part
    phone = part[part.index("@media (max-width: 760px)"):]
    assert "min-height: 44px" in phone and "font-size: 16px" in phone
    src = (STATIC / "photos.jsx").read_text(encoding="utf-8")
    # no fixed width anywhere a phone could overflow: the biggest is a 96px face
    widths = [int(w) for w in re.findall(r"\bwidth: (\d+)", src)]
    assert all(w <= 96 for w in widths), widths
    assert "nowrap" not in src


def test_turning_faces_off_asks_and_says_the_forget_list_is_kept(driven) -> None:
    """Owner decision 2026-11-22: faces off deletes every face, group and
    embedding but keeps the forget list, and the confirm says so."""
    o = driven["faces_off_confirm"]
    assert o["putsBefore"] == 0
    assert "Turn faces off?" in o["blob"]
    assert "This deletes every face and group: 9 saved faces" in o["blob"]
    assert "People who asked to be forgotten stay forgotten: their forget list is kept" in o["blob"]
    assert "aren’t learned again if faces are turned back on" in o["blob"]
