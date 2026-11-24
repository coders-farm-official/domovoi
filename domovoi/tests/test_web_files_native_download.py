"""The Files page's Download is the BROWSER's download, not a buffered Blob.

It used to fetch the whole file (or a whole folder's zip) into the page's
memory and save it from a Blob: a large audiobook folder looked like
nothing happening and could run a phone out of memory, and a refusal came
back as a bare "401". Now the page mints a one-use link through apiPost
(which owns the sign-in / pair prompt and its replay) and hands it to the
browser, and a failure is a plain sentence.

Runs the real ``web/static/files.jsx`` top level (vendored Babel) in a node
vm with stub globals. No DB.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const [root, apiBase] = process.argv.slice(1);
const babelMod = require(path.join(root, 'web/static/vendor/babel/babel.min.js'));
const Babel = babelMod.transform ? babelMod : (global.Babel || babelMod.default || babelMod);
const src = fs.readFileSync(path.join(root, 'web/static/files.jsx'), 'utf8');
const code = Babel.transform(src, { presets: ['react'], filename: 'files.jsx' }).code;
const out = { posts: [], clicked: [] };
const React = { createElement: () => ({}), useState: (v) => [v, () => {}], useEffect: () => {},
                useRef: (v) => ({ current: v }), useCallback: (f) => f, useMemo: (f) => f() };
const err = (status, extra) => Object.assign(new Error(`${status} x`), { status }, extra || {});
const sandbox = {
  console, React, API_BASE: apiBase, fmtBytes: () => '',
  apiPost: async (p, body) => {
    out.posts.push([p, body]);
    return { url: '/api/files/download?download_token=T1', expires_in: 60 };
  },
  document: {
    createElement: () => {
      const a = { click() { out.clicked.push({ href: a.href, download: a.download }); }, remove() {} };
      return a;
    },
    body: { appendChild() {} }, head: { appendChild() {} },
  },
  fetch: () => { throw new Error('the download must not be fetched by the page'); },
};
sandbox.window = {};
vm.createContext(sandbox);
vm.runInContext(code + `
;window.__run = async () => {
  await nativeFileDownload('core:audiobooks', 'Some Book');
  return {
    unpaired: downloadFailureText(err401dev, false),
    folder: downloadFailureText(err401, true),
    file: downloadFailureText(err401, false),
    big: downloadFailureText(err413, true),
    other: downloadFailureText(err500, false),
  };
};`, Object.assign(sandbox, {
  err401dev: err(401, { deviceTokenRequired: true }),
  err401: err(401), err413: err(413), err500: err(500),
}), { filename: 'files.jsx' });
sandbox.window.__run().then((texts) => {
  out.texts = texts;
  process.stdout.write(JSON.stringify(out));
}, (e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
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


def test_download_mints_a_link_and_hands_it_to_the_browser() -> None:
    base = "http://192.168.1.20:6369"
    out = _press(base)
    assert out["posts"] == [
        ["/api/files/download-ticket", {"library_id": "core:audiobooks", "path": "Some Book"}]
    ]
    assert out["clicked"] == [
        {"href": f"{base}/api/files/download?download_token=T1", "download": ""}
    ]


def test_a_refused_download_says_what_to_do_never_a_bare_status() -> None:
    texts = _press("")["texts"]
    assert "isn’t paired" in texts["unpaired"] and "Pair it" in texts["unpaired"]
    # A folder is device tier like a file: the same sentence, never "admin only".
    assert texts["folder"] == texts["file"] == texts["unpaired"]
    assert "admin sign-in" not in texts["folder"]
    assert "too many files" in texts["big"]
    for key in ("unpaired", "folder", "file", "big"):
        assert not re.search(r"\b40[13]\b", texts[key]), texts[key]
    assert texts["other"].startswith("Download failed:")


def test_files_page_buffers_no_download_in_memory() -> None:
    src = (REPO_ROOT / "web/static/files.jsx").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.S)
    assert "new Blob(" not in code and ".blob()" not in code
    assert "createObjectURL" not in code
    # The page says something at once while the link is being made.
    assert "Preparing download…" in code and 'className="dv-spin"' in code
