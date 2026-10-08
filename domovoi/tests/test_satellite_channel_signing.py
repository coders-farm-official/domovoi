"""The core serves both shapes of every satellite file list.

Signed, for a device that was prepared with this server's fingerprint and
will check it. Unsigned, for the two Pis in the house that were flashed
before any of this existed and must keep upgrading exactly as they do
today. Losing either one breaks somebody's satellite, so both are asserted
here rather than left to the integration run.

DB-free: the endpoint functions are called directly and the one DB touch
(``/v1/health``'s ``SELECT 1``) is replaced.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from domovoi import admin_auth, main, server_identity
from satellite import server_identity as sat_identity


@pytest.fixture(autouse=True)
def identity_in_tmp(tmp_path, monkeypatch):
    """Keep the key out of the developer's own ~/.domovoi."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    server_identity.reset_cache()
    yield server_identity.load_or_create()
    server_identity.reset_cache()


def _route_paths(methods: str = "GET") -> list[str]:
    return [
        r.path for r in main.app.routes
        if getattr(r, "methods", None) and methods in r.methods
    ]


def test_both_code_manifests_are_routed():
    paths = _route_paths()
    assert "/v1/satellite-code/manifest" in paths
    assert "/v1/satellite-code/manifest.sig" in paths


def test_both_payload_manifests_are_routed():
    paths = _route_paths()
    assert "/v1/satellite-plugins/manifest" in paths
    assert "/v1/satellite-plugins/manifest.sig" in paths


def test_the_sounds_and_wake_model_channels_are_signed_too():
    """What the room says and what it listens for come from its server,
    not from whatever is on the path."""
    paths = _route_paths()
    assert "/v1/sounds/manifest.sig" in paths
    assert "/v1/wake-models/manifest.sig" in paths


@pytest.mark.parametrize("prefix", [
    "/v1/satellite-code", "/v1/satellite-plugins", "/v1/sounds", "/v1/wake-models",
])
def test_the_signature_route_is_declared_before_the_catch_all(prefix):
    """FastAPI matches in declaration order: behind the ``{path:path}``
    route this would be a 404 for a file that does not exist."""
    paths = _route_paths()
    assert paths.index(f"{prefix}/manifest.sig") < paths.index(
        prefix + "/{path:path}"
    )


@pytest.mark.asyncio
async def test_the_signed_code_manifest_carries_the_same_list_as_the_plain_one(
    identity_in_tmp,
):
    plain = await main.satellite_code_manifest()
    envelope = await main.satellite_code_manifest_signed()
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.CODE_CHANNEL,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == plain


@pytest.mark.asyncio
async def test_the_signed_payload_manifest_carries_the_same_list_as_the_plain_one(
    identity_in_tmp, monkeypatch,
):
    manifest = {"files": {"radio/setup.sh": "a" * 64}, "meta": {"radio": {}}}

    async def fake_channel_manifest():
        return manifest

    monkeypatch.setattr(
        "domovoi.satellite_payload.build_channel_manifest", fake_channel_manifest
    )
    plain = await main.satellite_plugins_manifest()
    envelope = await main.satellite_plugins_manifest_signed()
    assert plain == manifest
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.PLUGIN_CHANNEL,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == manifest


@pytest.mark.asyncio
async def test_the_signed_sounds_manifest_carries_the_same_list_as_the_plain_one(
    identity_in_tmp, monkeypatch, tmp_path,
):
    voice_root = tmp_path / "voices" / "ryan"
    (voice_root / "greetings").mkdir(parents=True)
    (voice_root / "network_issues.mp3").write_bytes(b"net")
    (voice_root / "greetings" / "a.mp3").write_bytes(b"aaa")

    async def fake_root(voice):
        return voice_root

    monkeypatch.setattr(main, "_resolve_voice_root", fake_root)
    plain = await main.sounds_manifest("Ryan")
    envelope = await main.sounds_manifest_signed("Ryan")
    assert set(plain) == {"network_issues.mp3", "greetings/a.mp3"}
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.SOUNDS_CHANNEL,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == plain


@pytest.mark.asyncio
async def test_the_signed_wake_model_manifest_carries_the_same_list_as_the_plain_one(
    identity_in_tmp, monkeypatch, tmp_path,
):
    models = tmp_path / "wake_models"
    models.mkdir()
    (models / "hey_house.onnx").write_bytes(b"\x08onnx")
    (models / "notes.txt").write_bytes(b"not a model")
    monkeypatch.setattr(main.settings, "wake_models_dir", str(models))
    plain = await main.wake_models_manifest()
    envelope = await main.wake_models_manifest_signed()
    assert set(plain) == {"hey_house.onnx"}
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.WAKE_MODELS_CHANNEL,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == plain


@pytest.mark.asyncio
async def test_every_signed_manifest_carries_freshness(identity_in_tmp, monkeypatch, tmp_path):
    """issued_at and a serial, under signature_v2 — on all four channels."""
    async def fake_channel_manifest():
        return {"files": {}, "meta": {}}

    monkeypatch.setattr(
        "domovoi.satellite_payload.build_channel_manifest", fake_channel_manifest
    )
    monkeypatch.setattr(main.settings, "wake_models_dir", str(tmp_path / "none"))
    for envelope in (
        await main.satellite_code_manifest_signed(),
        await main.satellite_plugins_manifest_signed(),
        await main.wake_models_manifest_signed(),
    ):
        assert isinstance(envelope["issued_at"], int)
        assert isinstance(envelope["serial"], int) and envelope["serial"] > 0
        assert envelope["signature_v2"] and envelope["signature"]


# ─── /v1/health ───────────────────────────────────────────────────────────

@pytest.fixture
def healthy_core(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        class _S:
            async def execute(self, *_a, **_k):
                return None
        yield _S()

    monkeypatch.setattr(main, "session_scope", fake_scope)
    monkeypatch.setattr(main, "HANDLERS", [object()])


@pytest.mark.asyncio
async def test_health_still_answers_what_it_always_answered(healthy_core):
    doc = await main.health()
    assert doc["status"] == "ok"
    assert doc["bot_name"]
    assert doc["use_stubs"] in ("true", "false")


@pytest.mark.asyncio
async def test_health_publishes_the_identity_without_being_asked(
    healthy_core, identity_in_tmp,
):
    doc = await main.health()
    assert doc["identity"]["fingerprint"] == identity_in_tmp.fingerprint
    assert "signature" not in doc["identity"]


@pytest.mark.asyncio
async def test_health_signs_the_nonce_it_is_given(healthy_core, identity_in_tmp):
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge)
    assert sat_identity.verify_health_document(
        doc, challenge=challenge,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == identity_in_tmp.fingerprint


@pytest.mark.asyncio
async def test_a_satellite_will_not_accept_an_answer_to_a_different_nonce(
    healthy_core, identity_in_tmp,
):
    doc = await main.health(challenge="the-nonce-from-last-time")
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_health_document(
            doc, challenge=sat_identity.new_challenge(),
            expected_fingerprint=identity_in_tmp.fingerprint,
        )


@pytest.mark.asyncio
async def test_an_oversized_challenge_is_refused_rather_than_signed(healthy_core):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await main.health(challenge="x" * 5000)
    assert e.value.status_code == 400


# ─── the proof names the address the satellite dialed ─────────────────────
#
# A LAN host forwarding /v1/health to this core and handing back its answer
# used to pass every identity check a satellite makes: nothing signed said
# which host had been asked. The satellite now says which address it
# dialed; this core signs for it only when it is one of its own.

@pytest.mark.asyncio
async def test_health_signs_for_an_address_of_this_server(healthy_core, identity_in_tmp):
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="127.0.0.1:6370")
    assert doc["identity"]["addr"] == "127.0.0.1:6370"
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="127.0.0.1:6370",
    ) == identity_in_tmp.fingerprint


@pytest.mark.asyncio
async def test_health_will_not_sign_for_an_address_that_is_not_this_servers(
    healthy_core, identity_in_tmp, caplog,
):
    """The relay's address. The answer still carries the public identity
    (a person can read it) but no signature, and says why."""
    challenge = sat_identity.new_challenge()
    with caplog.at_level("WARNING"):
        doc = await main.health(challenge=challenge, addr="192.0.2.5:6370")
    block = doc["identity"]
    assert block["addr_refused"] == "192.0.2.5:6370"
    assert "signature" not in block and "addr" not in block
    assert "TRUSTED_HOSTS" in caplog.text
    with pytest.raises(sat_identity.IdentityError) as e:
        sat_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
            addr="192.0.2.5:6370",
        )
    assert "not the one dialed" in str(e.value)


@pytest.mark.asyncio
async def test_health_signs_for_a_name_the_operator_listed(
    healthy_core, identity_in_tmp, monkeypatch,
):
    """A household whose satellites dial the core by a name, or through a
    NAT, lists that address; nothing is ever resolved to decide."""
    monkeypatch.setattr(main.settings, "trusted_hosts", "host.docker.internal")
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="host.docker.internal:6394")
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="host.docker.internal:6394",
    ) == identity_in_tmp.fingerprint
    monkeypatch.setattr(main.settings, "trusted_hosts", "")
    doc = await main.health(challenge=sat_identity.new_challenge(), addr="host.docker.internal:6394")
    assert doc["identity"]["addr_refused"] == "host.docker.internal:6394"


@pytest.mark.asyncio
async def test_health_still_answers_the_unbound_form_for_older_satellites(
    healthy_core, identity_in_tmp, caplog,
):
    """The transition: a satellite on earlier code asks without an address
    and gets what it always got, and the log says one is still out there."""
    monkeypatch_seen = main._UNBOUND_CHALLENGE_SEEN
    main._UNBOUND_CHALLENGE_SEEN = False
    try:
        challenge = sat_identity.new_challenge()
        with caplog.at_level("INFO"):
            doc = await main.health(challenge=challenge)
        assert "addr" not in doc["identity"]
        assert sat_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        ) == identity_in_tmp.fingerprint
        assert "before the address binding" in caplog.text
    finally:
        main._UNBOUND_CHALLENGE_SEEN = monkeypatch_seen


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", ["abc\ndef", "abc def", "nonce\n192.168.0.2:6370"])
async def test_a_challenge_with_a_newline_or_space_is_refused_unsigned(healthy_core, challenge):
    """An unbound signature over ``C\\nA`` would be a bound one for ``C``
    and ``A``; the alphabet is what makes the two forms distinct."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await main.health(challenge=challenge)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_an_addr_that_is_not_a_host_port_is_refused(healthy_core):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await main.health(challenge="abc", addr="127.0.0.1:6370 extra")
    assert e.value.status_code == 400
