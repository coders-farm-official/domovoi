"""TTS engine router.

The `edge → piper → system` fallback chain. Output
is always a complete WAV file's bytes (int16 mono, native sample rate of the
engine), so HTTP clients can stream it directly without re-encoding.

- `edge`   — Microsoft Edge neural voices (online, very natural, ~24 kHz)
- `piper`  — local neural (offline, auto-downloads voice, 22 kHz)
- `system` — pyttsx3/SAPI5 on Windows; skipped on other platforms

Per-engine failure (network drop, missing voice, etc.) advances to the next.
The chain starts at the configured preferred engine. Synchronous engine work
runs in a threadpool to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import tempfile
import wave
from pathlib import Path
from typing import Protocol

from domovoi.config import settings
from domovoi.speech_sanitize import sanitize_for_speech

log = logging.getLogger(__name__)


class TTSClient(Protocol):
    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes: ...


class TTSStubClient:
    """Deterministic stub — returns an empty WAV so tests can assert length
    without actually running TTS."""

    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        # Parity with the real client — the scrub is a no-op on the stub's
        # empty-WAV output, but keeps behavior identical if a future stub ever
        # echoes text back.
        _ = sanitize_for_speech(text or "")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(b"")
        return buf.getvalue()


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _wav_is_silent(wav_bytes: bytes) -> bool:
    """True only if ``wav_bytes`` parses as a WAV with ZERO audio frames — a
    header-only render that's effectively silence. Non-WAV / unparseable bytes
    return False (benefit of the doubt — that's the test markers' shape and any
    future raw format). The engine router treats a zero-frame "success" (e.g. a
    bad uploaded voice model or an engine hiccup) as a FAILURE and falls through
    to the next engine, so a flaky primary engine degrades to audible local TTS
    instead of silence."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getnframes() == 0
    except (wave.Error, EOFError, OSError):
        return False


def _synth_edge_sync(text: str, voice: str, speed: float) -> bytes | None:
    """Run edge-tts (async under the hood) from a sync context."""
    try:
        import edge_tts
        import miniaudio
    except ImportError:
        return None

    rate = f"{int(round((speed - 1.0) * 100)):+d}%"

    async def _go() -> bytes:
        chunks: list[bytes] = []
        async for c in edge_tts.Communicate(text, voice, rate=rate).stream():
            if c["type"] == "audio":
                chunks.append(c["data"])
        return b"".join(chunks)

    mp3 = asyncio.run(_go())
    if not mp3:
        return None
    # miniaudio.decode defaults to nchannels=2, sample_rate=44100 — silently
    # upmixes/resamples. Edge MP3 is mono 24 kHz; pin both so the WAV we
    # write matches the bytes (otherwise _pcm_to_wav_bytes writes a mono
    # header over stereo-interleaved data → playback at half speed).
    decoded = miniaudio.decode(
        mp3,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=24_000,
    )
    pcm_bytes = decoded.samples.tobytes()
    return _pcm_to_wav_bytes(pcm_bytes, decoded.sample_rate)


_piper_voice_cache: dict[str, object] = {}


def _voices_dir() -> Path:
    return Path(settings.voice_models_dir)


def _piper_voice_path(model_ref: str) -> Path:
    """Resolve a Piper ``model_ref`` to its ``.onnx`` file.

    Three forms, checked in order:
      1. A direct path to an uploaded ``.onnx`` (absolute or relative) that
         exists on disk — load it as-is (web-uploaded voices store this).
      2. A bare name whose ``<name>.onnx`` + ``.onnx.json`` already sit in
         the voice-models dir (uploaded-by-name, or a previously downloaded
         HF voice).
      3. A standard HF voice name (LANG-SPEAKER-QUALITY) — auto-download
         into the voice-models dir, one time.
    """
    import requests

    voices_dir = _voices_dir()

    # (1) Direct path to an uploaded model.
    cand = Path(model_ref)
    if cand.suffix == ".onnx" and cand.is_file():
        return cand

    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{model_ref}.onnx"
    jpath = voices_dir / f"{model_ref}.onnx.json"
    # (2) Already present under the voices dir.
    if onnx.exists() and jpath.exists():
        return onnx

    # (3) HF auto-download.
    parts = model_ref.split("-")
    if len(parts) != 3:
        raise ValueError(
            f"Piper voice must be an uploaded .onnx or a LANG-SPEAKER-QUALITY "
            f"HF name, got {model_ref!r}"
        )
    lang_full, speaker, quality = parts
    lang = lang_full.split("_")[0]
    base = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"{lang}/{lang_full}/{speaker}/{quality}/{model_ref}"
    )
    log.info("downloading Piper voice %s (one-time)", model_ref)
    for suffix, path in ((".onnx", onnx), (".onnx.json", jpath)):
        r = requests.get(base + suffix, stream=True, timeout=60)
        r.raise_for_status()
        with open(path, "wb") as f:
            for block in r.iter_content(32768):
                f.write(block)
    return onnx


def _synth_piper_sync(text: str, voice: str, speed: float) -> bytes | None:
    try:
        from piper import PiperVoice, SynthesisConfig
    except ImportError:
        return None

    if voice not in _piper_voice_cache:
        _piper_voice_cache[voice] = PiperVoice.load(str(_piper_voice_path(voice)))
    v = _piper_voice_cache[voice]

    syn_config = SynthesisConfig(length_scale=(1.0 / speed) if speed > 0 else 1.0)
    pcm_parts: list[bytes] = []
    sr: int | None = None
    for chunk in v.synthesize(text, syn_config=syn_config):
        if sr is None:
            sr = chunk.sample_rate
        pcm_parts.append(chunk.audio_int16_bytes)
    pcm = b"".join(pcm_parts)
    if not pcm or sr is None:
        # Defensive: if a voice ever renders no audio (a bad/empty uploaded
        # model, a degenerate phonemization) that's a failure, not a silent
        # success — return None so the router falls through to the next engine
        # instead of emitting a zero-frame WAV. (Verified 2026-06-13: piper-tts
        # 1.4.2 renders every current voice fine; this is a safety net, not a
        # known bug.)
        return None
    return _pcm_to_wav_bytes(pcm, sr)


def _synth_system_sync(text: str) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import pyttsx3
    except ImportError:
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        engine = pyttsx3.init()
        engine.save_to_file(text, path)
        engine.runAndWait()
        with open(path, "rb") as f:
            data = f.read()
        Path(path).unlink(missing_ok=True)
        return data
    except Exception:
        return None


class RealTTSClient:
    """Engine router with per-engine fallback. Returns full WAV bytes."""

    def __init__(
        self,
        *,
        preferred_engine: str,
        edge_voice: str,
        piper_voice: str,
        speed: float,
    ) -> None:
        self.preferred_engine = preferred_engine
        self.edge_voice = edge_voice
        self.piper_voice = piper_voice
        self.speed = speed

    def _engine_order(self, preferred: str) -> list[str]:
        all_engines = ("edge", "piper", "system")
        return [preferred] + [e for e in all_engines if e != preferred]

    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        # Strip emoji + Markdown so the engines never verbalize an asterisk or
        # narrate an emoji's Unicode name. Single choke point: every spoken
        # path in the core lands here (see domovoi/speech_sanitize).
        text = sanitize_for_speech(text or "")
        return await asyncio.to_thread(self._synth_blocking, text, engine, voice)

    def _synth_blocking(
        self, text: str, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        # Per-call overrides win, else the construct-time globals. The voice
        # override only applies to the preferred engine (the registry pairs a
        # voice with its engine); fallback engines use their own default voice.
        preferred = engine or self.preferred_engine
        for eng in self._engine_order(preferred):
            v = voice if (voice and eng == preferred) else None
            try:
                if eng == "edge":
                    out = _synth_edge_sync(text, v or self.edge_voice, self.speed)
                elif eng == "piper":
                    out = _synth_piper_sync(text, v or self.piper_voice, self.speed)
                elif eng == "system":
                    out = _synth_system_sync(text)
                else:
                    continue
                # Reject a zero-frame WAV (engine "succeeded" but produced no
                # audio) so we fall through to the next engine rather than
                # returning silence — the local-first safety net working.
                if out and not _wav_is_silent(out):
                    return out
            except Exception as e:
                log.warning("TTS engine %s failed: %s", eng, e)
        # If every engine fails, return an empty WAV so callers don't crash.
        log.error("all TTS engines failed for text len=%d", len(text))
        return _pcm_to_wav_bytes(b"", 16_000)


_client: TTSClient | None = None


def get_tts_client() -> TTSClient:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = TTSStubClient()
    else:
        _client = RealTTSClient(
            preferred_engine=settings.tts_engine,
            edge_voice=settings.tts_edge_voice,
            piper_voice=settings.tts_piper_voice,
            speed=settings.tts_speed,
        )
    return _client


def reset_tts_client() -> None:
    """Drop the cached TTS client so the next ``get_tts_client()`` rebuilds
    from the current settings (engine / speed). Backs the config editor's
    'reapply' tier. An in-flight synth keeps its old client; the change is
    picked up on the next turn."""
    global _client
    _client = None


# Backward-compat alias.
tts_client: TTSClient = TTSStubClient()  # replaced at startup in main.py
