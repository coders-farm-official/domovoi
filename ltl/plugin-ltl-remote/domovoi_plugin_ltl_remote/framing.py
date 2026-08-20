"""Frame encode/decode for ``ltl-remote/v1`` — see ``ltl/docs/PROTOCOL.md``.

Pure bytes in, pure bytes out. No sockets, no crypto, no settings — the
sealed layer wraps the *output* of this module, and the relay parses only
the outer header. Splitting framing from crypto means the noisy,
easy-to-get-wrong part (offsets, lengths, JSON headers) is testable on
its own, and the crypto module stays small enough to read in one sitting.

Every length here is bounded. A frame codec that trusts a peer-supplied
length field is a memory-exhaustion bug waiting to be reported, and the
peer on the far end of this one has, by construction, already been
authenticated but is still not trusted with our heap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

LINK_ID_LEN = 16
ZERO_LINK_ID = b"\x00" * LINK_ID_LEN

# ── outer opcodes (relay-visible) ──────────────────────────────────────────
OP_LINK_OPEN = 0x01
OP_LINK_DATA = 0x02
OP_LINK_CLOSE = 0x03
OP_CONTROL = 0x10

_OUTER_OPCODES = frozenset({OP_LINK_OPEN, OP_LINK_DATA, OP_LINK_CLOSE, OP_CONTROL})

# ── inner frame types (end-to-end only) ────────────────────────────────────
REQ = 0x01
REQ_CHUNK = 0x02
REQ_END = 0x03
RES_HEAD = 0x11
RES_CHUNK = 0x12
RES_END = 0x13
WS_OPEN = 0x21
WS_OPEN_OK = 0x22
WS_DATA = 0x23
WS_CLOSE = 0x24
PING = 0x31
PONG = 0x32
ERROR = 0x41

_INNER_TYPES = frozenset({
    REQ, REQ_CHUNK, REQ_END, RES_HEAD, RES_CHUNK, RES_END,
    WS_OPEN, WS_OPEN_OK, WS_DATA, WS_CLOSE, PING, PONG, ERROR,
})

INNER_TYPE_NAMES = {
    REQ: "REQ", REQ_CHUNK: "REQ_CHUNK", REQ_END: "REQ_END",
    RES_HEAD: "RES_HEAD", RES_CHUNK: "RES_CHUNK", RES_END: "RES_END",
    WS_OPEN: "WS_OPEN", WS_OPEN_OK: "WS_OPEN_OK", WS_DATA: "WS_DATA",
    WS_CLOSE: "WS_CLOSE", PING: "PING", PONG: "PONG", ERROR: "ERROR",
}

# Bounds. The chunk size is the one the home server actually emits; the
# max is what it will *accept*, with headroom so a peer that rounds up
# differently isn't rejected for it.
CHUNK_BYTES = 64 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_INNER_BYTES = 4 * 1024 * 1024
MAX_OUTER_BYTES = MAX_INNER_BYTES + 4096          # sealed overhead + outer header

# ── error codes (PROTOCOL.md §5.3) ─────────────────────────────────────────
ERR_DEVICE_NOT_APPROVED = "DEVICE_NOT_APPROVED"
ERR_PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
ERR_LOCAL_UNREACHABLE = "LOCAL_UNREACHABLE"
ERR_BODY_TOO_LARGE = "BODY_TOO_LARGE"
ERR_TOO_MANY_STREAMS = "TOO_MANY_STREAMS"
ERR_PROTOCOL = "PROTOCOL_ERROR"
ERR_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
ERR_SUBSCRIPTION_INACTIVE = "SUBSCRIPTION_INACTIVE"
ERR_HOUSEHOLD_OFFLINE = "HOUSEHOLD_OFFLINE"


class FramingError(Exception):
    """A malformed frame. Always fatal to the link: a peer that cannot
    frame correctly cannot be reasoned with, and continuing would mean
    guessing at stream boundaries."""


# ─── outer frame (PROTOCOL.md §2) ──────────────────────────────────────────


@dataclass(frozen=True)
class OuterFrame:
    opcode: int
    link_id: bytes
    payload: bytes


def encode_outer(opcode: int, link_id: bytes, payload: bytes) -> bytes:
    if opcode not in _OUTER_OPCODES:
        raise FramingError(f"unknown outer opcode 0x{opcode:02x}")
    if len(link_id) != LINK_ID_LEN:
        raise FramingError(f"link_id must be {LINK_ID_LEN} bytes")
    return bytes([PROTOCOL_VERSION, opcode]) + bytes(link_id) + bytes(payload)


def decode_outer(raw: bytes) -> OuterFrame:
    if len(raw) < 2 + LINK_ID_LEN:
        raise FramingError("outer frame is shorter than its header")
    if len(raw) > MAX_OUTER_BYTES:
        raise FramingError("outer frame exceeds the size cap")
    version = raw[0]
    if version != PROTOCOL_VERSION:
        # Versioned rather than guessed — see PROTOCOL.md §9.
        raise FramingError(f"unsupported protocol version {version}")
    opcode = raw[1]
    if opcode not in _OUTER_OPCODES:
        raise FramingError(f"unknown outer opcode 0x{opcode:02x}")
    return OuterFrame(
        opcode=opcode,
        link_id=raw[2:2 + LINK_ID_LEN],
        payload=raw[2 + LINK_ID_LEN:],
    )


def encode_json_payload(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_json_payload(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_HEADER_BYTES:
        raise FramingError("JSON payload exceeds the size cap")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FramingError("payload is not valid UTF-8 JSON") from e
    if not isinstance(obj, dict):
        raise FramingError("payload must be a JSON object")
    return obj


# ─── inner frame (PROTOCOL.md §5) ──────────────────────────────────────────


@dataclass(frozen=True)
class InnerFrame:
    type: int
    stream_id: int
    header: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""

    @property
    def type_name(self) -> str:
        return INNER_TYPE_NAMES.get(self.type, f"0x{self.type:02x}")


def encode_inner(
    frame_type: int,
    stream_id: int,
    header: dict[str, Any] | None = None,
    body: bytes = b"",
) -> bytes:
    if frame_type not in _INNER_TYPES:
        raise FramingError(f"unknown inner frame type 0x{frame_type:02x}")
    if not 0 <= stream_id <= 0xFFFFFFFF:
        raise FramingError("stream_id must fit in uint32")
    header_bytes = encode_json_payload(header or {})
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise FramingError("inner header exceeds the size cap")
    out = (
        bytes([frame_type])
        + stream_id.to_bytes(4, "big")
        + len(header_bytes).to_bytes(4, "big")
        + header_bytes
        + bytes(body)
    )
    if len(out) > MAX_INNER_BYTES:
        raise FramingError("inner frame exceeds the size cap")
    return out


def decode_inner(raw: bytes) -> InnerFrame:
    if len(raw) < 9:
        raise FramingError("inner frame is shorter than its header")
    if len(raw) > MAX_INNER_BYTES:
        raise FramingError("inner frame exceeds the size cap")
    frame_type = raw[0]
    if frame_type not in _INNER_TYPES:
        raise FramingError(f"unknown inner frame type 0x{frame_type:02x}")
    stream_id = int.from_bytes(raw[1:5], "big")
    header_len = int.from_bytes(raw[5:9], "big")
    # Check the declared length against what we actually hold BEFORE
    # slicing: a peer claiming a 4 GiB header must not get us to allocate
    # anything at all.
    if header_len > MAX_HEADER_BYTES:
        raise FramingError("declared header length exceeds the size cap")
    if 9 + header_len > len(raw):
        raise FramingError("declared header length runs past the frame")
    return InnerFrame(
        type=frame_type,
        stream_id=stream_id,
        header=decode_json_payload(raw[9:9 + header_len]),
        body=raw[9 + header_len:],
    )


# ─── convenience constructors ──────────────────────────────────────────────
#
# Every place that builds a frame goes through one of these, so a header
# key can never be misspelled in one call site and not another.


def request(
    stream_id: int,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes = b"",
    *,
    streaming: bool = False,
) -> bytes:
    return encode_inner(
        REQ,
        stream_id,
        {"method": method, "path": path, "headers": headers, "streaming": streaming},
        body,
    )


def response_head(stream_id: int, status: int, headers: dict[str, str]) -> bytes:
    return encode_inner(RES_HEAD, stream_id, {"status": status, "headers": headers})


def response_chunk(stream_id: int, body: bytes) -> bytes:
    return encode_inner(RES_CHUNK, stream_id, {}, body)


def response_end(stream_id: int) -> bytes:
    return encode_inner(RES_END, stream_id, {})


def ws_open(stream_id: int, path: str, headers: dict[str, str]) -> bytes:
    return encode_inner(WS_OPEN, stream_id, {"path": path, "headers": headers})


def ws_open_ok(stream_id: int) -> bytes:
    return encode_inner(WS_OPEN_OK, stream_id, {})


def ws_data(stream_id: int, payload: bytes, *, binary: bool) -> bytes:
    return encode_inner(WS_DATA, stream_id, {"binary": binary}, payload)


def ws_close(stream_id: int, code: int = 1000, reason: str = "") -> bytes:
    return encode_inner(WS_CLOSE, stream_id, {"code": code, "reason": reason})


def error(stream_id: int, code: str, message: str = "") -> bytes:
    return encode_inner(ERROR, stream_id, {"code": code, "message": message})


def ping(timestamp: int) -> bytes:
    return encode_inner(PING, 0, {"ts": timestamp})


def pong(timestamp: int) -> bytes:
    return encode_inner(PONG, 0, {"ts": timestamp})


def iter_chunks(data: bytes, size: int = CHUNK_BYTES):
    """Split a body into transmit-sized pieces. Used only for in-memory
    bodies; streamed responses are chunked by the reader instead, so a
    large file never becomes a large allocation."""
    for offset in range(0, len(data), size):
        yield data[offset:offset + size]
