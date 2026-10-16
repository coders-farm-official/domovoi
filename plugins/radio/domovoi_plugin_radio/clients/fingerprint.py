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

Heavy imports (numpy, scipy) are deferred to function bodies; absence is
handled by returning None so the sampler degrades to online-identify-only
without an exception (the fingerprinter is told, and tries again later).
Audio is decoded to 16 kHz mono by the system ``ffmpeg`` (a required tool
of this plugin), run as its own program by the fingerprint child (below);
until 2026-10-15 it was ``librosa.load``, whose import brings soxr and
soundfile (LGPL libraries) and numba.

**The decode and the hashing never run in the core process.** Both are
synchronous, CPU-bound and, for a library track, take seconds to tens of
seconds; until 2026-10-14 they ran on the core's event loop, so every
satellite, the dashboard and the health check stood still meanwhile
(about 1.9 s per 20 s test track), and a decode that never returned
froze the core for good. Each file now goes to a child process (this
module, run with ``-m``) at a lower CPU priority, under a hard time
limit: past it the child and anything it started are killed and
:class:`FingerprintTimeout` is raised. At most :data:`MAX_CHILDREN` run
at once. The matcher's database work stays in the core (it awaits).

DB access notes: ``track_fingerprints`` is schema-qualified as
``plugin_radio.*`` so these helpers work on ANY session regardless of
its search_path (the router hands handlers a public-search_path
session). The two read-only touches of ``public.library_tracks`` are
soft-ref resolution — the SDK has no bulk metadata-by-id surface, and a
read can't violate the "plugins never DDL/write core tables" policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import struct
import subprocess
import sys
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

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

# Time limits for one child: a library track (the fingerprinter passes the
# plugin's fingerprinter_timeout_sec) and a radio sample (about 15 s of
# WAV, a second or two of work).
FINGERPRINT_TIMEOUT_SEC = 300.0
SAMPLE_TIMEOUT_SEC = 60.0
# Children decoding at once, across the fingerprinter and the sampler.
MAX_CHILDREN = 2


class FingerprintTimeout(Exception):
    """Decoding and hashing one file ran past its time limit; the child
    doing it (and anything it started) was killed. The fingerprinter tries
    the file again later, and gives up on it after a few of these
    (workers/track_fingerprinter.py)."""


class FingerprintFailed(Exception):
    """This attempt failed for a reason other than the file's content: the
    file isn't there to read (missing, a media disk not mounted, no
    permission), the child could not start, was stopped (a ``systemctl
    stop`` sends SIGTERM to it too) or crashed, the audio libraries are
    missing, or its answer was garbled. Try the file again later; it says
    nothing about the file."""


async def fingerprint_file(
    file_path: str | Path, *, timeout_sec: float = FINGERPRINT_TIMEOUT_SEC
) -> list[FingerprintRow]:
    """Compute landmark hashes for one audio file, in a child process.
    Empty list when the file is there and gives none: the decoder refused
    it, or it is too short or silent, verdicts about the file. Raises
    :class:`FingerprintTimeout` when the work ran past ``timeout_sec`` and
    was killed, and :class:`FingerprintFailed` when the attempt failed for
    any other reason, a file that isn't there to read included (try again
    later)."""
    return await _hash_file_in_child(Path(file_path), timeout_sec)


async def match_sample(
    session: AsyncSession,
    wav_path: str | Path,
    *,
    min_confidence: int = 10,
    timeout_sec: float = SAMPLE_TIMEOUT_SEC,
) -> LocalMatch | None:
    """Query ``track_fingerprints`` for hashes overlapping the sample at
    ``wav_path``. Returns the best-scoring track above
    ``min_confidence`` (callers pass
    ``config.fingerprinter_match_threshold``), else ``None`` (also when
    hashing the sample ran past ``timeout_sec``)."""
    try:
        rows = await _hash_file_in_child(Path(wav_path), timeout_sec)
    except (FingerprintTimeout, FingerprintFailed) as e:
        log.warning("radio sample not matched locally: %s", e)
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


# ─── Internal: the child process ────────────────────────────────────────
#
# The child is this module run with ``-m`` (_child_main). It writes
# _MAGIC, the row count (uint32) and then each row as 8 hash bytes plus
# the offset (int32), little-endian, to stdout; anything it has to say
# goes to stderr. Exit status 0: the rows (possibly none); otherwise one
# of the _EXIT_* reasons. Only _EXIT_LOAD (the decoder refused a file that
# is there) is a verdict, an empty result; every other one is
# FingerprintFailed in the parent.

_CHILD_MODULE = "domovoi_plugin_radio.clients.fingerprint"
_MAGIC = b"DOMOVOI-FP1\n"
_ROW = struct.Struct("<8si")
_COUNT = struct.Struct("<I")
_EXIT_USAGE = 2
_EXIT_DEPS = 3
_EXIT_LOAD = 4
_EXIT_HASH = 5
# The file isn't there to read: missing, a media disk not mounted, no
# permission, an I/O error. Says nothing about its content.
_EXIT_NOT_THERE = 6
# The tail of a child's stderr the parent puts in its log line.
_STDERR_MAX = 600
# How long a killed child gets to be reaped before it is left to the OS.
_REAP_SEC = 5.0
# After the child has exited, how long its pipes may stay open (something
# it started still holding them) before that group is killed too.
_PIPE_GRACE_SEC = 2.0
# The child runs below normal priority, so the core's own work (speech
# above all) comes first: a nice value on POSIX, a priority class on
# Windows.
_CHILD_NICE = 10
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

# One semaphore per event loop (tests open a new loop each time, and an
# asyncio.Semaphore stays bound to the loop it first waited on).
_SLOTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


@contextlib.asynccontextmanager
async def _child_slot() -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    sem = _SLOTS.get(loop)
    if sem is None:
        sem = _SLOTS[loop] = asyncio.Semaphore(MAX_CHILDREN)
    async with sem:
        yield


def _child_env() -> dict[str, str]:
    """The core's environment, plus the plugin's own directory on the
    import path (the plugin loader puts it on sys.path, which a child
    does not inherit), and one thread per numeric library: a background
    job, not a race. The core's systemd notify socket stays the core's."""
    env = dict(os.environ)
    plugin_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (plugin_root, env.get("PYTHONPATH", "")) if p
    )
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMBA_NUM_THREADS"):
        env.setdefault(var, "1")
    for var in ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID"):
        env.pop(var, None)
    return env


def _child_command(path: Path) -> list[str]:
    """The command line of the child that hashes ``path``."""
    return [sys.executable, "-m", _CHILD_MODULE, str(path)]


def _spawn_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": _BELOW_NORMAL_PRIORITY_CLASS}
    # A process group of its own, so a kill reaches whatever it started
    # too (the decode is an ffmpeg of its own).
    return {"start_new_session": True}


async def _kill_tree(proc: "asyncio.subprocess.Process") -> None:
    """Kill the child and everything it started; reap it if that's quick."""
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), _REAP_SEC)
        except (OSError, asyncio.TimeoutError):
            pass
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), _REAP_SEC)


def _kill_group(proc: "asyncio.subprocess.Process") -> None:
    """What the child left behind once it has exited itself: its process
    group (POSIX; the group outlives its leader while a member lives).
    On Windows an orphan can't be found from a dead parent: nothing."""
    if sys.platform == "win32":
        return
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, signal.SIGKILL)


def _kill_now(proc: "asyncio.subprocess.Process") -> None:
    """:func:`_kill_tree` without awaiting anything (a cancelled caller)."""
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _parse_child_output(out: bytes) -> list[FingerprintRow] | None:
    """The rows a child wrote, or None when the output isn't an answer."""
    head = len(_MAGIC) + _COUNT.size
    if len(out) < head or not out.startswith(_MAGIC):
        return None
    (count,) = _COUNT.unpack_from(out, len(_MAGIC))
    if len(out) != head + count * _ROW.size:
        return None
    return [
        FingerprintRow(hash_bytes=h, offset_ms=off)
        for h, off in _ROW.iter_unpack(out[head:])
    ]


async def _hash_file_in_child(path: Path, timeout_sec: float) -> list[FingerprintRow]:
    """Decode and hash ``path`` in a child process. The rows; [] for a
    verdict about the file (the decoder refused it, nothing to hash);
    :class:`FingerprintFailed` for a file that isn't there (review round 2,
    case L7: a media disk not mounted marked a track a minute for good);
    :class:`FingerprintTimeout` when the child ran past
    ``timeout_sec`` (killed first); :class:`FingerprintFailed` for any
    other failure. The time limit is the child's: once it has exited, what
    it started may not hold its pipes open past :data:`_PIPE_GRACE_SEC`
    (that group is killed). A caller cancelled while it waits takes the
    child down with it."""
    if not path.is_file():
        raise FingerprintFailed(
            f"{path} isn't there to read (missing, or its disk not mounted?)")
    async with _child_slot():
        try:
            proc = await asyncio.create_subprocess_exec(
                *_child_command(path),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=_child_env(), **_spawn_kwargs(),
            )
        except OSError as e:
            raise FingerprintFailed(f"the fingerprint child for {path} didn't start: {e}") from None
        talk = asyncio.ensure_future(proc.communicate())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_sec
        try:
            # Until the answer is in, or the child has exited (watched by
            # its return code: Process.wait() also waits for every pipe to
            # close, which something the child started can hold forever).
            while not talk.done() and proc.returncode is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await _kill_tree(proc)
                    talk.cancel()
                    raise FingerprintTimeout(
                        f"decoding {path} took longer than {timeout_sec:.0f} s; stopped")
                await asyncio.wait({talk}, timeout=min(0.2, remaining))
        except FingerprintTimeout:
            raise
        except BaseException:
            _kill_now(proc)
            talk.cancel()
            raise
        try:
            out, err = await asyncio.wait_for(asyncio.shield(talk), _PIPE_GRACE_SEC)
        except asyncio.TimeoutError:
            _kill_group(proc)
            try:
                out, err = await asyncio.wait_for(asyncio.shield(talk), _REAP_SEC)
            except asyncio.TimeoutError:
                talk.cancel()
                raise FingerprintFailed(
                    f"the fingerprint child for {path} exited, but something it started "
                    "kept its output open") from None
    detail = (err or b"")[-_STDERR_MAX:].decode("utf-8", "replace").strip()
    code = proc.returncode
    if code == _EXIT_LOAD:
        # The decoder refused the file: a verdict, not worth another try.
        log.debug("fingerprint child for %s: unreadable: %s", path, detail)
        return []
    if code != 0:
        why = (f"stopped by signal {-code}" if code is not None and code < 0
               else {_EXIT_DEPS: "the audio libraries are missing",
                     _EXIT_NOT_THERE: "the file isn't there to read (missing, or its disk not mounted?)",
                     _EXIT_HASH: "hashing crashed",
                     _EXIT_USAGE: "it was started wrongly"}.get(code, f"exit {code}"))
        raise FingerprintFailed(f"fingerprinting {path} failed ({why}): {detail[-200:]}")
    rows = _parse_child_output(out or b"")
    if rows is None:
        raise FingerprintFailed(f"the fingerprint child for {path} wrote no usable answer: {detail[-200:]}")
    return rows


def _child_main(argv: list[str]) -> int:
    """The child: decode one file, hash it, write the rows to stdout."""
    if len(argv) != 2:
        print("usage: python -m domovoi_plugin_radio.clients.fingerprint FILE", file=sys.stderr)
        return _EXIT_USAGE
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.nice(_CHILD_NICE)
    path = Path(argv[1])
    if not path.is_file():
        print(f"not there: {path}", file=sys.stderr)
        return _EXIT_NOT_THERE
    try:
        samples = _load_audio_to_mono_16k(path)
    except _DepMissingError as e:
        print(f"fingerprint deps missing: {e}", file=sys.stderr)
        return _EXIT_DEPS
    except Exception as e:  # noqa: BLE001 - any other decode failure is "unreadable"
        print(f"load failed: {type(e).__name__}: {e}", file=sys.stderr)
        # Reading the file failed (gone meanwhile, its disk went away, no
        # permission, an I/O error), or the decoder refused what it read.
        # Only the second is about the file.
        if isinstance(e, OSError) or not path.is_file():
            return _EXIT_NOT_THERE
        return _EXIT_LOAD
    rows: list[FingerprintRow] = []
    # Need at least 1 second of audio for meaningful hashes.
    if samples is not None and len(samples) >= _SAMPLE_RATE:
        try:
            rows = _hash_samples(samples)
        except _DepMissingError as e:
            print(f"fingerprint deps missing during hash: {e}", file=sys.stderr)
            return _EXIT_DEPS
        except Exception as e:  # noqa: BLE001
            print(f"hash failed: {type(e).__name__}: {e}", file=sys.stderr)
            return _EXIT_HASH
    out = sys.stdout.buffer
    out.write(_MAGIC + _COUNT.pack(len(rows)))
    out.write(b"".join(_ROW.pack(r.hash_bytes, r.offset_ms) for r in rows))
    out.flush()
    return 0


# ─── Internal: dep-gated implementations ────────────────────────────────


class _DepMissingError(Exception):
    """numpy/scipy not importable or ffmpeg not on PATH — 'fingerprinting
    unavailable', explicitly traceable to this subsystem."""


class _DecodeRefused(Exception):
    """ffmpeg read the file and could not decode it: a verdict about the
    file (the child's _EXIT_LOAD), unlike a file that isn't there."""


# Cap library tracks at ~5 min to bound CPU. Longer tracks still match —
# radio samples come from the middle of songs anyway. No time limit of the
# decode's own: the parent's (FINGERPRINT_TIMEOUT_SEC, or the plugin's
# fingerprinter_timeout_sec) kills the child and its ffmpeg.
_MAX_DECODE_SEC = 300


def _load_audio_to_mono_16k(file_path: Path) -> "Any":
    """Decode an audio file to a mono 16 kHz float32 array (the first
    :data:`_MAX_DECODE_SEC` seconds) with ffmpeg: channels averaged,
    resampled by ffmpeg's resampler, 16-bit samples scaled by 1/32768.
    Raises FileNotFoundError for a file that isn't there, an OSError for
    one that can't be read, and :class:`_DecodeRefused` for one ffmpeg
    can't decode. Blocking: it runs in the fingerprint child
    (:func:`_child_main`), never in the core, under the parent's time
    limit (which kills the ffmpeg too)."""
    try:
        import numpy as np  # type: ignore[import]
    except ImportError as e:
        raise _DepMissingError(str(e)) from e
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise _DepMissingError("ffmpeg is not on PATH")

    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))
    # Read here first, so a file that can't be read (no permission, an I/O
    # error) is an OSError, not ffmpeg's refusal: only a refusal of what
    # was read is a verdict about the file (_child_main).
    with file_path.open("rb") as fh:
        fh.read(1)
    try:
        proc = subprocess.run(
            [exe, "-nostdin", "-hide_banner", "-loglevel", "error",
             "-t", str(_MAX_DECODE_SEC), "-i", str(file_path),
             "-vn", "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "f32le", "pipe:1"],
            capture_output=True,
        )
    except OSError as e:
        raise _DepMissingError(f"ffmpeg could not run: {e}") from e
    if proc.returncode != 0:
        raise _DecodeRefused(
            f"ffmpeg could not decode {file_path}: "
            + (proc.stderr or b"").decode("utf-8", "replace").strip()[-200:])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


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


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv))
