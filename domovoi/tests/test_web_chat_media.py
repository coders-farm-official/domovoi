"""The chat page draws photo and video results as cards (chat_media.jsx,
media understanding slice 6).

Driven outside a browser (jsx_interact_harness.js): a reply's ``media``
becomes a grid of thumbnails, each with "Might show: …" as TEXT (a caption
that is markup stays words), its date and, on a video, a time badge for its
best moment. A photo opens the picture in a new tab; a video opens the
Videos page's player at that moment. Thumbnails ride the household token,
and only the two thumbnail routes the server names are loaded. In the chat
page the cards sit under the reply, at a phone's width too, in a grid that
can't scroll sideways.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"
STATIC = REPO_ROOT / "web" / "static"

EVIL = '<img src=x onerror="alert(1)">'
MEDIA = {
    "state": "found", "query": {"words": "beach", "kind": None, "when": None}, "person": None,
    "count": 14, "more": False,
    "items": [
        {"id": 1, "kind": "image", "name": "a.jpg", "library_id": "core:pictures", "path": "Trips/a b.jpg",
         "thumb": "/api/images/thumb?library_id=core%3Apictures&path=Trips%2Fa+b.jpg&size=m",
         "taken_at": "2025-07-14T10:00:00+00:00", "date_from": None, "might_show": EVIL,
         "tags": ["beach"], "text": None, "at_s": None},
        {"id": 2, "kind": "video", "name": "party.mp4", "library_id": "core:pictures", "path": "party.mp4",
         "thumb": "/api/videos/poster?library_id=core%3Apictures&path=party.mp4",
         "taken_at": None, "date_from": "2025-08-02", "might_show": "A cake.", "tags": [], "text": None,
         "at_s": 42.0},
        {"id": 3, "kind": "video", "name": "long.mp4", "library_id": "core:videos", "path": "long.mp4",
         "thumb": "/api/videos/poster?library_id=core%3Avideos&path=long.mp4",
         "taken_at": None, "date_from": None, "might_show": None, "tags": [], "text": None, "at_s": 3725.0},
        {"id": 4, "kind": "image", "name": "odd.jpg", "library_id": "core:pictures", "path": "odd.jpg",
         "thumb": "https://elsewhere.example/odd.jpg", "taken_at": None, "date_from": None,
         "might_show": None, "tags": [], "text": None, "at_s": None},
    ],
}

SETUP = (
    "globalThis.API_BASE = '';\n"
    "globalThis.withDeviceToken = (u) => u + (u.includes('?') ? '&' : '?') + 'device_token=t';\n"
    "globalThis.__opened = []; window.open = (u, t, f) => { globalThis.__opened.push([u, t, f]); };\n"
    "globalThis.__overlay = [];\n"
    "globalThis.VideoOverlay = (p) => { globalThis.__overlay.push({ video: p.video, startSec: p.startSec }); return null; };\n"
)

CARDS_SCRIPT = r"""
h.render(); await h.settle(); h.rerender();
const all = h.tree();
const textOf = (el) => all.filter((x) => x === el || h.ancestors(x).includes(el)).map((x) => x.text)
  .filter(Boolean).join(' ');
const cards = h.findAll((el) => el.props && el.props.className === 'chat-media-card');
const imgs = h.findAll((el) => el.type === 'img').map((el) => el.props.src);
const unsafe = all.filter((el) => el.props && (el.props.dangerouslySetInnerHTML || el.props.onerror || el.props.onError === 'alert(1)'))
  .filter((el) => el.props.dangerouslySetInnerHTML).length;
const imgsWithEvil = all.filter((el) => el.type === 'img' && String(el.props.src || '').includes('alert')).length;
const badges = h.findAll((el) => el.props && el.props.className === 'mono chat-media-badge').map((el) => textOf(el));
const captions = h.findAll((el) => el.props && el.props.className === 'chat-media-caption').map((el) => textOf(el));
const labels = cards.map((el) => el.props['aria-label']);
const more = h.find((el) => el.props && el.props.className === 'chat-media-more');
const emptyTiles = h.findAll((el) => el.props && el.props.className === 'chat-media-thumb chat-media-thumb-empty').length;
const types = cards.map((el) => el.type + ':' + el.props.type);
await cards[0].props.onClick(); await h.settle(); h.rerender();
const afterPhoto = h.global('__opened').slice();
const cards2 = h.findAll((el) => el.props && el.props.className === 'chat-media-card');
await cards2[1].props.onClick(); await h.settle(); h.rerender();
return { n: cards.length, imgs, unsafe, imgsWithEvil, badges, captions, labels, types, emptyTiles,
         more: more ? textOf(more) : null, opened: afterPhoto, overlay: h.global('__overlay').slice(-1)[0] || null };
"""


def _run(scen: dict) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps({"d": scen})],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["d"]
    assert "__harness_error" not in out, out.get("__harness_error")
    return out


@pytest.fixture(scope="module")
def cards() -> dict:
    return _run({"files": ["web/static/components.jsx", "web/static/chat_media.jsx"], "component": "ChatMedia.Cards",
                 "props": {"media": MEDIA}, "setup": SETUP, "script": CARDS_SCRIPT})


def test_one_card_per_item_each_a_button(cards):
    assert cards["n"] == 4
    assert cards["types"] == ["button:button"] * 4
    assert cards["labels"] == ["open a.jpg", "play party.mp4 from 0:42", "play long.mp4 from 1:02:05", "open odd.jpg"]


def test_a_caption_that_is_markup_stays_text(cards):
    assert cards["captions"][0] == "Might show: " + EVIL
    assert cards["captions"][1] == "Might show: A cake."
    assert len(cards["captions"]) == 2          # no caption, no line
    assert cards["unsafe"] == 0 and cards["imgsWithEvil"] == 0


def test_videos_wear_a_time_badge(cards):
    assert cards["badges"] == ["0:42", "1:02:05"]


def test_thumbnails_ride_the_household_token_and_only_the_servers_routes(cards):
    assert cards["imgs"] == [
        "/api/images/thumb?library_id=core%3Apictures&path=Trips%2Fa+b.jpg&size=m&device_token=t",
        "/api/videos/poster?library_id=core%3Apictures&path=party.mp4&device_token=t",
        "/api/videos/poster?library_id=core%3Avideos&path=long.mp4&device_token=t",
    ]
    assert cards["emptyTiles"] == 1             # the outside URL is never loaded


def test_a_photo_opens_the_picture_and_a_video_plays_at_its_moment(cards):
    assert cards["opened"] == [["/api/images/raw?library_id=core%3Apictures&path=Trips%2Fa%20b.jpg&device_token=t",
                                "_blank", "noopener"]]
    ov = cards["overlay"]
    assert ov["startSec"] == 42
    assert ov["video"]["library_id"] == "core:pictures" and ov["video"]["rel"] == "party.mp4"


def test_the_rest_point_to_the_photos_page(cards):
    assert cards["more"] == "14 found · the rest are on the Photos page"


# ─── inside the chat page, at a phone's width ────────────────────────────

FILES = ["web/static/components.jsx", "web/static/chat_markdown.js", "web/static/chat_details.js",
         "web/static/chat_documents.jsx", "web/static/chat_lookups.jsx", "web/static/chat_drafts.jsx",
         "web/static/chat_media.jsx", "web/static/chat.jsx"]
THREAD = {"id": 11, "title": "Beach", "archived": False, "created_at": "2026-11-22T08:00:00Z",
          "updated_at": "2026-11-22T08:00:00Z", "message_count": 2, "last_snippet": "x"}


def _msg(i, role, content, media=None):
    return {"id": i, "role": role, "content": content, "images": None, "documents": None, "doc": None,
            "model": None, "error": None, "created_at": "2026-11-22T08:00:0%dZ" % i, "media": media,
            "stats": {"model_role": "media", "media": media} if media else None}


PAGE_API = {
    "GET /api/chat/threads": {"threads": [THREAD]},
    "GET /api/chat/models": {"installed": [], "default_model": "m", "vision_model": "v", "vision_installed": True},
    "GET /api/chat/threads/11/messages": {"messages": [
        _msg(1, "user", "show me photos of the beach"),
        _msg(2, "assistant", "I found 14 photos that might show the beach. They're on the Photos page.", MEDIA)]},
    "GET /api/chat/threads/11/drafts": {"drafts": []},
}

PAGE_SCRIPT = r"""
const all = () => h.tree();
const textOf = (el) => all().filter((x) => x === el || h.ancestors(x).includes(el)).map((x) => x.text)
  .filter(Boolean).join(' ');
h.render(); await h.settle(); h.rerender();
if (h.find((el) => el.type === 'button' && textOf(el).includes('your chats'))) {
  await h.click((el) => el.type === 'button' && textOf(el).includes('your chats'));
}
await h.click((el) => el.type === 'div' && typeof el.props.onClick === 'function' && textOf(el).includes('Beach'));
await h.settle(); h.rerender(); await h.settle(); h.rerender();
const grid = h.find((el) => el.props && el.props.className === 'chat-media');
const bubble = grid ? h.ancestors(grid)[0] : null;
return { cards: h.findAll((el) => el.props && el.props.className === 'chat-media-card').length,
         reply: !!h.find((el) => el.props && el.props.className === 'chat-md' && el.props.dangerouslySetInnerHTML
           && String(el.props.dangerouslySetInnerHTML.__html).includes('I found 14 photos')),
         flex: bubble && bubble.props.style ? bubble.props.style.flex : null, text: h.text().join(" ").slice(0, 1500) };
"""


def _page_setup() -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m
    return ("window.innerWidth = 390;\n" + SETUP
            + "globalThis.useDebouncedValue = (v) => v;\nglobalThis.deviceBlockReason = () => null;\n"
            "globalThis.liveRelTime = () => 'now';\n"
            "globalThis.apiFetchRaw = () => new Promise(() => {});\n"
            "globalThis.callerDeviceHeaders = () => ({});\n" + m.group(0))


def test_the_cards_sit_under_the_reply_on_a_phone():
    out = _run({"files": FILES, "component": "ChatPage", "setup": _page_setup(), "api": PAGE_API,
                "script": PAGE_SCRIPT})
    assert out["reply"] and out["cards"] == 4, out["text"]
    assert out["flex"] == 1                      # the reply takes the row, so the grid has room


def test_the_grid_cannot_scroll_sideways():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    grid = re.search(r"\.chat-media-grid \{([^}]*)\}", css).group(1)
    assert "repeat(auto-fill, minmax(min(132px, 42%), 1fr))" in grid and "min-width: 0" in grid
    card = re.search(r"\.chat-media-card \{([^}]*)\}", css).group(1)
    assert "min-width: 0" in card
    cap = re.search(r"\.chat-media-caption \{([^}]*)\}", css).group(1)
    assert "overflow-wrap: anywhere" in cap


def test_the_page_loads_the_cards_and_the_player_starts_at_a_moment():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="videos.jsx"') < html.index('src="chat_media.jsx"') < html.index('src="chat.jsx"')
    videos = (STATIC / "videos.jsx").read_text(encoding="utf-8")
    assert "const VideoOverlay = ({ video, resumeSec, startSec, onClose, onInfo })" in videos
    assert "if (!el.duration || at < el.duration) el.currentTime = at;" in videos
    src = (STATIC / "chat_media.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
