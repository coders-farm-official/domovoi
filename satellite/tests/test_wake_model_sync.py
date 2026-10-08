"""The wake-model mirror takes its list from this device's server.

The model is what the room LISTENS for, and its body is parsed by
onnxruntime inside the client. The channel checked each body against the
list — but the list itself came unsigned from whatever host was dialed. A
pinned device now takes a signed list only.

HTTP is a fake; nothing touches the network.
"""

from __future__ import annotations

import hashlib

import pytest

from satellite import wake_model_sync
from satellite.tests.test_signed_manifests import _envelope, _server


class _Resp:
    def __init__(self, *, json_data=None, content=b"", status_code=200):
        self._json, self.content, self.status_code = json_data, content, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def _server_with(monkeypatch, seed, public, models: dict[str, bytes], *,
                 serve_signed=True, seen=None, bodies=None):
    manifest = {rel: hashlib.sha256(body).hexdigest() for rel, body in models.items()}
    bodies = models if bodies is None else bodies

    def fake_get(url, params=None, timeout=None):
        if seen is not None:
            seen.append(url)
        if url.endswith("/v1/wake-models/manifest.sig"):
            if not serve_signed:
                return _Resp(status_code=404)
            return _Resp(json_data=_envelope(
                seed, public, "satellite-wake-models", manifest, serial=4))
        if url.endswith("/v1/wake-models/manifest"):
            return _Resp(json_data=manifest)
        rel = url.split("/v1/wake-models/", 1)[1]
        return _Resp(content=bodies[rel])

    monkeypatch.setattr(wake_model_sync.requests, "get", fake_get)


def test_a_pinned_device_takes_only_the_signed_model_list(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    seen: list[str] = []
    _server_with(monkeypatch, seed, public,
                 {"hey_house.onnx": b"\x08onnx", "hey_house.onnx.json": b"{}"}, seen=seen)
    cache = tmp_path / "wake_models"
    assert wake_model_sync.sync("http://server:6370", cache,
                                expected_fingerprint=fingerprint) == 2
    assert (cache / "hey_house.onnx").read_bytes() == b"\x08onnx"
    assert any(u.endswith("/v1/wake-models/manifest.sig") for u in seen)
    assert not any(u.endswith("/v1/wake-models/manifest") for u in seen)


def test_a_pinned_device_refuses_a_model_list_signed_by_another_server(tmp_path, monkeypatch):
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    _server_with(monkeypatch, seed_b, public_b, {"hey_house.onnx": b"theirs"})
    cache = tmp_path / "wake_models"
    with pytest.raises(RuntimeError) as e:
        wake_model_sync.sync("http://server:6370", cache, expected_fingerprint=ours)
    assert "nothing was written" in str(e.value)
    assert not (cache / "hey_house.onnx").exists()


def test_a_pinned_device_refuses_a_server_with_no_signed_model_list(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    _server_with(monkeypatch, seed, public, {"hey_house.onnx": b"x"}, serve_signed=False)
    with pytest.raises(RuntimeError) as e:
        wake_model_sync.sync("http://server:6370", tmp_path / "wake_models",
                             expected_fingerprint=fingerprint)
    assert "upgrade the Domovoi server" in str(e.value)


def test_a_model_whose_bytes_do_not_match_the_signed_list_is_not_written(tmp_path, monkeypatch):
    seed, public, fingerprint = _server()
    _server_with(monkeypatch, seed, public, {"hey_house.onnx": b"real"},
                 bodies={"hey_house.onnx": b"substituted"})
    cache = tmp_path / "wake_models"
    assert wake_model_sync.sync("http://server:6370", cache,
                                expected_fingerprint=fingerprint) == 0
    assert not (cache / "hey_house.onnx").exists()


def test_an_unpinned_device_syncs_models_as_it_always_did(tmp_path, monkeypatch):
    seed, public, _fingerprint = _server()
    seen: list[str] = []
    _server_with(monkeypatch, seed, public, {"hey_house.onnx": b"x"},
                 serve_signed=False, seen=seen)
    assert wake_model_sync.sync("http://server:6370", tmp_path / "wake_models") == 1
    assert not any(u.endswith("manifest.sig") for u in seen)
