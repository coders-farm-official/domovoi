"""RealTTSClient per-call engine/voice routing (voices feature).

The engine-aware ``synthesize(text, *, engine=, voice=)`` lets the
streaming layer render each room in its own voice. These tests monkeypatch
the per-engine synth helpers (no real TTS) and assert which engine + voice
gets called for a given override, plus the fallback ordering.
"""

from __future__ import annotations

from domovoi.clients import tts as tts_mod
from domovoi.clients.tts import RealTTSClient


def _client() -> RealTTSClient:
    return RealTTSClient(
        preferred_engine="edge",
        edge_voice="en-US-AriaNeural",
        piper_voice="en_US-lessac-medium",
        speed=1.0,
    )


def _spy(monkeypatch):
    calls: list[tuple[str, str]] = []

    def edge(text, voice, speed):
        calls.append(("edge", voice))
        return b"EDGE"

    def piper(text, voice, speed):
        calls.append(("piper", voice))
        return b"PIPER"

    def system(text):
        calls.append(("system", ""))
        return b"SYS"

    monkeypatch.setattr(tts_mod, "_synth_edge_sync", edge)
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", piper)
    monkeypatch.setattr(tts_mod, "_synth_system_sync", system)
    return calls


def test_default_uses_preferred_engine_and_global_voice(monkeypatch):
    calls = _spy(monkeypatch)
    out = _client()._synth_blocking("hi")
    assert out == b"EDGE"
    assert calls == [("edge", "en-US-AriaNeural")]


def test_engine_and_voice_override(monkeypatch):
    calls = _spy(monkeypatch)
    out = _client()._synth_blocking("hi", engine="piper", voice="en_US-ryan-high")
    assert out == b"PIPER"
    # Piper goes first because it's the per-call preferred engine, with the
    # overridden voice.
    assert calls[0] == ("piper", "en_US-ryan-high")


def test_voice_override_only_applies_to_preferred_engine(monkeypatch):
    calls = _spy(monkeypatch)
    # Piper preferred but its synth returns None → falls back to edge, which
    # must use ITS OWN default voice, not the piper override.
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda *a, **k: None)
    out = _client()._synth_blocking("hi", engine="piper", voice="en_US-ryan-high")
    assert out == b"EDGE"
    assert ("edge", "en-US-AriaNeural") in calls


def test_all_engines_fail_returns_empty_wav(monkeypatch):
    monkeypatch.setattr(tts_mod, "_synth_edge_sync", lambda *a, **k: None)
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda *a, **k: None)
    monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda *a, **k: None)
    out = _client()._synth_blocking("hi")
    # Empty WAV — non-empty header, zero frames — so callers don't crash.
    assert out.startswith(b"RIFF")


def test_zero_frame_wav_falls_through_to_next_engine(monkeypatch):
    # An engine that "succeeds" but renders NO audio (e.g. a bad uploaded voice
    # model — a valid WAV header, zero frames) must be treated as a failure and
    # fall through, not returned as silence.
    empty = tts_mod._pcm_to_wav_bytes(b"", 22_050)              # header, 0 frames
    real = tts_mod._pcm_to_wav_bytes(b"\x00\x01" * 200, 22_050)  # has frames
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda *a, **k: empty)
    monkeypatch.setattr(tts_mod, "_synth_edge_sync", lambda *a, **k: None)
    monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda *a, **k: real)
    out = _client()._synth_blocking("hi", engine="piper")
    assert out == real  # fell through the empty Piper render + the None edge


def test_wav_is_silent_only_for_zero_frame_wav():
    assert tts_mod._wav_is_silent(tts_mod._pcm_to_wav_bytes(b"", 16_000)) is True
    assert tts_mod._wav_is_silent(tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 50, 16_000)) is False
    # Non-WAV bytes (test markers / any raw format) get the benefit of the doubt.
    assert tts_mod._wav_is_silent(b"EDGE") is False


def test_piper_voice_path_resolves_uploaded_onnx(tmp_path, monkeypatch):
    # A direct path to an existing .onnx is returned as-is (uploaded voice).
    onnx = tmp_path / "ryan.onnx"
    onnx.write_bytes(b"fake-model")
    assert tts_mod._piper_voice_path(str(onnx)) == onnx


def test_piper_voice_path_resolves_bare_name_in_voices_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(tmp_path))
    (tmp_path / "custom.onnx").write_bytes(b"m")
    (tmp_path / "custom.onnx.json").write_text("{}")
    assert tts_mod._piper_voice_path("custom") == tmp_path / "custom.onnx"
