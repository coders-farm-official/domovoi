"""A release's three Highlights, read as untrusted text
(domovoi/release_highlights.py; design note UPDATE-NOTICES §4).

Pure: no DB, no git, no network. The house and the release script share
this parser; anything off is ``[]``, never an error.
"""

from __future__ import annotations

import pytest

from domovoi.release_highlights import markdown_problems, parse_highlights

GOOD = [
    "Chat looks things up on the web when the offline library has no answer.",
    "Music keeps playing after you ask a question.",
    "Friend Houses is open to everyone in the household, by voice too.",
]


def _doc(items=GOOD, *, head="## Highlights", before="# Domovoi 1.5.0\n\nIntro.\n\n",
         after="\n## At a glance\n\n- **More**, with Markdown.\n") -> str:
    body = "\n".join(f"- {i}" for i in items)
    return f"{before}{head}\n\n{body}\n{after}"


def _parse(text: str) -> list[str]:
    return parse_highlights(text.encode("utf-8"))


def test_the_good_case():
    assert _parse(_doc()) == GOOD


def test_the_section_may_end_the_file_and_hold_blank_lines():
    text = "## Highlights\n\n- " + GOOD[0] + "\n\n- " + GOOD[1] + "\n- " + GOOD[2]
    assert _parse(text) == GOOD


@pytest.mark.parametrize("text", [
    "# Domovoi 1.4.1\n\n## At a glance\n\n- one\n",          # no section: the v1.4.1 tag's own file
    _doc(head="## Highlight"),
    _doc(head="### Highlights"),
    _doc(head="##Highlights"),
    "",
])
def test_no_section_is_no_bullets(text):
    assert _parse(text) == []


def test_two_highlights_headings_are_refused():
    assert _parse(_doc() + "\n## Highlights\n\n- " + "\n- ".join(GOOD) + "\n") == []


@pytest.mark.parametrize("items", [GOOD[:2], GOOD + ["A fourth highlight here."], []])
def test_exactly_three_items(items):
    assert _parse(_doc(items)) == []


@pytest.mark.parametrize("bad", [
    "- " + GOOD[0] + "\n  - nested item under it",
    "- " + GOOD[0] + "\n  continued on the next line",
    "* " + GOOD[0],
    "+ " + GOOD[0],
    "-" + GOOD[0],
    "-  " + GOOD[0],
    "Prose in the section.",
    "1. " + GOOD[0],
])
def test_only_plain_dash_items(bad):
    text = "## Highlights\n\n" + bad + "\n- " + GOOD[1] + "\n- " + GOOD[2] + "\n"
    assert _parse(text) == []


def test_8_and_80_characters_pass_7_and_81_do_not():
    ok = ["a" * 8, "b" * 80, "c" * 40]
    assert _parse(_doc(ok)) == ok
    assert _parse(_doc(["a" * 7, "b" * 40, "c" * 40])) == []
    assert _parse(_doc(["a" * 81, "b" * 40, "c" * 40])) == []


@pytest.mark.parametrize("char", [
    "\x07",        # Cc
    "\t",          # Cc
    "‮",      # Cf: right-to-left override
    "⁦",      # Cf: left-to-right isolate
    "​",      # Cf: zero-width space
    "‍",      # Cf: zero-width joiner
    "﻿",      # Cf: a BOM inside an item
    "",      # Co
    " ",      # Zl
    " ",      # Zp
])
def test_control_format_and_separator_characters_refuse_the_section(char):
    assert _parse(_doc([GOOD[0], f"Music keeps{char}playing on.", GOOD[2]])) == []


def test_a_lone_surrogate_never_decodes():
    raw = _doc().encode("utf-8").replace(b"Music", b"\xed\xa0\x80usic")
    assert parse_highlights(raw) == []


def test_invalid_utf8_is_no_bullets():
    assert parse_highlights(_doc().encode("utf-8") + b"\xff\xfe") == []
    assert parse_highlights(_doc().encode("latin-1", "replace").replace(b"a", b"\xe9", 1)) == []


def test_a_257_kib_file_is_no_bullets():
    pad = "x" * (257 * 1024)
    assert _parse(_doc(after=f"\n## At a glance\n\n{pad}\n")) == []
    # The same section in a file under the cap reads.
    assert _parse(_doc(after=f"\n## At a glance\n\n{'x' * 1000}\n")) == GOOD


def test_a_section_over_2_kib_is_no_bullets():
    blank_lines = "\n" * 2100
    text = "## Highlights\n\n- " + "\n- ".join(GOOD) + blank_lines + "## At a glance\n"
    assert _parse(text) == []


def test_crlf_and_a_bom():
    text = "﻿" + _doc().replace("\n", "\r\n")
    assert _parse(text) == GOOD


def test_nfc_and_runs_of_spaces():
    decomposed = "Café music keeps   playing."
    out = _parse(_doc([GOOD[0], decomposed, GOOD[2]]))
    assert out[1] == "Café music keeps playing."


def test_html_and_markdown_come_back_as_text_for_the_page_to_show_plainly():
    """The house shows the words as they are (a React text node): a <b>
    is text. Only the release check refuses Markdown."""
    items = ["Music <b>keeps</b> playing.", GOOD[1], GOOD[2]]
    assert _parse(_doc(items)) == items
    assert markdown_problems(items) and "<" in markdown_problems(items)[0]
    assert markdown_problems(GOOD) == []
    for c in "`*_[]<>|#\\":
        assert markdown_problems([f"Some words {c} here."]), c


def test_a_heading_with_trailing_spaces_still_counts():
    assert _parse(_doc(head="## Highlights   ")) == GOOD
