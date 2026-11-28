#!/usr/bin/env python3
"""Write DomovoiKit's away chat Noise fixture from the house's own code.

    PYTHONPATH=. python ios/scripts/make-away-noise-fixture.py        # from the repo root
    PYTHONPATH=. python ios/scripts/make-away-noise-fixture.py --check

The phone's Noise (ios/Packages/DomovoiKit/Sources/DomovoiKit/AwayNoise.swift)
must produce byte for byte what the house's (domovoi/friend_houses/noise.py)
expects. The published cacophony IK vector pins the pattern; this file adds a
handshake with away chat's own prologue and hellos, and transport messages
in both directions, all written by the house's implementation with fixed
keys, so `AwayNoiseTests` checks the Swift against the Python without a
socket. The keys are test constants, never anybody's.

Standard library plus `cryptography` (a core dependency). `--check` fails when
the committed file differs from what this writes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi.away import wire
from domovoi.friend_houses.noise import Handshake, public_bytes

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "ios/Packages/DomovoiKit/Tests/DomovoiKitTests/Fixtures/away_noise_interop.json"
VECTORS = ROOT / "domovoi/tests/fixtures/noise_vectors.json"


def key(byte: int) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(bytes([byte]) * 31 + b"\x40")


def raw(k: X25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    return k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()


def build() -> dict:
    ik = [v for v in json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
          if v["protocol_name"] == "Noise_IK_25519_ChaChaPoly_SHA256"]
    phone_s, phone_e, house_s, house_e = key(0x11), key(0x22), key(0x33), key(0x44)
    phone = Handshake("IK", initiator=True, prologue=wire.PROLOGUE, static=phone_s,
                      remote_static=public_bytes(house_s), ephemeral=phone_e)
    house = Handshake("IK", initiator=False, prologue=wire.PROLOGUE, static=house_s, ephemeral=house_e)
    hello = wire.encode_phone_hello(2, "1.5.0")
    msg1 = phone.write_message(hello)
    assert house.read_message(msg1) == hello
    msg2 = house.write_message(wire.HOUSE_HELLO)
    assert phone.read_message(msg2) == wire.HOUSE_HELLO
    pt, ht = phone.transport(), house.transport()
    to_house = [
        wire.encode_message(wire.REQ, 1, wire.dump({"op": "chat.list"})),
        wire.encode_message(wire.PING, 0),
        wire.encode_message(wire.BLOB, 2, wire.blob_body(0, b"\xff\xd8\xff\xe0")),
    ]
    to_phone = [
        wire.encode_message(wire.RESP, 1, wire.dump({"threads": [], "next": None})),
        wire.encode_message(wire.PONG, 0),
        wire.encode_message(wire.HOUSE_BYE, 0, wire.dump({"code": "revoked"})),
    ]
    transport = []
    for a, b in zip(to_house, to_phone):
        transport.append({"from": "phone", "plaintext": a.hex(), "ciphertext": pt.encrypt(a).hex()})
        assert ht.decrypt(bytes.fromhex(transport[-1]["ciphertext"])) == a
        transport.append({"from": "house", "plaintext": b.hex(), "ciphertext": ht.encrypt(b).hex()})
        assert pt.decrypt(bytes.fromhex(transport[-1]["ciphertext"])) == b
    return {
        "source": "ios/scripts/make-away-noise-fixture.py, from domovoi/friend_houses/noise.py and "
                  "domovoi/away/wire.py; the IK vector is cacophony's (domovoi/tests/fixtures/noise_vectors.json)",
        "cacophony_ik": ik,
        "away": {
            "prologue": wire.PROLOGUE.hex(),
            "phone_static": raw(phone_s),
            "phone_ephemeral": raw(phone_e),
            "house_static": raw(house_s),
            "house_static_public": public_bytes(house_s).hex(),
            "house_ephemeral": raw(house_e),
            "phone_hello": hello.hex(),
            "house_hello": wire.HOUSE_HELLO.hex(),
            "message1": msg1.hex(),
            "message2": msg2.hex(),
            "handshake_hash": phone.handshake_hash.hex(),
            "transport": transport,
        },
    }


def main() -> int:
    text = json.dumps(build(), indent=2) + "\n"
    if "--check" in sys.argv[1:]:
        if OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is out of date: run this script", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
