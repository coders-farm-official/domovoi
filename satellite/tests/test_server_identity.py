"""Which server is ours, and what happens to one that cannot prove it is.

Everything here is injected: no network, no filesystem outside tmp_path.
Nothing needs Postgres, so nothing can skip quietly.

The keys are made with the satellite tree's own vendored signer, so these
tests run in an environment that has no ``cryptography`` at all — which is
exactly the environment a 32-bit Pi and this repo's venv are.
"""

from __future__ import annotations

import base64
import json

import pytest

from satellite import _ed25519, server_identity


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _server(seed_byte: int = 1):
    """A fake core: its seed, public key and fingerprint."""
    seed = bytes([seed_byte]) * 32
    public = _ed25519.public_key(seed)
    return seed, public, server_identity.fingerprint_for(public)


def _health_opener(seed, public, *, fingerprint=None, sign_challenge=True,
                   identity=True, bot_name="Domovoi"):
    """An opener that answers /v1/health the way a core does, signing
    whatever challenge the caller actually sent."""
    def opener(url, timeout=None):
        challenge = url.split("challenge=", 1)[1] if "challenge=" in url else ""
        doc = {"status": "ok", "bot_name": bot_name, "use_stubs": "false"}
        if identity:
            block = {
                "algorithm": "ed25519",
                "fingerprint": fingerprint or server_identity.fingerprint_for(public),
                "public_key": base64.b64encode(public).decode("ascii"),
                "challenge": challenge,
            }
            if sign_challenge:
                block["signature"] = base64.b64encode(
                    _ed25519.sign(seed, server_identity.health_message(challenge))
                ).decode("ascii")
            doc["identity"] = block
        return _Resp(json.dumps(doc).encode("utf-8"))
    return opener


@pytest.fixture(autouse=True)
def _isolate_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server_identity, "RECORD_SIDECAR", tmp_path / "server-identity.json"
    )
    monkeypatch.setattr(server_identity, "ROOT_PIN", tmp_path / "etc-pin.json")
    monkeypatch.setattr(
        server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending-server.json"
    )


# ─── proving a server is ours ─────────────────────────────────────────────

def test_a_server_that_signs_our_nonce_is_accepted():
    seed, public, fingerprint = _server()
    assert server_identity.verify_server(
        "http://192.168.0.117:6370", expected_fingerprint=fingerprint,
        opener=_health_opener(seed, public),
    ) == fingerprint


def test_a_server_holding_a_different_key_is_refused():
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _theirs = _server(2)
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=ours,
            opener=_health_opener(seed_b, public_b),
        )


def test_a_server_that_claims_our_fingerprint_without_the_key_is_refused():
    """Answering with the right string is not the same as holding the key:
    the fingerprint has to be the hash of the key that signed."""
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=ours,
            opener=_health_opener(seed_b, public_b, fingerprint=ours),
        )


def test_a_server_that_offers_no_signature_is_refused():
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, sign_challenge=False),
        )


def test_a_server_that_offers_no_identity_at_all_is_refused_when_pinned():
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, identity=False),
        )


def test_an_unreachable_host_is_reported_as_unprovable_not_as_a_crash():
    def boom(url, timeout=None):
        raise OSError("connection refused")
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=None, opener=boom
        )


def test_with_nothing_pinned_the_identity_that_answers_is_reported():
    """A device prepared before fingerprints existed still gets a usable
    answer — that is what it records and holds the server to afterwards."""
    seed, public, fingerprint = _server()
    assert server_identity.verify_server(
        "http://192.168.0.117:6370", expected_fingerprint=None,
        opener=_health_opener(seed, public),
    ) == fingerprint


# ─── what we compare against, and where it comes from ─────────────────────

def test_config_wins_over_the_image_pin_and_the_recorded_one(tmp_path):
    server_identity.ROOT_PIN.write_text(
        json.dumps({"fingerprint": "SHA256:image"}), encoding="utf-8"
    )
    server_identity.record_fingerprint("SHA256:recorded")
    assert server_identity.pinned_fingerprint("SHA256:config") == (
        "SHA256:config", "config",
    )


def test_the_image_pin_wins_over_a_recorded_one():
    server_identity.ROOT_PIN.write_text(
        json.dumps({"fingerprint": "SHA256:image"}), encoding="utf-8"
    )
    server_identity.record_fingerprint("SHA256:recorded")
    assert server_identity.pinned_fingerprint() == ("SHA256:image", "image")


def test_a_device_with_nothing_pinned_says_so():
    assert server_identity.pinned_fingerprint() == (None, "none")
    assert server_identity.pinned_fingerprint("   ") == (None, "none")
    assert server_identity.pinned_fingerprint("not-a-fingerprint") == (None, "none")


def test_the_first_identity_met_is_recorded_and_never_replaced():
    assert server_identity.record_fingerprint("SHA256:first") is True
    assert server_identity.pinned_fingerprint() == ("SHA256:first", "recorded")
    # A second, different server does not get to overwrite the record —
    # trust on first use is only trust if it is on FIRST use.
    assert server_identity.record_fingerprint("SHA256:second") is False
    assert server_identity.pinned_fingerprint() == ("SHA256:first", "recorded")


def test_recording_the_same_identity_again_is_fine():
    server_identity.record_fingerprint("SHA256:first")
    assert server_identity.record_fingerprint("SHA256:first") is True


# ─── an address nobody has approved yet ───────────────────────────────────

def test_a_discovered_address_is_kept_out_of_the_way_until_approved():
    assert server_identity.write_pending_server(
        "ws://192.168.0.117:6370", "SHA256:abc"
    ) is True
    assert server_identity.read_pending_server() == (
        "ws://192.168.0.117:6370", "SHA256:abc",
    )
    server_identity.clear_pending_server()
    assert server_identity.read_pending_server() == (None, None)


def test_no_pending_address_reads_back_as_nothing():
    assert server_identity.read_pending_server() == (None, None)
