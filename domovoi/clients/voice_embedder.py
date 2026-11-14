"""Speaker-embedding client.

Real path: Resemblyzer's pretrained ``VoiceEncoder`` produces 256-dim
float32 embeddings from 16 kHz mono PCM. Sub-100 ms per utterance on
modern CPUs; we don't need GPU here since the model is tiny and the
call frequency is bounded by user speech (a few per minute, not per
second).

Stub path: deterministic vector derived from a SHA-256 hash of the PCM
bytes, projected onto the unit sphere. Same input → same vector, so
tests that want "the same speaker said two things" reuse the same PCM
bytes; tests that want "two different speakers" use different bytes.

Two embeddings per recording (1.3.0, :class:`VoicePrint`): the *current*
one, of the speech alone at one loudness (domovoi/voice_prep.py), which
every new sample is kept in (``resemblyzer-v2``), and the *legacy* one, of
the whole recording as it came (``resemblyzer-v1``), which voices enrolled
before 1.3.0 are still matched with until a room saves a current sample of
theirs (domovoi/voice_identifier.py, "Two embeddings"). Each is one pass of
the small encoder (~25 ms on a CPU).

Both paths are async-fronted (Resemblyzer is sync C-extension code; we
push it to a worker thread so it doesn't pin the event loop during
inference).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from domovoi.config import settings

log = logging.getLogger(__name__)

EMBEDDING_DIM = 256  # Resemblyzer's output dimension. Stub matches it.


@dataclass(frozen=True)
class VoicePrint:
    """One recording's voice. ``current``: the unit-norm embedding of its
    speech alone, at one loudness; ``legacy``: of the whole recording as it
    came (None when it couldn't be made); ``snr_db``: how far the speech
    stood above the rest of the recording (None: not measured — the stub);
    ``speech_sec``: how much speech was found."""

    current: np.ndarray
    legacy: np.ndarray | None = None
    snr_db: float | None = None
    speech_sec: float | None = None


class VoiceEmbedder(Protocol):
    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        """Return the unit-norm float32 *current* embedding, or None if the
        clip is too short / silent / otherwise unembeddable."""
        ...

    async def embed_print(self, pcm_int16: bytes) -> VoicePrint | None:
        """Both embeddings and the speech's clarity, or None as above."""
        ...


class StubVoiceEmbedder:
    """Hash-based deterministic embeddings. Same PCM → same vector.

    Tests use this to simulate "same speaker spoke twice" by feeding the
    same bytes, and "different speakers" by feeding different bytes.
    Real-speaker similarity isn't preserved (random projection), but the
    cosine-similarity comparison logic is fully exercised.
    """

    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        voice = await self.embed_print(pcm_int16)
        return None if voice is None else voice.current

    async def embed_print(self, pcm_int16: bytes) -> VoicePrint | None:
        """The same hash vector as both embeddings (a stub voice sounds the
        same either way), clarity unmeasured."""
        if not pcm_int16:
            return None
        # SHA-256 → 64 hex chars → seed an RNG → standard normal vector.
        seed = int.from_bytes(
            hashlib.sha256(pcm_int16).digest()[:8], byteorder="big", signed=False
        )
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        norm = float(np.linalg.norm(v))
        v = v / norm if norm > 0 else v
        return VoicePrint(current=v, legacy=v, snr_db=None,
                          speech_sec=len(pcm_int16) / (16_000 * 2))


class RealVoiceEmbedder:
    """Resemblyzer wrapper. The model loads once, in a worker thread: at
    boot (:meth:`preload`, from ``domovoi/speech_warmup.py``), or on the
    first embed if that hasn't happened yet.

    Never on the event loop: the load is ~600 ms of torch on a CPU host,
    and the loop serves every room's socket — a load there (as the first
    turn after a restart used to do) froze them all for that long."""

    def __init__(self) -> None:
        self._encoder = None
        self._load_lock = threading.Lock()

    def _ensure_encoder(self) -> None:
        """Load the encoder if it isn't. Blocking — worker threads only.
        The lock makes a boot preload and a first turn racing it load the
        model once between them."""
        if self._encoder is not None:
            return
        with self._load_lock:
            if self._encoder is not None:
                return
            # Imported lazily so USE_STUBS=true can run without torch.
            # Resemblyzer's encoder and weights, without librosa (soxr,
            # soundfile): domovoi/clients/speaker_encoder.py, 2026-10-15.
            from domovoi.clients.speaker_encoder import SpeakerEncoder

            log.info("loading Resemblyzer VoiceEncoder (one-time)")
            self._encoder = SpeakerEncoder()

    async def preload(self) -> bool:
        """Load the encoder and run it once on a short silence, in a worker
        thread, so the first turn after a boot pays neither the load nor
        the first forward pass. True when the encoder is ready."""
        from domovoi import executors

        return await executors.run("speech", self._preload_sync)

    def _preload_sync(self) -> bool:
        self._ensure_encoder()
        assert self._encoder is not None
        self._encoder.embed_utterance(np.zeros(16_000, dtype=np.float32))
        return True

    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        voice = await self.embed_print(pcm_int16)
        return None if voice is None else voice.current

    async def embed_print(self, pcm_int16: bytes) -> VoicePrint | None:
        if not pcm_int16:
            return None
        # Resemblyzer expects float32 in [-1, 1] at 16 kHz. The Pi sends
        # int16 PCM at exactly 16 kHz. Below the configured minimum length
        # we bail rather than emit a noisy embedding.
        sample_count = len(pcm_int16) // 2
        seconds = sample_count / 16_000.0
        if seconds < settings.voice_profile_min_utterance_sec:
            return None

        # The load (when the boot preload hasn't done it) and the NN
        # forward passes run in a worker thread — synchronous torch code
        # that would otherwise block the event loop.
        from domovoi import executors

        return await executors.run("speech", self._embed_sync, pcm_int16)

    def _embed_sync(self, pcm_int16: bytes) -> VoicePrint | None:
        from domovoi.voice_prep import prepare

        try:
            self._ensure_encoder()
            assert self._encoder is not None
            wav = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
            legacy = _unit(self._encoder.embed_utterance(wav))
            speech = prepare(wav)
            if speech.speech_sec < settings.voice_profile_min_utterance_sec:
                # Too little speech once the silence around it is gone: no
                # current embedding, so nothing is named or saved from it.
                log.debug("voice embedding: %.2f s of speech, too short", speech.speech_sec)
                return None
            current = _unit(self._encoder.embed_utterance(speech.audio))
            return VoicePrint(current=current, legacy=legacy,
                              snr_db=speech.snr_db, speech_sec=speech.speech_sec)
        except Exception as e:
            log.warning("voice embedding failed: %s", e)
            return None


def _unit(emb: np.ndarray) -> np.ndarray:
    """``embed_utterance`` already returns unit-norm; renormalised against
    future changes."""
    emb = np.asarray(emb, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    return emb / norm if norm > 0 else emb


_client: VoiceEmbedder | None = None


def get_voice_embedder() -> VoiceEmbedder:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = StubVoiceEmbedder()
    else:
        _client = RealVoiceEmbedder()
    return _client
