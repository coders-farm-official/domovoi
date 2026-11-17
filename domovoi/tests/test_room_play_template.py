"""PLG-13 (round 6): a plugin's room_play_template stays inside the plugin.

The card's proof of concept: a manifest sets
``/api/plugins/<slug>/%2e%2e/%2e%2e/auth/logout#{id}``. It passed both
checks (each looked for a literal ".." only), and a browser resolves %2e%2e
as a dot segment: casting one of the plugin's items to a room POSTed to
/api/auth/logout with the household token and the admin Bearer.

Now the manifest takes plain path characters only and no dot segment;
the dashboard (index.html) resolves the template and keeps it only when
the browser's path is the written one, under the plugin's prefix; and the
player (player.jsx) checks the path again once the item's id is in it.
The two page checks run here in node, as written in the files.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.plugins_runtime import manifest as m

REPO = Path(__file__).resolve().parents[2]
POC = "/api/plugins/demo/%2e%2e/%2e%2e/auth/logout#{id}"
BAD = [
    POC,
    "/api/plugins/demo/%2E%2E/%2e%2e/auth/logout/{id}",
    "/api/plugins/demo/.%2e/x/{id}",
    "/api/plugins/demo/./{id}",
    "/api/plugins/demo/x/../../music/{id}",
    "/api/plugins/demo/x\\..\\..\\auth/{id}",
    "/api/plugins/demo/{id}#frag",
    "/api/plugins/demo/{id}?x=1",
    "/api/plugins/other/{id}",
]
GOOD = ["/api/plugins/demo/items/{id}/play-in-room", "/api/plugins/demo/{id}", "/api/plugins/demo/v1.2/{id}"]


@pytest.mark.parametrize("tpl", BAD)
def test_the_manifest_refuses_a_template_that_leaves_the_plugin(tpl: str) -> None:
    with pytest.raises(m.ManifestError):
        m._room_play_template({"room_play_template": tpl}, "demo")


@pytest.mark.parametrize("tpl", GOOD)
def test_the_manifest_keeps_the_plugins_own_routes(tpl: str) -> None:
    assert m._room_play_template({"room_play_template": tpl}, "demo") == tpl


def _extract(path: Path, start: str) -> str:
    """The arrow function that begins with ``start``, up to its closing
    ``};`` at the same indentation."""
    text = path.read_text(encoding="utf-8")
    i = text.index(start)
    indent = text[text.rfind("\n", 0, i) + 1:i]
    end = text.index("\n" + indent + "};", i)
    return text[i:end + len(indent) + 3]


def _node(script: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_the_dashboard_and_the_player_refuse_it_too() -> None:
    page_check = _extract(REPO / "web/static/index.html", "const roomPlayOk")
    player_path = _extract(REPO / "web/static/player.jsx", "const _castRoomPlayPath")
    script = (
        "const window = { location: { origin: 'http://192.168.1.20:6369' } };\n"
        + page_check + "\n" + player_path + "\n"
        + "const own = '/api/plugins/demo/';\n"
        + f"const bad = {json.dumps(BAD)};\n"
        + f"const good = {json.dumps(GOOD)};\n"
        + "const play = (tpl, id) => { window.DomovoiPlayerSources = { radio: "
          "{ plugin: 'demo', room_play_template: tpl } };"
          " return _castRoomPlayPath({ kind: 'radio', itemId: id }); };\n"
        + "console.log(JSON.stringify({\n"
          "  bad: bad.map((t) => roomPlayOk(t, own)),\n"
          "  good: good.map((t) => roomPlayOk(t, own)),\n"
          "  poc_played: play(" + json.dumps(POC) + ", '7'),\n"
          "  dotdot: play('/api/plugins/demo/{id}', '..'),\n"
          "  dot: play('/api/plugins/demo/x/{id}/go', '.'),\n"
          "  normal: play('/api/plugins/demo/stations/{id}/play-in-room', 'a b/c'),\n"
          "}));\n"
    )
    got = _node(script)
    assert got["bad"] == [False] * len(BAD)
    assert got["good"] == [True] * len(GOOD)
    assert got["poc_played"] is None
    assert got["dotdot"] is None and got["dot"] is None
    assert got["normal"] == "/api/plugins/demo/stations/a%20b%2Fc/play-in-room"


def test_the_page_check_is_the_one_the_page_uses() -> None:
    page = (REPO / "web/static/index.html").read_text(encoding="utf-8")
    assert re.search(r"roomPlayOk\(src\.room_play_template, own\)", page)
