"""Our own IPP/2.0 message codec (RFC 8010 encoding, RFC 8011 model).

No library: pyipp/aioipp can't encode collections, ranges or resolutions,
and the GPL ones are out (docs/LICENSES.md). This is enough to send a real
job: every value tag the printing code uses, including ``media-col``
(collections), ``page-ranges`` (rangeOfInteger) and ``printer-resolution``
(resolution).

The decoder is strict and capped, because a printer's answer is data from
a device on the LAN, not something we trust:

* :data:`MAX_RESPONSE` bytes per message (1 MB), checked by the caller's
  reader and again here;
* :data:`MAX_VALUES_PER_ATTRIBUTE` values kept per attribute (2,000) and
  :data:`MAX_TOTAL_VALUES` values per message;
* strings cut at :data:`MAX_STRING` bytes (1,023) with control characters
  stripped (a printer-state-message cannot carry a terminal escape or a
  line break into a log, a page or speech);
* collections nested at most :data:`MAX_DEPTH` deep;
* any length that runs past the end of the message is an error, never a
  silent truncation.

Standard library only, so the Linux harness container (no venv) can
import it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# ─── Tags (RFC 8010 §3.5) ─────────────────────────────────────────────────

# delimiter tags
OPERATION = 0x01
JOB = 0x02
END = 0x03
PRINTER = 0x04
UNSUPPORTED = 0x05

# out-of-band values
UNSUPPORTED_VALUE = 0x10
UNKNOWN = 0x12
NO_VALUE = 0x13
NOT_SETTABLE = 0x15
DELETE_ATTRIBUTE = 0x16
ADMIN_DEFINE = 0x17

# integer types
INTEGER = 0x21
BOOLEAN = 0x22
ENUM = 0x23

# octet-string types
OCTET_STRING = 0x30
DATETIME = 0x31
RESOLUTION = 0x32
RANGE = 0x33
BEGIN_COLLECTION = 0x34
TEXT_WITH_LANGUAGE = 0x35
NAME_WITH_LANGUAGE = 0x36
END_COLLECTION = 0x37

# character-string types
TEXT = 0x41
NAME = 0x42
KEYWORD = 0x44
URI = 0x45
URI_SCHEME = 0x46
CHARSET = 0x47
LANGUAGE = 0x48
MIME_TYPE = 0x49
MEMBER_NAME = 0x4A

OPS: dict[str, int] = {
    "Print-Job": 0x0002,
    "Validate-Job": 0x0004,
    "Create-Job": 0x0005,
    "Send-Document": 0x0006,
    "Cancel-Job": 0x0008,
    "Get-Job-Attributes": 0x0009,
    "Get-Jobs": 0x000A,
    "Get-Printer-Attributes": 0x000B,
    "Identify-Printer": 0x003C,
}

STATUS_NAMES: dict[int, str] = {
    0x0000: "successful-ok",
    0x0001: "successful-ok-ignored-or-substituted-attributes",
    0x0002: "successful-ok-conflicting-attributes",
    0x0400: "client-error-bad-request",
    0x0401: "client-error-forbidden",
    0x0402: "client-error-not-authenticated",
    0x0403: "client-error-not-authorized",
    0x0404: "client-error-not-possible",
    0x0405: "client-error-timeout",
    0x0406: "client-error-not-found",
    0x0407: "client-error-gone",
    0x0408: "client-error-request-entity-too-large",
    0x040A: "client-error-document-format-not-supported",
    0x040B: "client-error-attributes-or-values-not-supported",
    0x040C: "client-error-uri-scheme-not-supported",
    0x040D: "client-error-charset-not-supported",
    0x040E: "client-error-conflicting-attributes",
    0x040F: "client-error-compression-not-supported",
    0x0410: "client-error-compression-error",
    0x0411: "client-error-document-format-error",
    0x0412: "client-error-document-access-error",
    0x0500: "server-error-internal-error",
    0x0501: "server-error-operation-not-supported",
    0x0502: "server-error-service-unavailable",
    0x0503: "server-error-version-not-supported",
    0x0504: "server-error-device-error",
    0x0505: "server-error-temporary-error",
    0x0506: "server-error-not-accepting-jobs",
    0x0507: "server-error-busy",
    0x0508: "server-error-job-canceled",
}
STATUS_BUSY = 0x0507
STATUS_FORMAT_NOT_SUPPORTED = 0x040A
STATUS_ATTRIBUTES_NOT_SUPPORTED = 0x040B
STATUS_OPERATION_NOT_SUPPORTED = 0x0501

JOB_STATES: dict[int, str] = {
    3: "pending",
    4: "pending-held",
    5: "processing",
    6: "processing-stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}
JOB_TERMINAL = frozenset({7, 8, 9})
PRINTER_STATES: dict[int, str] = {3: "idle", 4: "processing", 5: "stopped"}

# ─── Caps ─────────────────────────────────────────────────────────────────

MAX_RESPONSE = 1 << 20          # 1 MB per response
MAX_VALUES_PER_ATTRIBUTE = 2000
MAX_TOTAL_VALUES = 20000
MAX_STRING = 1023
MAX_DEPTH = 8


class IppDecodeError(ValueError):
    """The bytes are not a well-formed IPP message within our caps."""


def is_success(status: int) -> bool:
    """Every 0x000x status is a success. Brother answers Get-Job-Attributes
    with 0x0001 (ignored-or-substituted) because it doesn't support one
    requested attribute; that is not a failure (P0, 2026-10-21)."""
    return 0 <= status < 0x0100


def status_name(status: int) -> str:
    return STATUS_NAMES.get(status, "0x%04x" % status)


# ─── Value types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolution:
    x: int
    y: int
    units: int = 3  # 3 = dots per inch, 4 = dots per centimetre

    def __str__(self) -> str:
        return "%dx%d%s" % (self.x, self.y, "dpi" if self.units == 3 else "dpcm")

    @property
    def dpi(self) -> int:
        """Cross-feed resolution in dots per inch."""
        return self.x if self.units == 3 else round(self.x * 2.54)


@dataclass(frozen=True)
class Range:
    lower: int
    upper: int


@dataclass
class Attr:
    """One attribute to encode: ``tag`` applies to every value. A value
    for a collection is a ``dict`` of member name → :class:`Attr`."""

    tag: int
    name: str
    values: list[Any] = field(default_factory=list)

    @classmethod
    def of(cls, tag: int, name: str, value: Any) -> "Attr":
        vals = value if isinstance(value, list) else [value]
        return cls(tag, name, vals)


def _tlv(tag: int, name: bytes, value: bytes) -> bytes:
    if len(name) > 0x7FFF or len(value) > 0x7FFF:
        raise ValueError("IPP name or value too long")
    return bytes([tag]) + struct.pack("!H", len(name)) + name + struct.pack("!H", len(value)) + value


def _enc_one(tag: int, name: bytes, value: Any) -> bytes:
    if tag in (INTEGER, ENUM):
        return _tlv(tag, name, struct.pack("!i", int(value)))
    if tag == BOOLEAN:
        return _tlv(tag, name, b"\x01" if value else b"\x00")
    if tag == RANGE:
        lo, hi = (value.lower, value.upper) if isinstance(value, Range) else value
        return _tlv(tag, name, struct.pack("!ii", int(lo), int(hi)))
    if tag == RESOLUTION:
        return _tlv(tag, name, struct.pack("!iib", value.x, value.y, value.units))
    if tag == BEGIN_COLLECTION:
        out = bytearray(_tlv(BEGIN_COLLECTION, name, b""))
        for member_name, member in value.items():
            if not isinstance(member, Attr):
                raise TypeError("collection members must be Attr")
            out += _tlv(MEMBER_NAME, b"", member_name.encode("utf-8"))
            for one in member.values:
                out += _enc_one(member.tag, b"", one)
        out += _tlv(END_COLLECTION, b"", b"")
        return bytes(out)
    if tag in (NO_VALUE, UNKNOWN, UNSUPPORTED_VALUE, NOT_SETTABLE, DELETE_ATTRIBUTE):
        return _tlv(tag, name, b"")
    if isinstance(value, bytes):
        return _tlv(tag, name, value)
    return _tlv(tag, name, str(value).encode("utf-8"))


def encode_attr(attr: Attr) -> bytes:
    out = bytearray()
    name = attr.name.encode("utf-8")
    for i, v in enumerate(attr.values):
        out += _enc_one(attr.tag, name if i == 0 else b"", v)
    return bytes(out)


def encode_request(
    operation: str | int,
    request_id: int,
    groups: list[tuple[int, list[Attr]]],
    version: tuple[int, int] = (2, 0),
) -> bytes:
    """The IPP header and attribute groups, ending with end-of-attributes.
    The document data (if any) follows these bytes on the wire."""
    op = OPS[operation] if isinstance(operation, str) else int(operation)
    out = bytearray(struct.pack("!BBHI", version[0], version[1], op, request_id))
    for group_tag, attrs in groups:
        out.append(group_tag)
        for attr in attrs:
            out += encode_attr(attr)
    out.append(END)
    return bytes(out)


# ─── Decoding ─────────────────────────────────────────────────────────────


def _clean_string(raw: bytes) -> str:
    s = raw[:MAX_STRING].decode("utf-8", "replace")
    return "".join(ch for ch in s if ch >= " " and ch != "\x7f")


def _dec_one(tag: int, raw: bytes) -> Any:
    if tag in (INTEGER, ENUM):
        if len(raw) != 4:
            raise IppDecodeError("bad integer length")
        return struct.unpack("!i", raw)[0]
    if tag == BOOLEAN:
        if len(raw) != 1:
            raise IppDecodeError("bad boolean length")
        return raw != b"\x00"
    if tag == RANGE:
        if len(raw) != 8:
            raise IppDecodeError("bad rangeOfInteger length")
        lo, hi = struct.unpack("!ii", raw)
        return Range(lo, hi)
    if tag == RESOLUTION:
        if len(raw) != 9:
            raise IppDecodeError("bad resolution length")
        x, y, u = struct.unpack("!iib", raw)
        return Resolution(x, y, u)
    if tag == DATETIME:
        return raw.hex()
    if tag in (NO_VALUE, UNKNOWN, UNSUPPORTED_VALUE, NOT_SETTABLE, DELETE_ATTRIBUTE, ADMIN_DEFINE):
        return None
    if tag == OCTET_STRING:
        return raw[:MAX_STRING].hex()
    if tag in (TEXT_WITH_LANGUAGE, NAME_WITH_LANGUAGE):
        # 2-byte length + language, 2-byte length + text
        try:
            ln = struct.unpack("!H", raw[:2])[0]
            tl = struct.unpack("!H", raw[2 + ln:4 + ln])[0]
            return _clean_string(raw[4 + ln:4 + ln + tl])
        except struct.error as e:
            raise IppDecodeError("bad text-with-language") from e
    return _clean_string(raw)


@dataclass
class Message:
    version: str
    status: int
    request_id: int
    groups: list[tuple[int, dict[str, list[Any]]]]

    @property
    def ok(self) -> bool:
        return is_success(self.status)

    @property
    def status_name(self) -> str:
        return status_name(self.status)

    def group(self, tag: int) -> dict[str, list[Any]]:
        """The first group with ``tag`` (an empty dict when absent)."""
        for t, g in self.groups:
            if t == tag:
                return g
        return {}

    def groups_of(self, tag: int) -> list[dict[str, list[Any]]]:
        return [g for t, g in self.groups if t == tag]


def decode(data: bytes) -> Message:
    """Decode a whole IPP response. Raises :class:`IppDecodeError`."""
    if len(data) > MAX_RESPONSE:
        raise IppDecodeError("response too large")
    if len(data) < 9:
        raise IppDecodeError("short response")
    major, minor = data[0], data[1]
    status = struct.unpack("!H", data[2:4])[0]
    rid = struct.unpack("!I", data[4:8])[0]
    off = 8
    groups: list[tuple[int, dict[str, list[Any]]]] = []
    current: dict[str, list[Any]] | None = None
    last_name: str | None = None
    # Collection nesting: each frame is (the dict being filled, current member name)
    stack: list[list[Any]] = []
    total = 0
    ended = False
    while off < len(data):
        tag = data[off]
        off += 1
        if tag == END:
            ended = True
            break
        if tag < 0x10:
            if stack:
                raise IppDecodeError("group tag inside a collection")
            current = {}
            groups.append((tag, current))
            last_name = None
            continue
        if off + 2 > len(data):
            raise IppDecodeError("truncated name length")
        nl = struct.unpack("!H", data[off:off + 2])[0]
        off += 2
        if off + nl + 2 > len(data):
            raise IppDecodeError("truncated name")
        name = data[off:off + nl].decode("utf-8", "replace")
        off += nl
        vl = struct.unpack("!H", data[off:off + 2])[0]
        off += 2
        if off + vl > len(data):
            raise IppDecodeError("truncated value")
        raw = data[off:off + vl]
        off += vl
        total += 1
        if total > MAX_TOTAL_VALUES:
            raise IppDecodeError("too many values")
        if current is None:
            raise IppDecodeError("value outside a group")
        if tag == MEMBER_NAME:
            if not stack:
                raise IppDecodeError("memberAttrName outside a collection")
            stack[-1][1] = _clean_string(raw)
            continue
        if tag == END_COLLECTION:
            if not stack:
                raise IppDecodeError("unbalanced endCollection")
            stack.pop()
            continue
        # Where this value goes: a collection member, or a top-level attribute.
        if stack:
            target, key = stack[-1][0], stack[-1][1]
            if key is None:
                raise IppDecodeError("collection value without a member name")
        else:
            if name:
                last_name = name
            if last_name is None:
                raise IppDecodeError("additional value without an attribute")
            target, key = current, last_name
        bucket = target.setdefault(key, [])
        if tag == BEGIN_COLLECTION:
            if len(stack) >= MAX_DEPTH:
                raise IppDecodeError("collections nested too deep")
            col: dict[str, list[Any]] = {}
            if len(bucket) < MAX_VALUES_PER_ATTRIBUTE:
                bucket.append(col)
            stack.append([col, None])
            continue
        value = _dec_one(tag, raw)
        if len(bucket) < MAX_VALUES_PER_ATTRIBUTE:
            bucket.append(value)
    if stack:
        raise IppDecodeError("unterminated collection")
    if not ended:
        raise IppDecodeError("missing end-of-attributes")
    return Message(version="%d.%d" % (major, minor), status=status, request_id=rid, groups=groups)


def first(attrs: dict[str, list[Any]], name: str, default: Any = None) -> Any:
    vals = attrs.get(name) or []
    return vals[0] if vals else default


# ─── Request helpers ──────────────────────────────────────────────────────


def operation_attrs(printer_uri: str, *extra: Attr) -> list[Attr]:
    """The three operation attributes every request starts with, in the
    order RFC 8011 requires, then ``extra``."""
    return [
        Attr.of(CHARSET, "attributes-charset", "utf-8"),
        Attr.of(LANGUAGE, "attributes-natural-language", "en"),
        Attr.of(URI, "printer-uri", printer_uri),
        *extra,
    ]


def media_col(x_hundredths_mm: int, y_hundredths_mm: int, *, borderless: bool = False) -> Attr:
    """``media-col`` with a media-size collection (and margins of 0 when
    borderless). Sizes in hundredths of a millimetre, as IPP wants them."""
    size = {
        "x-dimension": Attr.of(INTEGER, "", x_hundredths_mm),
        "y-dimension": Attr.of(INTEGER, "", y_hundredths_mm),
    }
    col: dict[str, Attr] = {"media-size": Attr.of(BEGIN_COLLECTION, "", size)}
    if borderless:
        for side in ("bottom", "left", "right", "top"):
            col[f"media-{side}-margin"] = Attr.of(INTEGER, "", 0)
    return Attr.of(BEGIN_COLLECTION, "media-col", col)
