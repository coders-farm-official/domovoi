"""The star on a Stations search result is a real toggle (plugin board F-P013).

An online search hit that is not saved yet carries id 0. Its first star
POSTs it (which saves and favorites it); the page used to forget the id
that POST answered, so the second tap matched neither "PATCH the saved
row" nor "POST a new one" and did nothing: no request, no toast, the star
stayed filled. Now the row keeps the saved id and later taps PATCH it.

The real Stations page (``plugins/radio/web/static/stations.jsx``) runs in
``jsx_interact_harness.js`` with a scripted data layer: search, star, star
again, star a third time, and read what the page sent. No DB, no server.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
STATIONS = "plugins/radio/web/static/stations.jsx"
API = "/api/plugins/radio"

HIT = {
    "id": 0, "name": "101 SMOOTH JAZZ", "source": "online",
    "stream_url": "https://example.com/jazz.mp3", "external_id": "uuid-jazz",
    "country_code": "US", "language": "english", "tags": ["jazz"],
    "favorited": False, "sample_interval_sec": 180,
}
SAVED = {**HIT, "id": 7, "favorited": True}

SETUP = r"""
globalThis.usePlayback = () => ({ available: true, playItems() {} });
globalThis.setTimeout = (fn) => { return 0; };   // debounces and toast expiry: not under test
globalThis.clearTimeout = () => {};
"""

SCRIPT = r"""
  h.render();
  await h.settle();
  h.rerender();
  h.change({ type: 'input', placeholder: 'search stations — name, callsign, network…' }, 'jazz');
  await h.submit({ type: 'form' });
  await h.settle();
  h.rerender();
  // IconButton renders a <button title="favorite|unfavorite"> around the star.
  const star = () => h.findAll((el) => el.type === 'button' && el.props
                                    && ['favorite', 'unfavorite'].includes(el.props.title));
  const before = star().map((el) => el.props.title);
  const taps = [];
  for (let i = 0; i < 3; i++) {
    const s = star();
    if (!s.length) return { error: 'no star on the search row', text: h.text(), calls: h.calls };
    await s[0].props.onClick();
    await h.settle();
    h.rerender();
    taps.push(star()[0].props.title);
  }
  return {
    before,
    taps,
    mutations: h.calls.filter((c) => c.method !== 'GET').map((c) => ({ method: c.method, path: c.path, body: c.body })),
  };
"""


def _scenario() -> dict:
    return {
        "files": ["web/static/components.jsx", STATIONS],
        "component": "window.DomovoiPlugins.radio.pages.StationsPage",
        "setup": SETUP,
        "api": {
            f"GET {API}/search": [HIT],
            f"POST {API}/stations": SAVED,
            f"PATCH {API}/stations/7": SAVED,
            f"GET {API}/badge": {"favorites": 0},
        },
        "script": SCRIPT,
    }


@pytest.fixture(scope="module")
def outcome() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(INTERACT_HARNESS), str(REPO_ROOT), json.dumps({"star": _scenario()})],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)["star"]
    assert "__harness_error" not in res, res.get("__harness_error")
    assert "error" not in res, res
    return res


def test_the_first_star_saves_the_hit(outcome) -> None:
    assert outcome["before"] == ["favorite"]
    first = outcome["mutations"][0]
    assert first["method"] == "POST" and first["path"] == f"{API}/stations", outcome["mutations"]
    assert first["body"]["external_id"] == "uuid-jazz"
    assert outcome["taps"][0] == "unfavorite"


def test_the_second_star_unfavorites_the_saved_row(outcome) -> None:
    second = outcome["mutations"][1]
    assert second == {"method": "PATCH", "path": f"{API}/stations/7", "body": {"favorited": False}}, outcome
    assert outcome["taps"][1] == "favorite"


def test_a_third_star_favorites_it_again_without_a_second_row(outcome) -> None:
    third = outcome["mutations"][2]
    assert third == {"method": "PATCH", "path": f"{API}/stations/7", "body": {"favorited": True}}, outcome
    assert [m["method"] for m in outcome["mutations"]].count("POST") == 1, outcome["mutations"]
    assert outcome["taps"][2] == "unfavorite"
