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
* "poll now" names each show whose feed couldn't be read, with the
  server's plain-words reason (POST /api/podcasts/poll ``failures``), and
  an answer without ``failures`` (an older server) toasts as before.
* The show's episode drawer: published date (relative when recent, plain
  otherwise), length, the listener's progress, download state, chapters, a
  plain-text description that expands on click, and the show's own info
  line. The clock is fixed (Thu 12 Nov 2026 15:00 UTC, TZ=UTC).
"""

from __future__ import annotations

import json
import os
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
POLL_FAILED_API = {**API, "POST /api/podcasts/poll": {
    "polled": 1, "new": 3, "downloaded": 0, "evicted": 0, "failed": 1,
    "failures": [{"subscription_id": 7, "title": "The Big Show",
                  "reason": "too_large", "message": "feed too large"}]}}
POLL_OLD_API = {**API, "POST /api/podcasts/poll": {
    "polled": 2, "new": 1, "downloaded": 2, "evicted": 0}}
POLL_SCRIPT = r"""
  h.render();
  await h.settle();
  await h.click({ type: 'button', text: 'poll now' });
  await h.settle();
  return { texts: h.text() };
"""

BASE_SETUP = "globalThis.ListeningAsSelector = () => null;"
EPISODES = [
    {"id": 11, "title": "Today one", "published_at": "2026-11-12T09:05:00Z", "duration_sec": 2280,
     "chapters": [{"t": 0}] * 12, "download_status": "downloaded", "has_file": True,
     "position_sec": None,
     "description": "<p>Hello &amp; <b>welcome</b> &lt;b&gt;</p><script>alert(1)</script><p>Second para</p>"},
    {"id": 12, "title": "Yesterday one", "published_at": "2026-11-11T20:00:00Z", "duration_sec": 4320,
     "chapters": None, "download_status": "downloaded", "has_file": True, "position_sec": 2880,
     "description": None},
    {"id": 13, "title": "Midweek", "published_at": "2026-11-09T08:00:00Z", "duration_sec": 3600,
     "chapters": [{"t": 0}], "download_status": "skipped", "has_file": False, "position_sec": 3590,
     "description": ""},
    {"id": 14, "title": "Older this year", "published_at": "2026-10-12T14:05:00Z", "duration_sec": 20,
     "chapters": [], "download_status": "downloading", "has_file": False, "position_sec": 0,
     "description": None},
    {"id": 15, "title": "Last year", "published_at": "2025-03-04T10:00:00Z", "duration_sec": 3600,
     "chapters": [], "download_status": "failed", "has_file": False, "position_sec": 100,
     "description": None},
    {"id": 16, "title": "Old server row", "published_at": None, "duration_sec": None,
     "chapters": None, "download_status": "pending", "has_file": False, "description": None},
]
DRAWER_API = {
    "GET /api/podcasts/subscriptions": [{
        "id": 1, "title": "Served", "feed_url": "https://f.example/1.xml", "artwork": None,
        "episode_count": 6, "downloaded_count": 2,
        "description": "<p>A show &amp; its <i>blurb</i></p>",
        "last_polled_at": "2026-11-12T13:00:00Z"}],
    "GET /api/podcasts/subscriptions/1/episodes": {"items": EPISODES, "total": 6, "has_more": False},
}
DRAWER_SETUP = BASE_SETUP + r"""
const RealDate = Date;
globalThis.Date = class extends RealDate {
  constructor(...a) { if (a.length === 0) super('2026-11-12T15:00:00Z'); else super(...a); }
  static now() { return new RealDate('2026-11-12T15:00:00Z').getTime(); }
};
globalThis.usePlayback = () => ({ available: false, listenerPersonId: 7,
  spoken: { clientId: () => 'dev 1' } });
"""
DRAWER_OPEN = r"""
  h.render();
  await h.settle();
  h.rerender();
  await h.click((el) => el.type === 'div' && el.props.onClick && h.ancestors(el).length < 6
    && el.props.style && el.props.style.cursor === 'pointer');
  await h.settle();
"""
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
    "drawer": {
        "files": FILES, "component": "PodcastsPage", "api": DRAWER_API, "setup": DRAWER_SETUP,
        "script": DRAWER_OPEN + r"""
          const rows = h.findAll((el) => el.type === 'div' && el.props.style
            && el.props.style.borderBottom && el.props.style.padding === '10px 16px');
          const metaOf = (row) => h.findAll((el) => el.type === 'div' && el.props.className === 'mono'
            && h.ancestors(el).includes(row));
          const out = rows.map((r) => ({
            texts: h.findAll((el) => h.ancestors(el).includes(r) && el.text).map((el) => el.text),
            titles: h.findAll((el) => h.ancestors(el).includes(r) && el.props.title).map((el) => el.props.title),
            bar: h.findAll((el) => h.ancestors(el).includes(r) && el.props.style
              && el.props.style.height === '100%').map((el) => el.props.style.width),
          }));
          const desc = h.find({ type: 'div', title: 'show more', nth: 1 });
          const before = desc ? desc.text : null;
          if (desc) await h.click({ type: 'div', title: 'show more', nth: 1 });
          const after = h.findAll((el) => el.props.title === 'show less')
            .map((el) => ({ text: el.text, white: el.props.style.whiteSpace, clamp: el.props.style.WebkitLineClamp }));
          const clampedBefore = desc ? desc.props.style.WebkitLineClamp : null;
          return { out, before, after, clampedBefore, calls: h.hookCalls, texts: h.text() };
        """,
    },
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
    "drawer_plain": {
        "files": FILES, "component": "PodcastsPage", "api": DRAWER_API,
        "setup": DRAWER_SETUP.replace("globalThis.usePlayback", "globalThis.__unused"),
        "script": DRAWER_OPEN + r"""
          return { calls: h.hookCalls, texts: h.text(),
                   dots: h.findAll((el) => el.props.title === 'not played yet').length };
        """,
    },
    "poll_failed": {
        "files": FILES, "component": "PodcastsPage", "api": POLL_FAILED_API,
        "setup": BASE_SETUP, "script": POLL_SCRIPT,
    },
    "poll_old": {
        "files": FILES, "component": "PodcastsPage", "api": POLL_OLD_API,
        "setup": BASE_SETUP, "script": POLL_SCRIPT,
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        env={**os.environ, "TZ": "UTC"},
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


def _toast(texts: list[str]) -> str:
    hits = [t for t in texts if t.startswith("Polled")]
    assert len(hits) == 1, texts
    return hits[0]


def test_poll_now_names_a_show_that_could_not_be_read(driven) -> None:
    assert _toast(driven["poll_failed"]["texts"]) == (
        "Polled — 0 downloaded, 3 new. "
        "1 show couldn't be read: The Big Show — feed too large"
    )


def test_poll_now_from_an_older_server_toasts_the_counts(driven) -> None:
    assert _toast(driven["poll_old"]["texts"]) == "Polled — 2 downloaded, 1 new"


def _row(r: dict, i: int) -> list[str]:
    return r["out"][i]["texts"]


def test_drawer_rows_show_date_length_progress_and_download(driven) -> None:
    r = driven["drawer"]
    assert [row["texts"][1:] for row in r["out"]][:5] == [
        ["today", "·", "38 min", "·", "12 chapters", "·", "downloaded",
         "Hello & welcome <b>\nSecond para"],
        ["yesterday", "·", "1 h 12 min", "·", "24 min left", "·", "downloaded"],
        ["3 days ago", "·", "1 h", "·", "played", "·", "1 chapter", "·", "not downloaded"],
        ["12 Oct", "·", "<1 min", "·", "downloading"],     # this year: no year
        ["4 Mar 2025", "·", "1 h", "·", "58 min left", "·", "download failed"],
    ]
    # An older server's row (no position_sec, no date) shows only what it has.
    assert _row(r, 5) == ["Old server row", "not downloaded"]


def test_drawer_tooltip_has_the_full_date_time(driven) -> None:
    titles = [t for row in driven["drawer"]["out"] for t in row["titles"]]
    assert "Thu 12 Nov 2026, 09:05" in titles and "Tue 4 Mar 2025, 10:00" in titles


def test_drawer_marks_unplayed_and_draws_a_thin_bar_for_partial(driven) -> None:
    out = driven["drawer"]["out"]
    assert [("not played yet" in row["titles"]) for row in out] == [True, False, False, True, False, False]
    assert [row["bar"] for row in out] == [[], ["67%"], [], [], ["3%"], []]


def test_drawer_description_is_plain_text_and_expands(driven) -> None:
    r = driven["drawer"]
    assert r["before"] == "Hello & welcome <b>\nSecond para"      # tags gone, entities decoded
    assert not any("alert" in t or "<script" in t or "<p>" in t for t in r["texts"])
    assert r["clampedBefore"] == 2
    assert [a["white"] for a in r["after"]] == ["pre-wrap"] and "clamp" not in r["after"][0]


def test_drawer_header_has_the_show_info(driven) -> None:
    texts = driven["drawer"]["texts"]
    assert "6 episodes · updated 2h ago" in texts
    assert "A show & its blurb" in texts


def test_drawer_asks_for_the_listeners_place(driven) -> None:
    assert driven["drawer"]["calls"][-1] ==         "/api/podcasts/subscriptions/1/episodes?device_id=dev%201&person_id=7"


def test_drawer_without_a_listener_shows_no_progress(driven) -> None:
    r = driven["drawer_plain"]
    assert r["calls"][-1] == "/api/podcasts/subscriptions/1/episodes"
    assert r["dots"] == 2          # the table still says position_sec null for two rows
