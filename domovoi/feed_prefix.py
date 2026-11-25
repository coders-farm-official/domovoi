"""Make a readable feed out of the first part of one that is too big.

A podcast feed with thousands of episodes can run to tens of MB (one
real show's is 30 MB). The fetchers keep at most a bounded prefix of the
document in memory (``net_safety.fetch_bytes_sync(..., truncate=True)``);
this module turns that prefix into a complete document a feed parser can
read:

1. find the last closing ``</item>`` (RSS) or ``</entry>`` (Atom, any
   namespace prefix) that is a real tag, not text inside a CDATA section
   or a comment;
2. cut there, dropping the half item after it;
3. close the containers that were open before the first item
   (``</channel></rss>``, ``</feed>``, ``</rdf:RDF>``), read from the
   document itself rather than assumed.

Feeds list their newest items first in practice, so the prefix holds the
newest episodes; the caller checks the order it actually got (see
``podcast_feed_poller``). When the prefix holds no complete item at all,
:class:`NoCompleteItem` says so and nothing is guessed.
"""

from __future__ import annotations

import bisect
import re

__all__ = ["NoCompleteItem", "cut_to_last_item"]


class NoCompleteItem(ValueError):
    """The prefix ends before its first item does: there is nothing whole
    to read."""


_NAME = rb"(?:[A-Za-z_][\w.-]*:)?"
# A closing item/entry tag, with or without a namespace prefix.
_ITEM_CLOSE = re.compile(rb"</" + _NAME + rb"(?:item|entry)\s*>")
# The first item/entry opening tag (``<items>`` in RSS 1.0 is not one).
_ITEM_OPEN = re.compile(rb"<" + _NAME + rb"(?:item|entry)(?=[\s/>])")
# Sections whose text is not markup.
_OPAQUE_START = re.compile(rb"<!\[CDATA\[|<!--")
_OPAQUE_END = {b"<![CDATA[": b"]]>", b"<!--": b"-->"}
# The longest head (everything before the first item) that is read for
# its open containers. Real heads are a few KB.
MAX_HEAD_BYTES = 256 * 1024
# Pieces of an element tag in the head, each matched anchored at one
# position and never re-read: the head is scanned once, left to right.
# Attribute values are quoted, so a ">" inside one does not end the tag.
_TAG_NAME = re.compile(rb"(/?)([A-Za-z_][\w.:-]*)")
_ATTR_NAME = re.compile(rb"\s+[^\s=/<>]+")
_ATTR_EQ = re.compile(rb"\s*=\s*")
_TAG_END = re.compile(rb"\s*(/?)>")


def _opaque_spans(data: bytes) -> list[tuple[int, int]]:
    """(start, end) of every CDATA section and comment, in order; an
    unterminated one runs to the end of ``data``."""
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        m = _OPAQUE_START.search(data, pos)
        if m is None:
            return spans
        close = _OPAQUE_END[m.group(0)]
        end = data.find(close, m.end())
        end = len(data) if end < 0 else end + len(close)
        spans.append((m.start(), end))
        pos = end


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    # Binary search: spans are sorted and disjoint.
    lo, hi = 0, len(spans)
    while lo < hi:
        mid = (lo + hi) // 2
        start, end = spans[mid]
        if pos < start:
            hi = mid
        elif pos >= end:
            lo = mid + 1
        else:
            return True
    return False


def _first_real(pattern: re.Pattern[bytes], data: bytes, spans) -> re.Match[bytes] | None:
    for m in pattern.finditer(data):
        if not _inside(spans, m.start()):
            return m
    return None


def _span_end(spans: list[tuple[int, int]], pos: int) -> int | None:
    """The end of the span ``pos`` is inside, or None."""
    i = bisect.bisect_right(spans, pos, key=lambda span: span[0]) - 1
    if i >= 0 and spans[i][0] <= pos < spans[i][1]:
        return spans[i][1]
    return None


def _tags(head: bytes, spans: list[tuple[int, int]]):
    """``(closing, name, selfclosing)`` of each element tag in ``head``
    outside CDATA and comments, in order. One pass: a position is never
    read twice, and a malformed tag is skipped from where it stopped."""
    pos = 0
    n = len(head)
    while True:
        lt = head.find(b"<", pos)
        if lt < 0:
            return
        end = _span_end(spans, lt)
        if end is not None:
            pos = end
            continue
        m = _TAG_NAME.match(head, lt + 1)
        if m is None:
            pos = lt + 1
            continue
        closing, name = m.group(1), m.group(2)
        pos = m.end()
        while True:
            a = _ATTR_NAME.match(head, pos)
            if a is None:
                break
            pos = a.end()
            eq = _ATTR_EQ.match(head, pos)
            if eq is None or eq.end() >= n or head[eq.end()] not in b"\"'":
                continue
            close = head.find(head[eq.end():eq.end() + 1], eq.end() + 1)
            if close < 0:
                return  # an unclosed quote: no tag ends after it
            pos = close + 1
        e = _TAG_END.match(head, pos)
        if e is None:
            continue  # not a tag; go on from where it stopped
        pos = e.end()
        yield closing, name, e.group(1)


def _open_containers(head: bytes, spans: list[tuple[int, int]]) -> list[bytes]:
    """Names of the elements still open at the end of ``head``, outermost
    first (``[b"rss", b"channel"]``)."""
    stack: list[bytes] = []
    for closing, name, selfclosing in _tags(head, spans):
        if selfclosing:
            continue
        if not closing:
            stack.append(name)
        elif name in stack:
            # Pop to the matching open tag (tolerates a stray unclosed one).
            while stack and stack.pop() != name:
                pass
    return stack


def cut_to_last_item(data: bytes, *, max_items: int | None = None) -> bytes:
    """``data`` (the start of a feed) cut after its last complete item, or
    after its ``max_items``-th when that comes first, and closed into a
    whole document. Raises :class:`NoCompleteItem` when no item closes
    inside ``data``."""
    spans = _opaque_spans(data)
    first = _first_real(_ITEM_OPEN, data, spans)
    if first is None:
        raise NoCompleteItem("no item starts in the part that was read")
    last_close = None
    count = 0
    for m in _ITEM_CLOSE.finditer(data, first.end()):
        if not _inside(spans, m.start()):
            last_close = m
            count += 1
            if max_items is not None and count >= max_items:
                break
    if last_close is None:
        raise NoCompleteItem("no item ends in the part that was read")
    if first.start() > MAX_HEAD_BYTES:
        raise NoCompleteItem("the part before the first item is too long")
    containers = _open_containers(data[: first.start()], spans)
    tail = b"".join(b"</" + name + b">" for name in reversed(containers))
    return data[: last_close.end()] + b"\n" + tail + b"\n"
