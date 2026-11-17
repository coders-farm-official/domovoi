"""The header's address to share (web/static/share_address.js).

The owner's order, 2026-11-17: ``domovoi.local`` with no port when the
short address is on and the name is announced; ``domovoi.local:6369``
when the name works but the short address is off; the IP with ``:6369``
when no name is announced. Runs the real file under node. No DB.

The header shows it and copies it; it never changes which address the
page talks to (the last tests read components.jsx for that).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"

RUN = r"""
const fs = require('fs'); const vm = require('vm');
const sandbox = { window: {} }; sandbox.globalThis = sandbox; vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox, { filename: 'share_address.js' });
const cases = JSON.parse(process.argv[2]); const out = {};
for (const [name, c] of Object.entries(cases)) out[name] = sandbox.window.ShareAddress.pick(c.lan, c.short, c.here);
process.stdout.write(JSON.stringify(out));
"""

ANNOUNCED = {"state": "announced", "claimed": "domovoi", "fqdn": "domovoi.local", "addresses": ["192.168.1.20"]}
SHORT_ON = {"state": "on", "enabled": True, "dashboard_port": 6369}
SHORT_OFF = {"state": "off", "enabled": False, "dashboard_port": 6369}
SHORT_TAKEN = {"state": "taken", "enabled": True, "dashboard_port": 6369}
NO_NAME = {"state": "no_host", "claimed": None, "fqdn": None, "addresses": []}

CASES = {
    # 1. short address on + name announced: no port at all.
    "both": {"lan": ANNOUNCED, "short": SHORT_ON, "here": {"hostname": "192.168.1.20"}},
    "both_from_name": {"lan": ANNOUNCED, "short": SHORT_ON, "here": {"hostname": "domovoi.local"}},
    "held_by_this_computer": {"lan": {**ANNOUNCED, "state": "held"}, "short": SHORT_ON, "here": {"hostname": "localhost"}},
    "claimed_another": {"lan": {**ANNOUNCED, "claimed": "domovoi2", "fqdn": "domovoi2.local", "state": "taken"},
                        "short": SHORT_ON, "here": {"hostname": "192.168.1.20"}},
    "configured_name": {"lan": {**ANNOUNCED, "claimed": "kitchen", "fqdn": "kitchen.local"},
                        "short": SHORT_ON, "here": {"hostname": "192.168.1.20"}},
    # 2. the name works, the short address doesn't.
    "short_off": {"lan": ANNOUNCED, "short": SHORT_OFF, "here": {"hostname": "192.168.1.20"}},
    "short_taken": {"lan": ANNOUNCED, "short": SHORT_TAKEN, "here": {"hostname": "domovoi.local"}},
    "short_unknown": {"lan": ANNOUNCED, "short": None, "here": {"hostname": "domovoi.local"}},
    # 3. no name announced: the IP with the dashboard's port.
    "no_name_by_ip": {"lan": NO_NAME, "short": SHORT_ON, "here": {"hostname": "192.168.1.20"}},
    "name_off": {"lan": {**ANNOUNCED, "state": "off"}, "short": SHORT_ON, "here": {"hostname": "192.168.1.20"}},
    "no_name_from_localhost": {"lan": {"state": "failed", "claimed": None, "addresses": ["127.0.0.1", "192.168.1.20"]},
                               "short": SHORT_OFF, "here": {"hostname": "localhost"}},
    "no_name_unread": {"lan": None, "short": None, "here": {"hostname": "192.168.1.20"}},
    "ipv6": {"lan": None, "short": None, "here": {"hostname": "[fe80::20]"}},
    "other_port": {"lan": NO_NAME, "short": {**SHORT_OFF, "dashboard_port": 6390}, "here": {"hostname": "192.168.1.20"}},
    # Nothing to tell: the header keeps the label it always showed.
    "nothing": {"lan": None, "short": None, "here": {"hostname": "localhost"}},
    "nothing_by_a_router_name": {"lan": NO_NAME, "short": SHORT_ON, "here": {"hostname": "mybox.lan"}},
}


@pytest.fixture(scope="module")
def picked() -> dict:
    node = shutil.which("node")
    assert node, "node is required to run share_address.js (see jsxcheck)"
    out = subprocess.run(
        [node, "-e", RUN, str(STATIC / "share_address.js"), json.dumps(CASES)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(out.stdout)


def test_short_address_and_name_give_the_bare_name(picked) -> None:
    for case in ("both", "both_from_name", "held_by_this_computer"):
        assert picked[case] == {"kind": "short", "text": "domovoi.local", "url": "http://domovoi.local"}, case
    assert picked["claimed_another"]["text"] == "domovoi2.local"
    assert picked["configured_name"]["text"] == "kitchen.local"


def test_name_without_the_short_address_keeps_the_port(picked) -> None:
    for case in ("short_off", "short_taken", "short_unknown"):
        assert picked[case] == {"kind": "name", "text": "domovoi.local:6369", "url": "http://domovoi.local:6369"}, case


def test_no_name_gives_the_ip_with_the_port(picked) -> None:
    for case in ("no_name_by_ip", "name_off", "no_name_from_localhost", "no_name_unread"):
        assert picked[case] == {"kind": "ip", "text": "192.168.1.20:6369", "url": "http://192.168.1.20:6369"}, case
    assert picked["ipv6"]["text"] == "[fe80::20]:6369"
    assert picked["other_port"]["text"] == "192.168.1.20:6390"


def test_nothing_known_leaves_the_header_as_it_was(picked) -> None:
    assert picked["nothing"] is None
    assert picked["nothing_by_a_router_name"] is None


def test_the_name_states_match_the_core(picked) -> None:
    """The same "name works" set as the core's claimed_name()."""
    src = (REPO_ROOT / "domovoi" / "lan_name" / "service.py").read_text(encoding="utf-8")
    m = re.search(r'def claimed_name\(\).*?st\.get\("state"\) in \(([^)]*)\)', src, re.S)
    assert m, "claimed_name's state set moved"
    core = {s.strip().strip('"') for s in m.group(1).split(",") if s.strip()}
    js = (STATIC / "share_address.js").read_text(encoding="utf-8")
    jm = re.search(r"NAME_WORKS = \[([^\]]*)\]", js)
    assert {s.strip().strip("'") for s in jm.group(1).split(",")} == core


def test_the_header_shows_it_and_still_talks_to_the_same_server() -> None:
    comp = (STATIC / "components.jsx").read_text(encoding="utf-8")
    # Both header spots use the shareable address, click to copy, with the
    # label the page always showed until it is known.
    assert '<ShareAddressButton className="host" fallback={host}/>' in comp
    assert "<ShareAddressButton className=\"theme-toggle\" fallback={ServerStore.currentLabel()}" in comp
    assert "copyToClipboard(copyText)" in comp
    # The switch to another Domovoi stays.
    assert 'title="switch domovoi"' in comp
    # Display only: nothing here sets the address the page talks to.
    store = comp[comp.index("const ShareAddressStore"):comp.index("const useShareAddress")]
    assert "ServerStore.select" not in store and "ServerStore.upsert" not in store and "API_BASE =" not in store


def test_the_page_loads_it_before_the_header() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="share_address.js"') < html.index('src="components.jsx"')
    assert html.index('src="data.js"') < html.index('src="share_address.js"')
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "'/share_address.js'" in sw
