"""The browser's copy of the document allowlist (sanitize_html.js,
``sanitizeDocumentHtml``) holds on the same corpus as the server's.

The server (domovoi/richdoc/sanitize.py) is the guard; this copy is what
keeps a pasted or opened file from reaching the page raw before the server
sees it, so it must refuse the same things. Needs ``node`` (as the JSX
compile check does); fails, never skips, without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_documents_formatted_hardening import NEVER, XSS

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("document_sanitiser_harness.js")


def _clean(items: list[str]) -> list[str]:
    node = shutil.which("node")
    assert node, "node is required to run the dashboard's sanitiser"
    out = subprocess.run([node, str(HARNESS), str(REPO)], input=json.dumps(items),
                         capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def cleaned() -> dict[str, str]:
    extra = [
        '<img src="https://t.example/p.gif" data-pending="https://t.example/p.gif">',
        '<img src="data:image/png;base64,AAAA" data-pending="javascript:alert(1)">',
        '<span style="font-family: x; background-image: u&#114;l(//e)">x</span>',
        '<figure data-width="50%; x" data-align="left&quot; onclick=&quot;x"><img src="data:image/png;base64,AA"></figure>',
    ]
    items = XSS + extra
    return dict(zip(items, _clean(items)))


@pytest.mark.parametrize("markup", XSS, ids=range(len(XSS)))
def test_the_browser_copy_refuses_the_same_corpus(cleaned, markup):
    """Checked in the MARKUP it writes: a tag it could not read comes out
    as escaped text (``&lt;p/onclick…&gt;``), which is words on the page."""
    import re

    tags = " ".join(re.findall(r"<[^>]*>", cleaned[markup])).lower()
    for word in NEVER:
        assert word not in tags, (word, cleaned[markup])
    assert "<" not in re.sub(r"<[^>]*>", "", cleaned[markup])


def test_pending_pictures_and_entity_tricks(cleaned):
    vals = list(cleaned.values())[len(XSS):]
    assert vals[0] == ""  # an outside picture never survives as a picture
    assert "javascript" not in vals[1] and "data-pending" not in vals[1]
    assert "url" not in vals[2].lower() and "style" not in vals[2]
    assert "onclick" not in vals[3] and "data-width" not in vals[3] and "data-align" not in vals[3]
