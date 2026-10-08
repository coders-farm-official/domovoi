"""The satellite payload's openWakeWord models are pinned (SAT-11).

DB-free and network-free: the release host is an ``httpx.MockTransport``
behind the fetcher's client factory, ``net_safety.resolve_host`` is patched,
and the cache root is a ``tmp_path``. Handlers dispatch on the URL path.

* Every model is downloaded from the pinned release URL and kept only at
  its pinned size and SHA-256; a replaced asset leaves nothing behind.
* What is already cached and verified is not downloaded again; anything in
  the bucket the pins don't vouch for is removed before a payload is built.
* Every hop goes through net_safety: a redirect to loopback is refused.
* The pins cover exactly what ``openwakeword.utils.download_models()``
  fetches (checked against the installed package's source when present).
"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import re
from pathlib import Path

import httpx
import pytest

from domovoi import egress, net_safety
from domovoi.satellite_media import cache, fetchers

A = b"model-a" * 100
B = b"model-b" * 50
PINS = {
    "a_v0.1.onnx": (hashlib.sha256(A).hexdigest(), len(A)),
    "b.tflite": (hashlib.sha256(B).hexdigest(), len(B)),
}
RELEASE_PATH = "/dscripka/openWakeWord/releases/download/v0.5.1/"


@pytest.fixture
def release(monkeypatch, tmp_path):
    class Release:
        files: dict[str, tuple[int, bytes, dict]] = {}
        seen: list[str] = []

    Release.files = {
        RELEASE_PATH + "a_v0.1.onnx": (200, A, {}),
        RELEASE_PATH + "b.tflite": (200, B, {}),
    }
    Release.seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        Release.seen.append(request.url.path)
        status, body, headers = Release.files.get(request.url.path, (404, b"", {}))
        return httpx.Response(status, content=body, headers=headers)

    def resolve(host: str):
        if host in ("127.0.0.1", "localhost"):
            return [ipaddress.ip_address("127.0.0.1")]
        return [ipaddress.ip_address("140.82.112.3")]

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    monkeypatch.setattr(
        fetchers, "_oww_http_client",
        lambda: egress.sync_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(fetchers, "OWW_MODEL_FILES", dict(PINS))
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    return Release


def _bucket(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "oww_models"


def _names(tmp_path: Path) -> list[str]:
    d = _bucket(tmp_path)
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def test_models_download_from_the_pinned_release_and_verify(release, tmp_path) -> None:
    with egress.override_policy("always"):
        ok, msg = fetchers.fetch_oww_models()
    assert ok, msg
    assert _names(tmp_path) == ["a_v0.1.onnx", "b.tflite"]
    assert (_bucket(tmp_path) / "a_v0.1.onnx").read_bytes() == A
    assert sorted(release.seen) == sorted(RELEASE_PATH + n for n in PINS)
    assert "oww_models" in cache.read_stamps()


def test_a_replaced_asset_is_refused_and_not_kept(release, tmp_path) -> None:
    release.files[RELEASE_PATH + "a_v0.1.onnx"] = (200, A[::-1], {})
    with egress.override_policy("always"):
        ok, msg = fetchers.fetch_oww_models()
    assert not ok and "checksum mismatch" in msg
    assert "a_v0.1.onnx" not in _names(tmp_path)
    assert not any(n.endswith(".part") for n in _names(tmp_path))
    assert "oww_models" not in cache.read_stamps()


def test_an_oversized_asset_is_cut_off(release, tmp_path) -> None:
    release.files[RELEASE_PATH + "b.tflite"] = (200, B + b"x" * 10_000, {})
    with egress.override_policy("always"):
        ok, msg = fetchers.fetch_oww_models()
    assert not ok and "larger than" in msg
    assert "b.tflite" not in _names(tmp_path)


def test_a_redirect_to_loopback_is_refused(release, tmp_path) -> None:
    release.files[RELEASE_PATH + "a_v0.1.onnx"] = (302, b"", {"location": "http://127.0.0.1:6370/v1/health"})
    with egress.override_policy("always"):
        ok, msg = fetchers.fetch_oww_models()
    assert not ok
    assert "/v1/health" not in release.seen
    assert "a_v0.1.onnx" not in _names(tmp_path)


def test_a_redirect_to_the_asset_cdn_is_followed(release, tmp_path) -> None:
    release.files[RELEASE_PATH + "a_v0.1.onnx"] = (
        302, b"", {"location": "https://release-assets.githubusercontent.com/x/a"}
    )
    release.files["/x/a"] = (200, A, {})
    with egress.override_policy("always"):
        ok, msg = fetchers.fetch_oww_models()
    assert ok, msg


def test_verified_models_are_not_downloaded_again(release, tmp_path) -> None:
    bucket = cache.bucket("oww_models")
    (bucket / "a_v0.1.onnx").write_bytes(A)
    with egress.override_policy("always"):
        ok, _ = fetchers.fetch_oww_models()
    assert ok
    assert release.seen == [RELEASE_PATH + "b.tflite"]


def test_a_tampered_cached_model_is_replaced(release, tmp_path) -> None:
    bucket = cache.bucket("oww_models")
    (bucket / "a_v0.1.onnx").write_bytes(b"tampered")
    with egress.override_policy("always"):
        ok, _ = fetchers.fetch_oww_models()
    assert ok
    assert (bucket / "a_v0.1.onnx").read_bytes() == A


def test_verify_drops_whatever_the_pins_dont_vouch_for(release, tmp_path) -> None:
    bucket = cache.bucket("oww_models")
    (bucket / "a_v0.1.onnx").write_bytes(A)
    (bucket / "b.tflite").write_bytes(b"short")
    (bucket / "extra.onnx").write_bytes(b"from an older refresh")
    (bucket / "nested").mkdir()
    verified, removed = fetchers.verify_oww_models()
    assert verified == 1
    assert sorted(removed) == ["b.tflite", "extra.onnx", "nested"]
    assert _names(tmp_path) == ["a_v0.1.onnx"]


def test_under_never_nothing_is_requested(release, tmp_path) -> None:
    with egress.override_policy("never"):
        ok, msg = fetchers.fetch_oww_models()
    assert not ok and release.seen == []


def test_the_builder_verifies_the_bucket_before_assembling() -> None:
    import inspect

    from domovoi.satellite_media import builder

    src = inspect.getsource(builder.build)
    assert src.index("fetchers.verify_oww_models()") < src.index("payload.assemble(")


# ─── the pins themselves ──────────────────────────────────────────────────

def test_real_pins_are_well_formed() -> None:
    import domovoi.satellite_media.fetchers as real

    pins = real.__dict__["OWW_MODEL_FILES"]
    assert len(pins) == 17
    for name, (sha, size) in pins.items():
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name
        assert 100_000 < size < 4_000_000, name
    assert real.OWW_RELEASE_URL.startswith("https://github.com/dscripka/openWakeWord/releases/download/")


def _package_model_names() -> set[str] | None:
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.origin:
        return None
    src = Path(spec.origin).read_text(encoding="utf-8")
    urls = re.findall(r'"download_url":\s*"([^"]+)"', src)
    names: set[str] = set()
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        names.add(name)
        # download_models() fetches the ONNX twin of every .tflite feature
        # and wake-word model too (the VAD model is ONNX already).
        if name.endswith(".tflite"):
            names.add(name[: -len(".tflite")] + ".onnx")
    return names


def test_pins_cover_exactly_what_the_package_downloads() -> None:
    names = _package_model_names()
    if names is None:
        pytest.skip("openwakeword is not installed here")
    import domovoi.satellite_media.fetchers as real

    assert set(real.OWW_MODEL_FILES) == names
