"""The kiosk display pairs itself from its own URL (WEB-15).

Once the room's row, the live push and the transport verbs answer only a
household credential, a video satellite's kiosk (``display.html``) — which
nobody sits in front of to pair — has to carry the token in the URL its
launcher opens: ``/display.html?room=<room>&device_token=<token>``. The
page stores it exactly as the pair modal stores a pasted token
(``Auth.pair``: verbatim bar the outer whitespace) BEFORE anything renders,
so the first read and the ``/ws/state`` handshake already carry it, and
takes it back out of the address.

Runs the real ``web/static/display.jsx`` top level, compiled with the
vendored Babel, in a node vm with a stub window / Auth / React. No DB.
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
const [root, search] = process.argv.slice(1);
const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);
const src = fs.readFileSync(path.join(root, 'web/static/display.jsx'), 'utf8');
const code = Babel.transform(src, { presets: ['react'], filename: 'display.jsx' }).code;
const out = { pairs: [], replaced: [], rendered: false, pairedBeforeRender: null };
const React = { createElement: (t, p, ...c) => ({ t, p, c }), useState: (v) => [v, () => {}],
                useEffect: () => {}, useRef: (v) => ({ current: v }), useCallback: (f) => f };
const sandbox = {
  URLSearchParams, console, React,
  ReactDOM: { createRoot: () => ({ render: () => {
    out.rendered = true; out.pairedBeforeRender = out.pairs.length > 0; } }) },
  document: { documentElement: { dataset: {} }, getElementById: () => ({}) },
  Auth: { pair: (v) => { out.pairs.push(v); return true; } },
  API_BASE: '',
};
sandbox.window = {
  location: { search, pathname: '/display.html', hash: '' },
  history: { replaceState: (_s, _t, url) => out.replaced.push(url) },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'display.jsx' });
process.stdout.write(JSON.stringify(out));
"""


def _run(search: str) -> dict:
    node = shutil.which("node")
    assert node, "node is required (the JSX compile check already relies on it)"
    proc = subprocess.run(
        [node, "-e", HARNESS, str(REPO_ROOT), search],
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
