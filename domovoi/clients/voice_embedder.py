"""Speaker-embedding client.

Real path: 3D-Speaker's ERes2Net on ONNX Runtime
(domovoi/clients/speaker_encoder.py) makes a unit-norm 192-dim float32
embedding of each utterance's voice, from 16 kHz mono PCM: about 75 ms
for a three-second command on two threads (``voice_profile_encoder_threads``)
of a mini-PC-class core (measured on Gracemont E-cores, 2026-11; 140 ms on
one, 42 ms on four), on the speech executor beside Whisper. Its model file (25 MB) is fetched once into the
voice-models directory; until it is there, the embedder makes nothing and
voice ID names nobody (``speaker_encoder.model_problem`` says why).

Stub path: deterministic vector derived from a SHA-256 hash of the PCM
bytes, projected onto the unit sphere. Same input → same vector, so
tests that want "the same speaker said two things" reuse the same PCM
bytes; tests that want "two different speakers" use different bytes.

What is embedded is the recording's speech alone, at one level
(domovoi/voice_prep.py, :func:`~domovoi.voice_prep.for_embedding`, which
says why, measured), and with it comes how clear that speech was
(:class:`VoicePrint`), which voice ID's music gate reads.

Both paths are async-fronted: the network runs in a worker thread so it
doesn't pin the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from domovoi.clients.speaker_encoder import EMBEDDING_DIM
from domovoi.config import settings

log = logging.getLogger(__name__)

__all__ = ["EMBEDDING_DIM", "VoicePrint", "VoiceEmbedder", "get_voice_embedder"]


@dataclass(frozen=True)
class VoicePrint:
    """One recording's voice. ``current``: the unit-norm embedding of its
    speech; ``snr_db``: how far the speech stood above the rest of the
    recording (None: not measured — the stub); ``speech_sec``: how much
    speech was found."""

    current: np.ndarray
    snr_db: float | None = None
    speech_sec: float | None = None


class VoiceEmbedder(Protocol):
    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        """Return the unit-norm float32 embedding, or None if the clip is
        too short / silent / otherwise unembeddable."""
        ...

    async def embed_print(self, pcm_int16: bytes) -> VoicePrint | None:
        """The embedding and the speech's clarity, or None as above."""
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
        """The hash vector, clarity unmeasured."""
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
        return VoicePrint(current=v, snr_db=None, speech_sec=len(pcm_int16) / (16_000 * 2))


class RealVoiceEmbedder:
    """The ERes2Net encoder. The model loads once, in a worker thread: at
    boot (:meth:`preload`, from ``domovoi/speech_warmup.py``, which also
    fetches the model file when it isn't on disk yet), or on the first
    embed if that hasn't happened.

    A turn never waits for the download: without the model file an embed
    starts a fetch in the background (on the ``downloads`` executor, at
    most once every ``speaker_encoder.RETRY_AFTER_SEC`` after a failure,
    never with internet access turned off) and returns None at once."""

    def __init__(self) -> None:
        self._encoder = None
        self._load_lock = threading.Lock()
        self._fetching: asyncio.Task | None = None

    def _load(self) -> bool:
        """Load the encoder if it isn't, when its model is on disk.
        Blocking — worker threads only. The lock makes a boot preload and
        a first turn racing it load the model once between them."""
        if self._encoder is not None:
            return True
        with self._load_lock:
            if self._encoder is not None:
                return True
            from domovoi.clients import speaker_encoder as se

            if not se.model_on_disk():
                return False
            log.info("loading the voice ID speaker model (one-time)")
            self._encoder = se.SpeakerEncoder(threads=settings.voice_profile_encoder_threads)
            return True

    async def _fetch(self) -> bool:
        """Download the model on the ``downloads`` executor. False (logged)
        when it can't be had now."""
        from domovoi import egress, executors
        from domovoi.clients import speaker_encoder as se

        if egress.internet_turned_off():
            log.warning("voice ID: the speaker model isn't on this server and internet access "
                        "is turned off; nobody will be recognized by voice")
            return False
        try:
            await executors.run("downloads", se.download_model)
            return True
        except Exception as e:  # noqa: BLE001 — logged; voice ID stays off until it works
            log.warning("voice ID: the speaker model could not be downloaded (%s); nobody will "
                        "be recognized by voice until it is", e)
            return False

    def _start_fetch(self) -> None:
        from domovoi import egress
        from domovoi.clients import speaker_encoder as se

        if self._fetching is not None and not self._fetching.done():
            return
        if egress.internet_turned_off() or not se.may_retry_download():
            return
        self._fetching = asyncio.get_running_loop().create_task(self._fetch())

    async def preload(self) -> bool:
        """Fetch the model if it isn't on disk, then load the encoder and
        run it once on a second of silence, in worker threads, so the first
        turn after a boot pays neither. True when the encoder is ready."""
        from domovoi import executors
        from domovoi.clients import speaker_encoder as se

        if not se.model_on_disk() and not await self._fetch():
            return False
        return await executors.run("speech", self._preload_sync)

    def _preload_sync(self) -> bool:
        if not self._load():
            return False
        assert self._encoder is not None
        self._encoder.embed_utterance(np.zeros(16_000, dtype=np.float32))
        return True

    async def embed(self, pcm_int16: bytes) -> np.ndarray | None:
        voice = await self.embed_print(pcm_int16)
        return None if voice is None else voice.current

    async def embed_print(self, pcm_int16: bytes) -> VoicePrint | None:
        if not pcm_int16:
            return None
        # Below the configured minimum length we bail rather than emit a
        # noisy embedding.
        seconds = (len(pcm_int16) // 2) / 16_000.0
        if seconds < settings.voice_profile_min_utterance_sec:
            return None
        if self._encoder is None:
            from domovoi.clients import speaker_encoder as se

            if not se.model_on_disk():
                self._start_fetch()
                return None
        from domovoi import executors

        return await executors.run("speech", self._embed_sync, pcm_int16)

    def _embed_sync(self, pcm_int16: bytes) -> VoicePrint | None:
        from domovoi.voice_prep import for_embedding

        try:
            if not self._load():
                return None
            assert self._encoder is not None
            wav = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
            speech = for_embedding(wav)
            if speech.speech_sec < settings.voice_profile_min_utterance_sec:
                # Too little speech in it: no embedding, so nothing is named
                # or saved from it.
                log.debug("voice embedding: %.2f s of speech, too short", speech.speech_sec)
                return None
            current = _unit(self._encoder.embed_utterance(speech.audio))
            return VoicePrint(current=current, snr_db=speech.snr_db, speech_sec=speech.speech_sec)
        except Exception as e:
            log.warning("voice embedding failed: %s", e)
            return None


def _unit(emb: np.ndarray) -> np.ndarray:
    """``embed_utterance`` already returns unit-norm; renormalised against
    future changes."""
    emb = np.asarray(emb, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    return emb / norm if norm > 0 else emb


#: What voice replies say when voice ID can't run on this server
#: (:func:`voice_id_unavailable`): never a promise to recognize anyone.
UNAVAILABLE_SPOKEN = (
    "I can't recognize voices right now: my voice recognition model isn't on the server. "
    "The People page in the dashboard says why."
)


def voice_id_unavailable() -> bool:
    """Whether voice ID can't make an embedding on this server at all: the
    real embedder without its model file (domovoi/clients/speaker_encoder.py).
    The stub always can."""
    if not isinstance(get_voice_embedder(), RealVoiceEmbedder):
        return False
    from domovoi.clients.speaker_encoder import model_on_disk

    return not model_on_disk()


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
