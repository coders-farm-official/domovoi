"""B11 + the internet answer on the Podcasts page (web/static/podcasts.jsx).

Driven outside a browser through domovoi/tests/jsx_interact_harness.js:

* PodArt draws an ``<img>`` only for a server path (``/api/...``): a
  publisher's URL — what an older server answered — or a protocol-relative
  ``//host`` gets the placeholder, so the page never sends the browser to a
  publisher. Discovery thumbnails follow the same rule.
* Under the answer "never" (components.jsx ``useInternetPolicy``, which
  reads ``/api/config``; on a tree without the helpers the ``setup`` stubs
  stand in for them): "poll now" and the directory search are greyed with
  the "needs internet" title, the subscribe dialog opens on "by RSS URL",
  and that tab still works.
* With no internet helpers at all the page renders as before.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/podcasts.jsx"]

SUBS = [
    {"id": 1, "title": "Served", "feed_url": "https://f.example/1.xml",
     "artwork": "/api/podcasts/subscriptions/1/artwork?v=0a1b2c3d",
     "episode_count": 3, "downloaded_count": 1},
    {"id": 2, "title": "Remote", "feed_url": "https://f.example/2.xml",
     "artwork": "https://cdn.publisher.example/art.jpg", "episode_count": 0, "downloaded_count": 0},
    {"id": 3, "title": "Sneaky", "feed_url": "https://f.example/3.xml",
     "artwork": "//cdn.publisher.example/art.jpg", "episode_count": 0, "downloaded_count": 0},
    {"id": 4, "title": "None", "feed_url": "https://f.example/4.xml",
     "artwork": None, "episode_count": 0, "downloaded_count": 0},
]
DISCOVER = [
    {"title": "Thumb", "author": "A", "feed_url": "https://f.example/t.xml",
     "artwork": "/api/podcasts/discover/artwork/0123456789abcdef0123456789abcdef"},
    {"title": "Old server", "author": "B", "feed_url": "https://f.example/o.xml",
     "artwork": "https://is1-ssl.mzstatic.com/image/100x100bb.jpg"},
]
API = {"GET /api/podcasts/subscriptions": SUBS, "GET /api/podcasts/discover?q=npr": DISCOVER}
NEVER_API = {**API, "GET /api/config": {"internet_access": "never"}}

BASE_SETUP = "globalThis.ListeningAsSelector = () => null;"
# Stand-ins for the components.jsx internet helpers, for a tree that doesn't
# have them yet; where components.jsx declares them, its own (which read
# GET /api/config from the table) take precedence.
NEVER_SETUP = BASE_SETUP + r"""
globalThis.NEEDS_INTERNET_TEXT = 'needs internet · Settings → Internet';
globalThis.INTERNET_OFF_MESSAGE = 'internet access is turned off for this box (Settings → Internet)';
globalThis.useInternetPolicy = () => ({ access: 'never', off: true, unanswered: false, loaded: true, refresh() {} });
globalThis.isInternetOffError = (e) => !!(e && e.status === 409);
globalThis.NeedsInternetNote = () => React.createElement('span', null, 'needs internet · Settings → Internet');
"""

IMG_SRCS = "h.findAll({ type: 'img' }).map((el) => el.props.src)"

SCENARIOS = {
    "art": {
        "files": FILES, "component": "PodcastsPage", "api": API, "setup": BASE_SETUP,
        "script": r"""
          h.render();
          await h.settle();
          h.rerender();
          const listImgs = __IMGS__;
          const poll = h.plain(h.find({ type: 'button', text: 'poll now' }));
          await h.click({ type: 'button', text: 'subscribe' });
          await h.type({ placeholder: 'show name…' }, 'npr');
          await h.click({ type: 'button', text: 'search', nth: 1 });
          return { listImgs, poll, allImgs: __IMGS__, texts: h.text() };
        """.replace("__IMGS__", IMG_SRCS),
    },
    "never": {
        "files": FILES, "component": "PodcastsPage", "api": NEVER_API, "setup": NEVER_SETUP,
        "script": r"""
          h.render();
          await h.settle();
          h.rerender();
          await h.settle();
          const poll = h.plain(h.find({ type: 'button', text: 'poll now' }));
          const note = h.text().some((t) => t.includes('needs internet'));
          await h.click({ type: 'button', text: 'subscribe' });
          const urlField = !!h.find({ placeholder: 'https://…/feed.xml' });
          await h.click({ type: 'button', text: 'search' });
          const search = h.plain(h.find({ type: 'button', text: 'search', nth: 1 }));
          const query = h.plain(h.find({ placeholder: 'show name…' }));
          await h.click({ type: 'button', text: 'by RSS URL' });
          await h.type({ placeholder: 'https://…/feed.xml' }, 'https://f.example/new.xml');
          await h.click({ type: 'button', text: 'subscribe', nth: 1 });
          return { poll, note, urlField, search, query, calls: h.calls };
        """,
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_only_server_paths_are_drawn(driven) -> None:
    r = driven["art"]
    assert r["listImgs"] == ["/api/podcasts/subscriptions/1/artwork?v=0a1b2c3d"]
    assert not any("publisher.example" in (src or "") for src in r["allImgs"])


def test_discovery_thumbnails_follow_the_same_rule(driven) -> None:
    r = driven["art"]
    assert "Thumb" in r["texts"] and "Old server" in r["texts"]
    assert "/api/podcasts/discover/artwork/0123456789abcdef0123456789abcdef" in r["allImgs"]
    assert not any("mzstatic" in (src or "") for src in r["allImgs"])


def test_without_the_internet_helpers_nothing_is_greyed(driven) -> None:
    assert not driven["art"]["poll"]["props"].get("disabled")


def test_under_never_the_online_controls_are_greyed(driven) -> None:
    r = driven["never"]
    assert r["poll"]["props"]["disabled"] is True
    assert r["poll"]["props"]["title"] == "needs internet · Settings → Internet"
    assert r["note"] is True
    assert r["urlField"] is True                     # the dialog opens on "by RSS URL"
    assert r["search"]["props"]["disabled"] is True
    assert r["query"]["props"]["disabled"] is True


def test_under_never_subscribing_by_url_still_posts(driven) -> None:
    posts = [c for c in driven["never"]["calls"] if c["method"] == "POST"]
    assert posts == [{"method": "POST", "path": "/api/podcasts/subscriptions",
                      "body": {"feed_url": "https://f.example/new.xml"}}]
