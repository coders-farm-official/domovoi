"""A cheap, linear pre-filter every incoming Yjs update passes before it is
sent to a document worker (web/backend/collab/worker.py).

The real protection against yrs crashing on deep nesting is that pycrdt
never runs in the web process: every document lives in a worker
subprocess, which checks nesting after each apply and whose crash is
contained (review round 2, 2026-10-26). This filter only refuses what is
plainly not a Yjs v1 update from an editor, and the one thing that crashes
``apply_update`` itself, cheaply and before any process is involved:

* malformed or truncated input, trailing bytes, sub-documents, unknown
  content or type references, zero-length structs, a parent-info flag that
  is neither 0 nor 1;
* numbers yrs would read differently than Yjs: yrs keeps clocks and
  lengths in 32 bits, so any clock, length or count >= 2^32 is refused, and
  so is a struct whose clock + length reaches 2^32 (client ids may go to
  2^53, as Yjs's own do);
* values nested deeper than ``MAX_VALUE_DEPTH`` (ContentAny, and the JSON
  in ContentJSON / embeds / format attributes), scanned without recursion:
  a few kilobytes of nesting crash ``apply_update`` before anything could
  check the result;
* a root type no editor here uses (``content.ROOTS``);
* more than ``MAX_STRUCTS`` structs in one update.

Where a struct sits (its parent, its depth) is deliberately NOT worked out
here: yrs does that, and mirroring it in Python produced differentials
(round 2). The worker measures depth on the integrated document instead.

:func:`check` returns what the worker needs to know about the update: the
clock ranges it brings per client (to tell when yrs is holding structs back
as pending) and whether it carries any shared types (only then can nesting
grow).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from web.backend.collab.protocol import ProtocolError

MAX_VALUE_DEPTH = 64
MAX_STRUCTS = 200_000          # in ONE update (a whole offline document is fine)
_U32 = 2 ** 32
_MAX_CLIENT = 2 ** 53


class UpdateRejected(ProtocolError):
    """An update this server will not hand to a worker."""


@dataclass
class UpdateInfo:
    has_types: bool = False
    # client -> highest clock (exclusive) this update brings for it
    ends: dict[int, int] = field(default_factory=dict)
    structs: int = 0


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

    def var_uint(self, limit: int = _U32) -> int:
        value = 0
        shift = 0
        for _ in range(8):
            b = self.u8()
            value |= (b & 0x7F) << shift
            if b < 0x80:
                if value >= limit:
                    raise UpdateRejected("number out of range")
                return value
            shift += 7
        raise UpdateRejected("number too long")

    def client(self) -> int:
        return self.var_uint(_MAX_CLIENT)

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

    def count(self) -> int:
        """A count of things that each take at least one byte."""
        n = self.var_uint()
        if n > self.remaining():
            raise UpdateRejected("truncated update")
        return n


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
            stack.append([r.count(), t == 118])
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


def _read_content(r: _Reader, ref: int, info: UpdateInfo) -> int:
    """The length of one item's content."""
    if ref == 1:                               # Deleted
        return r.var_uint()
    if ref == 2:                               # JSON
        n = r.count()
        for _ in range(n):
            _check_json(r.var_string())
        return n
    if ref == 3:                               # Binary
        r.var_bytes()
        return 1
    if ref == 4:                               # String
        return _utf16_len(r.var_string())
    if ref == 5:                               # Embed
        _check_json(r.var_string())
        return 1
    if ref == 6:                               # Format
        r.var_string()
        _check_json(r.var_string())
        return 1
    if ref == 7:                               # Type
        tref = r.var_uint()
        if tref > 6:
            raise UpdateRejected("unknown shared type")
        if tref in (3, 5):                     # XmlElement name, XmlHook key
            r.var_string()
        info.has_types = True
        return 1
    if ref == 8:                               # Any
        n = r.count()
        for _ in range(n):
            _skip_any(r)
        return n
    if ref == 9:
        raise UpdateRejected("sub-documents are not supported")
    raise UpdateRejected("unknown content")


def _id(r: _Reader) -> None:
    r.client()
    r.var_uint()                               # a clock: < 2^32


def check(update: bytes, roots: frozenset[str] | set[str] | None = None) -> UpdateInfo:
    """Read ``update`` once, front to back. Raises :class:`UpdateRejected`.
    ``roots``: the root names an editor may write to (None: any)."""
    r = _Reader(update)
    info = UpdateInfo()
    for _ in range(r.count()):
        n_structs = r.count()
        client = r.client()
        clock = r.var_uint()
        for _ in range(n_structs):
            info.structs += 1
            if info.structs > MAX_STRUCTS:
                raise UpdateRejected("too many changes in one update")
            sinfo = r.u8()
            ref = sinfo & 0x1F
            if ref in (0, 10):                 # GC, Skip
                length = r.var_uint()
            else:
                if sinfo & 0x80:
                    _id(r)                     # origin
                if sinfo & 0x40:
                    _id(r)                     # right origin
                if not sinfo & 0xC0:
                    flag = r.var_uint()
                    if flag == 1:
                        name = r.var_string()
                        if roots is not None and name not in roots:
                            raise UpdateRejected("not a part of a document this editor writes")
                    elif flag == 0:
                        _id(r)                 # parent
                    else:
                        raise UpdateRejected("bad parent")
                    if sinfo & 0x20:
                        r.var_string()         # parentSub (a map key)
                length = _read_content(r, ref, info)
            if length == 0:
                raise UpdateRejected("empty struct")
            clock += length
            if clock >= _U32:
                raise UpdateRejected("number out of range")
        if n_structs:
            info.ends[client] = max(info.ends.get(client, 0), clock)
    for _ in range(r.count()):                 # the delete set
        r.client()
        for _ in range(r.count()):
            start = r.var_uint()
            if start + r.var_uint() >= _U32:
                raise UpdateRejected("number out of range")
    if r.remaining():
        raise UpdateRejected("trailing bytes after the update")
    return info
