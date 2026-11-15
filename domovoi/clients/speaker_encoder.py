"""Voice ID's speaker encoder: 3D-Speaker's ERes2Net on ONNX Runtime.

Each utterance becomes one 192-number voice embedding, made by the
ERes2Net speaker-verification model of Alibaba's 3D-Speaker project
(``iic/speech_eres2net_sv_en_voxceleb_16k``, trained on VoxCeleb2; code and
weights Apache License 2.0), as exported to ONNX by the sherpa-onnx
project. It replaced Resemblyzer (and with it torch) in 1.3.0: on the same
simulated rooms it tells voices apart about twice as often at the same
wrong-name ceiling, and it needs only ONNX Runtime, which Domovoi already
runs for Piper (scripts/eval_voice_id.py; the numbers are in
docs/RELEASE_NOTES.md, 1.3.0).

The front end is the one the model was trained with, in numpy:
:func:`kaldi_fbank` is Kaldi's 80-band log mel filter bank exactly as
3D-Speaker calls ``torchaudio.compliance.kaldi.fbank`` (samples in [-1, 1],
25 ms Povey windows every 10 ms, no dither), and the utterance's mean is
taken off every band (the model's ``feature_normalize_type: global-mean``).
``domovoi/tests/test_speaker_encoder.py`` holds it to torchaudio's output
and the embeddings to ones recorded on the ONNX Runtime the server runs.

The model file is not in the repo: it is fetched once into the voice-models
directory (``settings.voice_models_dir``, beside the Piper voices) from a
pinned URL and held to a pinned size and SHA-256 (:func:`download_model`),
through the egress gate, so under ``INTERNET_ACCESS=never`` nothing is
fetched. Without it voice ID names nobody (:func:`model_problem` says why,
and the People page shows it).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
N_MELS = 80
FRAME = 400            # 25 ms
SHIFT = 160            # 10 ms
N_FFT = 512            # the frame rounded up to a power of two (Kaldi)
EMBEDDING_DIM = 192

#: What a voice sample made by this encoder is stored as
#: (``voice_profiles.model``, ``voice_denylist.model``). A sample with any
#: other tag (Resemblyzer's ``resemblyzer-v1``/``-v2`` from before 1.3.0)
#: can't be compared with this model's embeddings and is never used.
MODEL_TAG = "eres2net-vox-v1"

# ─── the pinned model file ────────────────────────────────────────────────
#
# The sherpa-onnx project's ONNX export of 3D-Speaker's English ERes2Net,
# from its ``speaker-recongition-models`` release (the tag's spelling is
# upstream's). The digest is the one in that release's checksum.txt and of
# the file the 2026-11 comparison measured (design notes, outside the repo).
# A new export means a new pin, a new MODEL_TAG and re-enrolment, never a
# looser check.

MODEL_FILE = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    + MODEL_FILE
)
MODEL_SHA256 = "c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5"
MODEL_BYTES = 26_485_263


class ModelUnavailable(RuntimeError):
    """The speaker model isn't on disk and couldn't be fetched."""


def models_dir() -> Path:
    from domovoi.config import settings

    return Path(settings.voice_models_dir)


def model_path() -> Path:
    return models_dir() / MODEL_FILE


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# (path, size, mtime_ns) -> the digest matched; checking 25 MB takes ~0.1 s,
# so a file is hashed once per change rather than on every question.
_verified: tuple[str, int, int] | None = None


def model_on_disk(path: Path | None = None) -> bool:
    """Whether the pinned model is on disk: its size, and its SHA-256 once
    per change of the file."""
    global _verified
    path = path or model_path()
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != MODEL_BYTES:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns)
    if _verified == key:
        return True
    if _sha256_file(path) != MODEL_SHA256:
        return False
    _verified = key
    return True


_download_lock = threading.Lock()
#: The last fetch that failed: (when, why). The embedder retries no sooner
#: than :data:`RETRY_AFTER_SEC` after it, so a house without internet isn't
#: asked to fetch on every turn.
_last_failure: tuple[float, str] | None = None
RETRY_AFTER_SEC = 600.0


def download_model(dest_dir: Path | None = None) -> Path:
    """Fetch the pinned model into ``dest_dir`` (the voice-models directory)
    unless it is there already: through the egress gate and net_safety,
    reading at most :data:`MODEL_BYTES`, moved into place only once its
    size and SHA-256 match. Raises ``egress.InternetTurnedOff`` under
    ``never`` (before any request), :class:`ModelUnavailable` otherwise."""
    global _last_failure
    from domovoi import egress

    dest_dir = dest_dir or models_dir()
    target = dest_dir / MODEL_FILE
    with _download_lock:
        if model_on_disk(target):
            return target
        egress.require_destination(MODEL_URL)
        dest_dir.mkdir(parents=True, exist_ok=True)
        from domovoi.satellite_media.fetchers import _fetch_pinned

        log.info("voice ID: downloading the speaker model (%.0f MB, one-time) from %s",
                 MODEL_BYTES / 1e6, MODEL_URL)
        try:
            _fetch_pinned(MODEL_URL, target, sha256=MODEL_SHA256, size=MODEL_BYTES)
        except egress.InternetTurnedOff:
            raise
        except Exception as e:
            _last_failure = (time.monotonic(), str(e) or e.__class__.__name__)
            raise ModelUnavailable(f"the speaker model could not be downloaded: {e}") from e
        _last_failure = None
        log.info("voice ID: speaker model verified, in %s", target)
        return target


def may_retry_download() -> bool:
    """Whether a fetch may be tried now (none failed in the last
    :data:`RETRY_AFTER_SEC`)."""
    return _last_failure is None or time.monotonic() - _last_failure[0] >= RETRY_AFTER_SEC


def model_problem() -> str | None:
    """Why voice ID can't run, in words for the household; None when the
    model is on disk."""
    from domovoi import egress

    if model_on_disk():
        return None
    if egress.internet_turned_off():
        return (
            "Voice recognition needs a 25 MB model that isn't on this server yet, and internet "
            "access is turned off (Settings → Privacy → Internet), so Domovoi can't download it. "
            "Until it can, nobody is recognized by voice."
        )
    if _last_failure is not None:
        return (
            "Voice recognition needs a 25 MB model that Domovoi couldn't download "
            f"({_last_failure[1]}). It tries again every few minutes; until then nobody is "
            "recognized by voice."
        )
    return (
        "Voice recognition needs a 25 MB model that isn't on this server yet. Domovoi "
        "downloads it when it starts; until then nobody is recognized by voice."
    )


# ─── the front end ────────────────────────────────────────────────────────


def _mel(f: np.ndarray) -> np.ndarray:
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def mel_banks(n_mels: int = N_MELS) -> np.ndarray:
    """Kaldi's triangular mel filters, 20 Hz to Nyquist, on the FFT's
    ``N_FFT // 2 + 1`` bins (the Nyquist bin unweighted):
    ``(n_mels, N_FFT // 2 + 1)``."""
    lo, hi = _mel(20.0), _mel(SAMPLE_RATE / 2)
    centers = np.linspace(lo, hi, n_mels + 2)
    bins = _mel(np.arange(N_FFT // 2) * SAMPLE_RATE / N_FFT)
    left, mid, right = centers[:-2, None], centers[1:-1, None], centers[2:, None]
    w = np.maximum(0.0, np.minimum((bins - left) / (mid - left), (right - bins) / (right - mid)))
    return np.concatenate([w, np.zeros((n_mels, 1))], axis=1)


_BANKS: np.ndarray | None = None
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME) / (FRAME - 1))) ** 0.85   # Povey


def kaldi_fbank(wav: np.ndarray) -> np.ndarray:
    """``torchaudio.compliance.kaldi.fbank(wav, num_mel_bins=80, dither=0)``
    in numpy, as 3D-Speaker computes its features: 16 kHz samples in
    [-1, 1]; 25 ms frames every 10 ms, only whole ones (``snip_edges``);
    each frame's DC removed, pre-emphasis 0.97, Povey window, 512-point
    power spectrum, 80 mel bands, natural log floored at float32's epsilon.
    ``(frames, 80)`` float32; no frames for less than 25 ms of audio."""
    global _BANKS
    if _BANKS is None:
        _BANKS = mel_banks()
    x = np.asarray(wav, dtype=np.float64)
    if x.size < FRAME:
        return np.zeros((0, N_MELS), dtype=np.float32)
    n = 1 + (x.size - FRAME) // SHIFT
    frames = np.lib.stride_tricks.as_strided(x, (n, FRAME), (x.strides[0] * SHIFT, x.strides[0]))
    fr = frames - frames.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] - 0.97 * fr[:, :1], fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    power = np.abs(np.fft.rfft(fr * _WINDOW, N_FFT)) ** 2
    return np.log(np.maximum(power @ _BANKS.T, np.finfo(np.float32).eps)).astype(np.float32)


def features(wav: np.ndarray) -> np.ndarray:
    """The model's input for one utterance: :func:`kaldi_fbank` with the
    utterance's mean taken off each band, ``(frames, 80)``."""
    f = kaldi_fbank(wav)
    return f - f.mean(axis=0, keepdims=True) if len(f) else f


# ─── the network ──────────────────────────────────────────────────────────


class SpeakerEncoder:
    """The ERes2Net session. :meth:`embed_utterance` takes 16 kHz float32
    audio in [-1, 1] and returns a unit-norm 192-dim float32 vector.

    ``threads``: ONNX Runtime's intra-op threads. The embedding runs on
    the speech executor beside Whisper; two threads keep it well under a
    decode's time on a small CPU without taking every core from it."""

    def __init__(self, path: Path | None = None, threads: int = 2) -> None:
        import onnxruntime as ort

        path = path or model_path()
        if not model_on_disk(path):
            raise ModelUnavailable(f"the speaker model is not at {path}")
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        self._session = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        dim = self._session.get_outputs()[0].shape[-1]
        if dim != EMBEDDING_DIM:
            raise ModelUnavailable(f"the speaker model gives {dim} numbers, not {EMBEDDING_DIM}")

    def embed_utterance(self, wav: np.ndarray) -> np.ndarray:
        feats = features(wav)
        if len(feats) == 0:
            raise ValueError("too little audio to embed")
        out = self._session.run(None, {self._input: feats[None]})[0].reshape(-1)
        out = out.astype(np.float32)
        norm = float(np.linalg.norm(out))
        return out / norm if norm > 0 else out
