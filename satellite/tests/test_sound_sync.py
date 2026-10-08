"""Tests for the satellite sound-sync logic (HTTP mocked)."""

from __future__ import annotations

import hashlib

import pytest

from satellite import sound_sync


def test_http_base_from_ws():
    assert sound_sync.http_base_from_ws("ws://domovoi.local:6370") == "http://domovoi.local:6370"
    assert sound_sync.http_base_from_ws("wss://h:8443/v1/stream/x") == "https://h:8443"
    assert sound_sync.http_base_from_ws("ws://1.2.3.4:6370/") == "http://1.2.3.4:6370"


def test_safe_rel():
    assert sound_sync._safe_rel("greetings/greet_a.mp3")
    assert sound_sync._safe_rel("network_issues.mp3")
    assert not sound_sync._safe_rel("../escape.mp3")
    assert not sound_sync._safe_rel("/abs.mp3")
    assert not sound_sync._safe_rel("")


class _FakeResp:
    status_code = 200

    def __init__(self, *, json_data=None, content=b""):
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_sync_downloads_then_noops_and_prunes(tmp_path, monkeypatch):
    server = tmp_path / "server"
    (server / "greetings").mkdir(parents=True)
    (server / "network_issues.mp3").write_bytes(b"net")
    (server / "greetings" / "greet_a.mp3").write_bytes(b"aaa")
    (server / "greetings" / "greet_b.mp3").write_bytes(b"bbb")

    def manifest():
        return {
            p.relative_to(server).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in server.rglob("*.mp3")
        }

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/v1/sounds/manifest"):
            return _FakeResp(json_data=manifest())
        rel = url.split("/v1/sounds/", 1)[1]
        return _FakeResp(content=(server / rel).read_bytes())

    monkeypatch.setattr(sound_sync.requests, "get", fake_get)

    cache = tmp_path / "cache"
    # A stale clip not in the manifest should be pruned.
    (cache / "greetings").mkdir(parents=True)
    (cache / "greetings" / "greet_old.mp3").write_bytes(b"old")

    n = sound_sync.sync("http://server:6370", cache)
    assert n == 3
    assert (cache / "network_issues.mp3").read_bytes() == b"net"
    assert (cache / "greetings" / "greet_a.mp3").read_bytes() == b"aaa"
    assert not (cache / "greetings" / "greet_old.mp3").exists()  # pruned

    # Nothing changed on a second run → no downloads.
    assert sound_sync.sync("http://server:6370", cache) == 0


def test_sync_passes_voice_param(tmp_path, monkeypatch):
    seen: list = []

    def fake_get(url, params=None, timeout=None):
        seen.append((url, params))
        if url.endswith("/v1/sounds/manifest"):
            return _FakeResp(json_data={})
        return _FakeResp(content=b"")

    monkeypatch.setattr(sound_sync.requests, "get", fake_get)
    sound_sync.sync("http://server:6370", tmp_path / "cache", voice="Ryan")
    # The manifest request carried the voice query param.
    assert seen and seen[0][1] == {"voice": "Ryan"}

    # No voice → no params (back-compat: server uses its default voice).
    seen.clear()
    sound_sync.sync("http://server:6370", tmp_path / "cache2")
    assert seen[0][1] is None


def test_sync_skips_unsafe_manifest_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sound_sync.requests,
        "get",
        lambda url, params=None, timeout=None: _FakeResp(json_data={"../evil.mp3": "deadbeef"}),
    )
    cache = tmp_path / "cache"
    assert sound_sync.sync("http://server:6370", cache) == 0
    assert not (tmp_path / "evil.mp3").exists()


# ─── what the room says is decided by its server, not by the path ─────────
#
# The clips are what the room SAYS; nothing here is executed, but a host on
# the path used to be able to substitute them: the sound channel had no
# signed list, and bodies were written without being compared to the list
# at all. A pinned device now takes a signed list only, and every body has
# to hash to what the list says before it lands.

from satellite.tests.test_signed_manifests import _envelope, _server  # noqa: E402


class _NotFound:
    status_code = 404

    def raise_for_status(self):
        raise RuntimeError("HTTP 404")

    def json(self):
        raise ValueError("not json")


def _signed_server(monkeypatch, seed, public, clips: dict[str, bytes], *,
                   serve_signed=True, seen=None):
    manifest = {rel: hashlib.sha256(body).hexdigest() for rel, body in clips.items()}

    def fake_get(url, params=None, timeout=None):
        if seen is not None:
            seen.append((url, params))
        if url.endswith("/v1/sounds/manifest.sig"):
            if not serve_signed:
                return _NotFound()
            return _FakeResp(json_data=_envelope(
                seed, public, "satellite-sounds", manifest, serial=3))
        if url.endswith("/v1/sounds/manifest"):
            return _FakeResp(json_data=manifest)
        rel = url.split("/v1/sounds/", 1)[1]
        return _FakeResp(content=clips[rel])

    monkeypatch.setattr(sound_sync.requests, "get", fake_get)


def test_a_pinned_device_takes_only_the_signed_clip_list(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    seen: list = []
    _signed_server(monkeypatch, seed, public,
                   {"greetings/greet_a.mp3": b"aaa", "network_issues.mp3": b"net"},
                   seen=seen)
    cache = tmp_path / "cache"
    n = sound_sync.sync("http://server:6370", cache, voice="Ryan",
                        expected_fingerprint=fingerprint)
    assert n == 2
    assert (cache / "greetings" / "greet_a.mp3").read_bytes() == b"aaa"
    asked = [u for u, _p in seen]
    assert any(u.endswith("/v1/sounds/manifest.sig") for u in asked)
    assert not any(u.endswith("/v1/sounds/manifest") for u in asked)
    # The voice rides on the signed request too.
    assert seen[0][1] == {"voice": "Ryan"}


def test_a_pinned_device_refuses_a_clip_list_signed_by_another_server(tmp_path, monkeypatch):
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    _signed_server(monkeypatch, seed_b, public_b, {"greetings/greet_a.mp3": b"attacker"})
    cache = tmp_path / "cache"
    with pytest.raises(RuntimeError) as e:
        sound_sync.sync("http://server:6370", cache, expected_fingerprint=ours)
    assert "nothing was written" in str(e.value)
    assert not (cache / "greetings" / "greet_a.mp3").exists()


def test_a_pinned_device_refuses_a_server_with_no_signed_clip_list(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    _signed_server(monkeypatch, seed, public, {"network_issues.mp3": b"net"},
                   serve_signed=False)
    with pytest.raises(RuntimeError) as e:
        sound_sync.sync("http://server:6370", tmp_path / "cache",
                        expected_fingerprint=fingerprint)
    assert "upgrade the Domovoi server" in str(e.value)


def test_a_clip_whose_bytes_do_not_match_the_list_is_not_written(tmp_path, monkeypatch):
    """Signed or not, the list says what the body must hash to."""
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/v1/sounds/manifest"):
            return _FakeResp(json_data={"network_issues.mp3": hashlib.sha256(b"real").hexdigest()})
        return _FakeResp(content=b"substituted")

    monkeypatch.setattr(sound_sync.requests, "get", fake_get)
    cache = tmp_path / "cache"
    assert sound_sync.sync("http://server:6370", cache) == 0
    assert not (cache / "network_issues.mp3").exists()


def test_an_unpinned_device_syncs_clips_as_it_always_did(tmp_path, monkeypatch, caplog):
    seed, public, _fingerprint = _server()
    seen: list = []
    _signed_server(monkeypatch, seed, public, {"network_issues.mp3": b"net"},
                   serve_signed=False, seen=seen)
    monkeypatch.setattr(sound_sync, "_UNSIGNED_WARNED", False)
    with caplog.at_level("WARNING"):
        assert sound_sync.sync("http://server:6370", tmp_path / "cache") == 1
    assert "taken on trust" in caplog.text
    assert not any(u.endswith("manifest.sig") for u, _p in seen)
