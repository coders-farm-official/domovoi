"""A deploy reaches the browser — the dashboard's own files are not
served as if they never change.

This module is about the DELIVERY MECHANISM for every front-end change in
this product, and it exists because that mechanism was broken in a way no
feature test could see. ``StaticFiles`` sends ``ETag`` and
``Last-Modified`` and NO ``Cache-Control``, so a response carries no
explicit freshness and the browser falls back to the RFC 9111 heuristic —
about 10% of the age since ``Last-Modified``. On a checkout weeks old that
licenses a browser to reuse ``files.jsx`` for DAYS without asking the
server anything. Deploy a fix, reload, and the old file is what runs.

The fix that does NOT work, asserted against below so nobody
"simplifies" back to it: ``Cache-Control: no-cache`` alone. A browser
only learns a header by making a request, and the entire problem is that
it does not make one. Measured in a real headless Chrome — warm cache,
one ordinary reload after a deploy: header fix only -> old bundle; no fix
at all -> old bundle. The same answer.

What works is changing the URL, because a URL the browser has never seen
has nothing to serve from cache. The page is rewritten as it is served so
every same-origin asset carries ``?v=<that file's token>``, and the page
itself is ``no-cache`` — one conditional GET, and it is the one request a
reload always makes anyway.

Both layers are pinned here:

* the HTTP layer — :mod:`web.backend.static_cache`, asserted as behaviour
  on the real mount;
* the service worker — ``web/static/sw.js``, which sits in FRONT of the
  HTTP cache and can hand a browser a stale file no matter what the
  server says. Its source is asserted below, because a worker that
  answers from its own cache without revalidating makes the server's
  headers irrelevant, and that is what it used to do.

DB-free: the static mount needs no database, and these are the GETs a
browser makes before it holds any credential at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.backend.main import app
from web.backend.static_cache import IMMUTABLE, REVALIDATE, VERSION_PARAM

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
SW_PATH = STATIC / "sw.js"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _stamped_urls(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="([^"]+\?' + VERSION_PARAM + r'=[0-9a-f]+)"', html)


# ═══ The page ════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_page_is_always_revalidated(client, path):
    """The one thing that must be re-read on every load, and the one thing
    a browser always does re-read: an explicit reload validates the main
    resource whatever its freshness says."""
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers.get("cache-control") == REVALIDATE


def test_the_page_stamps_every_asset_it_pulls(client):
    """The mechanism. ``index.html`` references about 35 local files with
    no version of any kind; each comes back carrying its own token, so a
    deploy that changes a file changes that file's URL."""
    html = client.get("/").text
    stamped = _stamped_urls(html)
    assert len(stamped) >= 30, f"only {len(stamped)} assets stamped"
    names = {u.split("?")[0].split("/")[-1] for u in stamped}
    # A page script, a stylesheet, a vendored bundle and the manifest —
    # one of each kind that has gone stale on somebody at least once.
    for expected in (
        "files.jsx",
        "data.js",
        "styles.css",
        "babel.min.js",
        "manifest.webmanifest",
    ):
        assert expected in names, f"{expected} was not stamped"


def test_the_rewrite_does_not_touch_what_is_not_ours(client):
    """Fragments, other origins and data URIs stay exactly as the page
    wrote them, and so does anything that does not resolve to a file this
    mount serves. The page's own inline JavaScript is untouched as well:
    only opening ``<script>``/``<link>``/``<img>`` tags are rewritten,
    never script bodies, because matching ``src=`` across the whole
    document would corrupt a string in the plugin bootstrap."""
    html = client.get("/").text
    assert 'href="#' in html, "a fragment link should still read as one"
    assert not any(u.startswith("#") for u in _stamped_urls(html))
    assert "window.DomovoiCore" in html


def test_the_pages_etag_follows_the_bytes_it_actually_sends(client):
    """The page's ETag is computed over the REWRITTEN body. Starlette's own
    304 works off the file's stat, which no longer describes what is sent —
    the same index.html produces different bytes when a sibling asset
    changes, and a 304 against that stale ETag would pin the browser to the
    old asset URLs: the bug in a different hat."""
    r = client.get("/")
    etag = r.headers.get("etag")
    assert etag
    again = client.get("/", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers.get("cache-control") == REVALIDATE


# ═══ The assets ══════════════════════════════════════════════════════

def test_an_asset_at_its_current_token_costs_no_round_trip(client):
    """The answer to "won't that be a revalidation storm?". A versioned URL
    cannot change meaning, so it is immutable for a year: a warm reload
    with nothing deployed makes ONE request, for the page."""
    url = next(u for u in _stamped_urls(client.get("/").text) if "files.jsx" in u)
    r = client.get("/" + url.lstrip("/"))
    assert r.status_code == 200
    assert r.headers.get("cache-control") == IMMUTABLE


@pytest.mark.parametrize("url", ["/files.jsx", "/files.jsx?v=deadbeefcafe"])
def test_an_asset_at_a_stale_or_absent_token_is_revalidated(client, url):
    """Everything not asked for at its current token falls back to the
    conservative answer: a font referenced from inside a stylesheet, a URL
    somebody typed, a token from three deploys ago."""
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers.get("cache-control") == REVALIDATE


def test_the_token_changes_when_and_only_when_the_file_does(tmp_path):
    """Per FILE, not per tree. ``web/static`` is 12 MB, 11 of it vendored
    bundles that change when somebody re-vendors them and never otherwise;
    a tree-wide token would change whenever anything changed and
    re-download all of it on every release."""
    from web.backend.static_cache import asset_token

    victim = tmp_path / "thing.js"
    victim.write_text("one", encoding="utf-8")
    first = asset_token(victim)
    assert first == asset_token(victim)
    victim.write_text("two but longer", encoding="utf-8")
    assert asset_token(victim) != first
    assert asset_token(tmp_path / "missing.js") is None


def test_a_static_404_is_not_remembered_as_missing(client):
    """The next deploy is often exactly what adds it."""
    r = client.get("/not-a-real-asset-9a83f.js")
    assert r.status_code == 404
    assert "max-age" not in (r.headers.get("cache-control") or "")


# ═══ The other layer: the service worker ═════════════════════════════

@pytest.fixture(scope="module")
def sw_source() -> str:
    return SW_PATH.read_text(encoding="utf-8")


def test_the_worker_serves_the_shell_network_first(sw_source):
    """The worker answers BEFORE the HTTP cache is consulted, so a
    cache-first worker makes the server's headers moot. The old branch was
    ``caches.match(req).then((hit) => hit || fetch(req))`` with no
    revalidation anywhere: a warm browser kept the bundle it installed with
    until somebody renamed the cache by hand."""
    parts = sw_source.split("if (url.origin === self.location.origin) {", 1)
    assert len(parts) == 2, "the same-origin branch went missing"
    branch = parts[1].split("return;", 1)[0]
    assert "shellNetworkFirst(req)" in branch
    assert "caches.match" not in branch, (
        "the shell is being answered from the worker's cache before the "
        "network is consulted again"
    )


def test_the_worker_revalidates_instead_of_trusting_the_server_headers(sw_source):
    """``cache: 'no-cache'`` on the worker's own fetch. The worker outlives
    deploys by design, so it must not be as stale as the oldest server it
    ever spoke to."""
    assert "function fetchFresh(req)" in sw_source
    assert "cache: 'no-cache'" in sw_source


def test_the_worker_still_answers_when_the_box_is_not_there(sw_source):
    """Network-first must not mean network-only: an offline dashboard is
    one of this file's two stated jobs and is not being traded away. The
    lookup ignores the search string so a versioned URL still finds the
    copy the install precached under the plain name."""
    assert "SHELL_NETWORK_TIMEOUT_MS" in sw_source
    assert "ignoreSearch: true" in sw_source
    assert "caches.match('/index.html')" in sw_source


def test_only_a_navigation_falls_back_to_the_page(sw_source):
    """A subresource that falls back to index.html gets a page of HTML
    where a script tag was expected, which fails louder and later than the
    offline it was trying to survive."""
    assert "req.mode === 'navigate'" in sw_source


def test_the_shell_cache_name_moved_for_this_release(sw_source):
    """Browsers that installed the OLD worker hold a cache filled under the
    OLD, cache-first rules. Renaming the cache is what makes them throw
    those entries away on the upgrade instead of serving them once more."""
    name = re.search(r"const SHELL_CACHE = '([^']+)'", sw_source)
    assert name, "SHELL_CACHE went missing"
    assert name.group(1) != "domovoi-shell-v3", (
        "the cache name is the one the pre-fix worker filled; a browser "
        "holding it keeps the pre-fix bundle through the upgrade"
    )
