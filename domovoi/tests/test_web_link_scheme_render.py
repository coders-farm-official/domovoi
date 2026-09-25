"""Dashboard links from feed/server data render as hyperlinks only for
http(s) URLs (FE-1), checked outside a browser.

web/static/news.jsx (SavedFeed) and web/static/music.jsx (NPCard) are
compiled with the dashboard's own vendored Babel and rendered with the
plain-object React in domovoi/tests/jsx_render_harness.js, with
web/static/components.jsx preloaded so the shared ``webHref`` helper is the
real one. The assertions pin the behaviour the page must keep on its own,
independent of the server-side ingest gate:

* a story whose ``url`` is an http(s) link renders an ``<a href>``;
* a story whose ``url`` has any other scheme renders its title as plain
  text with no ``<a>`` at all — and the same for a now-playing
  ``source_url``, where the "open source" pill is simply not shown.

No DB, no ``requires_db`` — never skips. Needs ``node`` (the runtime the
JSX compile check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
COMPONENTS = "web/static/components.jsx"
NEWS = "web/static/news.jsx"
MUSIC = "web/static/music.jsx"

WEB_LINK = "https://news.example/story/1"
PLAIN_HTTP_LINK = "http://news.example/story/2"
ODD_SCHEME_LINKS = [
    "javascript:void(0)",
    "data:text/html;base64,AAAA",
    "intent://scan/#Intent;scheme=zxing;end",
    "tel:+15551234567",
    "file:///etc/hostname",
    "//news.example/protocol-relative",
]


def _story(i: int, url: str | None) -> dict:
    return {"id": i, "url": url, "title": f"Story {i}", "summary": None, "source": "Ex",
            "topic": "tech", "published_at": "2026-09-22T08:00:00Z", "fetched_at": None,
            "favorited": False, "read_at": None}


def _now_playing(source_url: str | None) -> dict:
    return {"room_id": "kitchen", "state": "play", "elapsed_sec": 10,
            "song": {"title": "t", "artist": "a", "duration_sec": 100},
            "source": "provider", "source_url": source_url}


NEWS_ITEMS = [_story(1, WEB_LINK), _story(2, PLAIN_HTTP_LINK), _story(3, None)] + [
    _story(10 + n, url) for n, url in enumerate(ODD_SCHEME_LINKS)
]

SCENARIOS = {
    "saved_feed": {
        "file": NEWS, "component": "SavedFeed", "preload": [COMPONENTS],
        "props": {"personId": 1, "items": NEWS_ITEMS}, "fnProps": ["onChanged", "fire"],
    },
    "np_web_source": {
        "file": MUSIC, "component": "NPCard", "preload": [COMPONENTS],
        "props": {"np": _now_playing("https://video.example/watch?v=1"), "tick": 0},
        "fnProps": ["onPlayRandom", "onPause", "onResume", "onSkip", "onStop", "onFavorite"],
    },
    "np_odd_source": {
        "file": MUSIC, "component": "NPCard", "preload": [COMPONENTS],
        "props": {"np": _now_playing("javascript:void(0)"), "tick": 0},
        "fnProps": ["onPlayRandom", "onPause", "onResume", "onSkip", "onStop", "onFavorite"],
    },
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _anchors(nodes: list[dict]) -> list[dict]:
    return [n for n in nodes if n["type"] == "a"]


def _texts(nodes: list[dict]) -> list[str]:
    return [n["text"] for n in nodes if n["text"]]


# ── news.jsx SavedFeed ────────────────────────────────────────────────

def test_saved_feed_links_only_http_and_https_stories(rendered):
    hrefs = [a["props"].get("href") for a in _anchors(rendered["saved_feed"])]
    assert hrefs == [WEB_LINK, PLAIN_HTTP_LINK]


def test_saved_feed_story_links_open_in_a_new_tab_without_an_opener(rendered):
    for a in _anchors(rendered["saved_feed"]):
        assert a["props"].get("target") == "_blank"
        assert "noopener" in a["props"].get("rel", "")


def test_saved_feed_still_shows_the_title_of_an_unlinked_story(rendered):
    texts = _texts(rendered["saved_feed"])
    for story in NEWS_ITEMS:
        assert story["title"] in texts


def test_saved_feed_renders_no_href_with_an_odd_scheme(rendered):
    rendered_hrefs = {a["props"].get("href") for a in _anchors(rendered["saved_feed"])}
    for url in ODD_SCHEME_LINKS:
        assert url not in rendered_hrefs
    # Belt and braces: nothing in the render carries such a value under any prop.
    for n in rendered["saved_feed"]:
        for v in n["props"].values():
            assert v not in ODD_SCHEME_LINKS


# ── music.jsx NPCard source pill ──────────────────────────────────────

def test_now_playing_source_pill_links_a_web_source(rendered):
    hrefs = [a["props"].get("href") for a in _anchors(rendered["np_web_source"])]
    assert hrefs == ["https://video.example/watch?v=1"]


def test_now_playing_source_pill_is_absent_for_an_odd_scheme(rendered):
    assert _anchors(rendered["np_odd_source"]) == []
