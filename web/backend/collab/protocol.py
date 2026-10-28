"""The y-websocket wire format, as the stock ``y-websocket`` client speaks it.

Every WebSocket message is one binary frame that starts with a varuint
message type:

* ``0`` SYNC: a varuint sync step (``0`` step 1, ``1`` step 2, ``2`` update)
  then a varuint-length byte string (a state vector or a Yjs update);
* ``1`` AWARENESS: a varuint-length byte string holding an awareness update
  (``count``, then per client ``clientID``, ``clock``, JSON state as a
  varuint-length UTF-8 string; ``"null"`` means "gone");
* ``2`` AUTH: permission-denied notices (server to client only; unused);
* ``3`` QUERY_AWARENESS: "send me everyone's presence";
* ``100`` CONTROL: Domovoi's own small JSON messages (hello, seed, ready,
  save, reset, ...). The client adds a handler for it
  (``provider.messageHandlers[100]``, web/static/collab.js); the stock
  provider has no type 100, so nothing else is ever sent one.

pycrdt supplies the sync half (``create_sync_message``,
``handle_sync_message``, ...). The awareness payload is parsed here rather
than through ``pycrdt.Awareness``: the server only RELAYS presence, it never
has a presence of its own, and it needs to know which client ids a socket
owns so it can announce them gone when the socket drops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Nothing here imports pycrdt: the web process never loads it (documents
# live in worker subprocesses, web/backend/collab/worker.py), so the
# y-websocket framing is written out by hand.


def write_var_uint(num: int) -> bytes:
    out = bytearray()
    while num > 127:
        out.append(128 | (num & 127))
        num >>= 7
    out.append(num)
    return bytes(out)

MSG_SYNC = 0
MSG_AWARENESS = 1
MSG_AUTH = 2
MSG_QUERY_AWARENESS = 3
MSG_CONTROL = 100

SYNC_STEP1 = 0
SYNC_STEP2 = 1
SYNC_UPDATE = 2

# The root types the editors here write to, by name and kind. The web
# process refuses any other root before a worker sees it (guard.check);
# the worker maps the kinds to pycrdt types (content.ROOTS).
ROOT_KINDS: dict[str, str] = {"default": "xml", "text": "text"}

# An update that changes nothing, as Yjs encodes it (no structs, empty
# delete set). A client's sync step 2 is this when the server already had
# everything it holds.
EMPTY_UPDATE = b"\x00\x00"


class ProtocolError(ValueError):
    """A frame that is not the y-websocket protocol (or is truncated)."""


def read_var_uint(buf: bytes, pos: int) -> tuple[int, int]:
    """lib0 varuint at ``pos``: ``(value, next_pos)``. At most 10 bytes, so
    a hostile frame cannot make this loop for long."""
    value = 0
    shift = 0
    for _ in range(10):
        if pos >= len(buf):
            raise ProtocolError("truncated varuint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise ProtocolError("varuint too long")


def read_var_bytes(buf: bytes, pos: int) -> tuple[bytes, int]:
    length, pos = read_var_uint(buf, pos)
    end = pos + length
    if end > len(buf):
        raise ProtocolError("truncated byte string")
    return bytes(buf[pos:end]), end


def var_bytes(data: bytes) -> bytes:
    return write_var_uint(len(data)) + data


def var_string(text: str) -> bytes:
    return var_bytes(text.encode("utf-8"))


# ─── Awareness ─────────────────────────────────────────────────────────────


@dataclass
class AwarenessEntry:
    client_id: int
    clock: int
    state: str  # the JSON text as sent; "null" when the client left


def parse_awareness_update(update: bytes) -> list[AwarenessEntry]:
    count, pos = read_var_uint(update, 0)
    if count > 64:
        raise ProtocolError("too many awareness entries in one update")
    out: list[AwarenessEntry] = []
    for _ in range(count):
        client_id, pos = read_var_uint(update, pos)
        clock, pos = read_var_uint(update, pos)
        raw, pos = read_var_bytes(update, pos)
        try:
            state = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProtocolError("awareness state is not UTF-8") from e
        out.append(AwarenessEntry(client_id, clock, state))
    return out


def encode_awareness_update(entries: list[AwarenessEntry]) -> bytes:
    parts = [write_var_uint(len(entries))]
    for e in entries:
        parts.append(write_var_uint(e.client_id))
        parts.append(write_var_uint(e.clock))
        parts.append(var_string(e.state))
    return b"".join(parts)


def awareness_message(update: bytes) -> bytes:
    return bytes([MSG_AWARENESS]) + var_bytes(update)


# ─── Domovoi control messages ──────────────────────────────────────────────


def control_message(payload: dict[str, Any]) -> bytes:
    return write_var_uint(MSG_CONTROL) + var_string(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )


def parse_control(buf: bytes, pos: int) -> dict[str, Any]:
    raw, _ = read_var_bytes(buf, pos)
    if len(raw) > 4096:
        raise ProtocolError("control message too long")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ProtocolError("control message is not JSON") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("t"), str):
        raise ProtocolError("control message has no type")
    return doc


# ─── Sync messages (y-protocols/sync) ───────────────────────────────────


def sync_step1(state_vector: bytes) -> bytes:
    return bytes([MSG_SYNC, SYNC_STEP1]) + var_bytes(state_vector)


def sync_step2(update: bytes) -> bytes:
    return bytes([MSG_SYNC, SYNC_STEP2]) + var_bytes(update)


def sync_update(update: bytes) -> bytes:
    return bytes([MSG_SYNC, SYNC_UPDATE]) + var_bytes(update)


EMPTY_STATE_VECTOR = bytes([0])


def parse_state_vector(sv: bytes) -> dict[int, int]:
    """client -> clock, from an encoded state vector."""
    n, pos = read_var_uint(sv, 0)
    out: dict[int, int] = {}
    for _ in range(n):
        client, pos = read_var_uint(sv, pos)
        clock, pos = read_var_uint(sv, pos)
        out[client] = clock
    return out
