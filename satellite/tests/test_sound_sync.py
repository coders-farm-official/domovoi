"""Tests for the satellite sound-sync logic (HTTP mocked)."""

from __future__ import annotations

import hashlib

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
