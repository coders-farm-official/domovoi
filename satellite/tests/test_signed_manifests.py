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


ISSUED_AT = 1_760_000_000


def _envelope(seed, public, channel, manifest, *, serial=1, issued_at=ISSUED_AT,
              v2=True):
    """A ``manifest.sig`` envelope the way a core builds it: the original
    signature over channel + list (what satellites in the field verify),
    and — unless ``v2=False``, an older core — the second signature over
    the list with its issue time and serial."""
    doc = {
        "algorithm": "ed25519",
        "fingerprint": server_identity.fingerprint_for(public),
        "public_key": base64.b64encode(public).decode("ascii"),
        "channel": channel,
        "manifest": manifest,
        "signature": base64.b64encode(
            _ed25519.sign(seed, server_identity.manifest_message(channel, manifest))
        ).decode("ascii"),
    }
    if v2:
        doc["issued_at"] = issued_at
        doc["serial"] = serial
        doc["signature_v2"] = base64.b64encode(
            _ed25519.sign(seed, server_identity.manifest_message_v2(
                channel, manifest, issued_at, serial))
        ).decode("ascii")
    return doc


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


# ─── a genuine list from earlier is not a new one ─────────────────────────
#
# A signature proves who published a list, not when. A host on the path that
# recorded a genuine manifest.sig could serve it again later and have the
# device install an older tree, or re-run an older plugin post_install as
# root. The envelope therefore carries a per-channel serial under the
# signature, and the device remembers the last one it accepted.

def _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint, manifest,
                   bodies, **envelope_kw):
    routes = _code_routes(seed, public, manifest, bodies)
    routes["/v1/satellite-code/manifest.sig"] = _FakeResponse(
        json_body=_envelope(seed, public, server_identity.CODE_CHANNEL, manifest,
                            **envelope_kw)
    )
    monkeypatch.setattr(code_sync, "requests", _FakeRequests(routes))
    return code_sync.sync_code(
        BASE, tmp_path / "satellite", CODE_EXT_ALLOW, {},
        expected_fingerprint=fingerprint,
    )


def test_a_list_signed_the_old_way_only_is_refused_by_a_pinned_device(tmp_path, monkeypatch):
    """No serial, no way to tell a recording from a publication. The core
    serves both signatures; one that serves only the old one predates this
    code and is told so."""
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    with pytest.raises(RuntimeError) as e:
        _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint, manifest,
                       {"client.py": body}, v2=False)
    assert "upgrade the Domovoi server" in str(e.value)
    assert not (tmp_path / "satellite" / "client.py").exists()


def test_a_recording_of_an_older_list_is_refused(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    new_body, old_body = b"print('v2')\n", b"print('v1')\n"
    _sign_and_sync(
        tmp_path, monkeypatch, seed, public, fingerprint,
        {"client.py": hashlib.sha256(new_body).hexdigest()}, {"client.py": new_body},
        serial=7,
    )
    assert (tmp_path / "satellite" / "client.py").read_bytes() == new_body

    with pytest.raises(RuntimeError) as e:
        _sign_and_sync(
            tmp_path, monkeypatch, seed, public, fingerprint,
            {"client.py": hashlib.sha256(old_body).hexdigest()}, {"client.py": old_body},
            serial=6,
        )
    assert "older than the one this device already accepted" in str(e.value)
    assert "nothing was written" in str(e.value)
    assert (tmp_path / "satellite" / "client.py").read_bytes() == new_body


def test_the_same_list_served_again_is_the_ordinary_case(tmp_path, monkeypatch):
    """Every connect on which nothing changed on the server."""
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    first = _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                           manifest, {"client.py": body}, serial=7)
    second = _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                            manifest, {"client.py": body}, serial=7)
    assert (first["downloaded"], second["downloaded"]) == (1, 0)


def test_a_different_list_under_an_already_accepted_serial_is_refused(tmp_path, monkeypatch):
    """Our server never issues two lists under one serial, so this is a
    recording of something — or a server whose serial store went wrong,
    which a new publication (a greater serial) fixes."""
    seed, public, fingerprint = _server()
    a, b = b"print('a')\n", b"print('b')\n"
    _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                   {"client.py": hashlib.sha256(a).hexdigest()}, {"client.py": a},
                   serial=7)
    with pytest.raises(RuntimeError) as e:
        _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                       {"client.py": hashlib.sha256(b).hexdigest()}, {"client.py": b},
                       serial=7)
    assert "already accepted for a different list" in str(e.value)


def test_a_newer_serial_is_taken_whatever_its_contents(tmp_path, monkeypatch):
    """Rolling the core's satellite tree back to an earlier commit is a new
    publication — the server mints a new serial for it — and reaches the
    device. Age is about publication order, never about content."""
    seed, public, fingerprint = _server()
    new_body, old_body = b"print('v2')\n", b"print('v1')\n"
    _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                   {"client.py": hashlib.sha256(new_body).hexdigest()},
                   {"client.py": new_body}, serial=7)
    _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                   {"client.py": hashlib.sha256(old_body).hexdigest()},
                   {"client.py": old_body}, serial=8)
    assert (tmp_path / "satellite" / "client.py").read_bytes() == old_body


def test_the_remembered_serial_is_per_channel(payload_sidecars, tmp_path, monkeypatch):
    """The code channel being at serial 9 says nothing about the payload
    channel."""
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    _sign_and_sync(tmp_path, monkeypatch, seed, public, fingerprint,
                   {"client.py": hashlib.sha256(body).hexdigest()}, {"client.py": body},
                   serial=9)
    manifest = {"files": {}, "meta": {}}
    routes = _plugin_routes(seed, public, manifest, {})
    routes["/v1/satellite-plugins/manifest.sig"] = _FakeResponse(
        json_body=_envelope(seed, public, server_identity.PLUGIN_CHANNEL, manifest,
                            serial=2)
    )
    monkeypatch.setattr(plugin_sync, "requests", _FakeRequests(routes))
    plugin_sync.sync_plugin_payloads(
        BASE, payload_sidecars / "payloads", expected_fingerprint=fingerprint
    )
    store = json.loads(server_identity.FRESHNESS_SIDECAR.read_text(encoding="utf-8"))
    assert store[server_identity.CODE_CHANNEL]["serial"] == 9
    assert store[server_identity.PLUGIN_CHANNEL]["serial"] == 2


def test_a_tampered_serial_breaks_the_signature(tmp_path, monkeypatch):
    """The serial is under the signature, or it would be a suggestion."""
    seed, public, fingerprint = _server()
    body = b"print('hello')\n"
    manifest = {"client.py": hashlib.sha256(body).hexdigest()}
    routes = _code_routes(seed, public, manifest, {"client.py": body})
    routes["/v1/satellite-code/manifest.sig"]._json["serial"] = 10 ** 9
    monkeypatch.setattr(code_sync, "requests", _FakeRequests(routes))
    with pytest.raises(RuntimeError) as e:
        code_sync.sync_code(BASE, tmp_path / "satellite", CODE_EXT_ALLOW, {},
                            expected_fingerprint=fingerprint)
    assert "did not verify" in str(e.value)


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


# ─── root gets to check the same envelope itself ──────────────────────────
#
# The helper that runs a payload's post_install as root used to trust this
# process's verdict about where the mirror came from. It now re-verifies the
# signed envelope with the root-owned verifier against the root-owned pin,
# so the envelope has to be where it can find it: beside the mirror.

def test_the_verified_envelope_is_saved_beside_the_mirror_for_root(
    payload_sidecars, monkeypatch
):
    seed, public, fingerprint = _server()
    body = b"#!/bin/sh\ntrue\n"
    manifest = {
        "files": {"radio/setup.sh": hashlib.sha256(body).hexdigest()},
        "meta": {"radio": {"post_install": "setup.sh"}},
    }
    routes = _plugin_routes(seed, public, manifest, {"radio/setup.sh": body})
    served = routes["/v1/satellite-plugins/manifest.sig"]._json
    monkeypatch.setattr(plugin_sync, "requests", _FakeRequests(routes))

    root = payload_sidecars / "payloads"
    plugin_sync.sync_plugin_payloads(BASE, root, expected_fingerprint=fingerprint)
    saved = json.loads((root / plugin_sync.ENVELOPE_NAME).read_text(encoding="utf-8"))
    assert saved == served
    assert plugin_sync.ENVELOPE_NAME.startswith("."), \
        "a dotfile at the mirror root: no <slug>/<rel> channel path can land on it"


def test_an_envelope_that_failed_to_verify_is_not_saved(payload_sidecars, monkeypatch):
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    manifest = {"files": {}, "meta": {}}
    monkeypatch.setattr(
        plugin_sync, "requests",
        _FakeRequests(_plugin_routes(seed_b, public_b, manifest, {})),
    )
    root = payload_sidecars / "payloads"
    with pytest.raises(RuntimeError):
        plugin_sync.sync_plugin_payloads(BASE, root, expected_fingerprint=ours)
    assert not (root / plugin_sync.ENVELOPE_NAME).exists()


def test_an_unpinned_device_saves_no_envelope(payload_sidecars, monkeypatch):
    """There was none to verify; the helper on such a unit has no root pin
    to check one against either."""
    seed, public, _fingerprint = _server()
    manifest = {"files": {}, "meta": {}}
    monkeypatch.setattr(
        plugin_sync, "requests",
        _FakeRequests(_plugin_routes(seed, public, manifest, {}, signed=False)),
    )
    root = payload_sidecars / "payloads"
    plugin_sync.sync_plugin_payloads(BASE, root, expected_fingerprint=None)
    assert not (root / plugin_sync.ENVELOPE_NAME).exists()
