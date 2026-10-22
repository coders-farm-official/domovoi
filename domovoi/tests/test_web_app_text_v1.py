"""The dashboard's words agree with its screens (the v1 manual pass).

* "Ask Domovoi" names the page you are on as the top bar does: an old
  address (#satellites, #containers) is the Settings section it became, and
  a Settings section is named by its label, "Settings → Phones & browsers",
  never a slug or a page that no longer exists.
* The hints that send someone to a Settings section name the section it is
  in now: a blocked device is lifted in Settings → Phones & browsers (not
  "Devices"), command recordings are reviewed in Settings → Voice history
  (not "settings → recordings"), the phone's household token is under
  Settings → Rooms & devices → Phones & browsers (not "Connection").
* The About card uses the plain descriptor, not "voice assistant".

The page name is driven through domovoi/tests/jsx_interact_harness.js (the
dashboard's own Babel). No DB, never ``requires_db``; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

# As index.html loads them: Settings (and the sections Health registers)
# before help.jsx.
FILES = ["web/static/components.jsx", "web/static/settings.jsx", "web/static/containers.jsx",
         "web/static/health.jsx", "web/static/help.jsx"]

HASHES = ["#home", "", "#music/queue", "#satellites", "#satellites/drawer", "#containers",
          "#settings", "#settings/phones-and-browsers", "#settings/devices", "#settings/recordings",
          "#settings/health", "#settings/rooms-and-devices", "#settings/nonsense", "#manual"]


@pytest.fixture(scope="module")
def names(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scenarios = {
        "names": {
            "files": FILES, "component": "helpPageName",
            "setup": "window.location.hash = '';",
            "script": "const w = h.global('window'); const out = {};"
                      f" for (const x of {json.dumps(HASHES)}) {{ w.location.hash = x; out[x] = w.__component(); }}"
                      " return out;",
        },
    }
    spec = tmp_path_factory.mktemp("app-text") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["names"]
    assert "__harness_error" not in out, out
    return out


def test_ask_domovoi_names_the_page_as_it_is_now(names) -> None:
    assert names["#home"] == "Home" and names[""] == "Home"
    assert names["#music/queue"] == "Music"
    assert names["#manual"] == "User Manual"
    assert names["#settings"] == "Settings"
    # Old pages are the Settings sections they became.
    assert names["#satellites"] == "Settings → Rooms"
    assert names["#satellites/drawer"] == "Settings → Rooms"
    assert names["#containers"] == "Settings → Health"
    # Sections and groups by their labels, old names included.
    assert names["#settings/phones-and-browsers"] == "Settings → Phones & browsers"
    assert names["#settings/devices"] == "Settings → Phones & browsers"
    assert names["#settings/recordings"] == "Settings → Voice history"
    assert names["#settings/health"] == "Settings → Health"
    assert names["#settings/rooms-and-devices"] == "Settings → Rooms & devices"
    assert names["#settings/nonsense"] == "Settings → nonsense"
    assert not any(v in ("Satellites", "Containers") for v in names.values()), names


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["printers.jsx", "chat_drafts.jsx", "components.jsx", "files.jsx"])
def test_a_blocked_device_is_lifted_in_phones_and_browsers(name) -> None:
    src = _src(name)
    assert "Settings → Devices" not in src
    assert "An admin lifts the block in Settings → Phones & browsers." in src


def test_recordings_are_reviewed_in_voice_history() -> None:
    src = _src("satellites.jsx")
    assert "settings → recordings" not in src
    assert "review in Settings → Voice history" in src


def test_no_screen_sends_a_phone_to_settings_connection() -> None:
    for path in STATIC.glob("*.js*"):
        if path.name == "manual-content.json":
            continue
        assert "Settings → Connection" not in path.read_text(encoding="utf-8"), path.name


def test_the_about_card_uses_the_descriptor() -> None:
    src = _src("settings.jsx")
    assert 'sub="The home computer system that just works."' in src
    assert "home voice assistant" not in src
    assert "The assistant's spoken" not in src
