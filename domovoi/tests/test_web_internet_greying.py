"""Online controls are greyed, not hidden, when the server is answered
``never`` (Settings → Internet) — builder 4's pages.

Each page reads the answer once from ``GET /api/config`` (its
``internet_access``) and greys what would reach the internet, with the
``needs internet · Settings → Internet`` title:

* Models: Install (catalog cards and pull-by-name);
* Plugins: install from GitHub (a zip install stays);
* News: "poll now";
* Satellites → prepare media: **Refresh caches** (Prepare stays);
* Radio Stations: **Import FCC FM**, the online-stations scope (the page
  falls back to local FM), and playing an internet station.

With the question unanswered (or answered yes) nothing is greyed. The page
files are compiled with the dashboard's Babel and driven in the Node
render harness (``jsx_interact_harness.js``), with the data layer scripted:
nothing is fetched, and the page makes NO extra call for the answer (it is a
hook read, never an ``apiGet`` the harnesses' call logs would count).

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
COMPONENTS = "web/static/components.jsx"
STATIONS = "plugins/radio/web/static/stations.jsx"
NEEDS = "needs internet · Settings → Internet"

# The shared strings and the note component live in components.jsx. A
# fallback is set as a plain global first, which a `const` of the same name
# in components.jsx shadows, so the scenarios run with or without it.
SETUP = r"""
globalThis.NEEDS_INTERNET_TEXT = 'needs internet · Settings → Internet';
globalThis.INTERNET_OFF_MESSAGE = 'internet access is turned off for this box (Settings → Internet)';
globalThis.NeedsInternetNote = () => null;
globalThis.isInternetOffError = () => false;
globalThis.relTime = () => 'just now';
"""

MODEL = {"name": "qwen2.5:7b", "role": "qa", "desc": "a model", "est_vram_gb": 5}
STATION_ONLINE = {"id": 4, "name": "KEXP", "source": "online",
                  "stream_url": "https://kexp.example/stream.mp3", "favorited": True,
                  "sample_interval_sec": 180, "tags": []}
STATION_LAN = {"id": 5, "name": "FM box", "source": "online",
               "stream_url": "http://192.168.1.50:8000/fm.mp3", "favorited": True,
               "sample_interval_sec": 180, "tags": []}


def _button(name: str) -> str:
    return (f"(() => {{ const b = h.find({{ type: 'button', text: {json.dumps(name)} }});"
            " return b ? h.plain(b).props : null; })()")


def _scenarios(answer: str) -> dict:
    cfg = {"GET /api/config": {"bot_name": "Domovoi", "internet_access": answer}}
    return {
        f"models_{answer or 'unset'}": {
            "files": [COMPONENTS, "web/static/models.jsx"], "component": "ModelsPanel",
            "setup": SETUP,
            "api": {**cfg,
                    "GET /api/models/installed": {"installed": [], "ollama_reachable": True},
                    "GET /api/models/catalog": {"ollama": [MODEL], "whisper": []},
                    "GET /api/models/active": {"roles": []},
                    "GET /api/models/hardware": {"gpus": [], "ram": None},
                    "GET /api/models/jobs": {"jobs": []}},
            "script": ("h.render();"
                       " const installs = h.findAll({ type: 'button', text: 'Install' }).map((b) => h.plain(b).props);"
                       " const box = h.find({ type: 'input', placeholder: 'Pull by name (e.g. qwen2.5:7b)' });"
                       " return { installs, box: box && h.plain(box).props, calls: h.calls.length };"),
        },
        f"plugins_{answer or 'unset'}": {
            "files": [COMPONENTS, "web/static/plugins.jsx"], "component": "PluginsPage",
            "setup": SETUP,
            "api": {**cfg, "GET /api/plugins": {"plugins": []}, "GET /api/config/version": {}},
            "script": ("h.render();"
                       " const gh = h.find({ type: 'input', placeholder: 'https://github.com/org/repo[@ref]' });"
                       f" return {{ fetch: {_button('fetch & preview')}, zip: {_button('Install from zip')},"
                       "  gh: gh && h.plain(gh).props, calls: h.calls.length };"),
        },
        f"news_{answer or 'unset'}": {
            "files": [COMPONENTS, "web/static/news.jsx"], "component": "PersonNews",
            "setup": SETUP,
            "props": {"person": {"id": 1, "name": "Ann"}, "categories": []},
            "fnProps": ["fire"],
            "api": {**cfg, "GET /api/news/people/1/topics": [], "GET /api/news/people/1/items": [],
                    "GET /api/news/people/1/briefing": {"briefing": None, "generated_at": None}},
            "script": f"h.render(); return {{ poll: {_button('poll now')}, calls: h.calls.length }};",
        },
        f"satmedia_{answer or 'unset'}": {
            "files": [COMPONENTS, "web/static/satellite_media.jsx"], "component": "PrepareMediaCard",
            "setup": SETUP,
            "fnProps": ["fire"],
            "api": {**cfg,
                    "GET /api/satellites/media/status": {"boards": [], "plugins": [], "cache": {},
                                                         "mic_profiles": [], "drive_targets": True},
                    "GET /api/satellites/media/targets": [],
                    "GET /api/satellites/media/jobs": []},
            "script": ("h.render();"
                       " const head = h.find({ type: 'span', text: 'prepare satellite media' });"
                       " const opener = h.ancestors(head).find((a) => a.type === 'button');"
                       " await h.click((el) => el === opener);"
                       f" return {{ refresh: {_button('Refresh caches')}, prepare: {_button('Prepare')},"
                       "  calls: h.calls.length };"),
        },
        f"stations_{answer or 'unset'}": {
            "files": [COMPONENTS, STATIONS],
            "component": "window.DomovoiPlugins.radio.pages.StationsPage",
            "setup": SETUP,
            "api": {**cfg,
                    "GET /api/plugins/radio/stations": [STATION_ONLINE, STATION_LAN],
                    "GET /api/plugins/radio/badge": {"favorites": 2},
                    "GET /api/plugins/radio/recent": [STATION_ONLINE]},
            "script": ("h.render(); await h.settle(); h.rerender();"
                       " const rows = (name) => h.findAll((el) => el.type === 'span' && el.text === name)"
                       "   .map((sp) => h.ancestors(sp).find((a) => a.type === 'button'))"
                       "   .filter(Boolean).map((b) => h.plain(b).props);"
                       f" return {{ fcc: {_button('Import FCC FM')}, online: {_button('online stations')},"
                       f"  search: {_button('browse')} || {_button('search')},"
                       "  kexp: rows('KEXP'), lan: rows('FM box'), calls: h.calls.length };"),
        },
    }


SCENARIOS = {**_scenarios("never"), **_scenarios("")}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scen = tmp_path_factory.mktemp("greying") / "scenarios.json"
    scen.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{scen}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _greyed(props: dict | None) -> bool:
    return bool(props) and props.get("disabled") is True and props.get("title") == NEEDS


def test_models_install_is_greyed_under_never(driven) -> None:
    off, on = driven["models_never"], driven["models_unset"]
    assert off["installs"] and all(_greyed(b) for b in off["installs"])
    assert off["box"]["disabled"] is True and off["box"]["title"] == NEEDS
    assert on["installs"] and not any(b.get("disabled") for b in on["installs"])
    assert not on["box"].get("disabled")
    assert off["calls"] == on["calls"] == 0          # the answer is a hook read


def test_plugins_github_is_greyed_and_zip_stays(driven) -> None:
    off, on = driven["plugins_never"], driven["plugins_unset"]
    assert _greyed(off["fetch"])
    assert off["gh"]["disabled"] is True
    assert not off["zip"].get("disabled")             # a zip install needs no internet
    assert on["fetch"].get("title") != NEEDS and not on["gh"].get("disabled")
    assert off["calls"] == on["calls"] == 0


def test_news_poll_is_greyed_under_never(driven) -> None:
    assert _greyed(driven["news_never"]["poll"])
    assert not driven["news_unset"]["poll"].get("disabled")


def test_satellite_cache_refresh_is_greyed_and_prepare_stays(driven) -> None:
    off, on = driven["satmedia_never"], driven["satmedia_unset"]
    assert _greyed(off["refresh"])
    assert not off["prepare"].get("disabled")
    assert not on["refresh"].get("disabled")


def test_radio_online_controls_are_greyed_and_fm_stays(driven) -> None:
    off, on = driven["stations_never"], driven["stations_unset"]
    assert _greyed(off["fcc"])
    assert _greyed(off["online"])                     # the directory scope
    assert off["search"]["disabled"] is not True      # local FM browsing stays usable
    assert off["kexp"] and all(_greyed(b) for b in off["kexp"])          # internet station
    assert off["lan"] and not any(b.get("disabled") for b in off["lan"])  # house network
    assert not on["fcc"].get("disabled") and not on["online"].get("disabled")
    assert not any(b.get("disabled") for b in on["kexp"])
    assert off["calls"] == on["calls"] == 0
