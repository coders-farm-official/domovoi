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
import urllib.parse

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


def _query(url: str) -> dict[str, str]:
    parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    return {k: v[0] for k, v in parsed.items() if v}


def _health_opener(seed, public, *, fingerprint=None, sign_challenge=True,
                   identity=True, bot_name="Domovoi", bind="echo", asked=None):
    """An opener that answers /v1/health the way a core does, signing
    whatever challenge the caller actually sent.

    ``bind`` is what the core does with the address the caller says it
    dialed: ``"echo"`` signs for it (a current core asked for one of its
    own addresses); ``None`` answers like a core from before the binding
    existed (no address, unbound signature); ``"refuse"`` answers like a
    core asked to sign for an address that is not its own; any other
    string is signed as the address — what a relay hands back after the
    real core signed for the address the RELAY dialed it on.

    ``asked`` (a list) records every query the opener saw."""
    def opener(url, timeout=None):
        query = _query(url)
        if asked is not None:
            asked.append(query)
        challenge = query.get("challenge", "")
        dialed = query.get("addr")
        doc = {"status": "ok", "bot_name": bot_name, "use_stubs": "false"}
        if identity:
            block = {
                "algorithm": "ed25519",
                "fingerprint": fingerprint or server_identity.fingerprint_for(public),
                "public_key": base64.b64encode(public).decode("ascii"),
                "challenge": challenge,
            }
            if dialed is not None and bind == "refuse":
                block["addr_refused"] = dialed
            elif sign_challenge:
                signed_addr = None
                if dialed is not None and bind == "echo":
                    signed_addr = dialed
                elif dialed is not None and bind is not None:
                    signed_addr = bind
                if signed_addr is not None:
                    block["addr"] = signed_addr
                block["signature"] = base64.b64encode(
                    _ed25519.sign(
                        seed, server_identity.health_message(challenge, signed_addr)
                    )
                ).decode("ascii")
            doc["identity"] = block
        return _Resp(json.dumps(doc).encode("utf-8"))
    return opener


@pytest.fixture(autouse=True)
def _isolate_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server_identity, "RECORD_SIDECAR", tmp_path / "server-fingerprint.json"
    )
    monkeypatch.setattr(
        server_identity, "LEGACY_RECORD_SIDECAR",
        tmp_path / "server-identity.json",
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
    """Told apart from the other refusals, because "this host has none" is
    permanent in a way that "unreachable" is not, and an unpinned device
    uses that to stop asking."""
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityUnavailable):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, identity=False),
        )


def test_an_unreachable_host_is_not_reported_as_having_no_identity():
    def boom(url, timeout=None):
        raise OSError("connection refused")
    with pytest.raises(server_identity.IdentityError) as e:
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint="SHA256:ours",
            opener=boom,
        )
    assert not isinstance(e.value, server_identity.IdentityUnavailable)


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


# ─── the proof is bound to the address we dialed ──────────────────────────
#
# A host on the LAN that forwards our /v1/health question to the real core
# and hands back its answer used to pass every check: nothing in the
# signed message said WHICH host had been asked. Now the question names the
# address we dialed, the core signs for it only if it is one of its own,
# and the answer has to name that same address back.

def test_the_question_names_the_address_dialed_and_the_answer_must_match_it():
    seed, public, fingerprint = _server()
    asked: list[dict[str, str]] = []
    assert server_identity.verify_server(
        "http://192.168.0.117:6370", expected_fingerprint=fingerprint,
        opener=_health_opener(seed, public, asked=asked),
    ) == fingerprint
    assert asked[0]["addr"] == "192.168.0.117:6370"


def test_an_answer_the_core_signed_for_a_different_address_is_refused():
    """What a relay hands back: the real core signed, for the address the
    RELAY dialed it on. Not the one we dialed, so not proof of this host."""
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityError) as e:
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, bind="192.168.0.117:6370"),
        )
    assert "not the one dialed" in str(e.value)


def test_an_answer_bound_to_no_address_is_refused_when_pinned():
    """An older core, or a relay that stripped our question. Either way
    the pinned device cannot tell this host from one in between."""
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityError) as e:
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, bind=None),
        )
    assert "not bound to the address dialed" in str(e.value)


def test_a_core_that_would_not_sign_for_the_address_is_refused_with_the_reason():
    """The core's own refusal, forwarded: it was asked to sign for an
    address that is not its own — the relay's."""
    seed, public, fingerprint = _server()
    with pytest.raises(server_identity.IdentityError) as e:
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=fingerprint,
            opener=_health_opener(seed, public, bind="refuse"),
        )
    assert "would not sign" in str(e.value)
    assert "not the one dialed" in str(e.value)


def test_a_bound_answer_still_has_to_be_signed_with_the_pinned_key():
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _theirs = _server(2)
    with pytest.raises(server_identity.IdentityError) as e:
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=ours,
            opener=_health_opener(seed_b, public_b),
        )
    assert "a different server" in str(e.value)


def test_an_unpinned_device_still_takes_an_older_cores_unbound_answer():
    """Nothing to compare against: a device with no pin cannot tell a relay
    from a core by any means, and refusing would only strand it against a
    core from before the binding. It records what it meets, as before."""
    seed, public, fingerprint = _server()
    assert server_identity.verify_server(
        "http://192.168.0.117:6370", expected_fingerprint=None,
        opener=_health_opener(seed, public, bind=None),
    ) == fingerprint


def test_an_unpinned_device_refuses_an_answer_bound_to_somebody_elses_address():
    """When the answer DOES name an address, it has to be ours — pinned or
    not. A current core never signs for an address it was not asked about."""
    seed, public, _fingerprint = _server()
    with pytest.raises(server_identity.IdentityError):
        server_identity.verify_server(
            "http://192.168.0.9:6370", expected_fingerprint=None,
            opener=_health_opener(seed, public, bind="192.168.0.117:6370"),
        )


@pytest.mark.parametrize("url,addr", [
    ("http://192.168.0.117:6370", "192.168.0.117:6370"),
    ("http://core.lan:6370/", "core.lan:6370"),
    ("http://core.lan", "core.lan:80"),
    ("https://core.lan", "core.lan:443"),
    ("http://[fd00::1]:6370", "[fd00::1]:6370"),
    ("http://Host.Docker.Internal:6394", "host.docker.internal:6394"),
])
def test_the_dialed_address_is_spelled_one_way(url, addr):
    """The core signs the string as sent and we compare the string as
    sent, so the spelling only has to be consistent on this side — but it
    must be unambiguous, hence the port always present."""
    assert server_identity.dialed_address(url) == addr


# ─── what we compare against, and where it comes from ─────────────────────
#
# config.toml is the satellite account's file. If a fingerprint written
# there could override the root-owned pin, a shell as that account could
# point every trust decision — connect, code, root post_install — at a core
# of its own by editing one line. So the root-owned copy wins, and a config
# value that disagrees is said out loud.

def test_the_image_pin_wins_over_config_and_the_recorded_one(caplog):
    server_identity.ROOT_PIN.write_text(
        json.dumps({"fingerprint": "SHA256:image"}), encoding="utf-8"
    )
    server_identity.record_fingerprint("SHA256:recorded")
    with caplog.at_level("ERROR", logger="satellite.server_identity"):
        assert server_identity.pinned_fingerprint("SHA256:config") == (
            "SHA256:image", "image",
        )
    assert any(
        "SHA256:config" in r.getMessage() and "root-owned" in r.getMessage()
        for r in caplog.records
    ), "a config value that disagrees with the root pin is an ERROR, not a choice"


def test_a_config_that_agrees_with_the_image_pin_is_not_an_error(caplog):
    server_identity.ROOT_PIN.write_text(
        json.dumps({"fingerprint": "SHA256:image"}), encoding="utf-8"
    )
    with caplog.at_level("ERROR", logger="satellite.server_identity"):
        assert server_identity.pinned_fingerprint("SHA256:image") == (
            "SHA256:image", "image",
        )
    assert not caplog.records


def test_config_is_consulted_only_when_there_is_no_image_pin():
    """A hand-built unit has no root pin; its config.toml is then the pin,
    ahead of anything recorded on first contact."""
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


# ─── the record does not share a file with the core's private key ─────────

CORE_KEY_DOCUMENT = {
    "schema": 1,
    "algorithm": "ed25519",
    # The shape domovoi/server_identity.py writes: a 32-byte seed, and the
    # fingerprint of the key it belongs to.
    "private_key": base64.b64encode(bytes(32)).decode("ascii"),
    "public_key": base64.b64encode(bytes(32)).decode("ascii"),
    "fingerprint": "SHA256:thecoresown",
}


def test_the_record_and_the_cores_private_key_are_not_the_same_file():
    """The whole defect in one assertion. Both live in ``~/.domovoi`` on any
    box that runs a core and a satellite; they must not be one path."""
    from domovoi import server_identity as core_identity

    assert server_identity.RECORD_SIDECAR.name != "server-identity.json"
    assert (
        server_identity.RECORD_SIDECAR.name
        != core_identity.identity_path().name
    )


def test_a_document_with_a_private_key_is_never_used_as_the_record(caplog):
    """A core's identity file has a ``fingerprint`` field too. Reading it as
    a pin would hold this device to whatever core shares its disk."""
    server_identity.RECORD_SIDECAR.write_text(
        json.dumps(CORE_KEY_DOCUMENT), encoding="utf-8"
    )
    with caplog.at_level("ERROR", logger="satellite.server_identity"):
        assert server_identity.pinned_fingerprint() == (None, "none")
    assert any("private key" in r.getMessage() for r in caplog.records), \
        "refused loudly, not silently"


def test_a_core_key_at_the_old_path_is_not_migrated_into_the_record(caplog):
    server_identity.LEGACY_RECORD_SIDECAR.write_text(
        json.dumps(CORE_KEY_DOCUMENT), encoding="utf-8"
    )
    with caplog.at_level("ERROR", logger="satellite.server_identity"):
        assert server_identity.pinned_fingerprint() == (None, "none")
    assert not server_identity.RECORD_SIDECAR.exists(), "nothing was written"
    assert any("private key" in r.getMessage() for r in caplog.records)


def test_a_record_written_before_the_rename_migrates_across_once():
    """A Pi in the field recorded its pin at the old path. It must still be
    pinned after the upgrade — being silently unpinned is the regression."""
    server_identity.LEGACY_RECORD_SIDECAR.write_text(
        json.dumps({"algorithm": "ed25519", "fingerprint": "SHA256:inthefield",
                    "public_key": "AAAA"}),
        encoding="utf-8",
    )
    assert server_identity.pinned_fingerprint() == ("SHA256:inthefield", "recorded")

    migrated = json.loads(
        server_identity.RECORD_SIDECAR.read_text(encoding="utf-8")
    )
    assert migrated["fingerprint"] == "SHA256:inthefield"
    assert migrated["public_key"] == "AAAA"
    assert "private_key" not in migrated, "public fields only"
    assert server_identity.LEGACY_RECORD_SIDECAR.exists(), "the old file is left alone"

    # Read again with the old file gone: the new one is now the source.
    server_identity.LEGACY_RECORD_SIDECAR.unlink()
    assert server_identity.pinned_fingerprint() == ("SHA256:inthefield", "recorded")


def test_the_new_record_wins_over_a_stale_one_at_the_old_path():
    server_identity.record_fingerprint("SHA256:current")
    server_identity.LEGACY_RECORD_SIDECAR.write_text(
        json.dumps({"fingerprint": "SHA256:stale"}), encoding="utf-8"
    )
    assert server_identity.pinned_fingerprint() == ("SHA256:current", "recorded")


def test_a_file_this_module_did_not_write_is_never_overwritten():
    server_identity.RECORD_SIDECAR.write_text("not json at all", encoding="utf-8")
    assert server_identity.record_fingerprint("SHA256:new") is False
    assert server_identity.RECORD_SIDECAR.read_text(encoding="utf-8") == \
        "not json at all"


def test_recording_never_writes_to_the_cores_file():
    assert server_identity.record_fingerprint("SHA256:first") is True
    assert server_identity.RECORD_SIDECAR.exists()
    assert not server_identity.LEGACY_RECORD_SIDECAR.exists()


def test_a_migrated_record_is_still_only_written_once():
    server_identity.LEGACY_RECORD_SIDECAR.write_text(
        json.dumps({"fingerprint": "SHA256:inthefield"}), encoding="utf-8"
    )
    assert server_identity.record_fingerprint("SHA256:somethingelse") is False
    assert server_identity.pinned_fingerprint() == ("SHA256:inthefield", "recorded")


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
