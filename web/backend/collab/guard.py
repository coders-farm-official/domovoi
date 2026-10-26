"""Every Yjs update is read here BEFORE pycrdt sees it.

Why: yrs (the Rust core under pycrdt) walks two kinds of nesting
recursively, with no depth limit, and a deep enough structure overflows the
native stack and kills the whole web process (0xC00000FD on Windows,
SIGSEGV on Linux) — not an exception anything here could catch
(security review of wf/doc-collab, 2026-10-26, F1):

* **values** (ContentAny, and the JSON in ContentJSON / embeds / format
  attributes): ``[[[[...]]]]`` 10,000 deep is a 20 KB update that crashes
  ``apply_update`` itself;
* **shared types** nested in shared types (an XmlElement in an XmlElement
  in ...): 20,000 deep applies fine, then crashes the first time anything
  deletes it (a delete set of a few bytes) or prints it.

So this module reads a Yjs v1 update the way Yjs's own decoder does, with
loops and explicit stacks only, and refuses (``UpdateRejected``):

* anything malformed or truncated, trailing bytes, sub-documents, unknown
  content or type references, zero-length structs;
* a value nested deeper than ``MAX_VALUE_DEPTH``;
* a struct nested deeper than ``MAX_TYPE_DEPTH`` shared types below a root.

Type depth needs to know where every existing struct lives, because Yjs
only writes an item's parent when it has no left or right neighbour (the
others take their neighbour's parent). A :class:`StructIndex` per room
remembers, for every struct range the document holds, its depth (and
whether it is a shared type or garbage-collected); an update is resolved
against it and its own structs, and an update whose structs refer to
something neither has is refused rather than guessed at. Nothing is ever
applied that this module has not resolved.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Optional

from web.backend.collab.protocol import ProtocolError

MAX_VALUE_DEPTH = 64
MAX_TYPE_DEPTH = 64
MAX_STRUCTS = 200_000          # in one update
_MAX_UINT = 2 ** 53


class UpdateRejected(ProtocolError):
    """An update this server will not apply."""


class _Reader:
    __slots__ = ("buf", "pos", "end")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0
        self.end = len(buf)

    def remaining(self) -> int:
        return self.end - self.pos

    def u8(self) -> int:
        if self.pos >= self.end:
            raise UpdateRejected("truncated update")
        b = self.buf[self.pos]
        self.pos += 1
        return b

    def var_uint(self) -> int:
        value = 0
        shift = 0
        for _ in range(10):
            b = self.u8()
            value |= (b & 0x7F) << shift
            if b < 0x80:
                if value > _MAX_UINT:
                    raise UpdateRejected("number out of range")
                return value
            shift += 7
        raise UpdateRejected("number too long")

    def skip_var_int(self) -> None:
        for _ in range(10):
            if self.u8() < 0x80:
                return
        raise UpdateRejected("number too long")

    def take(self, n: int) -> bytes:
        if n > self.remaining():
            raise UpdateRejected("truncated update")
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return bytes(out)

    def var_bytes(self) -> bytes:
        return self.take(self.var_uint())

    def var_string(self) -> str:
        raw = self.var_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise UpdateRejected("text is not UTF-8") from None


def _skip_any(r: _Reader) -> None:
    """One lib0 ``writeAny`` value, without recursion."""
    stack: list[list] = [[1, False]]           # [values left, is an object]
    while stack:
        top = stack[-1]
        if top[0] == 0:
            stack.pop()
            continue
        top[0] -= 1
        if top[1]:
            r.var_string()                     # the key
        t = r.u8()
        if t in (127, 126, 121, 120):          # undefined, null, false, true
            continue
        if t == 125:
            r.skip_var_int()
        elif t == 124:
            r.take(4)
        elif t in (123, 122):
            r.take(8)
        elif t == 119:
            r.var_string()
        elif t == 116:
            r.var_bytes()
        elif t in (117, 118):                  # array, object
            n = r.var_uint()
            if n > r.remaining():
                raise UpdateRejected("truncated update")
            stack.append([n, t == 118])
            if len(stack) > MAX_VALUE_DEPTH + 1:
                raise UpdateRejected("value nested too deep")
        else:
            raise UpdateRejected("unknown value type")


def _check_json(text: str) -> None:
    """Nesting depth of a JSON text (Yjs ships embeds, format attributes and
    ContentJSON as JSON strings, which yrs parses)."""
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_VALUE_DEPTH:
                raise UpdateRejected("value nested too deep")
        elif ch in "]}":
            depth -= 1


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


# Shared type references (Yjs typeRefs): Array, Map, Text, XmlElement,
# XmlFragment, XmlHook, XmlText.
_TYPE_REFS = range(0, 7)


@dataclass
class _Struct:
    client: int
    clock: int
    length: int
    gc: bool = False                      # a GC struct, or will be garbage
    is_type: bool = False
    root: bool = False
    parent: Optional[tuple[int, int]] = None
    origin: Optional[tuple[int, int]] = None
    right: Optional[tuple[int, int]] = None
    depth: Optional[int] = None           # shared types between it and its root


def _read_content(r: _Reader, ref: int) -> tuple[int, bool]:
    """(length, is a shared type) of one item's content."""
    if ref == 1:                               # Deleted
        return r.var_uint(), False
    if ref == 2:                               # JSON
        n = r.var_uint()
        if n > r.remaining():
            raise UpdateRejected("truncated update")
        for _ in range(n):
            _check_json(r.var_string())
        return n, False
    if ref == 3:                               # Binary
        r.var_bytes()
        return 1, False
    if ref == 4:                               # String
        return _utf16_len(r.var_string()), False
    if ref == 5:                               # Embed
        _check_json(r.var_string())
        return 1, False
    if ref == 6:                               # Format
        r.var_string()
        _check_json(r.var_string())
        return 1, False
    if ref == 7:                               # Type
        tref = r.var_uint()
        if tref not in _TYPE_REFS:
            raise UpdateRejected("unknown shared type")
        if tref in (3, 5):                     # XmlElement name, XmlHook key
            r.var_string()
        return 1, True
    if ref == 8:                               # Any
        n = r.var_uint()
        if n > r.remaining():
            raise UpdateRejected("truncated update")
        for _ in range(n):
            _skip_any(r)
        return n, False
    if ref == 9:
        raise UpdateRejected("sub-documents are not supported")
    raise UpdateRejected("unknown content")


def _parse(update: bytes) -> list[_Struct]:
    r = _Reader(update)
    out: list[_Struct] = []
    n_clients = r.var_uint()
    if n_clients > r.remaining():
        raise UpdateRejected("truncated update")
    for _ in range(n_clients):
        n_structs = r.var_uint()
        client = r.var_uint()
        clock = r.var_uint()
        if n_structs > r.remaining():
            raise UpdateRejected("truncated update")
        for _ in range(n_structs):
            if len(out) >= MAX_STRUCTS:
                raise UpdateRejected("too many changes in one update")
            info = r.u8()
            ref = info & 0x1F
            if ref in (0, 10):                 # GC, Skip
                length = r.var_uint()
                if length == 0:
                    raise UpdateRejected("empty struct")
                if ref == 0:
                    out.append(_Struct(client, clock, length, gc=True))
                # A Skip (10) is a hole, not a struct: nothing is indexed for
                # it, so anything that refers into it is unresolvable.
                clock += length
                continue
            origin = (r.var_uint(), r.var_uint()) if info & 0x80 else None
            right = (r.var_uint(), r.var_uint()) if info & 0x40 else None
            parent = None
            root = False
            if not info & 0xC0:
                if r.var_uint() == 1:
                    r.var_string()
                    root = True
                else:
                    parent = (r.var_uint(), r.var_uint())
                if info & 0x20:
                    r.var_string()             # parentSub (a map key)
            length, is_type = _read_content(r, ref)
            if length == 0:
                raise UpdateRejected("empty struct")
            out.append(_Struct(client, clock, length, is_type=is_type, root=root,
                               parent=parent, origin=origin, right=right))
            clock += length
    # The delete set.
    n = r.var_uint()
    if n > r.remaining():
        raise UpdateRejected("truncated update")
    for _ in range(n):
        r.var_uint()
        m = r.var_uint()
        if m > r.remaining():
            raise UpdateRejected("truncated update")
        for _ in range(m):
            r.var_uint()
            r.var_uint()
    if r.remaining():
        raise UpdateRejected("trailing bytes after the update")
    return out


@dataclass
class StructIndex:
    """Per client, the struct ranges a document holds, sorted by clock:
    parallel lists of starts and (end, depth, is_type, gc)."""

    starts: dict[int, list[int]] = field(default_factory=dict)
    info: dict[int, list[tuple[int, Optional[int], bool, bool]]] = field(default_factory=dict)

    def find(self, ident: tuple[int, int]) -> Optional[tuple[Optional[int], bool, bool]]:
        client, clock = ident
        starts = self.starts.get(client)
        if not starts:
            return None
        i = bisect.bisect_right(starts, clock) - 1
        if i < 0:
            return None
        end, depth, is_type, gc = self.info[client][i]
        if clock >= end:
            return None
        return depth, is_type, gc

    def covered(self, client: int, clock: int) -> bool:
        return self.find((client, clock)) is not None

    def add(self, s: _Struct) -> None:
        starts = self.starts.setdefault(s.client, [])
        info = self.info.setdefault(s.client, [])
        i = bisect.bisect_left(starts, s.clock)
        if i < len(starts) and starts[i] == s.clock:
            return                             # already known (a duplicate)
        starts.insert(i, s.clock)
        info.insert(i, (s.clock + s.length, s.depth, s.is_type, s.gc))

    def size(self) -> int:
        return sum(len(v) for v in self.starts.values())


def check_update(update: bytes, index: StructIndex) -> list[_Struct]:
    """Read ``update`` against what the document already holds. Returns the
    new structs (for :func:`commit`) or raises :class:`UpdateRejected`.
    Never changes ``index``."""
    structs = _parse(update)
    fresh: list[_Struct] = []
    local = StructIndex()
    for s in structs:
        if index.covered(s.client, s.clock):
            continue                           # yrs ignores what it has
        if s.gc:
            s.depth = None
            local.add(s)
        fresh.append(s)

    def lookup(ident):
        hit = index.find(ident)
        if hit is not None:
            return hit
        return local.find(ident)

    pending = [s for s in fresh if not s.gc]
    while pending:
        progressed = False
        left: list[_Struct] = []
        for s in pending:
            depth: Optional[int] = None
            gone = False
            if s.root:
                depth = 0
            elif s.parent is not None:
                p = lookup(s.parent)
                if p is None:
                    left.append(s)
                    continue
                p_depth, p_type, p_gc = p
                if p_gc:
                    gone = True
                elif not p_type:
                    raise UpdateRejected("parent is not a shared type")
                elif p_depth is None:
                    left.append(s)
                    continue
                else:
                    depth = p_depth + 1
            else:
                o = lookup(s.origin) if s.origin is not None else None
                ro = lookup(s.right) if s.right is not None else None
                if (s.origin is not None and o is None) or (s.right is not None and ro is None):
                    left.append(s)
                    continue
                if o is not None and not o[2]:
                    if o[0] is None:
                        left.append(s)
                        continue
                    depth = o[0]
                elif ro is not None and not ro[2]:
                    if ro[0] is None:
                        left.append(s)
                        continue
                    depth = ro[0]
                else:
                    gone = True                # both neighbours are garbage
            if gone:
                s.gc = True
                s.depth = None
            else:
                if depth > MAX_TYPE_DEPTH or (s.is_type and depth + 1 > MAX_TYPE_DEPTH):
                    raise UpdateRejected("nested too deep")
                s.depth = depth
            local.add(s)
            progressed = True
        if not progressed:
            raise UpdateRejected("refers to changes this document does not have")
        pending = left
    return fresh


def check_values(update: bytes) -> None:
    """The format and value-depth half of :func:`check_update`, without
    resolving where structs sit: for stored blobs (which may be merges with
    holes in them) before they are applied."""
    _parse(update)


def commit(index: StructIndex, fresh: list[_Struct]) -> None:
    for s in fresh:
        index.add(s)


def index_for(state: Optional[bytes]) -> StructIndex:
    """The index of a whole encoded document (``Doc.get_update()``), which
    has no holes and nothing pending. Raises on a document that breaks the
    limits (one stored before them)."""
    index = StructIndex()
    if state:
        commit(index, check_update(state, index))
    return index
