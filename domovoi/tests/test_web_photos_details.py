"""Media ▸ Photos: the full details of one photo or video (1.5.0; the
Info sheet in web/static/photos.jsx, the "Info" button in the player in
web/static/videos.jsx; GET /api/media/items/{id}/details).

Driven in ``jsx_interact_harness.js`` with the data layer scripted. What
is pinned:

* the facts render (file, folder, size, date, place, state with its error
  code, when analysed, the vision model) and every caption, tag, OCR text,
  transcript line, summary and file name stays text (never HTML), with a
  caption reading "Might show: …";
* the empty states: "Not analysed yet", and "Understanding is off" with
  the link to Settings → Chat & AI → Photos and videos;
* places and people are left out when the server leaves them out (off, or
  a shared screen);
* a video's tile and the player have an "Info" button; a photo's tile has
  none (a tap opens its details); a moment in a video's details plays it
  from there; the player has no "Info" button when nothing handles it.

DB-free; needs ``node``. The endpoint itself: test_media_item_details.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domovoi.tests.test_web_photos_faces import EVIL, HITS, MU_ON, SNAP, _run, pytestmark  # noqa: F401

FILES = ["web/static/components.jsx", "web/static/photos.jsx"]
SETTINGS_HREF = "#settings/photos-and-videos"


def _setup(*, shared=False, admin=False) -> str:
    return f"""
globalThis.API_BASE = '';
globalThis.withDeviceToken = (u) => u;
globalThis.isGuestViewer = () => false;
globalThis.useGuestViewer = () => false;
globalThis.__overlay = null;
globalThis.__info = null;
globalThis.VideoOverlay = (p) => {{
  globalThis.__overlay = {{ rel: p.video.rel, at: p.resumeSec, start: p.startSec, info: typeof p.onInfo === 'function' }};
  globalThis.__info = p.onInfo || null;
  return null;
}};
globalThis.DeviceIdentity = {{ id: () => 'dev-1', name: () => 'harness',
  sharedScreen: () => {str(shared).lower()}, canRegister: () => true, owner: () => null }};
globalThis.Auth = {{ status: {{ authenticated: {str(admin).lower()} }}, subscribe: () => () => {{}},
  isLoggedIn: () => {str(admin).lower()}, headers: () => ({{}}), modalOpen: false }};
"""


def _snap(before: str = "") -> str:
    return (f"h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();\n{before}\n"
            "await h.settle(); h.rerender();\n"
            "const extra = { details: h.findAll((el) => el.props && el.props.role === 'dialog').length,"
            " infoLabels: h.tree().filter((el) => el.type === 'button' && String(el.props['aria-label'] || '').startsWith('info about'))"
            ".map((el) => el.props['aria-label']) };\n" + SNAP)


def _sc(api: dict, before: str = "", **setup) -> dict:
    return {"files": FILES, "component": "PhotosPage", "api": api, "setup": _setup(**setup), "script": _snap(before)}


OPEN_PHOTO = "await h.click((el) => el.type === 'button' && String(el.props['aria-label'] || '').includes('open a.jpg'));"
INFO_VIDEO = "await h.click((el) => el.type === 'button' && el.props['aria-label'] === 'info about b.mp4');"
PLAY_VIDEO = "await h.click((el) => el.type === 'button' && String(el.props['aria-label'] || '').includes('play b.mp4'));"

PHOTO = {
    "id": 1, "kind": "image", "name": "<b>a</b>.jpg", "folder": "2024", "library_id": "core:pictures",
    "library": "Pictures", "path": "2024/a.jpg", "size_bytes": 2_500_000, "duration_s": None,
    "taken_at": "2024-07-01T10:00:00+00:00", "date_from": "photo", "missing": False,
    "understanding": {"on": True, "faces_shown": True, "places_shown": True},
    "analysis": {"state": "done", "stage": None, "error_code": None, "attempts": 1,
                 "analysed_at": "2024-07-02T03:00:00+00:00", "vision_model": "vision-small:1b",
                 "frame_pass": None, "stages": []},
    "might_show": EVIL, "tags": ["<b>beach</b>", "dog"], "text": "<script>alert(2)</script>", "text_cut": False,
    "place": "Portland, Oregon, United States",
    "faces": [{"person_id": 7, "name": "alex", "confirmed": True, "group_id": 11, "at_s": None, "end_s": None},
              {"person_id": 8, "name": "sam", "confirmed": False, "group_id": 13, "at_s": None, "end_s": None},
              {"person_id": None, "name": None, "confirmed": False, "group_id": 12, "at_s": None, "end_s": None}],
}
PHOTO_OFF_BITS = {**{k: v for k, v in PHOTO.items() if k not in ("place", "faces")},
                  "understanding": {"on": True, "faces_shown": False, "places_shown": False}}
UNANALYSED = {**PHOTO_OFF_BITS, "analysis": {**PHOTO["analysis"], "state": "pending", "analysed_at": None,
                                             "vision_model": None},
              "might_show": None, "tags": [], "text": None, "date_from": "file"}
FAILED = {**UNANALYSED, "analysis": {**UNANALYSED["analysis"], "state": "failed", "error_code": "MU-PHOTO-3",
                                     "attempts": 3}}
OFF = {**UNANALYSED, "understanding": {"on": False, "faces_shown": False, "places_shown": False}}

VIDEO = {
    "id": 2, "kind": "video", "name": "b.mp4", "folder": "clips", "library_id": "core:videos", "library": "Videos",
    "path": "clips/b.mp4", "size_bytes": 90_000_000, "duration_s": 125.0, "taken_at": None, "date_from": "file",
    "missing": False, "understanding": {"on": True, "faces_shown": True, "places_shown": False},
    "analysis": {"state": "done", "stage": None, "error_code": None, "attempts": 1,
                 "analysed_at": "2024-07-02T03:00:00+00:00", "vision_model": "vision-small:1b",
                 "frame_pass": "quick", "stages": []},
    "might_show": None, "tags": [], "text": None, "text_cut": False,
    "faces": [{"person_id": 7, "name": "alex", "confirmed": True, "group_id": 11, "at_s": 40.0, "end_s": 55.0}],
    "frames": [{"at_s": 12.0, "might_show": "a <i>cake</i>", "tags": ["party"], "text": "HAPPY <u>BIRTHDAY</u>"}],
    "frames_page": {"offset": 0, "limit": 100, "total": 1, "more": False, "next": None},
    "transcript": [{"start_s": 3.0, "end_s": 6.0, "text": "<a href=x>blow out</a> the candles"}],
    "transcript_page": {"offset": 0, "limit": 200, "total": 250, "more": True, "next": 200},
    "summary": {"text": "**a birthday** <img src=x>", "suggested_title": "Party <em>time</em>", "model": "qa-small"},
}
VIDEO_MORE = {**VIDEO, "transcript": [{"start_s": 600.0, "end_s": 601.0, "text": "the last line"}],
              "transcript_page": {"offset": 200, "limit": 200, "total": 250, "more": False, "next": None}}


def _api(details_1=None, details_2=None) -> dict:
    api = {"GET /api/media-understanding": MU_ON, "GET /api/media/search": HITS}
    if details_1 is not None:
        api["GET /api/media/items/1/details"] = details_1
    if details_2 is not None:
        api["GET /api/media/items/2/details"] = details_2
    return api


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    tmp: Path = tmp_path_factory.mktemp("photos_details")
    return _run({
        "photo": _sc(_api(PHOTO), OPEN_PHOTO),
        "photo_hidden_bits": _sc(_api(PHOTO_OFF_BITS), OPEN_PHOTO),
        "unanalysed": _sc(_api(UNANALYSED), OPEN_PHOTO),
        "failed": _sc(_api(FAILED), OPEN_PHOTO),
        "off": _sc(_api(OFF), OPEN_PHOTO),
        "grid": _sc(_api()),
        "video_info": _sc(_api(details_2=VIDEO), INFO_VIDEO),
        "video_moment": _sc(_api(details_2=VIDEO), INFO_VIDEO + " await h.settle(); h.rerender();"
                            " await h.click((el) => el.type === 'button' && el.props['aria-label'] === 'play from 0:12');"),
        "video_face_moment": _sc(_api(details_2=VIDEO), INFO_VIDEO + " await h.settle(); h.rerender();"
                                 " await h.click((el) => el.type === 'button' && el.props['aria-label'] === 'play from 0:40');"),
        "video_more": _sc({**_api(details_2=VIDEO), "GET /api/media/items/2/details?segments_from=200": VIDEO_MORE},
                          INFO_VIDEO + " await h.settle(); h.rerender();"
                          " await h.click({ type: 'button', text: 'show more of what is said' });"),
        "player_info": _sc(_api(details_2=VIDEO), PLAY_VIDEO + " await h.settle(); h.rerender();"
                           " const ov = h.global('__overlay'); h.global('__info')(); await h.settle(); h.rerender();"),
        "overlay_with_info": {"files": ["web/static/components.jsx", "web/static/videos.jsx"], "component": "VideoOverlay",
                              "props": {"video": {"library_id": "core:videos", "rel": "clips/b.mp4", "name": "b.mp4"}},
                              "fnProps": ["onInfo", "onClose"], "setup": _setup(),
                              "script": "h.render(); await h.settle(); h.rerender();"
                                        " await h.click((el) => el.type === 'button' && el.props['aria-label'] === 'info about b.mp4');"
                                        " return { fn: h.fnCalls.map((c) => c.name),"
                                        " buttons: h.tree().filter((el) => el.type === 'button').map((el) => String(el.text).trim()) };"},
        "overlay_without_info": {"files": ["web/static/components.jsx", "web/static/videos.jsx"], "component": "VideoOverlay",
                                 "props": {"video": {"library_id": "core:videos", "rel": "clips/b.mp4", "name": "b.mp4"}},
                                 "fnProps": ["onClose"], "setup": _setup(),
                                 "script": "h.render(); await h.settle(); h.rerender();"
                                           " return { buttons: h.tree().filter((el) => el.type === 'button').map((el) => String(el.text).trim()) };"},
    }, tmp)


def test_a_photos_details_show_every_fact(driven) -> None:
    o = driven["photo"]
    blob = o["blob"]
    assert o["details"] == 1 and o["html"] == 0 and o["onerror"] == 0
    for word in ("File", "<b>a</b>.jpg", "Folder", "Pictures › 2024", "Size", "2.4 MB", "Taken", "Place",
                 "Portland, Oregon, United States", "State", "looked at", "Analysed", "Vision model", "vision-small:1b",
                 f"Might show: {EVIL}", "<b>beach</b>, dog", "Text in it: <script>alert(2)</script>", "People"):
        assert word in blob, word
    assert "alex" in blob and "sam (suggested)" in blob and "someone not named yet" in blob
    assert "#faces/person/7" in o["hrefs"] and "#faces/group/12" in o["hrefs"]
    assert "Not analysed yet" not in blob and "Understanding is off" not in blob
    assert "/api/media/items/1/details" in o["hooks"]


def test_places_and_people_are_hidden_when_the_server_leaves_them_out(driven) -> None:
    blob = driven["photo_hidden_bits"]["blob"]
    assert "Place" not in blob and "Portland" not in blob
    assert "People" not in blob and "No faces found" not in blob
    assert "Might show:" in blob


def test_an_unanalysed_photo_says_so(driven) -> None:
    blob = driven["unanalysed"]["blob"]
    assert "Not analysed yet" in blob and "waiting to be looked at" in blob
    assert "(the file’s date)" in blob
    assert "Analysed" not in blob.replace("Not analysed", "") and "Vision model" not in blob


def test_a_failed_photo_shows_its_error_code(driven) -> None:
    blob = driven["failed"]["blob"]
    assert "couldn’t be read" in blob and "MU-PHOTO-3" in blob


def test_understanding_off_points_to_settings(driven) -> None:
    o = driven["off"]
    assert "Understanding is off" in o["blob"] and "Not analysed yet" not in o["blob"]
    assert SETTINGS_HREF in o["hrefs"]


def test_only_a_video_tile_has_an_info_button(driven) -> None:
    o = driven["grid"]
    assert o["infoLabels"] == ["info about b.mp4"]
    assert o["details"] == 0


def test_a_videos_details_show_frames_transcript_and_summary(driven) -> None:
    o = driven["video_info"]
    blob = o["blob"]
    assert o["details"] == 1 and o["html"] == 0
    assert "Might show: a <i>cake</i>" in blob and "party" in blob and "Text in it: HAPPY <u>BIRTHDAY</u>" in blob
    assert "<a href=x>blow out</a> the candles" in blob
    assert "**a birthday** <img src=x>" in blob and "Party <em>time</em>" in blob
    assert "Videos › clips" in blob and "Length" in blob and "2:05" in blob
    assert "play" in o["buttons"] and "show more of what is said" in o["buttons"]
    assert "show more frames" not in o["buttons"]
    assert "Place" not in blob
    # No element was made from any of it: the markup is text.
    assert o["onerror"] == 0 and "x" not in [h for h in o["hrefs"] if h]


def test_a_moment_plays_the_video_from_there(driven) -> None:
    assert driven["video_moment"]["overlay"] == {"rel": "clips/b.mp4", "at": 0, "start": 12, "info": True}
    assert driven["video_moment"]["details"] == 0
    assert driven["video_face_moment"]["overlay"]["start"] == 40


def test_more_of_the_transcript_loads_on_request(driven) -> None:
    o = driven["video_more"]
    assert "the last line" in o["blob"] and "<a href=x>blow out</a> the candles" in o["blob"]
    assert any(c["path"] == "/api/media/items/2/details?segments_from=200" for c in o["calls"])
    assert "show more of what is said" not in o["buttons"]


def test_the_players_info_button_opens_the_details(driven) -> None:
    o = driven["player_info"]
    assert o["details"] == 1 and "<a href=x>blow out</a> the candles" in o["blob"]


def test_the_player_shows_info_only_when_it_can_do_something(driven) -> None:
    with_info = driven["overlay_with_info"]
    assert "Info" in with_info["buttons"] and with_info["fn"] == ["onInfo"]
    assert "Info" not in driven["overlay_without_info"]["buttons"]


def test_the_sheet_never_renders_html() -> None:
    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "photos.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src
    assert "marked" not in src and "renderMarkdown" not in src
    assert json.dumps(SETTINGS_HREF)[1:-1] in src
