"""Frame codec: round trips, bounds, and hostile input.

The bounds tests matter more than the round trips. A codec that trusts a
peer-supplied length field is a memory-exhaustion bug, and the peer here
— though authenticated — is still not trusted with the household
server's heap.
"""

from __future__ import annotations

import json

import pytest

from domovoi_plugin_ltl_remote import framing
from domovoi_plugin_ltl_remote.framing import FramingError

LINK = bytes(range(16))


# ─── outer frames ────────────────────────────────────────────────────────


def test_outer_round_trip():
    raw = framing.encode_outer(framing.OP_LINK_DATA, LINK, b"sealed bytes")
    frame = framing.decode_outer(raw)
    assert frame.opcode == framing.OP_LINK_DATA
    assert frame.link_id == LINK
    assert frame.payload == b"sealed bytes"


def test_outer_header_is_exactly_eighteen_bytes():
    """The relay parses this header and nothing else, so its layout is
    part of the contract with the Java side."""
    raw = framing.encode_outer(framing.OP_CONTROL, framing.ZERO_LINK_ID, b"")
    assert len(raw) == 18
    assert raw[0] == framing.PROTOCOL_VERSION
    assert raw[1] == framing.OP_CONTROL


def test_outer_rejects_a_wrong_length_link_id():
    with pytest.raises(FramingError):
        framing.encode_outer(framing.OP_LINK_DATA, b"short", b"")


def test_outer_rejects_unknown_opcodes():
    with pytest.raises(FramingError):
        framing.encode_outer(0x77, LINK, b"")
    with pytest.raises(FramingError):
        framing.decode_outer(bytes([1, 0x77]) + LINK)


def test_outer_rejects_a_future_version():
    """Versions are refused, never guessed at — PROTOCOL.md §9."""
    raw = bytearray(framing.encode_outer(framing.OP_CONTROL, LINK, b""))
    raw[0] = 2
    with pytest.raises(FramingError, match="version"):
        framing.decode_outer(bytes(raw))


def test_outer_rejects_a_truncated_header():
    with pytest.raises(FramingError):
        framing.decode_outer(b"\x01\x02short")


def test_outer_rejects_an_oversized_frame():
    huge = b"\x00" * (framing.MAX_OUTER_BYTES + 1)
    with pytest.raises(FramingError):
        framing.decode_outer(huge)


# ─── JSON payloads ───────────────────────────────────────────────────────


def test_json_payload_round_trip_preserves_unicode():
    payload = {"reason": "kitchen — café", "code": "OK"}
    assert framing.decode_json_payload(framing.encode_json_payload(payload)) == payload


@pytest.mark.parametrize("raw", [b"not json", b"[1,2,3]", b"\xff\xfe", b'"a string"'])
def test_json_payload_rejects_non_objects(raw):
    with pytest.raises(FramingError):
        framing.decode_json_payload(raw)


def test_json_payload_rejects_oversized_input():
    with pytest.raises(FramingError):
        framing.decode_json_payload(b"{}" + b" " * framing.MAX_HEADER_BYTES)


# ─── inner frames ────────────────────────────────────────────────────────


def test_inner_round_trip():
    raw = framing.request(
        7, "POST", "/api/satellites/kitchen/volume",
        {"content-type": "application/json"}, b'{"volume": 40}',
    )
    frame = framing.decode_inner(raw)
    assert frame.type == framing.REQ
    assert frame.type_name == "REQ"
    assert frame.stream_id == 7
    assert frame.header["method"] == "POST"
    assert frame.header["path"] == "/api/satellites/kitchen/volume"
    assert json.loads(frame.body) == {"volume": 40}


def test_inner_round_trip_with_an_empty_body():
    frame = framing.decode_inner(framing.response_end(3))
    assert frame.type == framing.RES_END
    assert frame.body == b""
    assert frame.header == {}


def test_inner_body_is_binary_safe():
    payload = bytes(range(256)) * 8
    frame = framing.decode_inner(framing.response_chunk(11, payload))
    assert frame.body == payload


@pytest.mark.parametrize(
    "builder,expected",
    [
        (lambda: framing.response_head(1, 204, {"x": "y"}), framing.RES_HEAD),
        (lambda: framing.ws_open(3, "/ws/state", {}), framing.WS_OPEN),
        (lambda: framing.ws_open_ok(3), framing.WS_OPEN_OK),
        (lambda: framing.ws_data(3, b"hi", binary=True), framing.WS_DATA),
        (lambda: framing.ws_close(3, 1001, "bye"), framing.WS_CLOSE),
        (lambda: framing.ping(1700), framing.PING),
        (lambda: framing.pong(1700), framing.PONG),
        (lambda: framing.error(3, framing.ERR_PROTOCOL, "no"), framing.ERROR),
    ],
)
def test_every_constructor_produces_its_own_type(builder, expected):
    assert framing.decode_inner(builder()).type == expected


def test_inner_rejects_unknown_types():
    with pytest.raises(FramingError):
        framing.encode_inner(0x99, 1, {})
    raw = bytearray(framing.response_end(1))
    raw[0] = 0x99
    with pytest.raises(FramingError):
        framing.decode_inner(bytes(raw))


def test_inner_rejects_an_out_of_range_stream_id():
    with pytest.raises(FramingError):
        framing.encode_inner(framing.RES_END, 2**32, {})


def test_inner_rejects_a_header_length_that_runs_past_the_frame():
    """The declared length is checked against what we actually hold
    before anything is sliced."""
    raw = bytearray(framing.response_end(1))
    raw[5:9] = (1000).to_bytes(4, "big")
    with pytest.raises(FramingError, match="runs past"):
        framing.decode_inner(bytes(raw))


def test_inner_rejects_an_absurd_declared_header_length():
    """A peer claiming a 4 GiB header must not get us to allocate
    anything at all."""
    raw = bytearray(framing.response_end(1))
    raw[5:9] = (0xFFFFFFFF).to_bytes(4, "big")
    with pytest.raises(FramingError, match="size cap"):
        framing.decode_inner(bytes(raw))


def test_inner_rejects_a_truncated_frame():
    with pytest.raises(FramingError):
        framing.decode_inner(b"\x01\x00\x00")


def test_inner_rejects_an_oversized_header_at_encode_time():
    with pytest.raises(FramingError):
        framing.encode_inner(
            framing.REQ, 1, {"path": "/" + "a" * framing.MAX_HEADER_BYTES}
        )


def test_inner_rejects_an_oversized_body_at_encode_time():
    with pytest.raises(FramingError):
        framing.encode_inner(
            framing.RES_CHUNK, 1, {}, b"\x00" * (framing.MAX_INNER_BYTES + 1)
        )


# ─── chunking ────────────────────────────────────────────────────────────


def test_iter_chunks_covers_the_input_exactly():
    data = bytes(range(256)) * 900        # ~230 KB, several chunks
    chunks = list(framing.iter_chunks(data))
    assert len(chunks) > 1
    assert all(len(c) <= framing.CHUNK_BYTES for c in chunks)
    assert b"".join(chunks) == data


def test_iter_chunks_of_empty_input_yields_nothing():
    assert list(framing.iter_chunks(b"")) == []


def test_a_full_chunk_still_fits_an_inner_frame():
    """The emitted chunk size has to leave room for the inner header and
    the sealed layer's overhead, or streaming would fail only on large
    responses — the worst place to discover it."""
    raw = framing.response_chunk(1, b"\x00" * framing.CHUNK_BYTES)
    assert len(raw) < framing.MAX_INNER_BYTES
