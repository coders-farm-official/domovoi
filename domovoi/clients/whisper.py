"""Whisper STT client.

Real path: `faster-whisper` on CUDA, loaded synchronously at startup.
Stub path: deterministic echo for tests.

Input contract: PCM bytes (16 kHz mono 16-bit). The WebSocket ingestion
path produces this format directly; other callers can pass a WAV via the
convenience `transcribe_wav_bytes` entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import wave
from pathlib import Path
from typing import Protocol

from domovoi.config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2  # int16


class WhisperClient(Protocol):
    async def transcribe(self, pcm_bytes: bytes) -> str: ...
    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str: ...


class WhisperStubClient:
    """Deterministic stub — real load is skipped entirely when USE_STUBS=true."""

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return f"(stub transcribe) {len(pcm_bytes)} bytes"

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        return f"(stub transcribe-wav) {len(wav_bytes)} bytes"


def _load_hint(model: str, device: str, compute_type: str, exc: Exception) -> str:
    """Turn a Whisper load failure into an actionable message.

    The two failures that actually happen in the field are a CUDA config on
    a machine that can't do CUDA, and a GPU compute type on a CPU device.
    Both surface as opaque library errors, so name the fix here rather than
    making the operator find it in the docs.
    """
    detail = f"{type(exc).__name__}: {exc}"
    base = (
        f"Whisper failed to load (model={model} device={device} "
        f"compute={compute_type}). {detail}"
    )
    if device == "cuda":
        return (
            f"{base}\n"
            "  If this machine has no NVIDIA GPU, set whisper_device=cpu and "
            "whisper_compute_type=int8 (dashboard gear -> Advanced, then "
            "restart). See docs/CPU_HOST.md.\n"
            "  If it does have one, the CUDA runtime wheels are a separate "
            'extra as of 1.0: pip install -e ".[real-clients,cuda]"'
        )
    if device == "cpu" and compute_type in ("float16", "fp16"):
        return (
            f"{base}\n"
            "  float16 is a GPU compute type. On CPU use "
            "whisper_compute_type=int8. See docs/CPU_HOST.md."
        )
    return base


class FasterWhisperClient:
    """Wraps `faster_whisper.WhisperModel` with an async interface.

    The model load is synchronous (and slow — ~30 s first run for large-v3).
    Transcription runs in a threadpool so it doesn't block the event loop.
    """

    def __init__(self, model: str, device: str, compute_type: str) -> None:
        # Import lazily so USE_STUBS=true doesn't require faster-whisper installed.
        from faster_whisper import WhisperModel

        log.info("loading Whisper model=%s device=%s compute=%s", model, device, compute_type)
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
        except Exception as e:
            raise RuntimeError(_load_hint(model, device, compute_type, e)) from e
        log.info("Whisper ready")

    async def transcribe(self, pcm_bytes: bytes) -> str:
        """Transcribe raw 16 kHz mono int16 PCM bytes."""
        return await asyncio.to_thread(self._transcribe_pcm_sync, pcm_bytes)

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        """Transcribe a complete WAV file's bytes."""
        return await asyncio.to_thread(self._transcribe_wav_sync, wav_bytes)

    def _transcribe_pcm_sync(self, pcm_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
            with wave.open(f, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(SAMPLE_WIDTH_BYTES)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_bytes)
        try:
            return self._run_model(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _transcribe_wav_sync(self, wav_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
            f.write(wav_bytes)
        try:
            return self._run_model(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _run_model(self, path: str) -> str:
        segments, _info = self._model.transcribe(path, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()


_client: WhisperClient | None = None


def get_whisper_client() -> WhisperClient:
    """Lazy singleton. Returns stub in USE_STUBS mode (no heavy import)."""
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = WhisperStubClient()
    else:
        # Bootstrap DLLs first on Windows. Safe to call repeatedly (cached).
        from domovoi.bootstrap import register_nvidia_dlls

        register_nvidia_dlls()
        _client = FasterWhisperClient(
            model=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return _client


# Backward-compat alias used by existing code.
whisper_client: WhisperClient = WhisperStubClient()  # replaced at startup in main.py
