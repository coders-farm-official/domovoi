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

import hashlib
import json
import re
import shutil
import subprocess
from email.utils import formatdate
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.backend.main import app
from web.backend.static_cache import (
    BUNDLE_GLOBAL,
    BUNDLE_ROUTE,
    IMMUTABLE,
    REVALIDATE,
    VERSION_PARAM,
    current_bundle,
    names_a_page,
    stamped_page,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
SW_PATH = STATIC / "sw.js"
FETCH_HARNESS = Path(__file__).with_name("sw_fetch_harness.js")


def _starlette_stat_etag(path: Path) -> str:
    """The ETag ``StaticFiles`` derives, which is the one every browser
    warmed under the pre-fix regime is holding on the day this ships:
    ``md5(f"{mtime}-{size}")``, i.e. a description of the FILE and not of
    the rewritten bytes this mount actually sends."""
    st = path.stat()
    base = f"{st.st_mtime}-{st.st_size}".encode()
    return '"%s"' % hashlib.md5(base, usedforsecurity=False).hexdigest()


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
    """The cheap steady state: a warm browser asks for the page and gets no
    body back. The page's ETag is computed over the REWRITTEN body, because
    the same index.html produces different bytes when a sibling asset
    changes. The matching hazard — a 304 against Starlette's STAT ETag — is
    the case below; this one is the path that was always correct."""
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


# ═══ The 304 that ate the deploy ═════════════════════════════════════

# The whole bug, in the shape a browser presents it: a release does not
# change index.html, so the page keeps its size and its mtime, so
# Starlette's stat ETag is exactly the one a pre-fix browser stored. If
# that is answered 304, the browser keeps the page with no ?v= on
# anything — and every asset URL in it is one it already has cached. The
# versioned HTML never arrives because the HTML carrying it never
# arrives, on the first reload or the tenth. Measured in a real browser:
# functional-testing/plan-20260922/reach-real-20260925/reach-real-1.json
# (lane T1 step 6) against reach-real-2.json (the unfixed control).


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_a_conditional_get_carrying_starlettes_stat_etag_gets_the_page(client, path):
    """THE regression. A browser holding the pre-fix page asks with the
    stat ETag; it must be answered with the rewritten page, not a 304."""
    r = client.get(path, headers={"If-None-Match": _starlette_stat_etag(STATIC / "index.html")})
    assert r.status_code == 200, "the page was 304'd against a stat ETag"
    assert r.headers.get("cache-control") == REVALIDATE
    assert len(_stamped_urls(r.text)) >= 30, "the page arrived without versioned URLs"


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_a_conditional_get_carrying_the_pages_last_modified_gets_the_page(client, path):
    """Same hazard through the other door. A browser that stored the page
    before ETags were interesting sends If-Modified-Since instead, and
    Starlette answers that from the same stat."""
    st = (STATIC / "index.html").stat()
    r = client.get(path, headers={"If-Modified-Since": formatdate(st.st_mtime, usegmt=True)})
    assert r.status_code == 200
    assert len(_stamped_urls(r.text)) >= 30


def test_an_assets_conditional_get_is_left_alone(client):
    """The conditionals are dropped for PAGES only. An asset's stat ETag
    does describe the bytes it sends, so its 304 is correct and is what
    keeps a warm reload to one round trip instead of thirty-five."""
    first = client.get("/files.jsx")
    assert first.status_code == 200
    again = client.get("/files.jsx", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_which_paths_count_as_a_page():
    """The predicate that decides it, asked before the file is looked up
    because the answer decides what Starlette is allowed to see. Directory
    paths count: html=True turns them into that directory's index.html."""
    assert names_a_page("") and names_a_page(".") and names_a_page("/")
    assert names_a_page("manual/") and names_a_page("index.html")
    assert names_a_page("sub/page.HTM")
    assert not names_a_page("files.jsx")
    assert not names_a_page("vendor/react/react.development.js")
    assert not names_a_page("styles.css")


def test_the_request_itself_is_not_mutated(client):
    """The scope belongs to the server and is read again after this mount
    answers. Stripping conditionals in place would rewrite the request in
    the access log and in every middleware above it."""
    from web.backend.static_cache import _without_conditionals

    scope = {"headers": [(b"if-none-match", b'"x"'), (b"accept", b"text/html")]}
    out = _without_conditionals(scope)
    assert out["headers"] == [(b"accept", b"text/html")]
    assert scope["headers"] == [(b"if-none-match", b'"x"'), (b"accept", b"text/html")]


# ═══ The build id, and the tab that was already open ═════════════════

# The one staleness no cache header reaches. Headers govern the NEXT
# request; a tab that finished loading makes none, so a dashboard left
# open on the kitchen tablet runs last week's bundle until somebody
# closes it. Measured: reach-real-8.json — a deploy under an untouched
# tab, and 26 s later the tab says so.


def test_the_page_carries_the_build_id_it_was_served_as(client):
    """Injected by the mount, not maintained by hand: a hand-maintained
    version string is a thing somebody forgets, which is the exact failure
    this whole module exists to stop."""
    html = client.get("/").text
    m = re.search(BUNDLE_GLOBAL + r'="([0-9a-f]+)"', html)
    assert m, "the page carries no build id"
    assert html.index(BUNDLE_GLOBAL) < html.index("<script src="), (
        "the build id must be set before any script the page pulls runs"
    )


def test_the_box_reports_the_same_build_id_the_page_carries(client):
    """The comparison the browser makes. Two implementations of "what
    would we send" would drift, and the drift would read as permanent
    staleness — so both sides call stamped_page()."""
    html = client.get("/").text
    mine = re.search(BUNDLE_GLOBAL + r'="([0-9a-f]+)"', html).group(1)
    r = client.get(BUNDLE_ROUTE)
    assert r.status_code == 200
    assert r.json()["bundle"] == mine


def test_the_build_id_moves_when_an_asset_moves(tmp_path):
    """A release usually changes an asset and not the page, so a build id
    that only followed index.html would report "up to date" through every
    deploy that matters."""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text(
        '<html><head></head><body><script src="app.js"></script></body></html>',
        encoding="utf-8",
    )
    (root / "app.js").write_text("one", encoding="utf-8")
    first = current_bundle(root)
    assert first and first == current_bundle(root)
    (root / "app.js").write_text("two but longer", encoding="utf-8")
    assert current_bundle(root) != first


def test_a_page_with_no_head_still_serves(tmp_path):
    """No marker rather than no page: the browser-side check turns itself
    off when the global is absent, so a fragment served by this mount must
    not become an error."""
    root = tmp_path / "static"
    root.mkdir()
    page = root / "index.html"
    page.write_text("<p>no head here</p>", encoding="utf-8")
    body, bundle = stamped_page(page, root)
    assert body == b"<p>no head here</p>"
    assert bundle


def test_there_is_no_static_tree_to_report(tmp_path):
    """A backend-only deployment answers "no opinion" rather than 500ing
    the one route the dashboard polls."""
    assert current_bundle(tmp_path / "nothing-here") is None


# ═══ The second door: plugin assets ══════════════════════════════════


def test_a_plugin_asset_is_revalidated_too():
    """A plugin upgrade changes these files WITHOUT changing their names,
    and they were served as a bare FileResponse — ETag, Last-Modified and
    no freshness at all, which is the same disease the static mount was
    just taken out of. Measured: reach-real-7.json (no header: a second
    upgrade still reads 'B', transferSize 0) against reach-real-6.json
    (with it: 'C', 323 bytes)."""
    from web.backend.plugin_host import PLUGIN_ASSET_CACHE_CONTROL

    assert PLUGIN_ASSET_CACHE_CONTROL == REVALIDATE


def test_the_page_asks_for_plugin_scripts_past_its_own_cache():
    """The client half. The server header only governs a copy stored WITH
    it, so a browser warmed under the old regime is not reached by it at
    all — index.html's own fetch has to say so. Measured: reach-real-6.json
    step 3 (bare fetch, still 'A') against step 4 (this fetch, 'B')."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    loader = html.split("const loadPluginScripts", 1)[1].split("const bootstrap", 1)[0]
    assert "fetch(" in loader, "loadPluginScripts no longer fetches anything"
    assert "cache: 'no-cache'" in loader, (
        "loadPluginScripts is back to a default fetch; a plugin upgrade "
        "will not reach a warm browser"
    )


# ═══ The worker, driven rather than grepped ══════════════════════════


@pytest.fixture(scope="module")
def sw_fetch() -> dict:
    """web/static/sw.js's fetch handler, run for real in node against a
    fake Cache Storage and a fake network.

    Asserting this by grepping the file for identifiers was the wrong
    trade on the delivery mechanism for every future front-end change: a
    rename goes red for nothing, and inverting the logic inside the same
    identifiers stays green.
    """
    node = shutil.which("node")
    assert node, "node is required to run the service worker (see jsxcheck)"
    proc = subprocess.run(
        [node, str(FETCH_HARNESS), str(REPO_ROOT)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    out = json.loads(proc.stdout)
    assert "error" not in out, out["error"]
    return out["cases"]


def test_the_worker_asks_the_network_before_its_own_cache(sw_fetch):
    """Cache-first here is what let a deployed fix sit unread on the
    server while the operator reloaded and reloaded: a worker sits in
    FRONT of the HTTP cache, so it can hand back a stale file no matter
    what the server's headers say."""
    case = sw_fetch["network_beats_cache"]
    assert case["fetched"], "the network was never consulted"
    assert case["body"].startswith("NETWORK"), "the worker served its cached copy"


def test_the_worker_revalidates_an_unversioned_name(sw_fetch):
    """A worker that trusted the server's Cache-Control on an unversioned
    name would be as stale as the oldest server it ever spoke to, and this
    worker outlives deploys by design."""
    assert [f["cache"] for f in sw_fetch["unversioned"]["fetched"]] == ["no-cache"]


def test_a_versioned_url_costs_the_worker_nothing(sw_fetch):
    """The token IS the identity of the bytes, so a hit on that URL is by
    definition the file it names. Re-validating it would make the worker
    path more expensive than no worker at all."""
    assert [f["cache"] for f in sw_fetch["versioned"]["fetched"]] == ["default"]


def test_a_deploy_does_not_add_an_entry_to_the_shell_cache(sw_fetch):
    """One entry per shell file, not one per file per deploy. A versioned
    URL is new on every release that touches its file, and activate only
    deletes whole caches by NAME — so each release used to leave the
    previous copy of each changed asset behind forever, on a phone, for
    as long as the install lives. Measured in a browser too:
    reach-real-5.json holds at 39 entries across a deploy, with no two
    entries sharing a pathname."""
    case = sw_fetch["deploy_growth"]
    assert case["before_drawings"] == ["http://test/drawings.jsx?v=0d0000000000"]
    assert case["after_drawings"] == ["http://test/drawings.jsx?v=e011111111aa"], (
        "last release's copy of a changed file is still in the cache"
    )


def test_the_worker_revalidates_a_plugin_asset(sw_fetch):
    """'Network-first' in a worker only means it asks the network before
    its OWN cache; the browser's HTTP cache still sits between the two,
    and a plugin asset has no token in its URL to keep that honest."""
    assert [f["cache"] for f in sw_fetch["plugin_asset"]["fetched"]] == ["no-cache"]


def test_live_state_is_never_intercepted(sw_fetch):
    assert sw_fetch["api_not_intercepted"] is True


def test_the_dashboard_still_opens_with_the_box_switched_off(sw_fetch):
    """Network-first must not mean network-only: an offline dashboard is
    one of this file's two stated jobs and is not traded away for this."""
    assert "index.html" in sw_fetch["offline_navigation"]["body"]
    assert "people.jsx" in sw_fetch["offline_versioned_finds_plain"]["body"], (
        "offline, a versioned URL must still find the copy the install "
        "precached under the plain name"
    )


def test_an_offline_subresource_is_not_handed_a_page_of_html(sw_fetch):
    """A script tag answered with index.html fails louder and later than
    the offline it was trying to survive."""
    assert sw_fetch["offline_subresource"]["tag"] == "ERROR"
