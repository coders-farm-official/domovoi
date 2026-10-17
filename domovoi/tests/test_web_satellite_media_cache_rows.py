"""Settings → Satellites → prepare satellite media: the cache row says what
"Refresh caches" can fill, and nothing else reads as an empty cache.

The owner, preparing cards on the Windows install (2026-10-16): the row read
"dtbo (empty)" with an idle dot after every "Refresh caches", which looks
broken. It isn't: ``cache/dtbo/`` has no fetcher on purpose
(domovoi/satellite_media/fetchers.py). The XVF3800 USB array needs no
overlay at all (satellite/PROVISIONING.md, "Skip the HAT-only steps"), and a
card for a 2-Mics HAT, with no prebuilt overlay in that folder, leaves the
overlay to the Pi: its first boot says so and PROVISIONING §4 compiles it
there by hand. So the row says that instead.

The other rows: the wheels and the wake-word models are what the refresh
downloads ("(empty)" until it has), and the debs need Docker; without it the
row says so rather than "(empty)" (the refresh skips them).

Driven through ``jsx_interact_harness.js`` with the page's data scripted.
DB-free; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx"]

SETUP = r"""
globalThis.NEEDS_INTERNET_TEXT = 'needs internet · Settings → Internet';
globalThis.NeedsInternetNote = () => null;
globalThis.relTime = () => 'just now';
"""

OPEN_AND_READ = r"""
h.render();
const head = h.find({ type: 'span', text: 'prepare satellite media' });
const opener = h.ancestors(head).find((a) => a.type === 'button');
await h.click((el) => el === opener);
const row = h.findAll((el) => el.type === 'span' && el.props && el.props.style
  && el.props.style.display === 'inline-flex' && h.inside(el, (a) => a.props
  && String(a.props.className || '').includes('mono')));
return row.map((el) => ({ text: el.text.replace(/\s+/g, ' ').trim(),
  dot: (h.findAll((x) => x.type === 'StatusDot' || (x.props && x.props.className === 'dot'))
        .filter((x) => h.ancestors(x).includes(el)).length),
  info: h.findAll((x) => x.type === 'Icon' && x.props.name === 'info')
        .filter((x) => h.ancestors(x).includes(el)).length }));
"""


def _status(cache: dict, docker: bool) -> dict:
    return {"boards": [], "plugins": [], "cache": cache, "mic_profiles": [],
            "drive_targets": True, "docker_available": docker}


def _scenario(cache: dict, docker: bool) -> dict:
    return {
        "files": FILES, "component": "PrepareMediaCard", "setup": SETUP, "fnProps": ["fire"],
        "api": {"GET /api/config": {"bot_name": "Domovoi"},
                "GET /api/satellites/media/status": _status(cache, docker),
                "GET /api/satellites/media/targets": [],
                "GET /api/satellites/media/jobs": []},
        "script": OPEN_AND_READ,
    }


FULL = {"wheels": {"ok": True, "files": 41}, "debs": {"ok": True, "files": 9},
        "oww_models": {"ok": True, "files": 3}, "dtbo": {"ok": False, "files": 0}}

SCENARIOS = {
    "fresh_with_docker": _scenario({}, True),
    "fresh_without_docker": _scenario({}, False),
    "refreshed": _scenario(FULL, True),
    "prebuilt_overlay": _scenario({**FULL, "dtbo": {"ok": True, "files": 1}}, True),
}


@pytest.fixture(scope="module")
def rows() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for name, res in out.items():
        assert not (isinstance(res, dict) and "__harness_error" in res), res
    return {name: {r["text"]: r for r in res} for name, res in out.items()}


OVERLAY = "mic-board overlay: XVF3800 needs none · 2-Mics HAT: built on the Pi by hand (PROVISIONING §4)"


def test_the_overlay_row_never_reads_as_an_empty_cache(rows) -> None:
    for name in ("fresh_with_docker", "fresh_without_docker", "refreshed"):
        texts = rows[name]
        assert not any(t.startswith("dtbo") for t in texts), texts
        assert OVERLAY in texts, texts
        # Not a status at all: an info mark, no dot that a refresh should turn green.
        assert texts[OVERLAY]["dot"] == 0 and texts[OVERLAY]["info"] == 1


def test_a_prebuilt_overlay_is_counted(rows) -> None:
    texts = rows["prebuilt_overlay"]
    assert "mic-board overlay (1)" in texts and texts["mic-board overlay (1)"]["dot"] == 1


def test_empty_reads_only_on_what_the_refresh_fills(rows) -> None:
    fresh = rows["fresh_with_docker"]
    assert {"wheels (empty)", "debs (empty)", "oww_models (empty)"} <= set(fresh)
    # Without Docker the refresh skips the debs: the row says why instead.
    no_docker = rows["fresh_without_docker"]
    assert "debs (needs docker)" in no_docker and "debs (empty)" not in no_docker
    assert {"wheels (empty)", "oww_models (empty)"} <= set(no_docker)
    done = rows["refreshed"]
    assert {"wheels (41)", "debs (9)", "oww_models (3)"} <= set(done)
    assert not any("(empty)" in t for t in done), done
