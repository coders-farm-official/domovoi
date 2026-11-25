"""A release's three Highlights: the one parser, standard library only.

``docs/release/vX.Y.Z.md`` carries them in a ``## Highlights`` section
right above ``## At a glance`` (docs/CONTRIBUTING.md, "The release
page"):

    ## Highlights

    - Chat looks things up on the web when the offline library has no answer.
    - Music keeps playing after you ask a question.
    - Friend Houses is open to everyone in the household, by voice too.

Two readers, one parser, so they can't disagree:

* a Domovoi server reads the file from git at the release's commit
  (domovoi/update_notice.py) and shows the bullets in Settings → System →
  About & updates. To it the file is UNTRUSTED text: anything off returns
  ``[]`` (no bullets), never an error, and what it returns is shown only as
  plain text;
* the release script (scripts/release/finish_release.py) refuses a release
  whose file :func:`parse_highlights` can't read, and also any Markdown in
  an item (:func:`markdown_problems`), since the page and the house show
  the words as they are.

Standard library only: finish_release.py imports this file by path.
"""

from __future__ import annotations

import re
import unicodedata

HEADING = "## Highlights"
#: The whole file, as git hands it over (``git cat-file -s``).
MAX_FILE_BYTES = 256 * 1024
#: The section, from the line after the heading to the next heading.
MAX_SECTION_BYTES = 2 * 1024
COUNT = 3
#: An item's length, in Unicode code points (see :func:`parse_highlights`).
MIN_CHARS = 8
MAX_CHARS = 80
#: Unicode categories no item may hold: controls, format characters (bidi
#: overrides, zero-width joiners and spaces), private use, surrogates, and
#: the line and paragraph separators.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})
_ITEM_RE = re.compile(r"^- (\S.*)\Z")
_SPACES_RE = re.compile(r" {2,}")
#: What the release check refuses in an item: Markdown, or HTML.
MARKDOWN_CHARS = "`*_[]<>|#\\"


def _lines(raw: bytes) -> list[str] | None:
    if len(raw) > MAX_FILE_BYTES:
        return None
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    return text.split("\n")


def section_lines(raw: bytes) -> list[str] | None:
    """The lines of the one ``## Highlights`` section, or None when the
    file is too big, not UTF-8, has no such heading or more than one, or
    the section is over 2 KiB."""
    lines = _lines(raw)
    if lines is None:
        return None
    at = [i for i, line in enumerate(lines) if line.rstrip() == HEADING]
    if len(at) != 1:
        return None
    out = []
    for line in lines[at[0] + 1:]:
        if line.startswith("#"):
            break
        out.append(line)
    if len("\n".join(out).encode("utf-8")) > MAX_SECTION_BYTES:
        return None
    return out


def _item(text: str) -> str | None:
    text = unicodedata.normalize("NFC", text)
    if any(unicodedata.category(c) in _REFUSED_CATEGORIES for c in text):
        return None
    text = _SPACES_RE.sub(" ", text).strip()
    return text if MIN_CHARS <= len(text) <= MAX_CHARS else None


def parse_highlights(raw: bytes) -> list[str]:
    """The three items of ``raw``'s ``## Highlights`` section, or ``[]``.

    The rules (design note UPDATE-NOTICES §4): at most 256 KiB, strict
    UTF-8 (a BOM dropped, CRLF read as LF), exactly one line that is
    ``## Highlights``, its section (up to the next line starting with
    ``#``) at most 2 KiB, every non-blank line in it ``- text`` (no
    nesting, no continuation lines, no ``*`` bullets), exactly 3 items,
    each NFC with no control, format, private-use, surrogate or separator
    character, runs of spaces collapsed, 8 to 80 characters. Anything else
    is ``[]``: the whole section, never a part of it.

    A "character" is a Unicode code point (Python's ``len``), after NFC:
    not a UTF-16 unit (JavaScript's ``.length``) and not a grapheme
    cluster, so an emoji outside the BMP counts once. Every reader (the
    web, the phones) counts the same way."""
    lines = section_lines(raw)
    if lines is None:
        return []
    items: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        m = _ITEM_RE.match(line)
        if not m:
            return []
        item = _item(m.group(1))
        if item is None:
            return []
        items.append(item)
    return items if len(items) == COUNT else []


def markdown_problems(items: list[str]) -> list[str]:
    """The release check's extra rule: no Markdown or HTML in an item (the
    house and the page show the words as they are)."""
    out = []
    for item in items:
        bad = sorted({c for c in item if c in MARKDOWN_CHARS})
        if bad:
            out.append(f"the highlight \"{item}\" has {' '.join(bad)}: plain words only, no Markdown")
    return out
