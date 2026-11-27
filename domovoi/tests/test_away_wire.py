"""Away chat's wire parser (domovoi/away/wire.py), DB-free and socket-free.

What the listener refuses silently (``ProtocolError``) and what it answers
with an ``ERR`` (``BadRequest`` and its code), field by field, against the
contract (design note AWAY-CHAT-WIRE §2-§5). The same parser is fuzzed
(fuzz_targets.away_wire, under Hypothesis in test_fuzz_parsers.py and
Atheris in scripts/dev/fuzz.py).
"""

from __future__ import annotations

import hashlib
import json
import struct

import pytest

from domovoi.away import wire

UUID = "6f1c2b9e-0d4a-4f7e-9c3b-2a1d5e8f7a60"


def _send(**over) -> dict:
    doc = {"op": "chat.send", "chat_id": None, "client_id": UUID, "text": "hello"}
    doc.update(over)
    return doc


def _bad(doc, code: str = "bad_request") -> None:
    with pytest.raises(wire.BadRequest) as e:
        wire.parse_request(doc) if isinstance(doc, dict) else wire.parse_request_bytes(doc)
    assert e.value.code == code


# ─── the hello ───────────────────────────────────────────────────────────


def test_phone_hello_exact_layout() -> None:
    raw = wire.encode_phone_hello(2, "1.5.0+3")
    hello = wire.parse_phone_hello(raw)
    assert (hello.proto, hello.platform, hello.version) == (1, "iphone", "1.5.0+3")
    assert wire.parse_phone_hello(wire.encode_phone_hello(1, "")).platform == "android"


@pytest.mark.parametrize("raw", [
    b"", b"\x00\x01\x01",                                   # too short
    struct.pack(">HBB", 2, 1, 0),                           # a proto this house doesn't speak
    struct.pack(">HBB", 1, 3, 0),                           # unknown platform
    struct.pack(">HBB", 1, 1, 4) + b"1.5",                  # ver_len says more than is there
    struct.pack(">HBB", 1, 1, 2) + b"1.5",                  # ...or less
    struct.pack(">HBB", 1, 1, 33) + b"1" * 33,              # over 32
    struct.pack(">HBB", 1, 1, 3) + b"1 5",                  # outside [0-9A-Za-z.+-]
    struct.pack(">HBB", 1, 1, 2) + b"\xc3\xa9",
])
def test_phone_hello_refusals_are_silent(raw: bytes) -> None:
    with pytest.raises(wire.ProtocolError):
        wire.parse_phone_hello(raw)


def test_house_hello_says_proto_and_picture_cap() -> None:
    assert wire.parse_house_hello(wire.HOUSE_HELLO) == (1, 1_048_576)
    assert len(wire.HOUSE_HELLO) == 6


def test_handshake_message_bounds_match_the_hello() -> None:
    assert wire.MSG1_MIN == 32 + 48 + 4 + 16 and wire.MSG1_MAX == 32 + 48 + 36 + 16


# ─── frames and headers ──────────────────────────────────────────────────


def test_frame_size_refuses_zero_and_over_long() -> None:
    assert wire.frame_size(b"\x00\x05") == 5
    for head in (b"\x00\x00", b"\x00"):
        with pytest.raises(wire.ProtocolError):
            wire.frame_size(head)
    with pytest.raises(wire.ProtocolError):
        wire.frame_size(struct.pack(">H", wire.MSG1_MAX + 1), max_size=wire.MSG1_MAX)
    with pytest.raises(ValueError):
        wire.frame(b"")


def test_header_round_trip() -> None:
    raw = wire.encode_message(wire.REQ, 7, b"{}", more=True)
    msg = wire.parse_message(raw)
    assert (msg.kind, msg.req, msg.more, msg.body) == (wire.REQ, 7, True, b"{}")


@pytest.mark.parametrize("raw", [
    b"\x01\x00\x00",                                         # short header
    struct.pack(">BIB", 0x05, 1, 0),                         # unknown kind
    struct.pack(">BIB", 0x81, 1, 0) + b"{}",                 # a house kind from the phone
    struct.pack(">BIB", wire.REQ, 1, 0x02) + b"{}",          # unknown flag
    struct.pack(">BIB", wire.REQ, 0, 0) + b"{}",             # req 0 on a request
    struct.pack(">BIB", wire.REQ, 1, 0),                     # empty request piece
    struct.pack(">BIB", wire.PING, 1, 0),                    # ping with a req
    struct.pack(">BIB", wire.PING, 0, 1),                    # ping with MORE
    struct.pack(">BIB", wire.BYE, 0, 0) + b"x",              # bye with a body
    struct.pack(">BIB", wire.BLOB, 1, 0) + b"\x00\x00\x00\x00",           # no picture bytes
    struct.pack(">BIB", wire.BLOB, 1, 0) + b"\x00" * (4 + 65_001),        # piece over 65,000
], ids=lambda raw: raw[:8].hex())
def test_malformed_headers_are_protocol_errors(raw: bytes) -> None:
    with pytest.raises(wire.ProtocolError):
        wire.parse_message(raw)


def test_a_long_resp_splits_with_more_and_nothing_else_does() -> None:
    body = b"x" * (wire.MAX_PLAINTEXT * 2)
    parts = wire.split_json(wire.RESP, 9, body)
    assert len(parts) == 3
    assert all(len(p) <= wire.MAX_PLAINTEXT for p in parts)
    assert b"".join(p[wire.HEADER_SIZE:] for p in parts) == body
    assert [p[5] & 1 for p in parts] == [1, 1, 0]
    with pytest.raises(ValueError):
        wire.split_json(wire.EVENT, 9, body)


# ─── strict JSON ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    b'{"op":"chat.list","op":"chat.list"}',                  # duplicate key
    b'{"op":"chat.list","limit":NaN}',
    b'{"op":"chat.list","limit":Infinity}',
    b'[1]', b'"chat.list"', b'not json', b'\xff\xfe',
    b'{"op":"chat.stream","chat_id":[[[[1]]]]}',             # depth 5
    b'{"op":"chat.list"} {"op":"chat.list"}',
])
def test_strict_json_refusals(raw: bytes) -> None:
    _bad(raw)


def test_depth_counts_brackets_outside_strings_only() -> None:
    text = "[[[[[" * 10
    req = wire.parse_request_bytes(json.dumps(_send(text=text)).encode())
    assert req.text == text


def test_a_deeply_nested_request_never_reaches_the_parser() -> None:
    _bad(b"[" * 60_000 + b"]" * 60_000)


def test_over_160_kib_is_too_large() -> None:
    _bad(b" " * (wire.MAX_REQ + 1), "too_large")


# ─── the request set ─────────────────────────────────────────────────────


def test_each_op_with_its_defaults() -> None:
    assert wire.parse_request({"op": "chat.list"}) == wire.Request("chat.list", archived=False, limit=50)
    h = wire.parse_request({"op": "chat.history", "chat_id": 12})
    assert (h.chat_id, h.before_id, h.limit) == (12, None, 50)
    assert wire.parse_request({"op": "chat.stream", "chat_id": 3}).chat_id == 3
    assert wire.parse_request({"op": "chat.cancel", "chat_id": 3}).chat_id == 3
    s = wire.parse_request(_send(admin_grant="a" * 64))
    assert (s.chat_id, s.client_id, s.text, s.admin_grant) == (None, UUID, "hello", "a" * 64)


@pytest.mark.parametrize("doc", [
    {"op": "chat.list"}, {"op": "chat.list", "archived": True, "limit": 1, "cursor": "abc_-1"},
    {"op": "chat.history", "chat_id": 1, "before_id": 9, "limit": 50},
    _send(), _send(chat_id=12, text="", picture={"type": "webp", "size": 1, "sha256": "a" * 64}),
    {"op": "chat.stream", "chat_id": 2**63 - 1},
])
def test_to_json_round_trips(doc: dict) -> None:
    req = wire.parse_request(doc)
    assert wire.parse_request(req.to_json()) == req


@pytest.mark.parametrize("doc", [
    {"op": "chat.delete", "chat_id": 1},                     # not in the set
    {"chat_id": 1},                                          # no op
    {"op": "chat.list", "q": "search"},                      # unknown key: no search away
    {"op": "chat.list", "scope": "all"},
    {"op": "chat.list", "limit": 0}, {"op": "chat.list", "limit": 51},
    {"op": "chat.list", "limit": True}, {"op": "chat.list", "limit": 5.0},
    {"op": "chat.list", "archived": "yes"},
    {"op": "chat.list", "cursor": "has space"}, {"op": "chat.list", "cursor": ""},
    {"op": "chat.history"},                                  # chat_id required
    {"op": "chat.history", "chat_id": 0}, {"op": "chat.history", "chat_id": 2**63},
    {"op": "chat.history", "chat_id": "12"},
    {"op": "chat.stream", "chat_id": None},
    {"op": "chat.send", "text": "x"},                        # client_id required
    _send(client_id="6F1C2B9E-0D4A-4F7E-9C3B-2A1D5E8F7A60"),   # upper case
    _send(client_id="not-a-uuid"),
    _send(text=""), _send(text="   "),                        # empty with no picture
    _send(text=5), _send(text="a\x00b"), _send(text="\ud800"),
    _send(chat_id=0),
    _send(admin_grant=""), _send(admin_grant="has space"), _send(admin_grant="x" * 257),
    _send(picture={"type": "png", "size": 1, "sha256": "a" * 64}),
    _send(picture={"type": "jpeg", "size": 0, "sha256": "a" * 64}),
    _send(picture={"type": "jpeg", "size": 1, "sha256": "A" * 64}),
    _send(picture={"type": "jpeg", "size": 1}),
    _send(picture={"type": "jpeg", "size": 1, "sha256": "a" * 64, "name": "x.jpg"}),
    _send(picture="yes"),
    _send(device_id="android-123"),                          # identity is never named
])
def test_bad_requests(doc: dict) -> None:
    _bad(doc)


def test_too_large_fields() -> None:
    _bad(_send(text="x" * 32_001), "too_large")
    _bad(_send(picture={"type": "jpeg", "size": 1_048_577, "sha256": "a" * 64}), "too_large")
    assert wire.parse_request(_send(text="é" * 32_000)).text == "é" * 32_000  # code points, not bytes


# ─── assembling ──────────────────────────────────────────────────────────


def test_request_pieces_join_and_cap() -> None:
    raw = json.dumps(_send(text="y" * 1000)).encode()
    pieces = wire.RequestPieces()
    for i in range(0, len(raw), 100):
        pieces.add(raw[i:i + 100])
    assert pieces.finish().text == "y" * 1000
    over = wire.RequestPieces()
    over.add(b" " * wire.MAX_REQ)
    over.add(b" ")
    with pytest.raises(wire.BadRequest) as e:
        over.finish()
    assert e.value.code == "too_large"
    far = wire.RequestPieces()
    with pytest.raises(wire.ProtocolError):
        for _ in range(20):
            far.add(b" " * 60_000)


def _picture(data: bytes, kind: str = "jpeg") -> wire.Picture:
    return wire.Picture(kind, len(data), hashlib.sha256(data).hexdigest())


def test_picture_reassembly_and_checks() -> None:
    data = b"\xff\xd8\xff" + bytes(range(256)) * 600
    sink = wire.PictureSink(_picture(data))
    offset = 0
    while offset < len(data):
        piece = data[offset:offset + 65_000]
        done = sink.add(offset, piece)
        offset += len(piece)
    assert done and sink.finish() == data


@pytest.mark.parametrize("pieces", [
    [(1, b"x")],                       # doesn't start at 0
    [(0, b"ab"), (1, b"cd")],          # overlap
    [(0, b"ab"), (3, b"cd")],          # gap
    [(0, b"abcdef")],                  # overrun (size 4)
])
def test_picture_gaps_overlaps_overruns_are_protocol_errors(pieces) -> None:
    sink = wire.PictureSink(wire.Picture("jpeg", 4, "0" * 64))
    with pytest.raises(wire.ProtocolError):
        for offset, data in pieces:
            sink.add(offset, data)


def test_picture_sha256_and_kind_are_checked() -> None:
    data = b"\xff\xd8\xff\xe0" + b"j" * 10
    wrong = wire.PictureSink(wire.Picture("jpeg", len(data), "0" * 64))
    wrong.add(0, data)
    with pytest.raises(wire.BadRequest) as e:
        wrong.finish()
    assert e.value.code == "unsupported_picture"
    said_webp = wire.PictureSink(_picture(data, "webp"))
    said_webp.add(0, data)
    with pytest.raises(wire.BadRequest):
        said_webp.finish()
    webp = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"w" * 8
    ok = wire.PictureSink(_picture(webp, "webp"))
    ok.add(0, webp)
    assert ok.finish() == webp


def test_a_refused_requests_picture_is_read_and_dropped() -> None:
    sink = wire.PictureSink(wire.Picture("jpeg", 4, "0" * 64), keep=False)
    assert not sink.add(0, b"ab")
    assert sink.add(2, b"cd")
    assert sink.parts == []
