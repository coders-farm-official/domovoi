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

Both paths are async-fronted (Resemblyzer is sync C-extension code; we
push it to a worker thread so it doesn't pin the event loop during
inference).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

import numpy as np

from domovoi.config import settings

log = logging.getLogger(__name__)

EMBEDDING_DIM = 256  # Resemblyzer's output dimension. Stub matches it.


class VoiceEmbedder(Protocol):
    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        """Return a unit-norm float32 embedding, or None if the clip is
        too short / silent / otherwise unembeddable."""
        ...


class StubVoiceEmbedder:
    """Hash-based deterministic embeddings. Same PCM → same vector.

    Tests use this to simulate "same speaker spoke twice" by feeding the
    same bytes, and "different speakers" by feeding different bytes.
    Real-speaker similarity isn't preserved (random projection), but the
    cosine-similarity comparison logic is fully exercised.
    """

    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        if not pcm_int16:
            return None
        # SHA-256 → 64 hex chars → seed an RNG → standard normal vector.
        seed = int.from_bytes(
            hashlib.sha256(pcm_int16).digest()[:8], byteorder="big", signed=False
        )
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v


class RealVoiceEmbedder:
    """Resemblyzer wrapper. Lazy model load on first embed."""

    def __init__(self) -> None:
        self._encoder = None

    def _ensure_encoder(self) -> None:
        if self._encoder is not None:
            return
        # Imported lazily so USE_STUBS=true can run without the heavy
        # resemblyzer + librosa stack installed.
        from resemblyzer import VoiceEncoder

        log.info("loading Resemblyzer VoiceEncoder (one-time)")
        self._encoder = VoiceEncoder()

    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        if not pcm_int16:
            return None
        # Resemblyzer expects float32 in [-1, 1] at 16 kHz. The Pi sends
        # int16 PCM at exactly 16 kHz so the conversion is the only
        # preprocessing needed. Below the configured minimum length we
        # bail rather than emit a noisy embedding.
        sample_count = len(pcm_int16) // 2
        seconds = sample_count / 16_000.0
        if seconds < settings.voice_profile_min_utterance_sec:
            return None

        self._ensure_encoder()

        # Push the actual NN forward pass to a worker thread — it's
        # synchronous C code that would otherwise block the event loop
        # for ~100 ms per call.
        import asyncio
        return await asyncio.to_thread(self._embed_sync, pcm_int16)

    def _embed_sync(self, pcm_int16: bytes) -> np.ndarray | None:
        try:
            wav = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
            assert self._encoder is not None
            emb = self._encoder.embed_utterance(wav)
            # `embed_utterance` already returns unit-norm; defensive
            # renormalize against future Resemblyzer behavior changes.
            norm = float(np.linalg.norm(emb))
            return emb.astype(np.float32) / norm if norm > 0 else emb.astype(np.float32)
        except Exception as e:
            log.warning("voice embedding failed: %s", e)
            return None


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
