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


@pytest.mark.parametrize("prefix", ["/v1/satellite-code", "/v1/satellite-plugins"])
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
