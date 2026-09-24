"""The service worker's shell install has to reach the server, not the
browser's own HTTP cache.

`SHELL_CACHE`'s name is the only thing that makes the install handler run
again, and this release bumped it to `domovoi-shell-v3` precisely because a
v2 browser's `auth.js` canonicalises the household token before storing it
and so cannot pair with a token an admin chose. That bump is defeated if
the install fetches are answered out of the HTTP cache: the new cache fills
with the old bundle, `sw.js` will not change again, and the browser is
stuck there with no way out but a hard reload.

It is not a hypothetical. The SPA is mounted with Starlette's
`StaticFiles`, whose `FileResponse` sets `last-modified` and `etag` and
nothing else — no `Cache-Control` anywhere in `web/backend` for the static
mount — so freshness is heuristic and a recently-opened dashboard has a
warm, non-revalidating copy of every shell file.

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
    """The name is the whole upgrade mechanism: it is what makes install
    run again on a browser that already has a worker."""
    assert install["shellCache"] == "domovoi-shell-v3"


@pytest.mark.asyncio
async def test_static_shell_files_carry_no_cache_control() -> None:
    """The premise of the fix above, pinned so it cannot go stale. If a
    `Cache-Control` ever does appear on the static mount, this fails and
    whoever added it can decide whether the `reload` is still needed —
    rather than the comment quietly becoming untrue."""
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        for path in ("/auth.js", "/sw.js"):
            r = await c.get(path)
            assert r.status_code == 200, path
            assert "cache-control" not in r.headers, (path, r.headers.get("cache-control"))
            # ...which is why the browser falls back to heuristic freshness.
            assert "last-modified" in r.headers or "etag" in r.headers, path
