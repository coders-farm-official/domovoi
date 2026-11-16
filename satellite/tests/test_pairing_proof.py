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
