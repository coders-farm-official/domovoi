"""Friend Houses' Noise handshakes (domovoi/friend_houses/noise.py).

The published cacophony vectors pin the exact bytes; the rest pin the
refusals a hostile peer could try.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi.friend_houses.noise import (
    MAX_MESSAGE,
    Handshake,
    NoiseError,
    public_bytes,
)

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "noise_vectors.json").read_text(encoding="utf-8")
)["vectors"]


def _key(hex_text: str) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(bytes.fromhex(hex_text))


@pytest.mark.parametrize("vector", VECTORS, ids=[v["protocol_name"] for v in VECTORS])
def test_published_vectors(vector: dict) -> None:
    pattern = vector["protocol_name"].split("_")[1]
    init_psk = bytes.fromhex(vector["init_psks"][0]) if vector.get("init_psks") else None
    resp_psk = bytes.fromhex(vector["resp_psks"][0]) if vector.get("resp_psks") else None
    init = Handshake(
        pattern, initiator=True, prologue=bytes.fromhex(vector["init_prologue"]),
        static=_key(vector["init_static"]), ephemeral=_key(vector["init_ephemeral"]),
        remote_static=bytes.fromhex(vector["init_remote_static"]), psk=init_psk,
    )
    resp = Handshake(
        pattern, initiator=False, prologue=bytes.fromhex(vector["resp_prologue"]),
        static=_key(vector["resp_static"]), ephemeral=_key(vector["resp_ephemeral"]),
        psk=resp_psk,
    )
    sides = [init, resp]
    transports = None
    for i, msg in enumerate(vector["messages"]):
        payload, expected = bytes.fromhex(msg["payload"]), bytes.fromhex(msg["ciphertext"])
        writer, reader = sides[i % 2], sides[(i + 1) % 2]
        if not init.complete:
            assert writer.write_message(payload) == expected
            assert reader.read_message(expected) == payload
            if init.complete:
                assert resp.complete
                assert init.handshake_hash.hex() == vector["handshake_hash"]
                assert resp.remote_static == public_bytes(_key(vector["init_static"]))
                transports = [init.transport(), resp.transport()]
        else:
            assert transports is not None
            assert transports[i % 2].encrypt(payload) == expected
            assert transports[(i + 1) % 2].decrypt(expected) == payload
    assert transports is not None


def _pair(pattern: str = "IK", psk_i: bytes | None = None, psk_r: bytes | None = None,
          prologue_r: bytes = b"p"):
    si, sr = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    init = Handshake(pattern, initiator=True, prologue=b"p", static=si,
                     remote_static=public_bytes(sr), psk=psk_i)
    resp = Handshake(pattern, initiator=False, prologue=prologue_r, static=sr, psk=psk_r)
    return init, resp, si


def test_round_trip_and_the_responder_learns_who_called() -> None:
    init, resp, si = _pair()
    assert resp.read_message(init.write_message(b"hello")) == b"hello"
    assert resp.remote_static == public_bytes(si)
    assert init.read_message(resp.write_message(b"hi")) == b"hi"
    ti, tr = init.transport(), resp.transport()
    assert tr.decrypt(ti.encrypt(b"one")) == b"one"
    assert ti.decrypt(tr.encrypt(b"two")) == b"two"


def test_a_caller_without_the_invite_secret_is_refused() -> None:
    init, resp, _ = _pair("IKpsk2", psk_i=b"\x01" * 32, psk_r=b"\x02" * 32)
    resp.read_message(init.write_message())  # the first message carries no psk proof
    with pytest.raises(NoiseError):
        init.read_message(resp.write_message())


def test_a_different_prologue_is_refused() -> None:
    init, resp, _ = _pair(prologue_r=b"other context")
    with pytest.raises(NoiseError):
        resp.read_message(init.write_message())


def test_a_flipped_bit_is_refused_and_does_not_move_the_counter() -> None:
    init, resp, _ = _pair()
    resp.read_message(init.write_message())
    init.read_message(resp.write_message())
    ti, tr = init.transport(), resp.transport()
    good = ti.encrypt(b"message")
    bad = bytearray(good)
    bad[0] ^= 1
    with pytest.raises(NoiseError):
        tr.decrypt(bytes(bad))
    assert tr.decrypt(good) == b"message"


def test_a_replayed_message_is_refused() -> None:
    init, resp, _ = _pair()
    resp.read_message(init.write_message())
    init.read_message(resp.write_message())
    ti, tr = init.transport(), resp.transport()
    msg = ti.encrypt(b"once")
    tr.decrypt(msg)
    with pytest.raises(NoiseError):
        tr.decrypt(msg)


@pytest.mark.parametrize("cut", [0, 10, 31, 47])
def test_a_short_first_message_is_refused(cut: int) -> None:
    init, resp, _ = _pair()
    with pytest.raises(NoiseError):
        resp.read_message(init.write_message()[:cut])


def test_a_low_order_ephemeral_is_refused() -> None:
    init, resp, _ = _pair()
    msg = bytearray(init.write_message())
    msg[:32] = b"\x00" * 32
    with pytest.raises(NoiseError):
        resp.read_message(bytes(msg))


def test_oversized_messages_are_refused() -> None:
    init, resp, _ = _pair()
    with pytest.raises(NoiseError):
        resp.read_message(b"\x00" * (MAX_MESSAGE + 1))
    resp2_init, resp2, _ = _pair()
    resp2.read_message(resp2_init.write_message())
    resp2_init.read_message(resp2.write_message())
    with pytest.raises(NoiseError):
        resp2_init.transport().encrypt(b"x" * MAX_MESSAGE)


def test_out_of_turn_is_refused() -> None:
    init, resp, _ = _pair()
    with pytest.raises(NoiseError):
        resp.write_message()
    with pytest.raises(NoiseError):
        init.read_message(b"\x00" * 64)


def test_the_pattern_and_psk_must_agree() -> None:
    s = X25519PrivateKey.generate()
    with pytest.raises(ValueError):
        Handshake("IK", initiator=False, prologue=b"", static=s, psk=b"\x00" * 32)
    with pytest.raises(ValueError):
        Handshake("IKpsk2", initiator=True, prologue=b"", static=s,
                  remote_static=public_bytes(s))
    with pytest.raises(ValueError):
        Handshake("XX", initiator=False, prologue=b"", static=s)
    with pytest.raises(ValueError):
        Handshake("IK", initiator=True, prologue=b"", static=s)


def test_the_responder_can_learn_its_psk_from_the_first_message() -> None:
    si, sr = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    psk = b"\x07" * 32
    init = Handshake("IKpsk2", initiator=True, prologue=b"p", static=si,
                     remote_static=public_bytes(sr), psk=psk)
    resp = Handshake("IKpsk2", initiator=False, prologue=b"p", static=sr)
    assert resp.read_message(init.write_message(b"invite 42")) == b"invite 42"
    with pytest.raises(NoiseError):
        Handshake("IKpsk2", initiator=False, prologue=b"p", static=sr).write_message()
    resp.provide_psk(psk)
    assert init.read_message(resp.write_message(b"ok")) == b"ok"
    with pytest.raises(ValueError):
        resp.provide_psk(psk)


def test_a_responder_that_never_got_its_psk_cant_finish() -> None:
    si, sr = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    init = Handshake("IKpsk2", initiator=True, prologue=b"p", static=si,
                     remote_static=public_bytes(sr), psk=b"\x07" * 32)
    resp = Handshake("IKpsk2", initiator=False, prologue=b"p", static=sr)
    resp.read_message(init.write_message())
    with pytest.raises(NoiseError):
        resp.write_message()
