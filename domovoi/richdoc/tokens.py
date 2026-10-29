"""A linear HTML tokenizer for formatted documents (sanitising and reading).

The standard library's ``html.parser`` is not used: on hostile input it
is slow (repeated ``<!--``, ``<a``, ``</`` take seconds to minutes per
megabyte; CVE-2025-6069 is one such case, fixed in later 3.12/3.13
releases than some servers run), and some malformed declarations
(``<![ x``) raise ``AssertionError`` out of ``feed()``. A document is
somebody else's bytes, so this module reads them with nothing that can
go quadratic and nothing that raises:

* every step finds its end with ``str.find`` or an anchored regex match
  that only consumes what it returns, so the work is linear in the input;
* comments, doctypes, ``<![CDATA[`` and processing instructions are
  skipped whole (to the end of the input when unterminated);
* a tag that never closes (``<p title="…`` to the end) ends the markup:
  the rest is text, which a caller escapes (fail closed);
* the raw-text elements (``script``, ``style``, ``textarea``, …) run to
  their end tag without being read as markup, as a browser reads them.

It does NOT build a tree or fix nesting: the sanitiser and the reader do
that over the token stream. Python 3.11+ (the project's floor); nothing
here depends on the interpreter's ``html.parser`` fixes.
"""

from __future__ import annotations

import html
import re
from typing import Iterator, NamedTuple

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9:_-]*")
_ATTR_NAME = re.compile(r"[^\s/>\"'=]+")
_UNQUOTED = re.compile(r"[^\s>]*")
_SPACE = re.compile(r"[\s/]*")

#: Elements whose content is text up to their own end tag.
RAW_TEXT = frozenset({
    "script", "style", "textarea", "title", "xmp", "iframe", "noembed",
    "noframes", "noscript", "plaintext",
})


_RAW_END: dict[str, "re.Pattern[str]"] = {}


def _raw_end(name: str) -> "re.Pattern[str]":
    pat = _RAW_END.get(name)
    if pat is None:
        pat = _RAW_END[name] = re.compile("</" + re.escape(name), re.I)
    return pat


class Token(NamedTuple):
    kind: str                         # "start" | "end" | "text"
    name: str = ""                    # tag name, lower case
    attrs: tuple = ()                 # ((name, value), …) values entity-decoded
    data: str = ""                    # text, entity-decoded
    self_closing: bool = False


def _attributes(s: str, j: int) -> tuple[list[tuple[str, str]], int, bool] | None:
    """The attributes of a tag from ``j`` (just past its name): the list,
    the index past the ``>``, and whether it ended ``/>``. None when the
    tag never closes."""
    n = len(s)
    attrs: list[tuple[str, str]] = []
    while True:
        m = _SPACE.match(s, j)
        self_closing = j < n and "/" in s[j:m.end()]
        j = m.end()
        if j >= n:
            return None
        if s[j] == ">":
            return attrs, j + 1, self_closing
        m = _ATTR_NAME.match(s, j)
        if not m:  # a lone quote or '=' where a name should be: skip it
            j += 1
            continue
        name = m.group().lower()
        j = m.end()
        while j < n and s[j] in " \t\n\r\f":
            j += 1
        value = ""
        if j < n and s[j] == "=":
            j += 1
            while j < n and s[j] in " \t\n\r\f":
                j += 1
            if j < n and s[j] in "\"'":
                close = s.find(s[j], j + 1)
                if close == -1:
                    return None
                value = s[j + 1:close]
                j = close + 1
            else:
                m = _UNQUOTED.match(s, j)
                value = m.group()
                j = m.end()
        attrs.append((name, html.unescape(value)))


def tokenize(markup: str) -> Iterator[Token]:
    """The tokens of ``markup``, in order (see the module docstring)."""
    s = str(markup or "")
    n = len(s)
    i = 0
    while i < n:
        lt = s.find("<", i)
        if lt == -1:
            yield Token("text", data=html.unescape(s[i:]))
            return
        if lt > i:
            yield Token("text", data=html.unescape(s[i:lt]))
        if s.startswith("<!--", lt):
            end = s.find("-->", lt + 4)
            i = n if end == -1 else end + 3
            continue
        nxt = s[lt + 1:lt + 2]
        if nxt in ("!", "?"):
            end = s.find(">", lt + 2)
            i = n if end == -1 else end + 1
            continue
        closing = nxt == "/"
        m = _NAME.match(s, lt + (2 if closing else 1))
        if not m:
            yield Token("text", data="<")
            i = lt + 1
            continue
        name = m.group().lower()
        parsed = _attributes(s, m.end())
        if parsed is None:
            # Never closes: what follows is text, not markup.
            yield Token("text", data=s[lt:])
            return
        attrs, after, self_closing = parsed
        if closing:
            yield Token("end", name=name)
            i = after
            continue
        yield Token("start", name=name, attrs=tuple(attrs), self_closing=self_closing)
        i = after
        if name in RAW_TEXT and not self_closing:
            # Searched in the ORIGINAL string, case-insensitively: offsets
            # into s.lower() are wrong wherever lowercasing changes a
            # character's length ("İ".lower() is two characters).
            m_end = _raw_end(name).search(s, i)
            if m_end is None:
                body, i = s[i:], n
            else:
                end = m_end.start()
                close = s.find(">", end)
                body, i = s[i:end], (n if close == -1 else close + 1)
            if body:
                yield Token("text", data=body)
            yield Token("end", name=name)
