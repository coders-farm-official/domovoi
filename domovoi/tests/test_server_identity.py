"""The server's identity: what it is, what it signs, and what a satellite
makes of it.

DB-free by construction — nothing here needs Postgres, so none of it can
hide behind ``requires_db`` and look green while skipping.

The round trips deliberately verify with :mod:`satellite.server_identity`
rather than with the core's own verifier: the two modules are separate
trees that must agree byte for byte on what was signed, and a test that
only ever talks to itself would not notice them drifting apart.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from domovoi import _ed25519, server_identity
from satellite import server_identity as sat_identity

# RFC 8032 §7.1 test vectors. These are what pin the vendored
# implementation to the standard — without them "it round-trips" would
# only prove it agrees with itself.
_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
        "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


@pytest.mark.parametrize("seed_hex,public_hex,message_hex,signature_hex", _VECTORS)
def test_the_vendored_signer_matches_rfc_8032(
    seed_hex, public_hex, message_hex, signature_hex
):
    seed = bytes.fromhex(seed_hex)
    message = bytes.fromhex(message_hex)
    assert _ed25519.public_key(seed).hex() == public_hex
    assert _ed25519.sign(seed, message).hex() == signature_hex
    assert _ed25519.verify(bytes.fromhex(public_hex), message,
                           bytes.fromhex(signature_hex))


def test_a_signature_is_refused_for_a_different_message():
    seed = bytes.fromhex(_VECTORS[0][0])
    public = _ed25519.public_key(seed)
    signature = _ed25519.sign(seed, b"the real message")
    assert _ed25519.verify(public, b"the real message", signature)
    assert not _ed25519.verify(public, b"a different message", signature)


def test_a_malformed_key_or_signature_is_an_answer_not_an_exception():
    assert _ed25519.verify(b"", b"m", b"") is False
    assert _ed25519.verify(b"\x00" * 32, b"m", b"\xff" * 64) is False
    assert server_identity.verify(b"short", b"m", b"\x00" * 64) is False


def _identity(tmp_path):
    server_identity.reset_cache()
    return server_identity.load_or_create(tmp_path / "server-identity.json")


def test_an_install_generates_one_identity_and_keeps_it(tmp_path):
    first = _identity(tmp_path)
    server_identity.reset_cache()
    second = server_identity.load_or_create(tmp_path / "server-identity.json")
    assert second.fingerprint == first.fingerprint
    assert second.seed == first.seed


def test_the_private_key_is_written_unreadable_to_anyone_else(tmp_path):
    path = tmp_path / "server-identity.json"
    _identity(tmp_path)
    if os.name != "nt":      # Windows has no POSIX mode bits to check
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["fingerprint"].startswith("SHA256:")


def test_what_the_server_publishes_carries_no_private_key(tmp_path):
    identity = _identity(tmp_path)
    published = identity.public_document()
    assert set(published) == {"algorithm", "fingerprint", "public_key"}
    assert server_identity.b64(identity.seed) not in json.dumps(published)


def test_the_fingerprint_is_the_hash_of_the_published_key(tmp_path):
    identity = _identity(tmp_path)
    published = identity.public_document()
    assert sat_identity.fingerprint_for(
        sat_identity.unb64(published["public_key"])
    ) == identity.fingerprint


# ─── the health challenge ─────────────────────────────────────────────────

def test_a_health_answer_proves_the_server_signed_our_nonce(tmp_path):
    identity = _identity(tmp_path)
    challenge = server_identity.new_challenge()
    doc = {"status": "ok", "bot_name": "domovoi",
           "identity": identity.health_answer(challenge)}
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity.fingerprint
    ) == identity.fingerprint


def test_a_recorded_answer_does_not_prove_anything_about_a_new_nonce(tmp_path):
    identity = _identity(tmp_path)
    doc = {"identity": identity.health_answer("the-nonce-from-last-time")}
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_health_document(
            doc, challenge=server_identity.new_challenge(),
            expected_fingerprint=identity.fingerprint,
        )


def test_another_server_answering_correctly_is_still_another_server(tmp_path):
    ours = _identity(tmp_path)
    server_identity.reset_cache()
    theirs = server_identity.load_or_create(tmp_path / "other.json")
    challenge = server_identity.new_challenge()
    doc = {"identity": theirs.health_answer(challenge)}
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=ours.fingerprint
        )


# ─── signed manifests ─────────────────────────────────────────────────────

def test_a_signed_manifest_round_trips(tmp_path):
    identity = _identity(tmp_path)
    manifest = {"client.py": "a" * 64, "devices.py": "b" * 64}
    envelope = identity.signed_manifest(server_identity.CODE_CHANNEL, manifest)
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.CODE_CHANNEL,
        expected_fingerprint=identity.fingerprint,
    ) == manifest


def test_editing_the_file_list_breaks_its_signature(tmp_path):
    identity = _identity(tmp_path)
    envelope = identity.signed_manifest(
        server_identity.CODE_CHANNEL, {"client.py": "a" * 64}
    )
    envelope["manifest"]["client.py"] = "c" * 64
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_manifest_envelope(
            envelope, channel=sat_identity.CODE_CHANNEL,
            expected_fingerprint=identity.fingerprint,
        )


def test_a_code_signature_is_not_accepted_for_the_payload_channel(tmp_path):
    identity = _identity(tmp_path)
    envelope = identity.signed_manifest(
        server_identity.CODE_CHANNEL, {"client.py": "a" * 64}
    )
    envelope["channel"] = sat_identity.PLUGIN_CHANNEL
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_manifest_envelope(
            envelope, channel=sat_identity.PLUGIN_CHANNEL,
            expected_fingerprint=identity.fingerprint,
        )


def test_a_health_signature_is_not_accepted_as_a_manifest_signature(tmp_path):
    identity = _identity(tmp_path)
    challenge = "abc"
    health = identity.health_answer(challenge)
    envelope = {
        "algorithm": server_identity.ALGORITHM,
        "channel": sat_identity.CODE_CHANNEL,
        "public_key": health["public_key"],
        "manifest": challenge,
        "signature": health["signature"],
    }
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_manifest_envelope(
            envelope, channel=sat_identity.CODE_CHANNEL,
            expected_fingerprint=identity.fingerprint,
        )


def test_both_trees_canonicalize_a_document_the_same_way():
    doc = {"b": 1, "a": {"z": [1, 2], "y": "ü"}}
    assert server_identity.canonical_json(doc) == sat_identity.canonical_json(doc)
    assert server_identity.health_message("n") == sat_identity.health_message("n")
    assert (
        server_identity.manifest_message("c", doc)
        == sat_identity.manifest_message("c", doc)
    )
