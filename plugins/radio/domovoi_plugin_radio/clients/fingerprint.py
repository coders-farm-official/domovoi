"""Local-library audio fingerprinting (wholly in-plugin — locked 8).

A scipy-STFT-based landmark hash modeled on the Wang 2003 "Shazam
algorithm": compute a spectrogram, pick high-amplitude peaks in
(time, freq) neighborhoods, pair each peak with peaks slightly later in
time. Each pair is a landmark hashed as ``(freq_anchor, freq_target,
dt)`` and stored with the anchor's time offset.

Two surfaces:

* :func:`fingerprint_file` — used by the ``track_fingerprinter`` worker
  to hash every library track that has no rows yet. Returns rows ready
  to bulk-INSERT into ``plugin_radio.track_fingerprints``.
* :func:`match_sample` — tier 1 of the sampler's identify chain: hash a
  captured WAV, look up overlapping hashes in the DB, tally offset
  deltas per track, return the best match above the confidence
  threshold (or ``None``).

The hasher and matcher LIVE IN THIS ONE MODULE deliberately: they must
share every algorithm parameter (FFT size, peak threshold, target
zone). Drift between the two silently kills match rates — keep them
coupled.

Heavy imports (numpy, scipy, librosa) are deferred to function bodies;
absence is handled by returning empty/None so the sampler degrades to
online-identify-only without an exception.

DB access notes: ``track_fingerprints`` is schema-qualified as
``plugin_radio.*`` so these helpers work on ANY session regardless of
its search_path (the router hands handlers a public-search_path
session). The two read-only touches of ``public.library_tracks`` are
soft-ref resolution — the SDK has no bulk metadata-by-id surface, and a
read can't violate the "plugins never DDL/write core tables" policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi_plugin_radio import SCHEMA

log = logging.getLogger(__name__)


# ─── Fingerprint algorithm parameters ───────────────────────────────────
#
# Tuned for 15 s radio samples against full-song library fingerprints.
# Match rates are ~80-90 % on identical-codec source material, dropping
# to ~50 % for heavily compressed Icecast vs a high-bitrate library
# copy. The matcher threshold rejects single-digit overlap counts; the
# legitimate "same song" score is in the high dozens for clean material.

_SAMPLE_RATE = 16_000          # mono, 16-bit downstream
_FFT_SIZE = 4096               # ~256 ms window @ 16 kHz
_HOP_SIZE = 1024               # 75 % overlap
_PEAK_NEIGHBORHOOD_F = 20      # bins
_PEAK_NEIGHBORHOOD_T = 20      # frames
# Peak-amplitude floor — relative to the spectrogram's own statistics.
# Take the larger of the 80th-percentile log-magnitude (rules out the
# noise floor) and 5 % of the peak magnitude (keeps clean signals from
# flooding on their own quiet bins). For all-zero (silent) input both
# terms are 0 and the `> 0` guard rejects everything.
_PEAK_AMP_PERCENTILE = 80
_PEAK_AMP_PEAK_FRACTION = 0.05
_TARGET_ZONE_T_MIN = 1         # frames (≈ 64 ms)
_TARGET_ZONE_T_MAX = 100       # frames (≈ 6.4 s)
_TARGET_ZONE_F = 60            # +/- bins around anchor freq
_FAN_OUT = 5                   # at most 5 hash pairs per anchor


@dataclass
class FingerprintRow:
    """One landmark hash. ``hash_bytes`` lands in
    ``track_fingerprints.hash`` (BYTEA, HASH-indexed for equality).

    ``offset_ms`` is DELIBERATELY second-granular (a multiple of 1000):
    the matcher's delta tally was tuned against second-binned offsets —
    coarse bins merge nearby deltas, which tolerates sub-frame
    misalignment between the sample and the library copy. The column is
    milliseconds for forward compatibility, the binning is behavior.
    """

    hash_bytes: bytes
    offset_ms: int

    def __iter__(self) -> Iterable[Any]:
        yield self.hash_bytes
        yield self.offset_ms


@dataclass
class LocalMatch:
    """Result of :func:`match_sample`. ``library_track_id`` keys back
    into ``public.library_tracks`` (soft ref); ``score`` is the count of
    offset-consistent hits that beat the confidence threshold."""

    library_track_id: int
    score: int
    artist: str | None = None
    title: str | None = None

    def to_identity(self) -> "FingerprintIdentity":
        return FingerprintIdentity(
            title=self.title or f"library track #{self.library_track_id}",
            artist=self.artist,
        )


@dataclass
class FingerprintIdentity:
    """Shape-compatible with
    :class:`domovoi_plugin_radio.clients.shazam_stream.TrackIdentity`
    so the sampler's identify chain treats both tiers interchangeably."""

    title: str
    artist: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "artist": self.artist}


# ─── Public coroutines ──────────────────────────────────────────────────


async def fingerprint_file(file_path: str | Path) -> list[FingerprintRow]:
    """Compute landmark hashes for one audio file. Empty list on any
    failure (missing scipy/librosa, unreadable file, audio too short)."""
    try:
        samples = _load_audio_to_mono_16k(Path(file_path))
    except _DepMissingError as e:
        log.debug("fingerprint deps missing for %s: %s", file_path, e)
        return []
    except Exception as e:
        log.debug("fingerprint load failed for %s: %s", file_path, e)
        return []

    if samples is None or len(samples) < _SAMPLE_RATE:
        # Need at least 1 second of audio for meaningful hashes.
        return []

    try:
        return _hash_samples(samples)
    except _DepMissingError as e:
        log.debug("fingerprint deps missing during hash: %s", e)
        return []
    except Exception as e:
        log.debug("fingerprint hash failed for %s: %s", file_path, e)
        return []


async def match_sample(
    session: AsyncSession,
    wav_path: str | Path,
    *,
    min_confidence: int = 10,
) -> LocalMatch | None:
    """Query ``track_fingerprints`` for hashes overlapping the sample at
    ``wav_path``. Returns the best-scoring track above
    ``min_confidence`` (callers pass
    ``config.fingerprinter_match_threshold``), else ``None``."""
    try:
        samples = _load_audio_to_mono_16k(Path(wav_path))
    except _DepMissingError as e:
        log.debug("match_sample deps missing: %s", e)
        return None
    except Exception as e:
        log.debug("match_sample load failed for %s: %s", wav_path, e)
        return None

    if samples is None or len(samples) < _SAMPLE_RATE:
        return None

    try:
        rows = _hash_samples(samples)
    except _DepMissingError:
        return None
    except Exception as e:
        log.debug("match_sample hash failed for %s: %s", wav_path, e)
        return None

    if not rows:
        return None

    # hash → sample offsets, for per-track delta tallies downstream.
    sample_offsets: dict[bytes, list[int]] = {}
    for row in rows:
        sample_offsets.setdefault(row.hash_bytes, []).append(row.offset_ms)

    # One indexed equality lookup for every matching library hash.
    result = await session.execute(
        text(
            f"""
            SELECT library_track_id, hash, offset_ms
            FROM {SCHEMA}.track_fingerprints
            WHERE hash = ANY(:hashes)
            """
        ),
        {"hashes": list(sample_offsets.keys())},
    )

    # Per-track tally of (sample_offset - library_offset). The right
    # track produces many hashes agreeing on one delta; wrong tracks
    # produce a scatter.
    delta_tally: dict[int, dict[int, int]] = {}
    for row in result.all():
        track_id = int(row[0])
        lib_hash = bytes(row[1])
        lib_offset = int(row[2])
        for sample_offset in sample_offsets.get(lib_hash, ()):
            delta = sample_offset - lib_offset
            track_deltas = delta_tally.setdefault(track_id, {})
            track_deltas[delta] = track_deltas.get(delta, 0) + 1

    if not delta_tally:
        return None

    best_track_id = -1
    best_score = 0
    for track_id, deltas in delta_tally.items():
        peak = max(deltas.values())
        if peak > best_score:
            best_score = peak
            best_track_id = track_id

    if best_score < min_confidence:
        return None

    # Canonical metadata for the winner — read-only soft-ref resolve
    # against the core table (see module docstring).
    meta = await session.execute(
        text("SELECT title, artist FROM public.library_tracks WHERE id = :id"),
        {"id": best_track_id},
    )
    meta_row = meta.first()
    title = meta_row[0] if meta_row is not None else None
    artist = meta_row[1] if meta_row is not None else None
    return LocalMatch(
        library_track_id=best_track_id,
        score=best_score,
        title=title,
        artist=artist,
    )


# ─── Internal: dep-gated implementations ────────────────────────────────


class _DepMissingError(Exception):
    """numpy/scipy/librosa not importable — 'fingerprinting unavailable',
    explicitly traceable to this subsystem."""


def _load_audio_to_mono_16k(file_path: Path) -> "Any":
    """Decode an audio file to a mono 16 kHz float32 array; None for
    unreadable files."""
    try:
        import librosa  # type: ignore[import]
        import numpy as np  # type: ignore[import]
    except ImportError as e:
        raise _DepMissingError(str(e)) from e

    if not file_path.exists():
        return None
    samples, _ = librosa.load(
        str(file_path),
        sr=_SAMPLE_RATE,
        mono=True,
        # Cap library tracks at ~5 min to bound CPU. Longer tracks still
        # match — radio samples come from the middle of songs anyway.
        duration=300.0,
    )
    return samples.astype(np.float32, copy=False)


def _hash_samples(samples: "Any") -> list[FingerprintRow]:
    """Landmark hashes from a mono 16 kHz float32 array."""
    try:
        import numpy as np                          # type: ignore[import]
        from scipy import signal                    # type: ignore[import]
    except ImportError as e:
        raise _DepMissingError(str(e)) from e

    import hashlib

    # 1. STFT → log-magnitude spectrogram.
    _, _, Zxx = signal.stft(
        samples,
        fs=_SAMPLE_RATE,
        nperseg=_FFT_SIZE,
        noverlap=_FFT_SIZE - _HOP_SIZE,
        padded=False,
        boundary=None,
    )
    spec = np.log1p(np.abs(Zxx))                    # log1p avoids log(0)

    # 2. Peaks via max-filter over a (freq, time) neighborhood: a bin
    #    equal to the local max AND above the amplitude floor.
    from scipy.ndimage import maximum_filter        # type: ignore[import]
    neighborhood = (_PEAK_NEIGHBORHOOD_F, _PEAK_NEIGHBORHOOD_T)
    local_max = maximum_filter(spec, size=neighborhood, mode="constant", cval=0.0)
    spec_max = float(spec.max())
    amp_floor = max(
        float(np.percentile(spec, _PEAK_AMP_PERCENTILE)),
        spec_max * _PEAK_AMP_PEAK_FRACTION,
    )
    peaks_mask = (spec == local_max) & (spec > amp_floor) & (spec > 0)
    peak_freqs, peak_times = np.where(peaks_mask)
    if peak_freqs.size == 0:
        return []

    # 3. Pair each peak with up to FAN_OUT later peaks in the target
    #    zone; time-sorted so pairing is O(N · FAN_OUT).
    order = np.argsort(peak_times)
    sorted_freqs = peak_freqs[order]
    sorted_times = peak_times[order]

    rows: list[FingerprintRow] = []
    seconds_per_frame = _HOP_SIZE / _SAMPLE_RATE
    j_start = 0
    for i in range(len(sorted_times)):
        t_anchor = int(sorted_times[i])
        f_anchor = int(sorted_freqs[i])
        while (
            j_start < len(sorted_times)
            and sorted_times[j_start] < t_anchor + _TARGET_ZONE_T_MIN
        ):
            j_start += 1
        fan = 0
        j = j_start
        while j < len(sorted_times) and fan < _FAN_OUT:
            t_target = int(sorted_times[j])
            if t_target > t_anchor + _TARGET_ZONE_T_MAX:
                break
            f_target = int(sorted_freqs[j])
            if abs(f_target - f_anchor) <= _TARGET_ZONE_F:
                dt = t_target - t_anchor
                # 8 bytes of truncated SHA1: 2^64 buckets, cheap to
                # index and store.
                payload = f"{f_anchor}:{f_target}:{dt}".encode("ascii")
                h = hashlib.sha1(payload).digest()[:8]
                # Second-granular binning, stored as ms — see
                # FingerprintRow docstring for why.
                offset_ms = int(round(t_anchor * seconds_per_frame)) * 1000
                rows.append(FingerprintRow(hash_bytes=h, offset_ms=offset_ms))
                fan += 1
            j += 1
    return rows
