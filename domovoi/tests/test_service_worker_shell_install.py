"""The service worker's shell install has to reach the server, not the
browser's own HTTP cache.

WHAT CHANGED, AND WHY THIS MODULE'S PREMISE MOVED. Until 2026-09-25 the
static mount sent NO `Cache-Control` at all, so freshness was heuristic and
a recently-opened dashboard held a warm, non-revalidating copy of every
shell file — which is exactly what `cache: 'reload'` in the install
handler was written to get past. That is no longer the state of the world:
`web/backend/static_cache.py` now stamps each asset URL in the page with
that file's own token and serves the page itself `no-cache`, because the
header alone was measured NOT to reach a browser that already holds a
heuristically-fresh copy. See `test_web_static_cache_headers`.

`cache: 'reload'` is kept, and still asserted here. An install is the one
fetch that must not depend on the server's headers being right, because
what it stores is what a browser will live on when the network is gone;
and the install fetches the SHELL_ASSETS list by its plain, unversioned
names, which is precisely the class of URL the new mount answers
`no-cache` rather than `immutable`.

`SHELL_CACHE`'s name is what makes the install handler run again on a
browser that already has a worker. It moved to `domovoi-shell-v4` in the
same release, so that a browser holding the v3 cache — filled under the
OLD cache-first, never-revalidate rules — throws those entries away on
the upgrade instead of serving them one more time.

DB-free: one runs `web/static/sw.js` in node against a fake Cache Storage,
the other reads response headers off static files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import web_app

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("sw_install_harness.js")


@pytest.fixture(scope="module")
def install() -> dict:
    node = shutil.which("node")
    assert node, "node is required to run the service worker (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    out = json.loads(proc.stdout)
    assert "error" not in out, out["error"]
    return out


def test_the_install_handler_fetches_the_shell_past_the_http_cache(install) -> None:
    """`cache.add('/auth.js')` is a default-mode fetch and a warm browser
    answers it from its own HTTP cache; `cache: 'reload'` does not."""
    assert install["installHandlers"] == 1
    urls = {a["url"] for a in install["added"]}
    for must in ("/", "/index.html", "/auth.js", "/data.js", "/settings.jsx"):
        assert must in urls, f"{must} is not in the installed shell"
    stale = sorted(a["url"] for a in install["added"] if a["cache"] != "reload")
    assert stale == [], stale


def test_the_shell_cache_name_still_matches_the_bundle_it_installs(install) -> None:
    """The name is what makes install run again on a browser that already
    has a worker, and what makes that browser drop the entries it filled
    under the previous rules. It is no longer the ONLY upgrade mechanism
    — the shell is network-first now and the mount versions its URLs —
    but it is what covers the one browser no server change can reach: the
    one still running the pre-fix, cache-first worker."""
    assert install["shellCache"] == "domovoi-shell-v4"


@pytest.mark.asyncio
async def test_the_shell_names_the_install_fetches_are_never_served_stale() -> None:
    """The premise of `cache: 'reload'`, restated for the world as it is
    now rather than deleted.

    This module used to assert that the static mount sends NO
    `Cache-Control`, which was true and was the reason `reload` had to
    exist. It sends one now. What matters to THIS file is unchanged and is
    what is pinned: the PLAIN, unversioned names in `SHELL_ASSETS` — the
    ones the install handler fetches — are answered `no-cache`, never
    with a long lifetime, so a browser can never install a shell out of a
    cache it was told it could keep. (`immutable` is reserved for a URL
    carrying the file's current token, and an install never asks for one.)
    """
    from web.backend.static_cache import REVALIDATE

    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        for path in ("/auth.js", "/sw.js", "/data.js", "/settings.jsx"):
            r = await c.get(path)
            assert r.status_code == 200, path
            assert r.headers.get("cache-control") == REVALIDATE, (
                path, r.headers.get("cache-control")
            )
            # The revalidation stays cheap: a 304 with no body, not a
            # re-download of the whole shell on every load.
            assert "last-modified" in r.headers or "etag" in r.headers, path
