"""The document editor's paste/drop picture rules (web/static/doc_editor.jsx).

Pasting a screenshot or dropping a picture file puts the picture in through
Insert photo's own path; text copied together with a picture keeps the normal
paste. The two pure helpers are cut out of the source between their markers
and run under node, as the sanitiser test does; fails, never skips, without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "web" / "static" / "doc_editor.jsx"

SCRIPT = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('// PASTE-HELPERS-START'), b = src.indexOf('// PASTE-HELPERS-END');
if (a < 0 || b < a) throw new Error('markers missing');
const api = new Function(src.slice(a, b) + '; return { imageFilesFrom, htmlIsOnlyPictures };')();
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const png = { type: 'image/png', name: 'a.png' };
const out = {
  html: cases.html.map((h) => api.htmlIsOnlyPictures(h)),
  filesOnly: api.imageFilesFrom({ files: [png, { type: 'text/plain' }] }).length,
  viaItems: api.imageFilesFrom({ files: [], items: [{ kind: 'file', type: 'image/png', getAsFile: () => png },
                                                   { kind: 'string', type: 'text/html' }] }).length,
  none: api.imageFilesFrom({ files: [{ type: 'application/pdf' }] }).length,
  noDt: api.imageFilesFrom(null).length,
};
process.stdout.write(JSON.stringify(out));
"""

HTML_CASES = [
    "",                                                                        # screenshot: no html
    "<html><body><!--StartFragment--><img src='https://x.example/p.png'><!--EndFragment--></body></html>",
    "<meta charset='utf-8'><img src=\"https://x.example/p.png\">",             # Copy image
    "<div><a href='https://x.example'><img src='https://x.example/p.png'></a></div>",
    "<p>Some words</p><img src='https://x.example/p.png'>",                    # text + picture
    "<table><tr><td><img src='x'></td></tr></table>",
    "<ul><li><img src='x'></li></ul>",
    "<span>caption</span><img src='x'>",
    "<style>p{color:red}</style><img src='x'>",
]


def test_paste_helpers():
    node = shutil.which("node")
    assert node, "node is required to check the editor's paste rules"
    res = subprocess.run([node, "-e", SCRIPT, str(SRC)], input=json.dumps({"html": HTML_CASES}),
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    out = json.loads(res.stdout)
    assert out["html"] == [True, True, True, True, False, False, False, False, True]
    assert out["filesOnly"] == 1 and out["viaItems"] == 1 and out["none"] == 0 and out["noDt"] == 0
