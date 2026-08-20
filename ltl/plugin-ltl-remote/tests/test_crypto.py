"""The end-to-end layer: handshake, sealing, identity.

These tests are the specification's teeth. ``ltl/docs/PROTOCOL.md`` §3
and §4 describe the construction; what is asserted here is that the
construction actually does what a security claim needs it to do — that a
wrong key fails, a replayed frame fails, a tampered frame fails, and a
peer who is not approved never reaches key agreement at all.
"""

from __future__ import annotations

import os
import stat

import pytest

from domovoi_plugin_ltl_remote import crypto
from domovoi_plugin_ltl_remote.crypto import (
    ClientHandshake,
    CryptoError,
    HomeHandshake,
    generate_keypair,
)


@pytest.fixture
def household():
    return crypto.Identity(dh=generate_keypair(), sig=generate_keypair())


@pytest.fixture
def device():
    return generate_keypair()


def complete_handshake(household, device, device_id="d_test"):
    """Drive both roles to a sealed pair, the way a real link does."""
    client = ClientHandshake(device, household.dh.public_raw, device_id)
    home = HomeHandshake(household.dh)
    home_hello = home.respond(client.hello(), device.public_raw)
    confirm, client_link = client.finish(home_hello)
    return client_link, home.finish(confirm)


# ─── base64url ───────────────────────────────────────────────────────────


def test_b64u_round_trips_and_is_unpadded():
    for length in range(0, 40):
        raw = os.urandom(length)
        encoded = crypto.b64u(raw)
        assert "=" not in encoded
        assert crypto.unb64u(encoded) == raw


def test_unb64u_rejects_non_string():
    with pytest.raises(CryptoError):
        crypto.unb64u(None)


# ─── keys ────────────────────────────────────────────────────────────────


def test_public_keys_are_uncompressed_sec1():
    pair = generate_keypair()
    assert len(pair.public_raw) == 65
    assert pair.public_raw[0] == 0x04


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"\x04" + b"\x00" * 63,                 # wrong length
        b"\x02" + b"\x00" * 64,                 # compressed form
        b"\x04" + b"\xff" * 64,                 # not on the curve
    ],
)
def test_load_public_rejects_bad_points(bad):
    """An invalid-curve point is the classic way to walk a private
    scalar out of a peer one exchange at a time. It must be refused
    before it ever reaches an ECDH call."""
    with pytest.raises(CryptoError):
        crypto.load_public(bad)


def test_identity_is_persisted_with_restrictive_permissions(tmp_path):
    first = crypto.load_or_create_identity(tmp_path)
    second = crypto.load_or_create_identity(tmp_path)
    assert first.dh.public_raw == second.dh.public_raw
    assert first.sig.public_raw == second.sig.public_raw
    assert first.fingerprint == second.fingerprint
    for name in ("household_dh.pem", "household_sig.pem"):
        mode = stat.S_IMODE((tmp_path / name).stat().st_mode)
        # Group/other must have nothing. On a POSIX host this is 0o600.
        assert mode & 0o077 == 0, f"{name} is readable by others: {oct(mode)}"


def test_fingerprint_covers_both_keys():
    dh, sig, other = generate_keypair(), generate_keypair(), generate_keypair()
    base = crypto.fingerprint_text(dh.public_raw, sig.public_raw)
    assert base != crypto.fingerprint_text(other.public_raw, sig.public_raw)
    assert base != crypto.fingerprint_text(dh.public_raw, other.public_raw)


def test_fingerprint_is_eight_groups_of_four_hex():
    pair_a, pair_b = generate_keypair(), generate_keypair()
    groups = crypto.fingerprint_text(pair_a.public_raw, pair_b.public_raw).split(" ")
    assert len(groups) == 8
    assert all(len(g) == 4 and g == g.upper() for g in groups)
    assert all(all(c in "0123456789ABCDEF" for c in g) for g in groups)


# ─── handshake ───────────────────────────────────────────────────────────


def test_handshake_produces_a_working_bidirectional_channel(household, device):
    client_link, home_link = complete_handshake(household, device)
    assert home_link.open(client_link.seal(b"up")) == b"up"
    assert client_link.open(home_link.seal(b"down")) == b"down"


def test_every_handshake_derives_fresh_keys(household, device):
    """Forward secrecy is only real if the ephemeral terms actually
    change the output. Two handshakes with identical static keys must
    not produce interchangeable sessions."""
    client_a, _ = complete_handshake(household, device)
    _, home_b = complete_handshake(household, device)
    with pytest.raises(CryptoError):
        home_b.open(client_a.seal(b"crossed wires"))


def test_home_refuses_a_device_key_it_did_not_approve(household, device):
    """The peer's claimed key is checked against the approved key from
    our own database before any secret is derived."""
    impostor = generate_keypair()
    client = ClientHandshake(impostor, household.dh.public_raw, "d_test")
    with pytest.raises(CryptoError, match="approved"):
        HomeHandshake(household.dh).respond(client.hello(), device.public_raw)


def test_client_rejects_a_home_that_holds_the_wrong_key(household, device):
    """A client that pinned one household key must not complete a
    handshake with a server holding a different one — this is the
    substituted-key case the fingerprint exists for."""
    other_household = generate_keypair()
    client = ClientHandshake(device, household.dh.public_raw, "d_test")
    wrong_home = HomeHandshake(other_household)
    home_hello = wrong_home.respond(client.hello(), device.public_raw)
    with pytest.raises(CryptoError, match="confirmation"):
        client.finish(home_hello)


def test_home_rejects_a_forged_client_confirmation(household, device):
    client = ClientHandshake(device, household.dh.public_raw, "d_test")
    home = HomeHandshake(household.dh)
    home_hello = home.respond(client.hello(), device.public_raw)
    client.finish(home_hello)
    forged = {
        "t": "client_confirm",
        "v": crypto.PROTOCOL_VERSION,
        "confirm": crypto.b64u(b"\x00" * 32),
    }
    with pytest.raises(CryptoError, match="confirmation"):
        home.finish(forged)


def test_tampering_with_the_transcript_breaks_the_handshake(household, device):
    """The relay sits between these two messages. If it edits one, the
    two sides derive different keys and the confirmation fails — which
    is what makes 'the relay cannot MITM an established link' true
    rather than aspirational."""
    client = ClientHandshake(device, household.dh.public_raw, "d_test")
    home = HomeHandshake(household.dh)
    home_hello = home.respond(client.hello(), device.public_raw)
    home_hello["nonce_s"] = crypto.b64u(os.urandom(16))     # a relay edit
    with pytest.raises(CryptoError):
        client.finish(home_hello)


@pytest.mark.parametrize(
    "message",
    [
        {"t": "not_a_hello", "v": 1},
        {"t": "client_hello", "v": 99},
        {"t": "client_hello", "v": 1, "device_pub": "!!", "eph_pub": "", "nonce_c": ""},
    ],
)
def test_home_rejects_malformed_hellos(household, device, message):
    with pytest.raises(CryptoError):
        HomeHandshake(household.dh).respond(message, device.public_raw)


def test_finish_before_respond_is_refused(household):
    with pytest.raises(CryptoError):
        HomeHandshake(household.dh).finish({"t": "client_confirm", "confirm": ""})


# ─── the sealed layer ────────────────────────────────────────────────────


def test_replayed_frames_are_rejected(household, device):
    client_link, home_link = complete_handshake(household, device)
    frame = client_link.seal(b"once")
    assert home_link.open(frame) == b"once"
    with pytest.raises(CryptoError, match="replayed"):
        home_link.open(frame)


def test_reordered_frames_are_rejected(household, device):
    client_link, home_link = complete_handshake(household, device)
    first, second = client_link.seal(b"a"), client_link.seal(b"b")
    assert home_link.open(second) == b"b"
    with pytest.raises(CryptoError):
        home_link.open(first)


def test_a_forged_counter_does_not_burn_the_window(household, device):
    """A frame that fails authentication must not advance the receive
    counter — otherwise anyone able to inject one garbage frame could
    lock a session out of every subsequent legitimate one."""
    client_link, home_link = complete_handshake(household, device)
    forged = (2**40).to_bytes(8, "big") + b"\x00" * 32
    with pytest.raises(CryptoError):
        home_link.open(forged)
    assert home_link.open(client_link.seal(b"still fine")) == b"still fine"


def test_flipping_any_ciphertext_bit_is_detected(household, device):
    client_link, home_link = complete_handshake(household, device)
    frame = bytearray(client_link.seal(b"payload worth protecting"))
    frame[12] ^= 0x01
    with pytest.raises(CryptoError, match="authentication"):
        home_link.open(bytes(frame))


def test_frames_shorter_than_a_tag_are_rejected(household, device):
    _, home_link = complete_handshake(household, device)
    with pytest.raises(CryptoError):
        home_link.open(b"\x00" * 12)


def test_directions_use_different_keys(household, device):
    """A frame sealed for the home server must not open on the client
    that sent it — otherwise reflection would be a viable attack."""
    client_link, _ = complete_handshake(household, device)
    with pytest.raises(CryptoError):
        client_link.open(client_link.seal(b"reflected"))


def test_large_payloads_round_trip(household, device):
    client_link, home_link = complete_handshake(household, device)
    blob = os.urandom(256 * 1024)
    assert home_link.open(client_link.seal(blob)) == blob


# ─── agent authentication ────────────────────────────────────────────────


def test_challenge_signature_round_trips(household):
    signature = crypto.sign_challenge(household.sig, b"n" * 32, "h_42")
    assert crypto.verify_challenge(
        household.sig.public_raw, b"n" * 32, "h_42", signature
    )


def test_challenge_signature_is_bound_to_the_household(household):
    """Binding the household id into the signed blob is what stops a
    signature captured from one household being replayed to claim
    another household's agent slot."""
    signature = crypto.sign_challenge(household.sig, b"n" * 32, "h_42")
    assert not crypto.verify_challenge(
        household.sig.public_raw, b"n" * 32, "h_99", signature
    )
    assert not crypto.verify_challenge(
        household.sig.public_raw, b"x" * 32, "h_42", signature
    )


def test_challenge_signature_rejects_another_key(household):
    other = generate_keypair()
    signature = crypto.sign_challenge(household.sig, b"n" * 32, "h_42")
    assert not crypto.verify_challenge(
        other.public_raw, b"n" * 32, "h_42", signature
    )


def test_verify_challenge_survives_garbage(household):
    assert not crypto.verify_challenge(
        household.sig.public_raw, b"n" * 32, "h_42", "!!!not base64!!!"
    )
