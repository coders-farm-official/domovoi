"""Chat replies render as Markdown (web/static/chat_markdown.js).

The reply is model output, so it is data: these cases run the REAL
renderer — the vendored ``marked`` in its own instance, then
``sanitize_html.js`` — through ``markdown_preview_harness.js`` and assert
on the exact string the chat bubble hands to the DOM.

No DB. Needs ``node``, like the document-preview test beside it.
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
    "reply": {"chat": (
        "# Raw chicken\n\n"
        "Short answer: **no**, and *not* raw.\n\n"
        "1. **Food safety:** bacteria\n2. **Bones:** splinter\n\n"
        "* cooked breast\n* sweet potato\n\n"
        "> ask your vet\n\n"
        "Use `boil()` first:\n\n```\nboil(chicken)\n```\n\n"
        "See [the guide](https://example.org/cats).\n"
    )},
    "table": {"chat": (
        "| food | ok? | kcal |\n"
        "|:-----|:---:|-----:|\n"
        "| cooked chicken | yes | 165 |\n"
        "| raw chicken | no | 120 |\n"
    )},
    "line_breaks": {"chat": "first line\nsecond line"},
    "raw_html": {"chat": "a <b>bold</b> <script>alert(1)</script> <img src=x onerror=alert(1)>"},
    "image": {"chat": "look: ![a cat](https://example.org/cat.png)"},
    "js_link": {"chat": "[click](javascript:alert(1))"},
    "half_streamed": {"chat": "so **far this is\n\n| a | b |\n|---"},
}


@pytest.fixture(scope="module")
def html() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render chat markdown (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(CASES)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return {k: v["sanitized"] for k, v in json.loads(proc.stdout).items()}


def test_a_typical_reply_renders_as_markdown(html) -> None:
    out = html["reply"]
    for fragment in ("<h1", "<strong>no</strong>", "<em>not</em>", "<ol>", "<ul>", "<li>",
                     "<blockquote>", "<code>boil()</code>", "<pre>"):
        assert fragment in out, f"{fragment} missing from {out}"


def test_tables_render_with_their_alignment(html) -> None:
    out = html["table"]
    for fragment in ("<table>", "<thead>", "<th", "<td", "cooked chicken", "165"):
        assert fragment in out, f"{fragment} missing from {out}"
    assert 'align="center"' in out and 'align="right"' in out


def test_single_newlines_stay_line_breaks(html) -> None:
    assert "<br" in html["line_breaks"]


def test_links_open_in_a_new_tab_and_stay_safe(html) -> None:
    out = html["reply"]
    assert 'href="https://example.org/cats"' in out
    assert 'target="_blank"' in out
    assert "noopener" in out
    assert "javascript:" not in html["js_link"].lower()


def test_raw_html_in_a_reply_is_shown_as_text(html) -> None:
    out = html["raw_html"]
    assert "<b>" not in out and "<script" not in out.lower() and "<img" not in out
    assert "&lt;b&gt;bold&lt;/b&gt;" in out      # escaped once: reads as "<b>bold</b>"


def test_images_are_not_rendered_yet(html) -> None:
    out = html["image"]
    assert "<img" not in out
    assert "a cat" in out


def test_half_streamed_markdown_still_renders(html) -> None:
    out = html["half_streamed"]
    assert "so **far" in out          # an unclosed marker stays literal
    assert "<script" not in out.lower()


@pytest.mark.parametrize("case", sorted(CASES))
def test_no_reply_carries_a_script_or_a_live_handler(html, case) -> None:
    out = html[case]
    assert "<script" not in out.lower()
    # Any "onerror=" left is escaped text inside the paragraph, never an attribute.
    for tag in re.findall(r"<[a-z][^>]*>", out, re.IGNORECASE):
        assert not ON_HANDLER.search(tag), tag
