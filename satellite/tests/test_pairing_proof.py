"""The approval exchange itself (satellite/pairing_proof.py): SPAKE2 over
RFC 3526 group 14. One implementation, imported by the device and the core.

No network, no database.
"""

from __future__ import annotations

import random

import pytest

from satellite import pairing_proof as pp

FP_A = "SHA256:" + "A" * 43
FP_B = "SHA256:" + "B" * 43
ROOM = "kitchen"
CODE = "481502"


def _run(device_code, server_code, *, dev_fp=FP_A, srv_fp=FP_A, dev_room=ROOM, srv_room=ROOM):
    dev = pp.DeviceExchange(device_code)
    srv = pp.ServerExchange(server_code, dev.message, room_id=srv_room, fingerprint=srv_fp)
    tag = dev.answer(srv.message, room_id=dev_room, fingerprint=dev_fp)
    return dev, srv, tag


def _probably_prime(n: int, rounds: int = 16) -> bool:
    if n % 2 == 0:
        return False
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    rng = random.Random(1)
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def test_the_group_is_the_rfc_3526_safe_prime():
    """A typo in the 2048-bit constant would make it composite (and the
    exchange unsafe without anything else noticing)."""
    assert pp._P.bit_length() == 2048
    assert _probably_prime(pp._P) and _probably_prime(pp._Q)
    assert pow(pp._G, pp._Q, pp._P) == 1
    for element in (pp._M, pp._N):
        assert pp._valid_element(element)
    assert pp._M != pp._N


def test_the_same_code_proves_both_ways():
    dev, srv, tag = _run(CODE, CODE)
    assert srv.device_proved(tag)
    assert dev.server_proved(srv.proof)


def test_a_wrong_code_proves_nothing_either_way():
    dev, srv, tag = _run(CODE, "481503")
    assert not srv.device_proved(tag)
    with pytest.raises(PermissionError):
        _ = srv.proof   # the server's half is never handed out after a failed check
    assert not dev.server_proved("00" * 32)


def test_the_proof_is_bound_to_the_server_key():
    """A device that proved key A at the address it dialled cannot be
    approved in a run the server computes for key B — replaying a run, or
    a proof, to another server gets nowhere."""
    dev, srv, tag = _run(CODE, CODE, dev_fp=FP_A, srv_fp=FP_B)
    assert not srv.device_proved(tag)


def test_the_proof_is_bound_to_the_room():
    dev, srv, tag = _run(CODE, CODE, dev_room="kitchen", srv_room="hall")
    assert not srv.device_proved(tag)


def test_a_proof_from_one_run_is_worthless_in_another():
    dev1, srv1, tag1 = _run(CODE, CODE)
    assert srv1.device_proved(tag1)
    dev2, srv2, tag2 = _run(CODE, CODE)
    assert not dev2.server_proved(srv1.proof)     # an old server proof, replayed
    assert not srv2.device_proved(tag1)           # an old device proof, replayed
    assert srv2.device_proved(tag2) and dev2.server_proved(srv2.proof)


def test_the_device_message_is_fresh_every_time():
    """The hello carries X = g^x * M^w with a new x per run: nothing in it
    repeats for a code, so it is not a hash of the code a server could
    look up."""
    assert pp.DeviceExchange(CODE).message != pp.DeviceExchange(CODE).message


def test_degenerate_elements_are_refused():
    for value in (0, 1, pp._P - 1, pp._P, pp._P + 1, 2 ** 2048 - 1):
        assert pp.decode(value.to_bytes(256, "big").hex()) is None
    for junk in (None, 5, "", "zz" * 256, "00" * 255, "00" * 257):
        assert pp.decode(junk) is None
    dev = pp.DeviceExchange(CODE)
    assert dev.answer("0" * (pp.ELEMENT_HEX_LEN - 1) + "1", room_id=ROOM, fingerprint=FP_A) is None
    with pytest.raises(ValueError):
        pp.ServerExchange(CODE, pp.encode(pp._P - 1), room_id=ROOM, fingerprint=FP_A)


def test_only_six_digit_codes():
    for bad in ("1234", "12345", "1234567", "12345a", "١٢٣٤٥٦", None, 123456):
        assert not pp.valid_code(bad)
        with pytest.raises((ValueError, TypeError)):
            pp.DeviceExchange(bad)        # type: ignore[arg-type]
    assert pp.valid_code("000000")


def test_malformed_tags_never_verify():
    dev, srv, tag = _run(CODE, CODE)
    for bad in (None, "", "zz" * 32, tag[:-2], tag + "00", 7):
        assert not srv.device_proved(bad)
    assert srv.device_proved(tag)
    for bad in (None, "", "zz" * 32):
        assert not dev.server_proved(bad)
    assert not pp.DeviceExchange(CODE).server_proved(srv.proof)   # never answered


# ─── known answers, for anyone porting the exchange ───────────────────────
#
# An app on another platform (an iPhone in room mode, a phone app's own
# pairing) has to produce these bytes exactly. Any change to the group, to
# M and N, to how the code becomes a scalar or to the transcript changes
# them — and is then a new VERSION of the exchange, not an edit.

KAT_M = (
    "e9138bfd8a67eb0c1de6b0b8b7ad9db702b835f91d6636bc52a87f0305995c17"
    "6636669b41bffc7ecc788b0189e859dd44c976616cfd0c5ac3fbe23a221bdb15"
    "e5d5f41dbd89025252eedd41e86ff190f149c6ed656c74e66a88aa7843b0b3c2"
    "ad25ce488e8559bf6390ab7b82a5fca0779c68506e1fdd222641b1341879252f"
    "802a958f611591bd50d070c31040a4fb9a32abc7af2d691e5cd613f218145fcb"
    "6e5c8cb0301d6cd0e0e44a8361cc67f6ec0ce27c0428cc77606c3f339f937095"
    "734949365c046b45162ff1b030972dce883d7949cb1f5893f4dd0bb9c7479449"
    "389769ec0357745e614e7a31123739873069987c61d4a8cee6f0a503a60c30e6"
)
KAT_N = (
    "475fad6cd736abf7e306edb3567e965036b383c99da8b7196868bc22c70810db"
    "8039e98f958bfa7fc62e44de2eac95d6ec1d7bd366c40a8a25d4f6935c25ea67"
    "ae14507263b92fe3bdc8bca039ecc7f7e6218f2c883add4de53ac445a3b1ac38"
    "172155a504802345e0a0611c62d3e4dd6001267acdbb914221876b9d983d6207"
    "74983a9115a2a76ca5f7792a22f13fcbe20e27033f76696c508a5325fe8fb431"
    "5793eff39ebd605eceb4171ff2ed910192b41134efa452cb6868590bbc738f91"
    "7baaa3115a8bd4a190f4adecfa1a8feb9c7808425a95025db9e99d8021810d3e"
    "75aab40d117b9cb7d56527ac5e371c891cd88a1c99ea93c078e900d0d693e865"
)
#: w for the code "481502".
KAT_W = int(
    "feb842d1f0035f4a86bb9a7cb26e00a79cb1affb0ec9c38bcba52544d885e6bf"
    "c32e5e7900cc6824e93b8cbe006d04699e42e6f8dc575dd7bc54a7449d1a4ffe", 16)
#: The secret exponents of the run below: SHA-256 of a label, as integers.
KAT_X_LABEL = b"domovoi kat x"
KAT_Y_LABEL = b"domovoi kat y"
KAT_FP = "SHA256:" + "1" * 43
#: SHA-256 of X's and Y's 256-byte encodings, and both confirmation tags.
KAT_X_SHA = "3d2c07a63e805f071c60a832050ba02569f695d0698ac345056da22df2732c48"
KAT_Y_SHA = "8300c1c33c9b5d08a1b53e3ca9d033d5ce5b5534ea5783685d1eea7a103bdbfb"
KAT_DEVICE_TAG = "adb967cfd4797043ea90e429848342e372c12576eddb8ac7697f64aeaa70ad8d"
KAT_SERVER_TAG = "98c2558dd793dbf9b103f692b77531b19927f69ffe5837566585bdd7ec1ccc8a"


def test_known_answer_m_n_and_the_code_scalar():
    assert pp.encode(pp._M) == KAT_M
    assert pp.encode(pp._N) == KAT_N
    assert pp._code_scalar(CODE) == KAT_W


def test_known_answer_transcript(monkeypatch):
    import hashlib

    exponents = iter([int(hashlib.sha256(KAT_X_LABEL).hexdigest(), 16),
                      int(hashlib.sha256(KAT_Y_LABEL).hexdigest(), 16)])
    monkeypatch.setattr(pp, "_secret_exponent", lambda: next(exponents))
    dev = pp.DeviceExchange(CODE)
    srv = pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=KAT_FP)
    assert hashlib.sha256(bytes.fromhex(dev.message)).hexdigest() == KAT_X_SHA
    assert hashlib.sha256(bytes.fromhex(srv.message)).hexdigest() == KAT_Y_SHA
    tag = dev.answer(srv.message, room_id=ROOM, fingerprint=KAT_FP)
    assert tag == KAT_DEVICE_TAG
    assert srv.device_proved(tag) and srv.proof == KAT_SERVER_TAG
    assert dev.server_proved(KAT_SERVER_TAG)


# --- version 2: the pairing token is in the transcript (L9, 1.5.0) ----------

TOKEN_HASH = pp.token_digest("t" * 64)


def test_v2_proves_both_ways_with_the_same_token():
    dev = pp.DeviceExchange(CODE)
    srv = pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                            token_hash=TOKEN_HASH)
    tag = dev.answer(srv.message, room_id=ROOM, fingerprint=FP_A, token_hash=TOKEN_HASH)
    assert srv.device_proved(tag) and dev.server_proved(srv.proof)


@pytest.mark.parametrize("dev_hash, srv_hash", [
    (TOKEN_HASH, pp.token_digest("r" * 64)),   # a token swapped on the way
    (TOKEN_HASH, None),                        # a v1 core, a v2 device
    (None, TOKEN_HASH),                        # a v1 device, a v2 run
])
def test_v2_fails_for_any_other_token_or_version(dev_hash, srv_hash):
    dev = pp.DeviceExchange(CODE)
    srv = pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                            token_hash=srv_hash)
    tag = dev.answer(srv.message, room_id=ROOM, fingerprint=FP_A, token_hash=dev_hash)
    assert not srv.device_proved(tag)


def test_a_malformed_token_hash_is_refused():
    dev = pp.DeviceExchange(CODE)
    with pytest.raises(ValueError):
        pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A, token_hash="zz")


def test_the_fixture_vector_matches():
    """The shared fixture's version-2 tag vector, so another implementation
    can check its transcript against this one."""
    import json
    from pathlib import Path

    doc = json.loads((Path(__file__).resolve().parents[2] / "docs" / "uml"
                      / "satellite-fixtures.json").read_text(encoding="utf-8"))
    vec = doc["pairing_proof"]["v2_tag_vector"]
    device, server = pp._tags(
        room_id=vec["room_id"], fingerprint=vec["fingerprint"],
        x_elem=int(vec["x"], 16), y_elem=int(vec["y"], 16), z_elem=int(vec["z"], 16),
        w=int(vec["w"], 16), token_hash=vec["token_hash"],
    )
    assert device.hex() == vec["device_tag"] and server.hex() == vec["server_tag"]
    assert pp.token_digest(vec["token"]) == vec["token_hash"]
    assert doc["pairing_proof"]["version"] == pp.VERSION
    assert doc["pairing_proof"]["hello_v2_adds"] == {"pairing_proof": pp.TOKEN_BOUND}


# --- version 3: the support key is in the transcript too (1.5.0) -----------

SUPPORT_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def test_v3_proves_both_ways_with_the_same_token_and_key():
    for key in (SUPPORT_KEY, ""):
        dev = pp.DeviceExchange(CODE)
        srv = pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                                token_hash=TOKEN_HASH, support_key=key)
        tag = dev.answer(srv.message, room_id=ROOM, fingerprint=FP_A, token_hash=TOKEN_HASH,
                         support_key=key)
        assert srv.device_proved(tag) and dev.server_proved(srv.proof)


@pytest.mark.parametrize("dev_key, srv_key", [
    (SUPPORT_KEY, "B" * 43 + "="),   # a key swapped on the way
    (SUPPORT_KEY, ""),               # a key stripped from the hello
    ("", SUPPORT_KEY),               # a key added to the hello
    (SUPPORT_KEY, None),             # a v2 run on the core's side
    (None, SUPPORT_KEY),             # a v2 device, a v3 run
])
def test_v3_fails_for_any_other_key_or_version(dev_key, srv_key):
    dev = pp.DeviceExchange(CODE)
    srv = pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                            token_hash=TOKEN_HASH, support_key=srv_key)
    tag = dev.answer(srv.message, room_id=ROOM, fingerprint=FP_A, token_hash=TOKEN_HASH,
                     support_key=dev_key)
    assert not srv.device_proved(tag)


def test_a_key_bound_run_needs_the_token_bound_too():
    dev = pp.DeviceExchange(CODE)
    with pytest.raises(ValueError):
        pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                          support_key=SUPPORT_KEY)
    with pytest.raises(ValueError):
        pp.ServerExchange(CODE, dev.message, room_id=ROOM, fingerprint=FP_A,
                          token_hash=TOKEN_HASH, support_key="k" * 300)


def test_the_v3_fixture_vector_matches():
    import json
    from pathlib import Path

    doc = json.loads((Path(__file__).resolve().parents[2] / "docs" / "uml"
                      / "satellite-fixtures.json").read_text(encoding="utf-8"))
    sec = doc["pairing_proof"]
    vec = sec["v3_tag_vector"]
    for key, d_name, s_name in ((vec["support_key"], "device_tag", "server_tag"),
                                ("", "no_key_device_tag", "no_key_server_tag")):
        device, server = pp._tags(
            room_id=vec["room_id"], fingerprint=vec["fingerprint"],
            x_elem=int(vec["x"], 16), y_elem=int(vec["y"], 16), z_elem=int(vec["z"], 16),
            w=int(vec["w"], 16), token_hash=vec["token_hash"], support_key=key,
        )
        assert device.hex() == vec[d_name] and server.hex() == vec[s_name]
    assert sec["hello_v3_adds"] == {"pairing_proof": pp.KEY_BOUND}
    assert sec["supported"] == list(pp.SUPPORTED)
