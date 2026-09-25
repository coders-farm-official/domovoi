"""How the dashboard's own files reach a browser.

This module is not a feature. It is the DELIVERY MECHANISM for every
front-end change anyone ever makes to this product, and it was silently
broken: ``StaticFiles`` sets ``ETag`` and ``Last-Modified`` and **no**
``Cache-Control`` at all, so a response carries no explicit freshness and
the browser falls back to the heuristic in RFC 9111 §4.2.2 — roughly 10%
of the age since ``Last-Modified``. A checkout six weeks old therefore
licenses a browser to reuse ``files.jsx`` for about four days **without
asking the server anything**. Deploy a fix, reload, and the old file is
still what runs; nothing in the UI says so, and the operator concludes the
fix did not work.

That was demonstrated on this dashboard: with the page open, the served
``drawings.jsx`` was replaced on disk (new bytes, new ``ETag``, new
``Last-Modified``) and a fresh navigation still executed the OLD file —
``performance.getEntriesByType('resource')`` reported
``{encodedBodySize: 17625, transferSize: 0}`` — while a cache-busted
``fetch()`` in the same breath returned the new bytes.

THE MISTAKE WORTH NOT REPEATING
-------------------------------

The obvious fix is ``Cache-Control: no-cache`` on the mount, and it is
NOT SUFFICIENT. That was settled by measurement, not argument, because it
is exactly the kind of thing that looks right and is not: a browser only
learns a header by making a request, and the whole problem is that it does
not make one. The copies it already holds were stored under the old,
header-less regime and stay heuristically fresh for days. Driven in a real
headless Chrome — warm cache, one ordinary reload after the deploy:

    header fix only  ->  VERSION-A   (transferSize 0; no request was made)
    no fix (control) ->  VERSION-A

The same answer. The header governs what happens after a browser next
asks, and nothing was making it ask.

WHAT ACTUALLY MAKES A DEPLOY ARRIVE
-----------------------------------

A URL the browser has never seen has nothing to serve from cache. So the
HTML is rewritten as it is served: every same-origin ``<script src>``,
``<link href>`` and ``<img src>`` is stamped with ``?v=<token>``, where the
token is that ONE file's size and mtime.

Per file, not per tree, and that is the whole difference between a cheap
deploy and an expensive one. ``web/static`` is 12 MB, 11 of it vendored
React/Babel/Excalidraw bundles that change when somebody deliberately
re-vendors them and at no other time. A tree-wide token would change
whenever any file changed and re-download all 11 MB on every release; a
per-file token changes the URL of exactly what was edited. Git only
rewrites files whose content actually changed, so the mtime it leaves
behind is a faithful "this one is different" signal.

The page itself is the one thing that must always be re-read, so it is
served ``no-cache``: stored, but never reused without asking. Once a
browser holds a copy carrying that header, EVERY navigation to it
revalidates — a reload, a bookmark, a typed address, a restored pinned
tab — and the release arrives on whichever of those the operator happens
to do first.

TWO THINGS THAT SENTENCE DOES NOT COVER, BOTH MEASURED
------------------------------------------------------

**1. Starlette answers the page before this class is consulted.** A
release almost never changes ``index.html`` — the whole point of the
rewrite above is that it does not have to — and ``git pull --ff-only``
rewrites only files whose content changed, so the page keeps its size and
its mtime. ``StaticFiles`` derives its ETag from exactly those two things
and returns a ``NotModifiedResponse`` from ``file_response()``, i.e.
BEFORE ``get_response`` here ever sees a body. A warm browser sends the
stat ETag it stored under the old regime, gets a 304 back, and keeps the
pre-fix page — the one with no ``?v=`` on anything, every URL of which it
already holds. The versioned HTML never arrives, on the first reload or
the tenth, because the HTML carrying it never arrives. So the conditional
headers are STRIPPED from the request before ``super()`` sees it whenever
the path names a page, and :meth:`_html_response` does its own comparison
against the bytes it is actually about to send. Assets keep their 304s
untouched: an asset's stat ETag does describe what it sends.

**2. A browser that does not ask cannot be told anything.** ``no-cache``
governs a copy stored WITH it. A copy stored under the old header-less
regime has no explicit freshness and falls to the RFC 9111 §4.2.2
heuristic, so an ordinary navigation to it — a bookmark, a pinned tab
restored at browser start, a home-screen icon — is answered out of the
browser's own cache with no request at all. Nothing a server sends can
reach a request that is never made. That is not a gap to engineer
around; it is the shape of the problem, and it splits the question in
two:

* **every release from now on** reaches any browser that has fetched the
  page once since this one, on any kind of navigation, because that fetch
  is what installs ``no-cache``;
* **this one upgrade** needs each browser to ask once. An ordinary reload
  (Ctrl-R / pull-to-refresh) is enough — no hard refresh, no clearing —
  or the heuristic window lapses on its own, which is about 10% of the
  gap between when that browser last fetched the page and when
  ``index.html`` was last written: hours on this box, not days.

That one-time cost is stated in the release notes and in
``docs/API_REFERENCE.md`` §3.22 rather than papered over. The dashboard
also notices it for itself from this release onward: the page goes out
carrying ``window.__DOMOVOI_BUNDLE__``, a digest of exactly the bytes
that were sent, and ``GET /api/bundle`` reports what the box would send
now. A tab whose marker no longer matches says so and offers a reload —
which catches the one case no cache header can ever reach, a tab that was
already open when the deploy landed. It cannot help a browser still
running a PRE-fix page, because the detector ships inside the page that
is not arriving. That is the honest limit of it.

THE COST, WEIGHED OUT LOUD
--------------------------

This runs on a Pi-class box serving a handful of browsers, so a
revalidation storm is a real thing to avoid: ``index.html`` pulls about 35
assets, and revalidating every one of them on every load would be 35
conditional GETs where today there are none.

It does not cost that. An asset requested at its CURRENT token is served
``immutable`` for a year, because a versioned URL genuinely cannot change
meaning — so a warm reload with nothing deployed makes ONE request, for
the page. A deploy changes the token of the files it touched, and those
arrive under URLs no cache has ever held. Anything reached without a
matching token — the page, a font referenced from inside a stylesheet, a
URL somebody typed — falls back to ``no-cache``: correct, and a 304 with
no body.

So: one conditional GET per page load, plus a full download of exactly
what changed, in exchange for a front-end fix that can be told apart from
a front-end fix that does not work. The "uniform ``no-cache``"
alternative was rejected because it is both slower AND, on a warm
browser, still wrong.

THE OTHER LAYER
---------------

``web/static/sw.js``. A service worker sits in FRONT of the HTTP cache and
can hand a browser a stale file without this module ever being consulted,
so the two have to agree, and both are needed:

* this module cannot reach a browser whose worker answers before the
  network is consulted — and that worker used to be cache-first with no
  revalidation at all;
* the worker cannot reach a browser where it never registers: a first
  load, a failed registration, or the ordinary case on Kamron's box, where
  the dashboard is served over plain HTTP at
  ``http://192.168.0.117:6369`` and a service worker therefore CANNOT
  register, because that is not a secure context. On that install this
  module is the only layer there is.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response

log = logging.getLogger(__name__)

# Store it, but revalidate before every use. ETag/Last-Modified make the
# revalidation a 304 with no body, so this costs a round trip and not a
# re-download. The answer for anything NOT asked for at its current token.
REVALIDATE = "no-cache"

# A versioned URL cannot change meaning: the token IS the identity of the
# bytes. Serve it for a year and let the next deploy mint a new URL.
IMMUTABLE = "public, max-age=31536000, immutable"

# The name the rest of the tree imports; the conservative answer, used
# wherever a version is not in play.
STATIC_CACHE_CONTROL = REVALIDATE

# The query parameter carrying a file's token.
VERSION_PARAM = "v"

# Only ``<script>``, ``<link>`` and ``<img>`` OPENING TAGS are rewritten,
# never the page's inline JavaScript: matching ``src=`` anywhere in the
# document would happily corrupt a string inside the plugin bootstrap.
_TAG_RE = re.compile(rb"<(?:script|link|img)\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    rb"""(?P<attr>\b(?:src|href)\s*=\s*)(?P<q>["'])(?P<url>[^"']*)(?P=q)""",
    re.IGNORECASE,
)

# A URL this module must not touch: another origin, a data/blob/mailto URI,
# a bare fragment, or one that already carries a query of its own.
_LEAVE_ALONE = re.compile(rb"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#|\?)")

_HTML_SUFFIXES = frozenset({".html", ".htm"})

# Where the page's own build id lands, and how the browser asks the box
# what it would serve now. The pair is what lets an ALREADY-OPEN tab
# notice it is running yesterday's dashboard — the one staleness no cache
# header can reach, because the tab has long since stopped asking.
BUNDLE_GLOBAL = "__DOMOVOI_BUNDLE__"
BUNDLE_ROUTE = "/api/bundle"

_HEAD_RE = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)

# Dropped from a PAGE request before Starlette sees it. Starlette's
# conditional machinery works off the file's stat, which does not describe
# the rewritten bytes this module sends, and a 304 against that stat ETag
# is the delivery bug wearing a different hat: the browser keeps the
# unversioned page and never learns a single new asset URL. ``If-Range``
# rides along because a range request answered from a stale extent is the
# same mistake with a smaller blast radius.
_CONDITIONAL_HEADERS = frozenset({b"if-none-match", b"if-modified-since", b"if-range"})


def names_a_page(path: str) -> bool:
    """Does this mount path resolve to an HTML page rather than an asset?

    Asked BEFORE the file is looked up, because the answer decides what
    Starlette is allowed to see. Directory paths count: ``html=True``
    turns ``/`` and ``/anything/`` into that directory's ``index.html``.
    """
    rel = str(path).replace("\\", "/")
    if rel in ("", ".", "/") or rel.endswith("/"):
        return True
    return PurePosixPath(rel).suffix.lower() in _HTML_SUFFIXES


def asset_token(path: Path) -> Optional[str]:
    """This file's version token, or ``None`` if it is not a file.

    Size and mtime rather than a content hash: hashing 12 MB of vendored
    bundles on every page load would cost more than it could possibly
    save, and git rewrites a file only when its content changed, so the
    mtime it leaves is a faithful "this one is different".
    """
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    h = hashlib.blake2b(digest_size=6)
    h.update(f"{st.st_size}:{st.st_mtime_ns}".encode("ascii"))
    return h.hexdigest()


def _token_for(url: bytes, page: Path, root: Path) -> Optional[str]:
    """The token for a URL as written in the page, or ``None`` when it
    does not name a file this mount serves.

    Containment is checked before the ``stat()``: an ``src`` is page
    content, and a page that asked for ``../../etc/passwd`` must not make
    this function look outside the static root, even to read a size.
    """
    rel = url.decode("utf-8", "replace").split("#", 1)[0]
    if not rel:
        return None
    base = root if rel.startswith("/") else page.parent
    target = (base / rel.lstrip("/")).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return asset_token(target)


def version_urls(body: bytes, page: Path, root: Path) -> bytes:
    """Every same-origin asset URL in ``body``, stamped with its token."""

    def fix_attr(m: "re.Match[bytes]") -> bytes:
        url = m.group("url")
        if not url or _LEAVE_ALONE.match(url):
            return m.group(0)
        token = _token_for(url, page, root)
        if token is None:
            return m.group(0)
        stamped = url + f"?{VERSION_PARAM}={token}".encode("ascii")
        return m.group("attr") + m.group("q") + stamped + m.group("q")

    def fix_tag(m: "re.Match[bytes]") -> bytes:
        return _ATTR_RE.sub(fix_attr, m.group(0))

    return _TAG_RE.sub(fix_tag, body)


def stamped_page(page: Path, root: Path) -> tuple[bytes, str]:
    """``(the bytes this page is served as, its build id)``.

    The build id is a digest of the STAMPED body, so it moves when the
    page moves and also when any asset the page pulls moves — which is
    what makes it a usable answer to "is this tab running what the box
    has?". It is computed before the marker is injected, so reading the
    marker out of a served page and recomputing it here agree.

    One function, used by both the mount and ``GET /api/bundle``,
    deliberately: two implementations of "what would we send" would drift
    and the drift would read as permanent staleness.
    """
    page = page.resolve(strict=False)
    root = root.resolve(strict=False)
    body = version_urls(page.read_bytes(), page, root)
    bundle = hashlib.blake2b(body, digest_size=12).hexdigest()
    marker = (
        b"<script>window."
        + BUNDLE_GLOBAL.encode("ascii")
        + b'="'
        + bundle.encode("ascii")
        + b'";</script>'
    )
    # After ``<head>`` so it is set before any script the page pulls runs,
    # and inline so it cannot itself go stale. A page with no ``<head>``
    # simply carries no marker and the browser-side check turns itself off.
    return _HEAD_RE.sub(lambda m: m.group(0) + marker, body, count=1), bundle


def current_bundle(root: Path | str) -> Optional[str]:
    """What ``index.html`` would be served as right now, or ``None`` if
    there is no page to serve. Cheap: one 15 kB read plus a ``stat()``
    per referenced asset, which is the same work a page load already does.
    """
    directory = Path(str(root)).resolve(strict=False)
    try:
        _, bundle = stamped_page(directory / "index.html", directory)
    except OSError:
        return None
    return bundle


class RevalidatingStaticFiles(StaticFiles):
    """Static files a deploy can actually get past a warm browser.

    Four behaviours, on every response this mount produces:

    * HTML is rewritten so each asset URL carries that asset's token, and
      carries a build id the browser can compare against the box;
    * a page request's conditional headers never reach Starlette, whose
      stat-based ETag does not describe those rewritten bytes;
    * a request that arrives WITH the file's current token is
      ``immutable`` — no revalidation, no round trip;
    * everything else is ``no-cache``: store it, but ask before reusing.
    """

    # ── the response ──────────────────────────────────────────────────
    async def get_response(self, path: str, scope: Any) -> Response:
        # A page's conditional headers are answered HERE, against the
        # bytes about to be sent, not by Starlette against a stat that
        # describes a file nobody is sending. See the module docstring:
        # a release does not change index.html, so its stat ETag is the
        # one a pre-fix browser already holds, and a 304 against it hands
        # that browser back the page with no versioned URLs in it.
        page = names_a_page(path)
        response = await super().get_response(
            path, _without_conditionals(scope) if page else scope
        )
        served = getattr(response, "path", None)

        if (
            isinstance(response, FileResponse)
            and response.status_code == 200
            and served is not None
            and Path(str(served)).suffix.lower() in _HTML_SUFFIXES
        ):
            return self._html_response(Path(str(served)), scope)

        asked = _requested_version(scope)
        current = asset_token(Path(str(served))) if served is not None else None
        fresh = bool(asked) and asked == current
        response.headers["Cache-Control"] = IMMUTABLE if fresh else REVALIDATE
        return response

    def _html_response(self, served: Path, scope: Any) -> Response:
        """The page, with its asset URLs stamped and its build id set, and
        its own ETag over the REWRITTEN bytes.

        The ETag has to be computed here because the same ``index.html``
        produces different bytes when a sibling asset changes — which is
        precisely the release this has to deliver. The 304 below is the
        cheap steady state: a warm browser with nothing deployed asks once
        for the page and gets no body back.
        """
        root = Path(str(self.all_directories[0])).resolve(strict=False)
        body, _bundle = stamped_page(served, root)
        etag = '"%s"' % hashlib.blake2b(body, digest_size=16).hexdigest()
        headers = {"Cache-Control": REVALIDATE, "ETag": etag}

        if _if_none_match(scope) == etag:
            return Response(status_code=304, headers=headers)
        return Response(
            content=body, media_type="text/html; charset=utf-8", headers=headers
        )


def _header(scope: Any, name: bytes) -> str:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _if_none_match(scope: Any) -> str:
    return _header(scope, b"if-none-match").strip()


def _without_conditionals(scope: Any) -> Any:
    """The same request with its conditional headers removed.

    A shallow copy, never a mutation: the scope belongs to the server and
    is read again after this mount answers (access logging, the middleware
    stack above it), so the request that arrived must stay the request
    that arrived.
    """
    stripped = dict(scope)
    stripped["headers"] = [
        (key, value)
        for key, value in (scope.get("headers") or ())
        if key.lower() not in _CONDITIONAL_HEADERS
    ]
    return stripped


def _requested_version(scope: Any) -> str:
    """The token this request asked for, if any.

    Parsed by hand rather than through ``urllib.parse``: the query string
    is the only part of the request this needs, and the answer is one
    equality test.
    """
    for part in (scope.get("query_string") or b"").split(b"&"):
        key, _, value = part.partition(b"=")
        if key == VERSION_PARAM.encode("ascii"):
            return value.decode("latin-1")
    return ""
