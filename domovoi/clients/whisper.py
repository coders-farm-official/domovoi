"""Whisper STT client.

Real path: `faster-whisper` (CUDA or CPU), loaded synchronously at startup.
Stub path: deterministic echo for tests.

Input contract: PCM bytes (16 kHz mono 16-bit). The WebSocket ingestion
path produces this format directly; other callers can pass a WAV via the
convenience `transcribe_wav_bytes` entrypoint.

On an English-only (``.en``) model, a capture of up to
:data:`SHORT_WINDOW_MAX_AUDIO_SEC` — every spoken command — is decoded on a
:data:`SHORT_WINDOW_SEC` window by :class:`ShortWindowDecoder`, calling
CTranslate2 directly, instead of the 30 s window faster-whisper pads every
call to. Longer captures, multilingual models, and any short decode that
comes back blank, unsure or failed take faster-whisper's own path exactly
as before (``whisper_short_window_enabled``).

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
import os
import tempfile
import time
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from domovoi.config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2  # int16

# ─── Short-window decoding ─────────────────────────────────────────────
# faster-whisper pads every call's mel spectrogram to 3000 frames (30 s) —
# its chunk_length option doesn't change that — so the encoder, most of a
# CPU decode, does the same work for "stop" as for 29 s of dictation.
# CTranslate2's Whisper encoder takes a shorter input, so a capture of up
# to SHORT_WINDOW_MAX_AUDIO_SEC is decoded on a SHORT_WINDOW_SEC window:
# the same features, zero-padded to 1000 frames instead of 3000. Measured
# with small.en int8 on 8 threads (oneDNN, the backend an AMD CPU gets):
# ~890 ms -> ~230 ms, with command accuracy equal on a 333-clip corpus.
# Shorter windows lose accuracy fast (5 s: 158 of 252 tier A/B commands
# right against 221; a window sized to the clip, 161), hence one fixed
# window, and at least a second of it left as padding.
SHORT_WINDOW_SEC = 10
SHORT_WINDOW_MAX_AUDIO_SEC = 9.0
# The window the 30 s path decodes on, for the per-turn record.
FULL_WINDOW_SEC = 30
# faster-whisper's own transcribe() defaults — what the 30 s path runs
# with, so the short decode judges its result by the same rules.
BEAM_SIZE = 5
NO_SPEECH_THRESHOLD = 0.6
LOG_PROB_THRESHOLD = -1.0
COMPRESSION_RATIO_THRESHOLD = 2.4
# A capture of 9 s holds a few dozen tokens of speech; a decode that runs
# to this cap is looping, and goes to the 30 s path (which has the
# temperature fallback this path leaves out).
SHORT_MAX_NEW_TOKENS = 128

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


async def transcribe_with_window(client: Any, pcm_bytes: bytes) -> tuple[str, int | None]:
    """``client.transcribe`` plus the mel window (seconds) the transcript
    was decoded on — :data:`SHORT_WINDOW_SEC` or :data:`FULL_WINDOW_SEC` —
    when the client says (the real one does; a stub or a test double
    doesn't, and gets None)."""
    detailed = getattr(client, "transcribe_with_window", None)
    if callable(detailed):
        text, window = await detailed(pcm_bytes)
        return text, window
    return await client.transcribe(pcm_bytes), None


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


def _physical_cores() -> int | None:
    """Physical core count, or None when psutil can't say (it returns
    None on some virtualised hosts, and may be missing entirely)."""
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
    except Exception:
        return None
    return int(n) if n else None


def resolve_cpu_threads(configured: int | None = None) -> int:
    """How many threads CTranslate2 gets when Whisper runs on the CPU.

    ``configured`` is ``whisper_cpu_threads`` (read from settings when not
    given). A positive value is used as written. ``0`` (the default) means
    one thread per PHYSICAL core — the encoder is dense matrix work, and a
    second hyperthread on the same core adds little but contention — and
    where that can't be read, half the logical count. Never below 1.

    faster-whisper's own default is 4 threads on any machine, which is what
    every CPU host ran before this setting existed: on the 8-core Domovoi
    server half the cores sat idle while a person waited for a transcript.
    """
    n = settings.whisper_cpu_threads if configured is None else configured
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    auto = _physical_cores() or (os.cpu_count() or 2) // 2
    return max(1, auto)


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


def compression_ratio(text: str) -> float:
    """faster-whisper's repetition measure: how well the text compresses.
    Above :data:`COMPRESSION_RATIO_THRESHOLD` it is looping."""
    raw = text.encode("utf-8")
    return len(raw) / len(zlib.compress(raw)) if raw else 0.0


@dataclass(frozen=True)
class ShortDecode:
    """One short-window decode, with what faster-whisper would judge it by."""

    text: str
    avg_logprob: float
    no_speech_prob: float
    # Ended on its own end-of-text rather than at SHORT_MAX_NEW_TOKENS.
    complete: bool

    @property
    def verdict(self) -> str:
        """``ok`` (use it), ``blank`` (no speech in it) or ``unsure`` —
        faster-whisper's own thresholds, one of them applied more strictly:

        * no speech when ``no_speech_prob`` is above 0.6 and the average
          log-probability isn't above -1.0 (faster-whisper skips such a
          window, and never retries it at a higher temperature);
        * unsure when ``no_speech_prob`` is above 0.6 at all. faster-whisper
          would keep such a window on a good enough log-probability, but on
          the short window that is how noise with nobody talking comes back
          as "you": every noise-only capture tried did it, while no spoken
          command in the 333-clip corpus got above 0.3. The 30 s window gets
          the say instead, so a noise capture ends as it did before;
        * unsure when the text is repetitive (compression ratio above 2.4)
          or unlikely (average log-probability below -1.0) — where
          faster-whisper would decode again at a higher temperature — or
          when the decode ran to the token cap;
        * and blank when what is left holds no letter or digit.

        Anything but ``ok`` sends the capture to the 30 s path.
        """
        if self.no_speech_prob > NO_SPEECH_THRESHOLD:
            return "blank" if self.avg_logprob <= LOG_PROB_THRESHOLD else "unsure"
        if (
            not self.complete
            or self.avg_logprob < LOG_PROB_THRESHOLD
            or compression_ratio(self.text) > COMPRESSION_RATIO_THRESHOLD
        ):
            return "unsure"
        if not any(ch.isalnum() for ch in self.text):
            return "blank"
        return "ok"


class ShortWindowDecoder:
    """Decode a short capture on a :data:`SHORT_WINDOW_SEC` mel window by
    calling CTranslate2's Whisper directly (see the constants above).
    English-only (``.en``) models only: a multilingual model detects the
    language from the same window, and on a short one that goes wrong
    (multilingual tiny: "Resume the music." came back half in Cyrillic,
    "Stop." as "Ciao!", and commands repeated themselves; tier A/B
    commands right fell from 211 to 175 of 252), so those keep the 30 s
    window (:meth:`for_model` refuses them).

    What faster-whisper's ``transcribe(path, beam_size=5)`` does for the
    first (and, for a command, only) window, and what this keeps:

    * the features: the same extractor, the capture's own frames, then
      zero padding — to 1000 frames here instead of 3000;
    * the decode: English, beam 5, patience 1, no length or repetition
      penalty, blank and non-speech tokens suppressed (faster-whisper's
      own list);
    * the text: every text token decoded, stripped — what joining a
      single window's segments gives.

    And what it leaves out, on purpose:

    * the temperature fallback — a result faster-whisper would decode again
      at a higher temperature is ``unsure`` here, and the caller takes the
      30 s path, fallback and all (:attr:`ShortDecode.verdict`);
    * timestamp tokens — a command is one segment, and decoding without
      them is the ``<|notimestamps|>`` prompt, a few tokens fewer;
    * the temp WAV and PyAV decode — the PCM is converted in memory, the
      same int16 → float32 scaling.

    Built by :meth:`for_model` from a loaded ``faster_whisper.WhisperModel``;
    the parts are injectable so the logic tests without faster-whisper.
    """

    def __init__(
        self,
        *,
        feature_extractor: Callable[[np.ndarray], np.ndarray],
        model: Any,
        encode: Callable[[np.ndarray], Any],
        tokenizer: Any,
        hop_length: int = 160,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._features = feature_extractor
        self._model = model
        self._encode = encode
        self._tok = tokenizer
        self.hop_length = int(hop_length)
        self.frames = int(SHORT_WINDOW_SEC * sample_rate) // self.hop_length
        self.max_samples = int(SHORT_WINDOW_MAX_AUDIO_SEC * sample_rate)
        self._prompt = [*tokenizer.sot_sequence, tokenizer.no_timestamps]
        # faster-whisper's get_suppressed_tokens for suppress_tokens=[-1].
        ids = set(tokenizer.non_speech_tokens)
        ids.update((tokenizer.transcribe, tokenizer.translate, tokenizer.sot,
                    tokenizer.sot_prev, tokenizer.sot_lm, tokenizer.no_speech))
        self._suppress = sorted(i for i in ids if isinstance(i, int))

    @classmethod
    def for_model(cls, whisper_model: Any) -> "ShortWindowDecoder":
        """The decoder for a loaded ``faster_whisper.WhisperModel``.
        Raises ValueError for a multilingual model (see above)."""
        if whisper_model.model.is_multilingual:
            raise ValueError(
                "short-window decoding is for English-only (.en) models; a "
                "multilingual model keeps the 30 s window"
            )
        from faster_whisper.tokenizer import Tokenizer

        fe = whisper_model.feature_extractor
        return cls(
            feature_extractor=fe,
            model=whisper_model.model,
            encode=whisper_model.encode,
            tokenizer=Tokenizer(whisper_model.hf_tokenizer, False),
            hop_length=int(getattr(fe, "hop_length", 160) or 160),
            sample_rate=int(getattr(fe, "sampling_rate", SAMPLE_RATE) or SAMPLE_RATE),
        )

    def fits(self, pcm_bytes: bytes) -> bool:
        """Whether a capture this long is decoded here."""
        return 0 < len(pcm_bytes) // SAMPLE_WIDTH_BYTES <= self.max_samples

    def window_features(self, audio: np.ndarray) -> np.ndarray:
        """The capture's own mel frames, zero-padded to the window. The
        extractor may return frames past the audio (older faster-whisper
        pads the waveform itself); only the audio's are kept, exactly the
        slice faster-whisper's first window takes."""
        feats = np.asarray(self._features(audio))
        content = min(len(audio) // self.hop_length, feats.shape[-1], self.frames)
        out = np.zeros((feats.shape[0], self.frames), dtype=np.float32)
        out[:, :content] = feats[:, :content]
        return out

    def decode(self, pcm_bytes: bytes) -> ShortDecode:
        """Decode one capture (16 kHz mono int16). Raises whatever
        CTranslate2 raises; the caller falls back on any exception."""
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        encoded = self._encode(self.window_features(audio))
        prompt = self._prompt
        result = self._model.generate(
            encoded,
            [prompt],
            beam_size=BEAM_SIZE,
            patience=1,
            length_penalty=1,
            repetition_penalty=1,
            no_repeat_ngram_size=0,
            max_length=len(prompt) + SHORT_MAX_NEW_TOKENS,
            return_scores=True,
            return_no_speech_prob=True,
            suppress_blank=True,
            suppress_tokens=self._suppress,
        )[0]
        ids = list(result.sequences_ids[0])
        n = len(ids)
        # faster-whisper's recovery of the average log-probability from
        # CTranslate2's length-normalised score (length_penalty 1).
        avg_logprob = float(result.scores[0]) * n / (n + 1)
        return ShortDecode(
            text=self._tok.decode(ids).strip(),
            avg_logprob=avg_logprob,
            no_speech_prob=float(result.no_speech_prob),
            complete=n < SHORT_MAX_NEW_TOKENS - 1,
        )


def _ctranslate2_version() -> str:
    try:
        import ctranslate2

        return str(ctranslate2.__version__)
    except Exception:
        return "unknown"


class FasterWhisperClient:
    """Wraps `faster_whisper.WhisperModel` with an async interface.

    The model load is synchronous (and slow — ~30 s first run for large-v3).
    Transcription runs in a threadpool so it doesn't block the event loop.
    A short capture goes to the :class:`ShortWindowDecoder` first (see the
    module docstring); ``short_window`` is None when this CTranslate2 can't
    run one, and then every capture takes the 30 s path.
    """

    def __init__(self, model: str, device: str, compute_type: str) -> None:
        # Import lazily so USE_STUBS=true doesn't require faster-whisper installed.
        from faster_whisper import WhisperModel

        # CPU threads only on a cpu device (whisper_cpu_threads). On cuda
        # the argument is left out, exactly as before, so a GPU host loads
        # the same way it always did.
        kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
        self.cpu_threads: int | None = None
        if _norm(device) == "cpu":
            self.cpu_threads = resolve_cpu_threads()
            kwargs["cpu_threads"] = self.cpu_threads
        log.info(
            "loading Whisper model=%s device=%s compute=%s%s", model, device, compute_type,
            f" cpu_threads={self.cpu_threads}" if self.cpu_threads else "",
        )
        try:
            self._model = WhisperModel(model, **kwargs)
        except Exception as e:
            raise RuntimeError(_load_hint(model, device, compute_type, e)) from e
        log.info("Whisper ready")
        self.short_window: ShortWindowDecoder | None = self._load_short_window()

    def _load_short_window(self) -> ShortWindowDecoder | None:
        """Build the short-window decoder and run it once on a second of
        silence: that proves this CTranslate2 takes a window shorter than
        3000 frames (4.x does; the check keeps an older or odd build on the
        30 s path instead of failing turns), and it warms the encoder for
        the window's shape so the first command after a boot doesn't pay
        for it. Built whatever ``whisper_short_window_enabled`` says, so
        the setting applies without a restart."""
        t0 = time.perf_counter()
        if getattr(getattr(self._model, "model", None), "is_multilingual", False) is True:
            log.info(
                "Whisper: multilingual model — every capture decodes on the 30 s "
                "window (short-window decoding is for English-only .en models)"
            )
            return None
        try:
            short = ShortWindowDecoder.for_model(self._model)
        except Exception as e:
            log.warning(
                "Whisper: short-window decoding is unavailable: %s: %s. Every "
                "capture uses the 30 s window.", type(e).__name__, e,
            )
            return None
        try:
            short.decode(bytes(SAMPLE_RATE * SAMPLE_WIDTH_BYTES))
        except Exception as e:
            log.warning(
                "Whisper: short-window decoding is unavailable (ctranslate2 %s): "
                "%s: %s. Every capture uses the 30 s window.",
                _ctranslate2_version(), type(e).__name__, e,
            )
            return None
        log.info(
            "Whisper: short-window decoding ready — captures up to %.0f s decode "
            "on a %d s window (warm-up %d ms)",
            SHORT_WINDOW_MAX_AUDIO_SEC, SHORT_WINDOW_SEC,
            int((time.perf_counter() - t0) * 1000),
        )
        return short

    async def transcribe(self, pcm_bytes: bytes) -> str:
        """Transcribe raw 16 kHz mono int16 PCM bytes."""
        text, _window = await asyncio.to_thread(self._transcribe_pcm_sync, pcm_bytes)
        return text

    async def transcribe_with_window(self, pcm_bytes: bytes) -> tuple[str, int]:
        """:meth:`transcribe`, plus the window (seconds) the text came from."""
        return await asyncio.to_thread(self._transcribe_pcm_sync, pcm_bytes)

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        """Transcribe a complete WAV file's bytes."""
        return await asyncio.to_thread(self._transcribe_wav_sync, wav_bytes)

    def _transcribe_pcm_sync(self, pcm_bytes: bytes) -> tuple[str, int]:
        """The short window when it applies and its result is usable,
        else faster-whisper's 30 s path. Returns (text, window seconds)."""
        short = self.short_window
        if short is not None and settings.whisper_short_window_enabled and short.fits(pcm_bytes):
            try:
                result = short.decode(pcm_bytes)
            except Exception as e:
                log.warning(
                    "Whisper: short-window decode failed (%s: %s); using the 30 s window",
                    type(e).__name__, e,
                )
            else:
                verdict = result.verdict
                if verdict == "ok":
                    return result.text, SHORT_WINDOW_SEC
                log.info(
                    "Whisper: short-window decode came back %s (%.1f s of audio, "
                    "avg_logprob %.2f, no_speech %.2f); using the 30 s window",
                    verdict, len(pcm_bytes) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES),
                    result.avg_logprob, result.no_speech_prob,
                )
        return self._transcribe_full_sync(pcm_bytes), FULL_WINDOW_SEC

    def _transcribe_full_sync(self, pcm_bytes: bytes) -> str:
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


def short_window_active() -> bool | None:
    """Whether short captures are decoded on the short window right now:
    the setting is on and the loaded model could run one. None when no
    real model is loaded (a stub, or nothing yet)."""
    if not hasattr(_client, "short_window"):
        return None
    return bool(settings.whisper_short_window_enabled and _client.short_window is not None)


def whisper_runtime() -> dict[str, Any]:
    """What is transcribing right now, flat, for the per-turn timing record
    and the latency summary: ``{state, model, device, compute_type,
    cpu_threads, short_window}``. The model fields are None unless
    something loaded (``stub`` included); ``cpu_threads`` is None off the
    cpu, and for a client that doesn't report one; ``short_window`` is
    :func:`short_window_active`."""
    st = stt_status()
    loaded = st.get("loaded") or {}
    threads = getattr(_client, "cpu_threads", None)
    return {
        "state": st.get("state"),
        "model": loaded.get("model"),
        "device": loaded.get("device"),
        "compute_type": loaded.get("compute_type"),
        "cpu_threads": threads if isinstance(threads, int) else None,
        "short_window": short_window_active(),
    }


# Backward-compat alias used by existing code.
whisper_client: WhisperClient = WhisperStubClient()  # replaced at startup in main.py
