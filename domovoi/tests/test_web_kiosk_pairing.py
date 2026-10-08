"""The kiosk display pairs itself from its own URL (WEB-15).

Once the room's row, the live push and the transport verbs answer only a
household credential, a video satellite's kiosk (``display.html``) — which
nobody sits in front of to pair — has to carry the token in the URL its
launcher opens: ``/display.html?room=<room>&device_token=<token>``. The
page stores it exactly as the pair modal stores a pasted token
(``Auth.pair``: verbatim bar the outer whitespace) BEFORE anything renders,
so the first read and the ``/ws/state`` handshake already carry it, and
takes it back out of the address.

A browser that already holds a DIFFERENT token keeps it unless the URL's
token passes one credentialed read first (REV-11 nit): display.html is
served to anyone, so a link carrying ``device_token=junk`` must not unpair
a household browser, while a kiosk whose URL carries a rotated token must
still re-pair itself.

Runs the real ``web/static/display.jsx`` top level, compiled with the
vendored Babel, in a node vm with a stub window / Auth / React / fetch. No
DB.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const [root, search, stored, probeStatus] = process.argv.slice(1);
const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);
const src = fs.readFileSync(path.join(root, 'web/static/display.jsx'), 'utf8');
const code = Babel.transform(src, { presets: ['react'], filename: 'display.jsx' }).code;
const out = { pairs: [], replaced: [], rendered: false, pairedBeforeRender: null, probes: [] };
const React = { createElement: (t, p, ...c) => ({ t, p, c }), useState: (v) => [v, () => {}],
                useEffect: () => {}, useRef: (v) => ({ current: v }), useCallback: (f) => f };
const sandbox = {
  URLSearchParams, console, React,
  ReactDOM: { createRoot: () => ({ render: () => {
    out.rendered = true; out.pairedBeforeRender = out.pairs.length > 0; } }) },
  document: { documentElement: { dataset: {} }, getElementById: () => ({}) },
  Auth: { pair: (v) => { out.pairs.push(v); return true; },
          deviceToken: () => (stored || null), DEVICE_TOKEN_HEADER: 'X-Device-Token' },
  API_BASE: '',
  fetch: (url, init) => {
    out.probes.push({ url, credentials: init && init.credentials,
                      headers: (init && init.headers) || {} });
    if (probeStatus === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    const status = Number(probeStatus || 200);
    return Promise.resolve({ status, ok: status >= 200 && status < 300 });
  },
};
sandbox.window = {
  location: { search, pathname: '/display.html', hash: '' },
  history: { replaceState: (_s, _t, url) => out.replaced.push(url) },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'display.jsx' });
// Let a probe's promise chain settle before reporting.
setTimeout(() => process.stdout.write(JSON.stringify(out)), 20);
"""


def _run(search: str, stored: str = "", probe_status: str = "200") -> dict:
    node = shutil.which("node")
    assert node, "node is required (the JSX compile check already relies on it)"
    proc = subprocess.run(
        [node, "-e", HARNESS, str(REPO_ROOT), search, stored, probe_status],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_url_token_is_stored_before_anything_renders() -> None:
    out = _run("?room=kitchen&device_token=acorn-maple-river-thistle")
    assert out["pairs"] == ["acorn-maple-river-thistle"]
    assert out["rendered"] and out["pairedBeforeRender"]


def test_a_chosen_token_is_stored_verbatim() -> None:
    """An admin-chosen token keeps its capitals, spaces and punctuation
    (percent-decoded from the URL, never canonicalised)."""
    out = _run("?room=kitchen&device_token=%20Maple%20Street%2C%201984!%20")
    assert out["pairs"] == ["Maple Street, 1984!"]


def test_the_token_is_taken_back_out_of_the_address() -> None:
    out = _run("?room=kitchen&device_token=acorn-maple&theme=light")
    assert out["replaced"] == ["/display.html?room=kitchen&theme=light"]


def test_a_kiosk_url_without_a_token_changes_nothing() -> None:
    out = _run("?room=kitchen")
    assert out["pairs"] == []
    assert out["replaced"] == []
    assert out["rendered"]


@pytest.mark.parametrize("search", ["?room=kitchen&device_token=", "?room=kitchen&device_token=%20"])
def test_an_empty_token_pairs_nothing_but_still_leaves_the_address(search) -> None:
    out = _run(search)
    assert out["pairs"] == []
    assert out["replaced"] == ["/display.html?room=kitchen"]


# ─── a browser that is already paired (REV-11 nit) ─────────────────────


def test_a_link_cannot_unpair_a_paired_browser() -> None:
    """``display.html?room=x&device_token=junk`` opened in a household
    browser: the junk is tried once and refused, and the stored token
    stays."""
    out = _run("?room=kitchen&device_token=junk", stored="the-real-token", probe_status="401")
    assert out["pairs"] == []
    assert len(out["probes"]) == 1
    # And it is still taken out of the address.
    assert out["replaced"] == ["/display.html?room=kitchen"]


@pytest.mark.parametrize("status", ["403", "429", "500", "network"])
def test_any_answer_short_of_acceptance_keeps_the_stored_token(status) -> None:
    out = _run("?room=kitchen&device_token=junk", stored="the-real-token", probe_status=status)
    assert out["pairs"] == []


@pytest.mark.parametrize("status", ["200", "404"])
def test_a_rotated_kiosk_url_still_re_pairs(status) -> None:
    """The kiosk's own case: an admin rotated the household token and put
    the new one in ``kiosk_url``. The server accepts it (404 is a room it
    does not know, past the gate), so it replaces the stale one."""
    out = _run("?room=kitchen&device_token=new-token", stored="old-token", probe_status=status)
    assert out["pairs"] == ["new-token"]


def test_the_probe_carries_the_url_token_alone() -> None:
    """No cookie (``credentials: 'omit'``) and no stored token can vouch for
    the candidate: the header is the URL's token, and nothing else rides."""
    out = _run("?room=kitchen&device_token=new-token", stored="old-token")
    (probe,) = out["probes"]
    assert probe["credentials"] == "omit"
    assert probe["headers"] == {"X-Device-Token": "new-token"}
    assert probe["url"] == "/api/satellites/kitchen"


def test_the_same_token_again_is_not_probed() -> None:
    out = _run("?room=kitchen&device_token=same", stored="same")
    assert out["pairs"] == []
    assert out["probes"] == []


def test_a_first_pairing_needs_no_probe() -> None:
    out = _run("?room=kitchen&device_token=first", stored="")
    assert out["pairs"] == ["first"]
    assert out["probes"] == []
    assert out["pairedBeforeRender"]


# ─── what the screen says about itself ─────────────────────────────────


def test_the_page_says_an_unpaired_screen_does_not_update() -> None:
    """The kiosk used to claim an unpaired screen "still shows what is
    playing"; it shows the first answer and freezes, because the state
    socket refuses it."""
    src = (REPO_ROOT / "web" / "static" / "display.jsx").read_text(encoding="utf-8")
    assert "still shows what is playing" not in src
    assert "stops" in src and "updating" in src
    doc = (REPO_ROOT / "satellite" / "VIDEO_SATELLITE.md").read_text(encoding="utf-8")
    assert "still shows what is playing" not in doc
