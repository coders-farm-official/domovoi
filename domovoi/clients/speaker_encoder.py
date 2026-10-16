"""Resemblyzer's speaker encoder, without librosa.

Voice ID embeds each utterance with Resemblyzer's pretrained encoder
(resemble-ai/Resemblyzer, Apache License 2.0, Copyright 2019 Resemble AI).
Importing the ``resemblyzer`` package imports its audio helpers, which
import librosa, and librosa imports soxr and soundfile (both carrying LGPL
libraries) and numba when it loads. The encoder needs one thing from them:
a 40-band mel spectrogram. So this module computes that spectrogram with
numpy (the same STFT and Slaney mel filter bank: within 3e-7 of librosa's)
and runs Resemblyzer's network — a 3-layer LSTM and a linear layer, as in
its ``VoiceEncoder`` — with the weights the package ships
(``resemblyzer/pretrained.pt``), found with :func:`importlib.util.find_spec`
so the package itself is never imported. The partial-utterance slicing
and averaging follow ``VoiceEncoder.embed_utterance``. The embeddings are
Resemblyzer's (``domovoi/tests/test_speaker_encoder.py`` holds them to
ones it computed), so enrolled voices keep matching.

``resemblyzer`` is installed without its dependencies (``pip install
--no-deps``) for the weights alone; librosa, webrtcvad and scipy are not
needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 16_000
N_FFT = 400            # 25 ms windows
HOP = 160              # 10 ms steps
N_MELS = 40
PARTIAL_FRAMES = 160   # 1.6 s of frames per partial utterance
HIDDEN = 256
EMBEDDING = 256
LAYERS = 3


# ─── the mel spectrogram ─────────────────────────────────────────────────


def _hz_to_mel(f: np.ndarray) -> np.ndarray:
    """Slaney's mel scale: linear to 1 kHz, logarithmic above."""
    f = np.asanyarray(f, dtype=np.float64)
    f_sp = 200.0 / 3
    min_log_hz, min_log_mel, logstep = 1000.0, 1000.0 / f_sp, np.log(6.4) / 27.0
    return np.where(f >= min_log_hz,
                    min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep,
                    f / f_sp)


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    m = np.asanyarray(m, dtype=np.float64)
    f_sp = 200.0 / 3
    min_log_hz, min_log_mel, logstep = 1000.0, 1000.0 / f_sp, np.log(6.4) / 27.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)


def mel_filters(sr: int = SAMPLE_RATE, n_fft: int = N_FFT, n_mels: int = N_MELS) -> np.ndarray:
    """Triangular filters from 0 Hz to Nyquist, each normalised to unit
    area (Slaney): shape ``(n_mels, 1 + n_fft // 2)``."""
    fft_freqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(sr / 2.0), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fft_freqs)
    weights = np.zeros((n_mels, len(fft_freqs)))
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return weights.astype(np.float32)


_FILTERS: np.ndarray | None = None


def mel_spectrogram(wav: np.ndarray) -> np.ndarray:
    """Power mel spectrogram of 16 kHz float audio, ``(frames, 40)``
    float32: periodic Hann windows of 400 samples every 160, centred (the
    signal zero-padded by half a window each side), |FFT|², the filter
    bank. Not a log-mel: the encoder takes it as it is."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = mel_filters()
    y = np.ascontiguousarray(wav, dtype=np.float32)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)
    y = np.pad(y, N_FFT // 2, mode="constant")
    n = 1 + (len(y) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(y, (n, N_FFT), (y.strides[0] * HOP, y.strides[0]))
    power = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    return (power.astype(np.float32) @ _FILTERS.T).astype(np.float32)


# ─── partial utterances ──────────────────────────────────────────────────


def partial_slices(n_samples: int, rate: float = 1.3, min_coverage: float = 0.75) -> tuple[list[slice], list[slice]]:
    """Where to cut an utterance into overlapping 1.6 s partials, about
    ``rate`` a second: (waveform slices, mel-frame slices). The last
    partial is dropped when less than ``min_coverage`` of it is audio,
    unless it is the only one."""
    samples_per_frame = SAMPLE_RATE * 10 // 1000
    n_frames = int(np.ceil((n_samples + 1) / samples_per_frame))
    frame_step = int(np.round((SAMPLE_RATE / rate) / samples_per_frame))
    wav_slices, mel_slices = [], []
    steps = max(1, n_frames - PARTIAL_FRAMES + frame_step + 1)
    for i in range(0, steps, frame_step):
        mel_slices.append(slice(i, i + PARTIAL_FRAMES))
        wav_slices.append(slice(i * samples_per_frame, (i + PARTIAL_FRAMES) * samples_per_frame))
    last = wav_slices[-1]
    coverage = (n_samples - last.start) / (last.stop - last.start)
    if coverage < min_coverage and len(mel_slices) > 1:
        mel_slices, wav_slices = mel_slices[:-1], wav_slices[:-1]
    return wav_slices, mel_slices


# ─── the network ─────────────────────────────────────────────────────────


def weights_path() -> Path:
    """``pretrained.pt`` inside the installed resemblyzer package, found
    without importing it. FileNotFoundError says how to install it."""
    spec = importlib.util.find_spec("resemblyzer")
    locations = list(spec.submodule_search_locations or []) if spec is not None else []
    for location in locations:
        path = Path(location) / "pretrained.pt"
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Resemblyzer's voice encoder weights are not installed "
        "(pip install --no-deps resemblyzer, or -r requirements-no-deps.in)"
    )


class SpeakerEncoder:
    """Resemblyzer's encoder on torch. ``embed_utterance`` takes 16 kHz
    float32 audio in [-1, 1] and returns a unit-norm 256-dim float32
    vector."""

    def __init__(self, device: str | None = None, weights: Path | None = None) -> None:
        import torch
        from torch import nn

        class _Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.lstm = nn.LSTM(N_MELS, HIDDEN, LAYERS, batch_first=True)
                self.linear = nn.Linear(HIDDEN, EMBEDDING)
                self.relu = nn.ReLU()

            def forward(self, mels: Any) -> Any:
                _, (hidden, _) = self.lstm(mels)
                raw = self.relu(self.linear(hidden[-1]))
                return raw / torch.norm(raw, dim=1, keepdim=True)

        self._torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        net = _Net()
        checkpoint = torch.load(str(weights or weights_path()), map_location="cpu")
        # The checkpoint also holds the training-time similarity scale and
        # bias, which inference doesn't use (Resemblyzer loads it the same way).
        net.load_state_dict(checkpoint["model_state"], strict=False)
        net.eval()
        self._net = net.to(self.device)

    def embed_utterance(self, wav: np.ndarray, rate: float = 1.3, min_coverage: float = 0.75) -> np.ndarray:
        wav_slices, mel_slices = partial_slices(len(wav), rate, min_coverage)
        longest = wav_slices[-1].stop
        if longest >= len(wav):
            wav = np.pad(wav, (0, longest - len(wav)), "constant")
        mel = mel_spectrogram(wav)
        mels = np.array([mel[s] for s in mel_slices])
        with self._torch.no_grad():
            partials = self._net(self._torch.from_numpy(mels).to(self.device)).cpu().numpy()
        raw = np.mean(partials, axis=0)
        return raw / np.linalg.norm(raw, 2)
