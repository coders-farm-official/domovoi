"""The one-time Piper voice download (CORE-18).

DB-free and network-free: Hugging Face is an ``httpx.MockTransport`` behind
the module's client factory, and ``net_safety.resolve_host`` is patched so
names resolve to what each test says. The handlers dispatch on the URL path
(never the host), so they keep working if the client connects to a vetted
address literal.

* A ``model_ref`` that isn't LANG_REGION-SPEAKER-QUALITY (a traversal, a
  path) is refused before any request, and nothing is written outside the
  voices dir.
* Both files are checked against a digest before either lands where the
  loader looks: the pins for catalogue voices, else the Hub's published
  digests; a mismatch, a short or an oversized body leaves nothing behind.
* Every hop goes through net_safety: a redirect to a loopback address is
  refused.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json

import httpx
import pytest

from domovoi import egress, net_safety, voice_catalog
from domovoi.clients import tts as tts_mod

PUBLIC = "93.184.216.34"
REF = "en_US-testvoice-medium"
REL = "en/en_US/testvoice/medium"
MODEL = b"\x08\x01onnx-model-bytes" * 64
CONFIG = b'{"audio": {"sample_rate": 22050}}\n'


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


PIN = (hashlib.sha256(MODEL).hexdigest(), len(MODEL), _blob(CONFIG), len(CONFIG))


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """A fake Hub. ``hub.files`` maps a URL path to (status, body, headers);
    ``hub.seen`` records each requested path."""

    class Hub:
        files: dict[str, tuple[int, bytes, dict]] = {}
        seen: list[str] = []

    Hub.files = {}
    Hub.seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        Hub.seen.append(request.url.path)
        status, body, headers = Hub.files.get(request.url.path, (404, b"", {}))
        return httpx.Response(status, content=body, headers=headers)

    def resolve(host: str):
        if host in ("127.0.0.1", "localhost"):
            return [ipaddress.ip_address("127.0.0.1")]
        return [ipaddress.ip_address(PUBLIC)]

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    monkeypatch.setattr(
        tts_mod, "_piper_http_client",
        lambda: egress.sync_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(tmp_path / "voices"))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    return Hub


def _serve_pinned(hub, monkeypatch, *, model=MODEL, config=CONFIG, rev=tts_mod.PIPER_PINNED_REVISION):
    monkeypatch.setitem(tts_mod.PIPER_PINS, REF, PIN)
    base = f"/rhasspy/piper-voices/resolve/{rev}/{REL}/{REF}"
    hub.files[base + ".onnx"] = (200, model, {})
    hub.files[base + ".onnx.json"] = (200, config, {})
    return base


def _voices(tmp_path):
    d = tmp_path / "voices"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# ─── the name ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ref",
    [
        "../../x-y-z",
        "en_US-lessac-medium/../../../evil",
        "..\\..\\x-y-medium",
        "en_US-a/b-medium",
        "en-lessac-medium",            # no region
        "en_US-lessac-ultra",          # not a quality
        "en_US-lessac-medium-extra",
    ],
)
def test_a_name_without_the_catalogue_shape_is_refused_before_any_request(hub, tmp_path, ref) -> None:
    with egress.override_policy("always"):
        with pytest.raises(ValueError):
            tts_mod._piper_voice_path(ref)
    assert hub.seen == []
    assert not any(tmp_path.rglob("*.onnx*"))


def test_a_traversing_name_never_reads_outside_the_voices_dir(hub, tmp_path) -> None:
    (tmp_path / "x-y-z.onnx").write_bytes(b"m")
    (tmp_path / "x-y-z.onnx.json").write_text("{}")
    (tmp_path / "voices").mkdir()
    assert tts_mod._piper_local_path("../x-y-z") is None


@pytest.mark.parametrize(
    "ref", ["en_GB-northern_english_male-medium", "zh_CN-huayan-x_low", "en_US-lessac-medium"]
)
def test_published_names_have_the_shape(ref) -> None:
    assert tts_mod.PIPER_MODEL_REF_RE.fullmatch(ref)


def test_every_catalogue_voice_is_pinned() -> None:
    for _label, ref in voice_catalog.PIPER_CATALOG:
        assert ref in tts_mod.PIPER_PINS, ref
        sha, size, blob, cfg_size = tts_mod.PIPER_PINS[ref]
        assert len(sha) == 64 and len(blob) == 40
        assert 0 < size <= tts_mod.PIPER_ONNX_MAX_BYTES and 0 < cfg_size <= tts_mod.PIPER_JSON_MAX_BYTES


def test_the_default_voice_is_pinned() -> None:
    assert tts_mod.settings.tts_piper_voice in tts_mod.PIPER_PINS


# ─── pinned voices ────────────────────────────────────────────────────────

def test_a_pinned_voice_downloads_from_the_pinned_revision(hub, monkeypatch, tmp_path) -> None:
    base = _serve_pinned(hub, monkeypatch)
    with egress.override_policy("always"):
        path = tts_mod._piper_voice_path(REF)
    assert path == tmp_path / "voices" / f"{REF}.onnx"
    assert path.read_bytes() == MODEL
    assert (tmp_path / "voices" / f"{REF}.onnx.json").read_bytes() == CONFIG
    assert hub.seen == [base + ".onnx.json", base + ".onnx"]
    assert _voices(tmp_path) == [f"{REF}.onnx", f"{REF}.onnx.json"]   # no .part left


def test_a_model_with_the_wrong_digest_is_refused_and_nothing_is_kept(hub, monkeypatch, tmp_path) -> None:
    tampered = bytearray(MODEL)
    tampered[5] ^= 0xFF
    _serve_pinned(hub, monkeypatch, model=bytes(tampered))
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError, match="checksum mismatch"):
            tts_mod._piper_voice_path(REF)
    assert _voices(tmp_path) == []
    assert not tts_mod.piper_voice_on_disk(REF)


def test_a_config_with_the_wrong_digest_is_refused(hub, monkeypatch, tmp_path) -> None:
    _serve_pinned(hub, monkeypatch, config=CONFIG.replace(b"22050", b"16000"))
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError, match="onnx.json"):
            tts_mod._piper_voice_path(REF)
    assert _voices(tmp_path) == []


def test_an_oversized_model_is_cut_off_at_the_expected_size(hub, monkeypatch, tmp_path) -> None:
    _serve_pinned(hub, monkeypatch, model=MODEL + b"x" * 4096)
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError, match="larger than"):
            tts_mod._piper_voice_path(REF)
    assert _voices(tmp_path) == []


def test_a_short_model_is_refused(hub, monkeypatch, tmp_path) -> None:
    _serve_pinned(hub, monkeypatch, model=MODEL[:-10])
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError, match="expected"):
            tts_mod._piper_voice_path(REF)
    assert _voices(tmp_path) == []


def test_a_redirect_to_loopback_is_refused(hub, monkeypatch, tmp_path) -> None:
    base = _serve_pinned(hub, monkeypatch)
    hub.files[base + ".onnx"] = (302, b"", {"location": "http://127.0.0.1:6370/v1/health"})
    with egress.override_policy("always"):
        with pytest.raises(net_safety.UnsafeOutboundURL):
            tts_mod._piper_voice_path(REF)
    assert "/v1/health" not in hub.seen
    assert _voices(tmp_path) == []


def test_a_redirect_to_a_public_cdn_is_followed(hub, monkeypatch, tmp_path) -> None:
    base = _serve_pinned(hub, monkeypatch)
    hub.files[base + ".onnx"] = (302, b"", {"location": "https://cdn-lfs.hf.co/blob/abc"})
    hub.files["/blob/abc"] = (200, MODEL, {})
    with egress.override_policy("always"):
        assert tts_mod._piper_voice_path(REF).read_bytes() == MODEL


def test_under_never_nothing_is_requested(hub, monkeypatch, tmp_path) -> None:
    _serve_pinned(hub, monkeypatch)
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            tts_mod._piper_voice_path(REF)
    assert hub.seen == []


# ─── voices outside the catalogue: the Hub's own digests ─────────────────

def _tree(model=MODEL, config=CONFIG):
    return json.dumps([
        {"type": "file", "path": f"{REL}/{REF}.onnx", "oid": "0" * 40,
         "size": len(model), "lfs": {"oid": hashlib.sha256(model).hexdigest(), "size": len(model)}},
        {"type": "file", "path": f"{REL}/{REF}.onnx.json", "oid": _blob(config), "size": len(config)},
    ]).encode()


def test_an_uncatalogued_voice_is_checked_against_the_hubs_digests(hub, tmp_path) -> None:
    assert REF not in tts_mod.PIPER_PINS
    hub.files[f"/api/models/rhasspy/piper-voices/tree/main/{REL}"] = (200, _tree(), {})
    base = f"/rhasspy/piper-voices/resolve/main/{REL}/{REF}"
    hub.files[base + ".onnx"] = (200, MODEL, {})
    hub.files[base + ".onnx.json"] = (200, CONFIG, {})
    with egress.override_policy("always"):
        assert tts_mod._piper_voice_path(REF).read_bytes() == MODEL


def test_an_uncatalogued_voice_that_doesnt_match_the_hub_is_refused(hub, tmp_path) -> None:
    hub.files[f"/api/models/rhasspy/piper-voices/tree/main/{REL}"] = (200, _tree(), {})
    base = f"/rhasspy/piper-voices/resolve/main/{REL}/{REF}"
    hub.files[base + ".onnx"] = (200, MODEL[::-1], {})
    hub.files[base + ".onnx.json"] = (200, CONFIG, {})
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError):
            tts_mod._piper_voice_path(REF)
    assert _voices(tmp_path) == []


def test_an_unlisted_voice_is_refused(hub, tmp_path) -> None:
    with egress.override_policy("always"):
        with pytest.raises(tts_mod.PiperDownloadError):
            tts_mod._piper_voice_path(REF)
    assert all("/resolve/" not in p for p in hub.seen)
