from __future__ import annotations

import numpy as np
import pytest

from domovoi.clients.voice_embedder import (
    EMBEDDING_DIM,
    StubVoiceEmbedder,
    get_voice_embedder,
)


# Synthetic 16 kHz mono int16 PCM samples for tests. The stub embedder
# only cares that bytes hash differently between "different speakers"
# and identically for "same speaker"; the actual content is irrelevant.
_PCM_A = (np.full(16_000, 1000, dtype=np.int16)).tobytes()  # 1.0s at amplitude 1000
_PCM_B = (np.full(16_000, 2000, dtype=np.int16)).tobytes()  # different speaker proxy


@pytest.mark.asyncio
async def test_stub_embedder_returns_unit_norm_vector() -> None:
    emb = await StubVoiceEmbedder().embed(_PCM_A)
    assert emb is not None
    assert emb.shape == (EMBEDDING_DIM,)
    assert emb.dtype == np.float32
    assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_stub_embedder_deterministic_per_pcm() -> None:
    """Same PCM bytes must produce the same embedding — that's how tests
    simulate "the same person spoke twice." If hashing weren't stable,
    matching tests would silently fail."""
    e1 = await StubVoiceEmbedder().embed(_PCM_A)
    e2 = await StubVoiceEmbedder().embed(_PCM_A)
    assert e1 is not None and e2 is not None
    assert np.array_equal(e1, e2)


@pytest.mark.asyncio
async def test_stub_embedder_distinct_pcm_distinct_vector() -> None:
    e1 = await StubVoiceEmbedder().embed(_PCM_A)
    e2 = await StubVoiceEmbedder().embed(_PCM_B)
    assert e1 is not None and e2 is not None
    # Different bytes → different hash → different RNG seed → different vec.
    assert not np.array_equal(e1, e2)
    # Cosine similarity between two random unit vectors in 256 dims has
    # mean 0 and std ~1/sqrt(256) ≈ 0.0625. Anything beyond ±0.5 is
    # vanishingly unlikely; this guards against an accidental
    # always-same-vector regression.
    cos = float(np.dot(e1, e2))
    assert -0.5 < cos < 0.5


@pytest.mark.asyncio
async def test_stub_embedder_returns_none_on_empty_input() -> None:
    assert await StubVoiceEmbedder().embed(b"") is None


def test_get_voice_embedder_returns_stub_when_use_stubs_true() -> None:
    """USE_STUBS=true (set in conftest) keeps the heavy resemblyzer +
    librosa stack out of the test suite."""
    embedder = get_voice_embedder()
    assert isinstance(embedder, StubVoiceEmbedder)
