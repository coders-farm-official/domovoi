"""Whisper STT client.

Real path: `faster-whisper` (CUDA or CPU), loaded synchronously at startup.
Stub path: deterministic echo for tests.

Input contract: PCM bytes (16 kHz mono 16-bit). The WebSocket ingestion
path produces this format directly; other callers can pass a WAV via the
convenience `transcribe_wav_bytes` entrypoint.

A failed load never takes the core down. :func:`load_whisper_client` walks
a short ladder — the configured model/device/compute type, then
``whisper_cpu_fallback_model`` on cpu at int8 — and when every rung fails
the core boots WITHOUT speech recognition: :func:`get_whisper_client` then
raises :class:`SttUnavailableError`, which the streaming layer turns into
a spoken explanation, and :func:`stt_status` tells the dashboard what
happened. Before this, a CUDA default on a machine with no NVIDIA GPU
stopped the core at boot, before it had written the first-run setup code,
so the dashboard that could have fixed the setting could never be claimed.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import wave
from pathlib import Path
from typing import Any, Protocol

from domovoi.config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2  # int16

# CTranslate2 compute types by where they can run. Asking a CPU for a GPU
# type fails the load ("target device or backend do not support efficient
# float16 computation"), and int16 exists only on the CPU (Intel MKL).
# "fp16" isn't a CTranslate2 name at all, but it's the spelling people
# reach for, so it gets the same GPU-only explanation.
GPU_ONLY_COMPUTE_TYPES = frozenset(
    {"float16", "fp16", "bfloat16", "int8_float16", "int8_bfloat16"}
)
CPU_ONLY_COMPUTE_TYPES = frozenset({"int16"})

# The rung every fallback lands on, whatever the configured device.
FALLBACK_DEVICE = "cpu"
FALLBACK_COMPUTE_TYPE = "int8"


class WhisperClient(Protocol):
    async def transcribe(self, pcm_bytes: bytes) -> str: ...
    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str: ...


class SttUnavailableError(RuntimeError):
    """Speech recognition failed to load at boot, and stays off until the
    Whisper settings are fixed and the core restarts. The message is the
    load error, for logs; it is not meant to be spoken."""


class WhisperStubClient:
    """Deterministic stub — real load is skipped entirely when USE_STUBS=true."""

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return f"(stub transcribe) {len(pcm_bytes)} bytes"

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        return f"(stub transcribe-wav) {len(wav_bytes)} bytes"


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def resolve_compute_type(device: str | None, compute_type: str | None) -> str:
    """The CTranslate2 compute type to actually load with.

    ``auto`` (the default) follows the device — int8 on cpu, float16 on
    cuda — and hands anything else (faster-whisper's own ``device=auto``)
    to CTranslate2's ``auto``, which picks the fastest type that device
    supports. An explicit value is used exactly as written, so an install
    whose .env pins ``int8`` or ``float16`` behaves as it always did.
    """
    ct = _norm(compute_type)
    if ct and ct != "auto":
        return ct
    dev = _norm(device)
    if dev == "cpu":
        return "int8"
    if dev.startswith("cuda"):
        return "float16"
    return "auto"


def compute_pair_problem(device: str | None, compute_type: str | None) -> str | None:
    """Why this device + compute type pair can't load, or None when it can.
    User-facing: it is the message the config write rejects with and the
    reason the boot log gives for falling back."""
    dev = _norm(device)
    ct = resolve_compute_type(device, compute_type)
    if dev == "cpu" and ct in GPU_ONLY_COMPUTE_TYPES:
        return (
            f"{ct} is a GPU compute type — it can't run on cpu. Use int8 "
            "on cpu, or auto (int8 on cpu, float16 on cuda)."
        )
    if dev.startswith("cuda") and ct in CPU_ONLY_COMPUTE_TYPES:
        return (
            f"{ct} only runs on a CPU. On cuda use float16, int8 or auto."
        )
    return None


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
            "  If this machine has no NVIDIA GPU, set whisper_device=cpu "
            "(whisper_compute_type=auto then runs int8) in the dashboard "
            "gear -> Advanced, then restart. See docs/CPU_HOST.md.\n"
            "  If it does have one, the CUDA runtime wheels are a separate "
            'extra as of 1.0: pip install -e ".[real-clients,cuda]"'
        )
    if device == "cpu" and compute_type in GPU_ONLY_COMPUTE_TYPES:
        return (
            f"{base}\n"
            f"  {compute_type} is a GPU compute type. On CPU use "
            "whisper_compute_type=int8 or auto. See docs/CPU_HOST.md."
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
# What the last load did — see stt_status(). None until something loads.
_status: dict[str, Any] | None = None

# Error text is shown on the dashboard; a library traceback can run long.
# Room for both rungs' messages (each carries its fix hint).
_MAX_ERROR_CHARS = 1500


def _configured() -> dict[str, Any]:
    return {
        "model": settings.whisper_model,
        "device": settings.whisper_device,
        "compute_type": settings.whisper_compute_type,
        "compute_type_resolved": resolve_compute_type(
            settings.whisper_device, settings.whisper_compute_type
        ),
    }


def _status_doc(
    state: str,
    *,
    loaded: tuple[str, str, str] | None = None,
    fallback_reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "configured": _configured(),
        "loaded": (
            {"model": loaded[0], "device": loaded[1], "compute_type": loaded[2]}
            if loaded else None
        ),
        "fallback_reason": fallback_reason[:_MAX_ERROR_CHARS] if fallback_reason else None,
        "error": error[:_MAX_ERROR_CHARS] if error else None,
    }


def _cuda_device_count() -> int | None:
    """How many CUDA devices CTranslate2 can see, or None when it can't be
    asked (CTranslate2 missing, or the probe itself failed) — in which case
    the load just tries. 0 means CTranslate2 reaches no GPU at all (no
    NVIDIA card, or no driver), and a cuda load would fail the same way."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return None


def _load_real_client() -> WhisperClient | None:
    """Walk the load ladder once and record the outcome. Never raises.

    1. The configured model/device/compute type (``auto`` resolved) —
       skipped when it asks for cuda and CTranslate2 sees no CUDA device.
    2. On failure, ``whisper_cpu_fallback_model`` on cpu at int8 — unless
       that is exactly what step 1 just tried, or faster-whisper itself is
       missing (every rung would fail the same way).
    3. Otherwise no STT: the core runs, turns get a spoken explanation.
    """
    global _client, _status

    model = settings.whisper_model
    device = settings.whisper_device
    compute = resolve_compute_type(device, settings.whisper_compute_type)

    # Bootstrap DLLs first on Windows. Safe to call repeatedly (cached).
    from domovoi.bootstrap import register_nvidia_dlls

    register_nvidia_dlls()

    log.info(
        "Whisper: loading the configured model=%s device=%s compute=%s "
        "(whisper_compute_type=%s)",
        model, device, compute, settings.whisper_compute_type,
    )
    problem = compute_pair_problem(device, compute)
    if problem:
        # Tried anyway — CTranslate2 decides, and a version that quietly
        # converts keeps working — but the reason is on record if it fails.
        log.warning("Whisper: the configured pair looks wrong: %s", problem)
    if _norm(device).startswith("cuda") and _cuda_device_count() == 0:
        # faster-whisper downloads the model BEFORE it touches the device,
        # so trying anyway would pull every byte of (by default) large-v3
        # just to fail on a machine with no NVIDIA GPU — minutes of boot
        # with the core's port still closed. Skip straight to the fallback.
        first_error = (
            f"Whisper not loaded (model={model} device={device} "
            f"compute={compute}): CTranslate2 sees no CUDA device, so this "
            "machine has no NVIDIA GPU or its driver isn't installed.\n"
            "  Set whisper_device=cpu (whisper_compute_type=auto then runs "
            "int8) in the dashboard gear -> Advanced, then restart. See "
            "docs/CPU_HOST.md."
        )
        log.error("Whisper: %s", first_error)
    else:
        try:
            _client = FasterWhisperClient(model=model, device=device, compute_type=compute)
            _status = _status_doc("ok", loaded=(model, device, compute))
            return _client
        except ImportError as e:
            reason = (
                f"faster-whisper is not installed ({e}). Install the real clients: "
                'pip install -e ".[real-clients]"'
            )
            log.error("Whisper: %s", reason)
            return _mark_unavailable(reason)
        except Exception as e:
            first_error = str(e)
            log.error("Whisper: the configured model failed to load: %s", first_error)

    fb_model = (settings.whisper_cpu_fallback_model or "").strip()
    rung = (fb_model, FALLBACK_DEVICE, FALLBACK_COMPUTE_TYPE)
    if not fb_model:
        log.error("Whisper: no whisper_cpu_fallback_model set — nothing to fall back to")
        return _mark_unavailable(first_error)
    if (model.strip(), _norm(device), compute) == rung:
        log.error(
            "Whisper: the configured model IS the cpu fallback (%s on cpu, int8) "
            "— nothing left to try", fb_model,
        )
        return _mark_unavailable(first_error)

    log.warning(
        "Whisper: falling back to model=%s device=%s compute=%s "
        "(whisper_cpu_fallback_model)", *rung,
    )
    try:
        _client = FasterWhisperClient(model=rung[0], device=rung[1], compute_type=rung[2])
    except Exception as e:
        log.error("Whisper: the cpu fallback failed to load too: %s", e)
        return _mark_unavailable(f"{first_error}\nFallback {fb_model} on cpu (int8): {e}")
    _status = _status_doc("fallback", loaded=rung, fallback_reason=first_error)
    log.warning(
        "Whisper: running on the cpu fallback (%s, int8). Speech works, but "
        "the configured Whisper (%s on %s, %s) did not load — fix it on the "
        "dashboard (gear -> Advanced -> Speech-to-text) and restart.",
        fb_model, model, device, compute,
    )
    return _client


def _mark_unavailable(error: str) -> None:
    global _client, _status
    _client = None
    _status = _status_doc("unavailable", error=error)
    log.error(
        "Whisper: speech recognition is UNAVAILABLE. The core is up and the "
        "dashboard works, but satellites can't be understood until the "
        "Whisper settings are fixed (gear -> Advanced -> Speech-to-text) "
        "and the core restarts. The Models page shows the error."
    )
    return None


def load_whisper_client() -> WhisperClient | None:
    """Load Whisper (stub under USE_STUBS) and return the client, or None
    when every rung of the ladder failed. Never raises — boot calls this
    and must carry on either way."""
    global _client, _status
    if settings.use_stubs:
        _client = WhisperStubClient()
        _status = _status_doc("stub")
        return _client
    try:
        return _load_real_client()
    except Exception as e:  # pragma: no cover — the ladder catches its own
        log.exception("Whisper: load raised unexpectedly")
        return _mark_unavailable(f"{type(e).__name__}: {e}")


def get_whisper_client() -> WhisperClient:
    """The loaded client. The core loads it at boot; a first call before
    that loads it here. Raises :class:`SttUnavailableError` once a load
    has failed — without retrying, since a retry is another full model
    load inside somebody's voice turn."""
    if _client is not None:
        return _client
    if _status is None or _status.get("state") != "unavailable":
        client = load_whisper_client()
        if client is not None:
            return client
    raise SttUnavailableError(
        (_status or {}).get("error") or "speech recognition is unavailable"
    )


def stt_status() -> dict[str, Any]:
    """What speech recognition is running on, for the dashboard.

    ``state`` is ``ok`` (the configured model loaded), ``fallback`` (the
    cpu fallback model loaded instead; ``fallback_reason`` says why),
    ``unavailable`` (nothing loaded; ``error`` says why), ``stub`` (tests)
    or ``not_loaded`` (nothing has asked yet). ``configured`` is the
    settings as the core read them at boot; ``loaded`` is what is
    actually transcribing."""
    if _status is None:
        return _status_doc("not_loaded")
    return {**_status, "configured": dict(_status["configured"])}


# Backward-compat alias used by existing code.
whisper_client: WhisperClient = WhisperStubClient()  # replaced at startup in main.py
