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


# ─── somebody else's document at our path ─────────────────────────────────

def test_a_satellites_recorded_fingerprint_is_not_generated_over(tmp_path):
    """The satellite's record used to share this filename. Generating a key
    over one would mint a new server identity and orphan every card ever
    prepared from this install, so it stops instead."""
    path = tmp_path / "server-identity.json"
    path.write_text(
        json.dumps({"schema": 1, "algorithm": "ed25519",
                    "kind": "satellite-server-fingerprint",
                    "fingerprint": "SHA256:somebodyelses"}),
        encoding="utf-8",
    )
    server_identity.reset_cache()
    with pytest.raises(server_identity.IdentityFileConflict) as e:
        server_identity.load_or_create(path)
    assert "server-fingerprint.json" in str(e.value), "says where it belongs"
    assert json.loads(path.read_text(encoding="utf-8"))["fingerprint"] == \
        "SHA256:somebodyelses", "left exactly as it was"


def test_the_conflict_degrades_health_instead_of_crashing_it(tmp_path, monkeypatch):
    """Callers already guard the identity with ``except OSError``; this is
    one, so /v1/health drops its identity block rather than 500ing — and a
    pinned satellite then refuses to connect, which is the safe direction."""
    from domovoi import admin_auth

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    (tmp_path / "server-identity.json").write_text(
        json.dumps({"fingerprint": "SHA256:somebodyelses"}), encoding="utf-8"
    )
    server_identity.reset_cache()
    assert isinstance(server_identity.IdentityFileConflict("x"), OSError)
    assert server_identity.public_document() == {}


def test_our_own_truncated_key_file_still_regenerates(tmp_path):
    """The control: a corrupt file of OUR shape is not somebody else's
    document, and the old behaviour is unchanged."""
    path = tmp_path / "server-identity.json"
    path.write_text("{", encoding="utf-8")
    server_identity.reset_cache()
    assert server_identity.load_or_create(path).fingerprint.startswith("SHA256:")


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
        server_identity.health_message("n", "192.168.0.117:6370")
        == sat_identity.health_message("n", "192.168.0.117:6370")
    )
    assert (
        server_identity.manifest_message("c", doc)
        == sat_identity.manifest_message("c", doc)
    )
    assert (
        server_identity.manifest_message_v2("c", doc, 1_760_000_000, 7)
        == sat_identity.manifest_message_v2("c", doc, 1_760_000_000, 7)
    )
    assert server_identity.manifest_digest(doc) == sat_identity.manifest_digest(doc)


# ─── freshness: a signed list says when, not only who ─────────────────────

def test_a_signed_manifest_carries_its_serial_and_issue_time_under_the_signature(tmp_path):
    identity = _identity(tmp_path)
    envelope = identity.signed_manifest(
        server_identity.CODE_CHANNEL, {"client.py": "a" * 64}, now=1_760_000_000
    )
    assert envelope["issued_at"] == 1_760_000_000
    assert isinstance(envelope["serial"], int) and envelope["serial"] > 0
    assert set(envelope) >= {"signature", "signature_v2", "issued_at", "serial"}
    # The satellite tree accepts it, and a tampered serial breaks it.
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.CODE_CHANNEL,
        expected_fingerprint=identity.fingerprint,
    ) == {"client.py": "a" * 64}
    envelope["serial"] += 1
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_manifest_envelope(
            envelope, channel=sat_identity.CODE_CHANNEL,
            expected_fingerprint=identity.fingerprint,
        )


def test_the_original_signature_is_still_there_for_satellites_in_the_field(tmp_path):
    """A satellite on earlier code verifies ``signature`` over the channel
    and the list — exactly as before. That is how it takes the code that
    teaches it the second signature."""
    identity = _identity(tmp_path)
    manifest = {"client.py": "a" * 64}
    envelope = identity.signed_manifest(server_identity.CODE_CHANNEL, manifest)
    assert _ed25519.verify(
        identity.public,
        server_identity.manifest_message(server_identity.CODE_CHANNEL, manifest),
        server_identity.unb64(envelope["signature"]),
    )


def test_an_unchanged_list_keeps_its_serial_and_a_changed_one_gets_a_greater_one(tmp_path):
    identity = _identity(tmp_path)
    channel = server_identity.CODE_CHANNEL
    first = identity.signed_manifest(channel, {"a.py": "1" * 64}, now=1_760_000_000)
    again = identity.signed_manifest(channel, {"a.py": "1" * 64}, now=1_760_000_900)
    assert (again["serial"], again["issued_at"]) == (first["serial"], first["issued_at"]), \
        "an unchanged list is the same publication"
    changed = identity.signed_manifest(channel, {"a.py": "2" * 64}, now=1_760_000_900)
    assert changed["serial"] > first["serial"]
    assert changed["issued_at"] == 1_760_000_900


def test_serials_are_kept_per_channel(tmp_path):
    identity = _identity(tmp_path)
    code = identity.signed_manifest(server_identity.CODE_CHANNEL, {"a.py": "1" * 64},
                                    now=1_760_000_000)
    plugins = identity.signed_manifest(server_identity.PLUGIN_CHANNEL,
                                       {"files": {}, "meta": {}}, now=1_760_000_000)
    sounds = identity.signed_manifest(server_identity.SOUNDS_CHANNEL, {}, now=1_760_000_000)
    store = json.loads(identity.serials_path().read_text(encoding="utf-8"))
    assert set(store) == {server_identity.CODE_CHANNEL, server_identity.PLUGIN_CHANNEL,
                          server_identity.SOUNDS_CHANNEL}
    assert code["channel"] != plugins["channel"] != sounds["channel"]


def test_a_new_serial_is_strictly_greater_even_within_one_second(tmp_path):
    identity = _identity(tmp_path)
    channel = server_identity.CODE_CHANNEL
    serials = [
        identity.signed_manifest(channel, {"a.py": str(i) * 64}, now=1_760_000_000)["serial"]
        for i in range(3)
    ]
    assert serials == sorted(serials) and len(set(serials)) == 3


def test_a_lost_serial_store_still_moves_forward(tmp_path):
    """The store is ``max(previous + 1, now)``: should the file be lost, the
    clock alone still yields something greater than any serial a satellite
    remembers, because every earlier one was at most its own minting time."""
    identity = _identity(tmp_path)
    channel = server_identity.CODE_CHANNEL
    before = identity.signed_manifest(channel, {"a.py": "1" * 64}, now=1_760_000_000)["serial"]
    identity.serials_path().unlink()
    after = identity.signed_manifest(channel, {"a.py": "1" * 64}, now=1_760_000_500)["serial"]
    assert after > before


def test_the_serial_store_sits_beside_the_key_not_in_the_developers_home(tmp_path):
    identity = _identity(tmp_path)
    identity.signed_manifest(server_identity.CODE_CHANNEL, {}, now=1_760_000_000)
    assert identity.serials_path().parent == tmp_path
    assert identity.serials_path().is_file()


# ─── the health proof names the address dialed ────────────────────────────

def test_a_bound_health_answer_proves_the_server_for_that_address(tmp_path):
    identity = _identity(tmp_path)
    challenge = server_identity.new_challenge()
    doc = {"status": "ok", "identity": identity.health_answer(challenge, addr="192.168.0.117:6370")}
    assert doc["identity"]["addr"] == "192.168.0.117:6370"
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity.fingerprint,
        addr="192.168.0.117:6370",
    ) == identity.fingerprint


def test_a_relayed_answer_signed_for_the_cores_address_is_not_proof_of_the_relay(tmp_path):
    identity = _identity(tmp_path)
    challenge = server_identity.new_challenge()
    doc = {"identity": identity.health_answer(challenge, addr="192.168.0.2:6370")}
    with pytest.raises(sat_identity.IdentityError) as e:
        sat_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=identity.fingerprint,
            addr="192.168.0.141:6370",
        )
    assert "not the one dialed" in str(e.value)


def test_a_bound_signature_is_not_an_unbound_one_and_vice_versa(tmp_path):
    identity = _identity(tmp_path)
    challenge = server_identity.new_challenge()
    bound = identity.health_answer(challenge, addr="192.168.0.2:6370")
    unbound = identity.health_answer(challenge)
    assert bound["signature"] != unbound["signature"]
    assert server_identity.health_message(challenge) != \
        server_identity.health_message(challenge, "192.168.0.2:6370")


@pytest.mark.parametrize("challenge", ["abc\ndef", "abc def", "", "a" * 129, "nonce\n192.168.0.2:6370"])
def test_a_challenge_that_could_smuggle_an_address_is_not_signable(challenge):
    """``<ctx>\\n<nonce>\\n<addr>`` vs ``<ctx>\\n<nonce>``: a nonce with a
    newline in it would let an UNBOUND signature over ``C\\nA`` pass as a
    BOUND one for nonce ``C`` and address ``A``. So no newline, no
    whitespace, ever."""
    assert server_identity.valid_challenge(challenge) is False


def test_an_ordinary_hex_nonce_and_a_host_port_are_fine():
    assert server_identity.valid_challenge(server_identity.new_challenge())
    assert server_identity.valid_addr("192.168.0.117:6370")
    assert server_identity.valid_addr("[fd00::1]:6370")
    assert server_identity.valid_addr("host.docker.internal:6394")
    assert not server_identity.valid_addr("a b:6370")
    assert not server_identity.valid_addr("a\nb:6370")


def test_which_addresses_this_server_will_sign_for(monkeypatch):
    """Loopback and our own interfaces, yes; a name, only when the
    operator listed it; never anything resolved."""
    from domovoi.config import settings

    ours = server_identity.dialed_address_is_ours
    own = lambda ip: str(ip) == "192.168.0.117"  # noqa: E731 — the box's one interface
    monkeypatch.setattr(settings, "trusted_hosts", "")
    assert ours("127.0.0.1:6370", can_bind=own) is True
    assert ours("[::1]:6370", can_bind=own) is True
    assert ours("localhost:6370", can_bind=own) is True
    assert ours("192.168.0.117:6370", can_bind=own) is True
    assert ours("192.168.0.141:6370", can_bind=own) is False       # the relay
    assert ours("host.docker.internal:6394", can_bind=own) is False  # a name, unlisted
    assert ours("domovoi-st-rogue:6370", can_bind=own) is False
    assert ours("0.0.0.0:6370", can_bind=own) is False
    assert ours("", can_bind=own) is False

    monkeypatch.setattr(
        settings, "trusted_hosts", "host.docker.internal, *.ts.net, 203.0.113.9:6370"
    )
    assert ours("host.docker.internal:6394", can_bind=own) is True
    assert ours("HOST.DOCKER.INTERNAL:6394", can_bind=own) is True
    assert ours("beelink.ts.net:6370", can_bind=own) is True
    assert ours("203.0.113.9:6370", can_bind=own) is True
    assert ours("203.0.113.9:6371", can_bind=own) is False, "an entry with a port means that port"


def test_the_real_bind_check_knows_loopback_from_test_net():
    """The kernel's answer, not a resolver's: binding a datagram socket to
    an address only works for one this host owns."""
    assert server_identity.dialed_address_is_ours("127.0.0.1:6370") is True
    assert server_identity.dialed_address_is_ours("192.0.2.5:6370") is False


# ─── rotation ─────────────────────────────────────────────────────────────

def test_rotation_without_confirmation_changes_nothing(tmp_path):
    path = tmp_path / "server-identity.json"
    before = _identity(tmp_path).fingerprint
    result = server_identity.rotate_identity(path)
    assert result["rotated"] is False
    assert result["old_fingerprint"] == before
    assert any("re-prepared" in line for line in result["consequences"])
    server_identity.reset_cache()
    assert server_identity.load_or_create(path).fingerprint == before
    assert list(tmp_path.glob("server-identity.json.retired-*")) == []


def test_rotation_retires_the_old_key_and_mints_a_new_one(tmp_path):
    path = tmp_path / "server-identity.json"
    before = _identity(tmp_path).fingerprint
    result = server_identity.rotate_identity(path, confirm=True, now=1_760_000_000)
    assert result["rotated"] is True
    assert result["old_fingerprint"] == before
    assert result["new_fingerprint"] != before
    retired = tmp_path / result["retired_path"].rsplit(os.sep, 1)[-1].rsplit("/", 1)[-1]
    assert retired.name.startswith("server-identity.json.retired-")
    assert json.loads(retired.read_text(encoding="utf-8"))["fingerprint"] == before, \
        "the old key is kept, for a successor statement later"
    if os.name != "nt":
        assert stat.S_IMODE(retired.stat().st_mode) == 0o600
    server_identity.reset_cache()
    assert server_identity.load_or_create(path).fingerprint == result["new_fingerprint"]


def test_the_cli_refuses_to_rotate_without_the_confirmation_flag(tmp_path, capsys):
    path = tmp_path / "server-identity.json"
    before = _identity(tmp_path).fingerprint
    assert server_identity.main(["--rotate-identity", "--path", str(path)]) == 2
    out = capsys.readouterr().out
    assert before in out and "Nothing was changed" in out
    assert server_identity.main(
        ["--rotate-identity", "--confirm-reprovision", "--path", str(path)]
    ) == 0
    out = capsys.readouterr().out
    assert "re-prepare and re-flash every satellite card" in out
    server_identity.reset_cache()
    assert server_identity.load_or_create(path).fingerprint != before
