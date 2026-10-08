"""Documents "Open raw" / "Download" go to the SELECTED server (FE-6).

``withDeviceToken`` appends the household token of the server the
switcher selected (``Auth`` keys tokens per server), but ``docRawUrl``
built an origin-relative path, so the request — and the token — went to
whichever box served the page: box A's access log received box B's
household credential as ``?device_token=``. Every other ``withDeviceToken``
caller builds on ``API_BASE``; now these do too.

Runs the real ``web/static/files.jsx`` top level (compiled with the
vendored Babel) in a node vm with stub globals, then presses the two
buttons' helpers. No DB.
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
const [root, apiBase] = process.argv.slice(1);
const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);
const src = fs.readFileSync(path.join(root, 'web/static/files.jsx'), 'utf8');
const code = Babel.transform(src, { presets: ['react'], filename: 'files.jsx' }).code;
const out = { opened: [], clicked: [] };
const React = { createElement: () => ({}), useState: (v) => [v, () => {}], useEffect: () => {},
                useRef: (v) => ({ current: v }), useCallback: (f) => f, useMemo: (f) => f() };
const sandbox = {
  console, React, API_BASE: apiBase, fmtBytes: () => '',
  withDeviceToken: (u) => `${u}${u.includes('?') ? '&' : '?'}device_token=TOKEN-OF-${apiBase || 'ORIGIN'}`,
  document: {
    createElement: () => { const a = { click() { out.clicked.push(a.href); }, remove() {} }; return a; },
    body: { appendChild() {} }, head: { appendChild() {} },
  },
};
sandbox.window = { open: (url) => { out.opened.push(url); return null; } };
vm.createContext(sandbox);
vm.runInContext(code + `
;openDocInNewTab('My Doc.pdf');
downloadDoc('notes.md');
window.__urls = { raw: docRawUrl('a b.txt'), text: docTextUrl('a b.txt') };`, sandbox, { filename: 'files.jsx' });
out.urls = sandbox.window.__urls;
process.stdout.write(JSON.stringify(out));
"""


def _press(api_base: str) -> dict:
    node = shutil.which("node")
    assert node, "node is required (the JSX compile check already relies on it)"
    proc = subprocess.run(
        [node, "-e", HARNESS, str(REPO_ROOT), api_base],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_open_raw_and_download_go_to_the_selected_server() -> None:
    base = "http://box-b.lan:6369"
    out = _press(base)
    assert out["opened"] == [
        f"{base}/api/documents/raw/My%20Doc.pdf?device_token=TOKEN-OF-{base}"
    ]
    assert out["clicked"] == [
        f"{base}/api/documents/raw/notes.md?device_token=TOKEN-OF-{base}"
    ]
    # The token rides only to the server it belongs to.
    for url in out["opened"] + out["clicked"]:
        assert url.startswith(base + "/")


def test_same_origin_stays_relative() -> None:
    out = _press("")
    assert out["opened"] == ["/api/documents/raw/My%20Doc.pdf?device_token=TOKEN-OF-ORIGIN"]
    assert out["urls"] == {
        "raw": "/api/documents/raw/a%20b.txt",
        "text": "/api/documents/text/a%20b.txt",
    }


@pytest.mark.parametrize("base", ["http://box-b.lan:6369", ""])
def test_the_text_url_is_built_in_one_place(base) -> None:
    out = _press(base)
    assert out["urls"]["text"] == f"{base}/api/documents/text/a%20b.txt"
    src = (REPO_ROOT / "web/static/files.jsx").read_text(encoding="utf-8")
    # No caller prefixes API_BASE by hand any more (it would double it).
    assert "${API_BASE}${docTextUrl(" not in src
    assert "${API_BASE}${docRawUrl(" not in src
