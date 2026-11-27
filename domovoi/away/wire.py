"""The away chat wire, as pure functions: no sockets, no clock, no files.

Away chat (1.5.0) lets a paired phone chat with its own house over a Tor
onion service of its own (design note AWAY-CHAT-WIRE, kept outside the
repo; docs/SECURITY_PRIVACY.md, "Away chat"). Everything here reads bytes
a phone sent, so it is written to be fuzzed (``domovoi/tests/fuzz_targets.py``,
target ``away_wire``) and runs inside the Friend Houses listener's
container (``listener_runtime.SOURCES``): standard library only.

On the wire::

    phone -> "DAC1"                               4-byte preface
    phone -> frame(Noise IK message 1)            payload: PHONE_HELLO
    house -> frame(Noise IK message 2)            payload: HOUSE_HELLO
    either-> frame(Noise transport message)       payload: one message

A frame is a 2-byte big-endian length (1..65535) and that many bytes. A
message is a 6-byte header (``kind u8``, ``req u32``, ``flags u8``) and a
body. Two kinds of failure, and they never mix:

* :class:`ProtocolError`: the stream is wrong (a bad header, an unknown
  kind, a picture piece that doesn't fit). The house closes the
  connection and says nothing.
* :class:`BadRequest`: a well-formed request asks for something it may not
  (``code`` is ``bad_request`` or ``too_large``). The house answers it with
  an ``ERR`` and the connection stays open.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from typing import Any

PREFACE = b"DAC1"
PROLOGUE = b"domovoi-away-v1"
PROTO = 1

TAGLEN = 16
MAX_FRAME = 65535
#: The most plaintext one transport message carries.
MAX_PLAINTEXT = MAX_FRAME - TAGLEN

# PHONE_HELLO: proto u16, platform u8, ver_len u8, version.
HELLO_MIN = 4
HELLO_MAX = 4 + 32
PLATFORMS = {1: "android", 2: "iphone"}
_VERSION = re.compile(rb"^[0-9A-Za-z.+\-]*\Z")
# Noise IK message 1 = e (32) + encrypted s (32 + 16) + encrypted payload.
MSG1_MIN = 32 + 32 + TAGLEN + HELLO_MIN + TAGLEN
MSG1_MAX = 32 + 32 + TAGLEN + HELLO_MAX + TAGLEN

MAX_PICTURE = 1_048_576
HOUSE_HELLO = struct.pack(">HI", PROTO, MAX_PICTURE)

# The header.
_HEADER = struct.Struct(">BIB")
HEADER_SIZE = _HEADER.size
FLAG_MORE = 0x01

# Phone -> house.
REQ = 0x01
BLOB = 0x02
PING = 0x03
BYE = 0x04
PHONE_KINDS = frozenset({REQ, BLOB, PING, BYE})
# House -> phone.
RESP = 0x81
EVENT = 0x82
ERR = 0x83
PONG = 0x84
HOUSE_BYE = 0x85

#: A REQ's JSON, all its pieces together.
MAX_REQ = 160 * 1024
#: A RESP's JSON, all its pieces together.
MAX_RESP = 1024 * 1024
#: One BLOB's picture bytes.
MAX_BLOB_PIECE = 65_000
#: Past this much of an over-long REQ, the house stops reading and closes.
MAX_REQ_DISCARD = 1024 * 1024

MAX_DEPTH = 4
MAX_TEXT = 32_000
MAX_LIMIT = 50
MAX_CHAT_ID = 2**63 - 1
MAX_GRANT = 256
MAX_CURSOR = 200
MAX_VERSION = 32

OPS = ("chat.list", "chat.history", "chat.send", "chat.stream", "chat.cancel")
PICTURE_TYPES = ("jpeg", "webp")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
_CURSOR = re.compile(r"^[A-Za-z0-9_\-]{1,200}\Z")
_GRANT = re.compile(r"^[\x21-\x7e]{1,256}\Z")


class ProtocolError(Exception):
    """The stream broke the protocol: close it without a word."""


class BadRequest(Exception):
    """A well-formed request that is refused: an ``ERR`` with ``code``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ─── the hello ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhoneHello:
    proto: int
    platform: str
    version: str


def parse_phone_hello(data: bytes) -> PhoneHello:
    """PHONE_HELLO, exactly: ``proto u16`` (1), ``platform u8`` (1 Android,
    2 iPhone), ``ver_len u8`` (0..32), then ``version`` in
    ``[0-9A-Za-z.+-]``. Anything else is a :class:`ProtocolError`."""
    if not HELLO_MIN <= len(data) <= HELLO_MAX:
        raise ProtocolError("hello has the wrong size")
    proto, platform, ver_len = struct.unpack(">HBB", data[:4])
    if proto != PROTO:
        raise ProtocolError("a proto this house doesn't speak")
    if platform not in PLATFORMS:
        raise ProtocolError("unknown platform")
    if ver_len > MAX_VERSION or len(data) != 4 + ver_len:
        raise ProtocolError("hello has the wrong size")
    version = data[4:]
    if not _VERSION.match(version):
        raise ProtocolError("bad version")
    return PhoneHello(proto, PLATFORMS[platform], version.decode("ascii"))


def encode_phone_hello(platform: int = 1, version: str = "") -> bytes:
    """For tests and the loopback client."""
    raw = version.encode("ascii")
    return struct.pack(">HBB", PROTO, platform, len(raw)) + raw


def parse_house_hello(data: bytes) -> tuple[int, int]:
    """For tests and the loopback client: ``(proto, max_picture)``."""
    if len(data) != len(HOUSE_HELLO):
        raise ProtocolError("house hello has the wrong size")
    return struct.unpack(">HI", data)


# ─── frames and headers ───────────────────────────────────────────────────


def frame(data: bytes) -> bytes:
    if not 1 <= len(data) <= MAX_FRAME:
        raise ValueError("a frame is 1..65535 bytes")
    return struct.pack(">H", len(data)) + data


def frame_size(header: bytes, *, max_size: int = MAX_FRAME, min_size: int = 1) -> int:
    """The length a 2-byte frame header announces; zero, or outside
    ``min_size..max_size``, is a :class:`ProtocolError`."""
    if len(header) != 2:
        raise ProtocolError("short frame header")
    (size,) = struct.unpack(">H", header)
    if size == 0 or size < min_size or size > max_size:
        raise ProtocolError("frame size out of range")
    return size


@dataclass(frozen=True)
class Message:
    kind: int
    req: int
    more: bool
    body: bytes


def parse_message(plain: bytes) -> Message:
    """One phone message (a decrypted transport payload), its header
    checked: a known phone kind, flags with only MORE, ``req`` zero exactly
    for PING and BYE (empty, never MORE), and a BLOB at least an offset and
    one byte, at most :data:`MAX_BLOB_PIECE` bytes of picture."""
    if len(plain) < HEADER_SIZE:
        raise ProtocolError("short message")
    kind, req, flags = _HEADER.unpack_from(plain)
    body = plain[HEADER_SIZE:]
    if kind not in PHONE_KINDS:
        raise ProtocolError("unknown message kind")
    if flags & ~FLAG_MORE:
        raise ProtocolError("unknown flags")
    more = bool(flags & FLAG_MORE)
    if kind in (PING, BYE):
        if req != 0 or more or body:
            raise ProtocolError("malformed ping or bye")
    elif req == 0:
        raise ProtocolError("req 0 is for ping and bye only")
    if kind == REQ and not body:
        raise ProtocolError("empty request piece")
    if kind == BLOB and not 4 < len(body) <= 4 + MAX_BLOB_PIECE:
        raise ProtocolError("blob piece out of range")
    return Message(kind, req, more, body)


def header(kind: int, req: int, *, more: bool = False) -> bytes:
    return _HEADER.pack(kind, req, FLAG_MORE if more else 0)


def encode_message(kind: int, req: int, body: bytes = b"", *, more: bool = False) -> bytes:
    if not 0 <= req <= 0xFFFFFFFF:
        raise ValueError("req is a u32")
    out = header(kind, req, more=more) + body
    if len(out) > MAX_PLAINTEXT:
        raise ValueError("message too large")
    return out


def split_json(kind: int, req: int, body: bytes) -> list[bytes]:
    """A house JSON body as one message, or several with MORE when it is
    longer than a transport message holds (``RESP`` only: an ``EVENT`` and
    an ``ERR`` are never split, and must fit)."""
    room = MAX_PLAINTEXT - HEADER_SIZE
    if len(body) <= room:
        return [encode_message(kind, req, body)]
    if kind != RESP:
        raise ValueError("only a RESP is split")
    pieces = [body[i:i + room] for i in range(0, len(body), room)]
    return [encode_message(kind, req, p, more=i < len(pieces) - 1) for i, p in enumerate(pieces)]


def blob_body(offset: int, data: bytes) -> bytes:
    return struct.pack(">I", offset) + data


def parse_blob(body: bytes) -> tuple[int, bytes]:
    (offset,) = struct.unpack_from(">I", body)
    return offset, body[4:]


# ─── strict JSON ──────────────────────────────────────────────────────────


def _depth_ok(raw: bytes, limit: int = MAX_DEPTH) -> bool:
    """Whether no object or array in ``raw`` nests deeper than ``limit``,
    before the JSON parser sees it (a deep one would otherwise reach the
    parser's own recursion limit). Brackets inside strings don't count."""
    if raw.count(b"{") + raw.count(b"[") <= limit:
        return True
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > limit:
                return False
        elif byte in (0x7D, 0x5D):
            depth -= 1
    return True


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise BadRequest("bad_request", "a key appears twice")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise BadRequest("bad_request", "NaN and Infinity aren't JSON")


def load_object(raw: bytes, *, max_size: int = MAX_REQ) -> dict[str, Any]:
    """One JSON object from UTF-8 ``raw``: no duplicate keys, nesting at
    most :data:`MAX_DEPTH`, no NaN or Infinity, at most ``max_size``
    bytes. A :class:`BadRequest` otherwise (``too_large`` for the size)."""
    if len(raw) > max_size:
        raise BadRequest("too_large", "the request is too large")
    if not _depth_ok(raw):
        raise BadRequest("bad_request", "nested too deep")
    try:
        text = raw.decode("utf-8")
        doc = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except BadRequest:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BadRequest("bad_request", "not JSON") from exc
    if not isinstance(doc, dict):
        raise BadRequest("bad_request", "not a JSON object")
    return doc


def dump(obj: Any) -> bytes:
    """A house JSON body: compact, UTF-8."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ─── the request set ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Picture:
    type: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Request:
    """One validated request. Only the fields its ``op`` takes are set."""

    op: str
    archived: bool = False
    limit: int = MAX_LIMIT
    cursor: str | None = None
    chat_id: int | None = None
    before_id: int | None = None
    client_id: str | None = None
    text: str = ""
    picture: Picture | None = None
    admin_grant: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The request as core is told it (and :func:`parse_request` reads
        back): only the op's own keys."""
        out: dict[str, Any] = {"op": self.op}
        if self.op == "chat.list":
            out |= {"archived": self.archived, "limit": self.limit, "cursor": self.cursor}
        elif self.op == "chat.history":
            out |= {"chat_id": self.chat_id, "before_id": self.before_id, "limit": self.limit}
        elif self.op == "chat.send":
            out |= {"chat_id": self.chat_id, "client_id": self.client_id, "text": self.text}
            if self.picture is not None:
                out["picture"] = {"type": self.picture.type, "size": self.picture.size,
                                  "sha256": self.picture.sha256}
            if self.admin_grant is not None:
                out["admin_grant"] = self.admin_grant
        else:
            out["chat_id"] = self.chat_id
        return out


_KEYS: dict[str, frozenset[str]] = {
    "chat.list": frozenset({"op", "archived", "limit", "cursor"}),
    "chat.history": frozenset({"op", "chat_id", "before_id", "limit"}),
    "chat.send": frozenset({"op", "chat_id", "client_id", "text", "picture", "admin_grant"}),
    "chat.stream": frozenset({"op", "chat_id"}),
    "chat.cancel": frozenset({"op", "chat_id"}),
}
_REQUIRED: dict[str, frozenset[str]] = {
    "chat.list": frozenset({"op"}),
    "chat.history": frozenset({"op", "chat_id"}),
    "chat.send": frozenset({"op", "client_id"}),
    "chat.stream": frozenset({"op", "chat_id"}),
    "chat.cancel": frozenset({"op", "chat_id"}),
}
_PICTURE_KEYS = frozenset({"type", "size", "sha256"})


def _bad(detail: str) -> BadRequest:
    return BadRequest("bad_request", detail)


def _int(doc: dict[str, Any], key: str, lo: int, hi: int, *, default: int | None = None,
         nullable: bool = False) -> int | None:
    if key not in doc:
        return default
    value = doc[key]
    if value is None and nullable:
        return None
    # bool is an int in Python; JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise _bad(f"{key} is out of range")
    return value


def _clean_text(value: str, key: str) -> str:
    """A string the house can store: no lone surrogate (an escaped one
    parses) and no NUL (Postgres text can't hold one)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _bad(f"{key} isn't valid text") from exc
    if "\x00" in value:
        raise _bad(f"{key} isn't valid text")
    return value


def parse_request(doc: dict[str, Any]) -> Request:
    """A REQ body (already a JSON object) as a :class:`Request`. The
    request set is closed: an unknown op or key, a wrong type or an
    out-of-range field is ``bad_request``; text over its cap or a picture
    over 1 MiB is ``too_large``."""
    op = doc.get("op")
    if not isinstance(op, str) or op not in _KEYS:
        raise _bad("unknown op")
    keys = set(doc)
    if keys - _KEYS[op]:
        raise _bad("unknown key")
    if _REQUIRED[op] - keys:
        raise _bad("a required key is missing")
    if op == "chat.list":
        archived = doc.get("archived", False)
        if not isinstance(archived, bool):
            raise _bad("archived is a boolean")
        cursor = doc.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or not _CURSOR.match(cursor)):
            raise _bad("bad cursor")
        limit = _int(doc, "limit", 1, MAX_LIMIT, default=MAX_LIMIT)
        assert limit is not None
        return Request(op, archived=archived, limit=limit, cursor=cursor)
    if op == "chat.history":
        limit = _int(doc, "limit", 1, MAX_LIMIT, default=MAX_LIMIT)
        assert limit is not None
        return Request(op, chat_id=_int(doc, "chat_id", 1, MAX_CHAT_ID),
                       before_id=_int(doc, "before_id", 1, MAX_CHAT_ID, nullable=True), limit=limit)
    if op in ("chat.stream", "chat.cancel"):
        return Request(op, chat_id=_int(doc, "chat_id", 1, MAX_CHAT_ID))
    # chat.send
    chat_id = _int(doc, "chat_id", 1, MAX_CHAT_ID, nullable=True)
    client_id = doc["client_id"]
    if not isinstance(client_id, str) or not _UUID.match(client_id):
        raise _bad("client_id is a lower-case UUID")
    text = doc.get("text", "")
    if not isinstance(text, str):
        raise _bad("text is a string")
    if len(text) > MAX_TEXT:
        raise BadRequest("too_large", "the message is too long")
    text = _clean_text(text, "text")
    picture = None
    if doc.get("picture") is not None:
        picture = _parse_picture(doc["picture"])
    if not text.strip() and picture is None:
        raise _bad("a message needs some words or a picture")
    grant = doc.get("admin_grant")
    if grant is not None and (not isinstance(grant, str) or not _GRANT.match(grant)):
        raise _bad("bad admin_grant")
    return Request(op, chat_id=chat_id, client_id=client_id, text=text, picture=picture,
                   admin_grant=grant)


def _parse_picture(raw: Any) -> Picture:
    if not isinstance(raw, dict) or set(raw) != _PICTURE_KEYS:
        raise _bad("picture is {type, size, sha256}")
    kind, size, digest = raw["type"], raw["size"], raw["sha256"]
    if not isinstance(kind, str) or kind not in PICTURE_TYPES:
        raise _bad("picture type is jpeg or webp")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise _bad("picture size is out of range")
    if size > MAX_PICTURE:
        raise BadRequest("too_large", "the picture is larger than 1 MiB")
    if not isinstance(digest, str) or not _SHA256.match(digest):
        raise _bad("picture sha256 is 64 lower-case hex")
    return Picture(kind, size, digest)


def parse_request_bytes(raw: bytes) -> Request:
    return parse_request(load_object(raw))


# ─── assembling a REQ and its picture ─────────────────────────────────────


@dataclass
class RequestPieces:
    """A REQ arriving in pieces (MORE). Over :data:`MAX_REQ` it keeps
    reading without keeping, to answer ``too_large`` at its last piece;
    past :data:`MAX_REQ_DISCARD` it is a :class:`ProtocolError`."""

    size: int = 0
    parts: list[bytes] = field(default_factory=list)
    over: bool = False

    def add(self, body: bytes) -> None:
        self.size += len(body)
        if self.size > MAX_REQ_DISCARD:
            raise ProtocolError("request far over its cap")
        if self.size > MAX_REQ:
            self.over = True
            self.parts = []
        elif not self.over:
            self.parts.append(body)

    def finish(self) -> Request:
        if self.over:
            raise BadRequest("too_large", "the request is larger than 160 KiB")
        return parse_request_bytes(b"".join(self.parts))


def sniff(data: bytes) -> str | None:
    """``"jpeg"`` / ``"webp"`` from the magic bytes, else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass
class PictureSink:
    """A request's picture arriving in BLOBs: offsets in order from 0, no
    gap, overlap or overrun (each a :class:`ProtocolError`). With ``keep``
    False the bytes are checked and dropped (a refused request's picture
    still on its way)."""

    picture: Picture
    keep: bool = True
    received: int = 0
    parts: list[bytes] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.received == self.picture.size

    def add(self, offset: int, data: bytes) -> bool:
        """Take one piece; True once the picture is complete."""
        if offset != self.received:
            raise ProtocolError("picture piece out of order")
        if not data or len(data) > MAX_BLOB_PIECE:
            raise ProtocolError("picture piece out of range")
        if self.received + len(data) > self.picture.size:
            raise ProtocolError("picture overrun")
        self.received += len(data)
        if self.keep:
            self.parts.append(data)
        return self.complete

    def finish(self) -> bytes:
        """The whole picture, checked: its sha256 and that its bytes are
        the JPEG or WebP it said (``unsupported_picture`` otherwise). The
        house decodes and re-encodes it later, before anything reads it."""
        if not self.complete or not self.keep:
            raise ProtocolError("picture not complete")
        data = b"".join(self.parts)
        self.parts = []
        if hashlib.sha256(data).hexdigest() != self.picture.sha256:
            raise BadRequest("unsupported_picture", "the picture's sha256 doesn't match")
        if sniff(data) != self.picture.type:
            raise BadRequest("unsupported_picture", "the picture isn't the JPEG or WebP it said")
        return data
