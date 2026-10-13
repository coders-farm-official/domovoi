"""The openWakeWord base-model channel for spare-device satellites.

An Android phone in room mode fetches the melspectrogram, embedding, wake
word and voice-detector models from its server (`/v1/wake-base`), signed,
and only files the satellite media cache holds with their pinned digest are
listed or served. DB-free: the endpoint functions are called directly.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException

from domovoi import admin_auth, main, server_identity, wake_base_models
from domovoi.satellite_media import cache, fetchers
from satellite import server_identity as sat_identity


@pytest.fixture(autouse=True)
def identity_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "cfg")
    server_identity.reset_cache()
    yield server_identity.load_or_create()
    server_identity.reset_cache()


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    """A cache whose pins are two small fake models (the real ones are MBs)."""
    root = tmp_path / "cache"
    monkeypatch.setattr(cache, "CACHE_ROOT", root)
    good = b"\x08\x07fake-melspectrogram"
    other = b"\x08\x07fake-embedding"
    pins = {
        "melspectrogram.onnx": (hashlib.sha256(good).hexdigest(), len(good)),
        "embedding_model.onnx": (hashlib.sha256(other).hexdigest(), len(other)),
        "melspectrogram.tflite": (hashlib.sha256(b"tfl").hexdigest(), 3),
    }
    monkeypatch.setattr(wake_base_models, "OWW_MODEL_FILES", pins)
    wake_base_models._DIGESTS.clear()
    d = root / "oww_models"
    d.mkdir(parents=True)
    (d / "melspectrogram.onnx").write_bytes(good)
    (d / "melspectrogram.tflite").write_bytes(b"tfl")
    return d, other


def test_the_channel_is_routed_signature_before_the_catch_all():
    paths = [r.path for r in main.app.routes if getattr(r, "methods", None) and "GET" in r.methods]
    assert paths.index("/v1/wake-base/manifest.sig") < paths.index("/v1/wake-base/{name}")


def test_the_real_pins_name_what_room_mode_needs():
    """The four files the phone loads are pinned in the core (the app keeps
    the same digests, android roommode/RoomModels.kt)."""
    for name in ("melspectrogram.onnx", "embedding_model.onnx",
                 "hey_jarvis_v0.1.onnx", "silero_vad.onnx"):
        assert name in fetchers.OWW_MODEL_FILES


@pytest.mark.asyncio
async def test_only_verified_pinned_onnx_files_are_listed(bucket):
    d, other = bucket
    # Missing: embedding_model. Wrong body under a pinned name: not listed.
    (d / "embedding_model.onnx").write_bytes(other + b"x")
    (d / "stray.onnx").write_bytes(b"not pinned")
    envelope = await main.wake_base_manifest_signed()
    manifest = sat_identity.verify_manifest_envelope(
        envelope, channel=wake_base_models.WAKE_BASE_CHANNEL,
        expected_fingerprint=server_identity.load_or_create().fingerprint,
    )
    assert manifest == {"melspectrogram.onnx": wake_base_models.OWW_MODEL_FILES["melspectrogram.onnx"][0]}
    (d / "embedding_model.onnx").write_bytes(other)
    assert set(wake_base_models.served_models()) == {"melspectrogram.onnx", "embedding_model.onnx"}


@pytest.mark.asyncio
async def test_an_empty_cache_is_an_empty_signed_list(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "nothing")
    envelope = await main.wake_base_manifest_signed()
    assert envelope["manifest"] == {}
    assert envelope["signature_v2"]
    assert not (tmp_path / "nothing").exists()


@pytest.mark.asyncio
async def test_a_listed_model_is_served_and_nothing_else(bucket):
    d, _ = bucket
    resp = await main.wake_base_file("melspectrogram.onnx")
    assert resp.path == d / "melspectrogram.onnx"
    for name in ("melspectrogram.tflite", "stray.onnx", "../oww_models/melspectrogram.onnx",
                 "embedding_model.onnx"):
        with pytest.raises(HTTPException) as e:
            await main.wake_base_file(name)
        assert e.value.status_code == 404
