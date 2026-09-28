"""The streaming fast lane, in shadow mode (early-endpointing option D).

Today a spoken command is transcribed once, after the fact: the satellite
streams every 30 ms frame to the core while the person talks, but it only
decides they have stopped after ``listen.silence_timeout`` (1.2 s) of
quiet, and only then does Whisper run, on the whole capture. For "pause
the music" that is well over a second of waiting after the last word
before anything is even routed.

The fast lane is a second, much smaller recognizer that reads those same
frames as they arrive. sherpa-onnx runs a streaming transducer (a NeMo
FastConformer, 80 ms look-ahead) that keeps a partial transcript a few
hundred milliseconds behind the speaker. When that partial is a complete
CLOSED command, one of the fast paths in :data:`TIERS`, and the room has
then been quiet for the tier's hold, the lane knows what the command is
long before the satellite's own silence timer runs out.

**Shadow mode acts on nothing.** It is the only mode there is. The lane
writes down what it would have done, and when Whisper's transcript
arrives it checks whether the two agree:

    fastlane would commit music._pause_from_match at +412ms (980 ms before
    utterance_end, hold 350 ms) in room=kitchen; lane heard 'pause the
    music'; whisper later said 'Pause the music.'; agree=True

The same record goes on the turn's ``intents_log.timings`` row
(``fastlane_*`` keys, see :func:`settle`), and ``GET /v1/stats/latency``
counts them, numbers only. Routing, the reply and everything the
satellite sees are exactly what they would have been with the lane off.
A week of these records on real household audio is what decides whether
the lane may ever act (the design's disagreement threshold).

What "a complete closed command" means here is decided locally, not by the
router: the same normalisation the router applies, the same band-ordered
first-match walk over the live handler registry, and :data:`TIERS`, which
names the fast paths whose match is a whole command. Anything else (an
open slot such as ``^play (.+)$``, a reminder's free-text message, a
plugin's fast path, a question for the language model) never commits.
Tier A paths hold 350 ms of quiet, tier B paths (a duration, a number, a
label, a calculation, the clock, words that can start a longer question)
650 ms, and a bare word (``stop``, ``pause``) or ``go back`` is always
tier B: most of them start a longer, different command (``stop the
timer``, ``go back a chapter``).

Settings (``domovoi/config.py``): ``fastlane_mode`` off | shadow (off by
default; applies without a restart), ``fastlane_model`` and
``fastlane_cpu_threads`` (restart). The recognizer is the ``fastlane``
extra (``pip install -e ".[fastlane]"``), NOT a core dependency: without
it, shadow mode logs once that it is unavailable and the core runs as
before. The model downloads on first enable into
``~/.domovoi/models/fastlane/`` from a pinned URL, checked against a
pinned SHA-256 (the archive and every file taken from it) before it is
ever loaded; ``python -m domovoi.fast_lane fetch`` does it ahead of time.

Threading: every room's frames are decoded on ONE worker thread, so the
lane's CPU is bounded by ``fastlane_cpu_threads`` whatever the number of
satellites talking at once; the event loop only appends bytes to a queue.
A capture the worker falls behind on, or that runs past
:data:`MAX_DECODE_MS` without a decision (a question, dictation, a TV),
stops being decoded rather than queueing work.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import math
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from domovoi.config import settings

log = logging.getLogger(__name__)

MODES = ("off", "shadow")

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # 960: one 30 ms int16 frame

# Quiet after the last voiced frame before a complete command counts.
HOLD_A_MS = 350
HOLD_B_MS = 650

# Only turns a person started for a command. Never a barge-in (it may be the
# satellite's own echo), a chat-mode turn (Letta owns it, not the router) or
# a wake-word training clip.
ELIGIBLE_TRIGGERS = frozenset({"wake_word", "followup"})

# Closed commands are short: a capture still undecided after this much audio
# is a question, dictation or a television, and is no longer decoded.
MAX_DECODE_MS = 8_000
# Audio the worker may be behind on for one capture before that capture is
# dropped: a decision that late would be no faster than Whisper's.
MAX_BACKLOG_MS = 1_500

# Voiced-frame detection (see _VoicedTracker).
VOICED_MARGIN_DB = 10.0
FLOOR_RISE_DB_PER_FRAME = 0.05  # 1.7 dB/s: a command can't drag it up
MIN_VOICED_FRAMES = 3           # 90 ms of speech before anything counts

# A fast path is identified by (handler name, method name), the same pair
# the router's audit trail carries. Plugin fast paths are never listed:
# they default to never.
TIERS: dict[tuple[str, str], int] = {
    # Tier A: closed phrases nothing in the registry extends.
    ("music", "_pause_from_match"): HOLD_A_MS,
    ("music", "_resume_from_match"): HOLD_A_MS,
    ("music", "_stop_from_match"): HOLD_A_MS,
    ("music", "_next_from_match"): HOLD_A_MS,
    ("music", "_prev_from_match"): HOLD_A_MS,
    ("music", "_now_playing_from_match"): HOLD_A_MS,
    ("music", "_volume_up_from_match"): HOLD_A_MS,
    ("music", "_volume_down_from_match"): HOLD_A_MS,
    ("timer", "_status_from_match"): HOLD_A_MS,
    ("dropin", "_end_from_match"): HOLD_A_MS,
    ("spoken_audio", "_next_chapter_from_match"): HOLD_A_MS,
    ("spoken_audio", "_prev_chapter_from_match"): HOLD_A_MS,
    ("spoken_audio", "_time_left_from_match"): HOLD_A_MS,
    ("wifi", "_status_from_match"): HOLD_A_MS,
    ("homelab", "_status_from_match"): HOLD_A_MS,
    ("voice", "_list_from_match"): HOLD_A_MS,
    ("voice", "_current_from_match"): HOLD_A_MS,
    ("playlist", "_play_favorites_from_match"): HOLD_A_MS,
    ("playlist", "_shuffle_favorites_from_match"): HOLD_A_MS,
    # Tier B: whole, but a number, a duration, a label or a language-model
    # question can follow ("... and 30 seconds", "what time is it in Tokyo").
    # Not a reminder: its message is free text, and the lane's small model
    # gets names wrong ("remind me to call ma'am in ten minutes" for "mom",
    # 7 clips of 9 on this box's TTS test set) where Whisper does not.
    ("timer", "_create_from_match"): HOLD_B_MS,
    # "cancel the timer" + "for the pasta": the pattern's own optional label.
    ("timer", "_cancel_from_match"): HOLD_B_MS,
    # Closed patterns whose words start a question for the language model:
    # "what was that" + "song", "what are my reminders" + "for tomorrow",
    # "how many albums" + "does Adele have", "what's this book" + "about".
    ("repeat", "_from_match"): HOLD_B_MS,
    ("reminder", "_list_from_match"): HOLD_B_MS,
    ("library", "_count_from_match"): HOLD_B_MS,
    ("spoken_audio", "_now_listening_from_match"): HOLD_B_MS,
    ("music", "_volume_set_from_match"): HOLD_B_MS,
    ("music", "_play_random_from_match"): HOLD_B_MS,
    ("dismiss", "_from_match"): HOLD_B_MS,
    ("calculator", "_arith_from_match"): HOLD_B_MS,
    ("calculator", "_arith_sqrt"): HOLD_B_MS,
    ("calculator", "_percent_from_match"): HOLD_B_MS,
    ("calculator", "_unit_from_match"): HOLD_B_MS,
    ("calculator", "_tip_split_from_match"): HOLD_B_MS,
    ("spoken_audio", "_skip_from_match"): HOLD_B_MS,
    ("double_check", "_from_match"): HOLD_B_MS,
    ("clock", "_time_from_match"): HOLD_B_MS,
    ("clock", "_date_from_match"): HOLD_B_MS,
    ("clock", "_day_of_week_from_match"): HOLD_B_MS,
    ("clock", "_year_from_match"): HOLD_B_MS,
    ("clock", "_month_from_match"): HOLD_B_MS,
    ("clock", "_tomorrow_from_match"): HOLD_B_MS,
    ("clock", "_yesterday_from_match"): HOLD_B_MS,
}

# Whole commands that are also the first words of a DIFFERENT command:
# "stop" / "stop the timer", "next" / "next chapter", "go back" / "go back
# a chapter", "cancel" / "cancel my reminder". Whatever their path's tier,
# they get the long hold.
EXTENSIBLE_HEADS = frozenset({
    "stop", "next", "skip", "previous", "back", "go back", "continue",
    "resume", "cancel", "shuffle",
})


# ─── The pinned models ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    sha256: str           # of the archive at `url`
    size: int             # bytes; the download refuses to read past it
    archive_dir: str      # the archive's single top-level directory
    files: dict[str, tuple[str, str]]  # role -> (file name, sha256)
    source: str           # where the weights come from, and their licence


MODELS: dict[str, ModelSpec] = {
    "nemo-fastconformer-en-80ms-int8": ModelSpec(
        name="nemo-fastconformer-en-80ms-int8",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8.tar.bz2"
        ),
        sha256="7bd33a914e93370a1ba9c2066d9e841bdcad8613fa2a00537c1ae15d851a14d8",
        size=102_813_625,
        archive_dir="sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8",
        files={
            "encoder": ("encoder.int8.onnx",
                        "8b982c67b45e3b735d3fee49a4fea525b3149b450ba437f4c8a933f4aa6744c0"),
            "decoder": ("decoder.int8.onnx",
                        "76eec598da07c204747a859a723b99077ef0bbdc19ef4b3f51eb43275662475d"),
            "joiner": ("joiner.int8.onnx",
                       "67f4291dc170fd06b3695a8511f5199fd5965ee3c77cfbd59afc10e145f173f2"),
            "tokens": ("tokens.txt",
                       "618dc110fc2213886b52e063ff42329bbdf37a266ca7705184090fa5f39f3131"),
        },
        source=(
            "NVIDIA NeMo stt_en_fastconformer_hybrid_large_streaming_80ms "
            "(CC-BY-4.0), int8 ONNX export by the sherpa-onnx project"
        ),
    ),
}
DEFAULT_MODEL = "nemo-fastconformer-en-80ms-int8"

MODELS_ROOT = Path.home() / ".domovoi" / "models" / "fastlane"
_VERIFIED_MARKER = ".verified"
_DOWNLOAD_CHUNK = 1 << 20


class ModelError(RuntimeError):
    """The model could not be fetched, verified or unpacked."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_DOWNLOAD_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path, *, size: int) -> str:
    """Stream ``url`` to ``dest``; the SHA-256 of what was written. Refuses
    to read past ``size`` bytes."""
    import httpx

    h = hashlib.sha256()
    written = 0
    timeout = httpx.Timeout(30.0, read=120.0)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(_DOWNLOAD_CHUNK):
                written += len(chunk)
                if written > size:
                    raise ModelError(f"{url} is larger than the pinned {size} bytes")
                h.update(chunk)
                f.write(chunk)
    return h.hexdigest()


def model_dir(spec: ModelSpec, root: Path | None = None) -> Path:
    return (root or MODELS_ROOT) / spec.name


def _files_verified(target: Path, spec: ModelSpec) -> bool:
    for fname, digest in spec.files.values():
        p = target / fname
        if not p.is_file() or _sha256_file(p) != digest:
            return False
    return True


def ensure_model(
    spec: ModelSpec,
    root: Path | None = None,
    *,
    fetch: Callable[..., str] = _download,
) -> Path:
    """The directory holding ``spec``'s files, downloading, verifying and
    unpacking it first when it isn't there yet. Nothing unverified is ever
    left where the loader looks, and the files already there are checked
    against their pinned digests on every call (about 0.1 s for this
    model), not just when they arrived: sherpa-onnx ends the whole process
    (``exit()``, not an exception) on a model file with the wrong metadata,
    so a file swapped or damaged after its download must never reach it."""
    root = root or MODELS_ROOT
    target = model_dir(spec, root)
    marker = target / _VERIFIED_MARKER
    if target.is_dir() and _files_verified(target, spec):
        try:
            current = marker.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if current != spec.sha256:
            marker.write_text(spec.sha256 + "\n", encoding="utf-8")
        return target

    root.mkdir(parents=True, exist_ok=True)
    archive = root / f".{spec.name}.download"
    staging = root / f".{spec.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        log.info(
            "fastlane: downloading model %s (%.0f MB, one-time) from %s",
            spec.name, spec.size / 1e6, spec.url,
        )
        got = fetch(spec.url, archive, size=spec.size)
        if got != spec.sha256:
            raise ModelError(
                f"model archive checksum mismatch: expected {spec.sha256}, got {got}"
            )
        _unpack(archive, staging, spec)
        (staging / _VERIFIED_MARKER).write_text(spec.sha256 + "\n", encoding="utf-8")
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    log.info("fastlane: model %s verified and unpacked into %s", spec.name, target)
    return target


def _unpack(archive: Path, staging: Path, spec: ModelSpec) -> None:
    """Take exactly the pinned files out of the archive, by name, and check
    each one's digest. Nothing else in the archive is written anywhere."""
    import tarfile

    wanted = {fname: digest for fname, digest in spec.files.values()}
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:*") as tf:
        for member in tf:
            parts = member.name.split("/")
            if (
                len(parts) != 2
                or parts[0] != spec.archive_dir
                or parts[1] not in wanted
                or not member.isfile()
            ):
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            h = hashlib.sha256()
            with src, open(staging / parts[1], "wb") as out:
                for block in iter(lambda: src.read(_DOWNLOAD_CHUNK), b""):
                    h.update(block)
                    out.write(block)
            if h.hexdigest() != wanted[parts[1]]:
                raise ModelError(f"{parts[1]} in the model archive fails its checksum")
    missing = [f for f in wanted if not (staging / f).is_file()]
    if missing:
        raise ModelError(f"model archive is missing {', '.join(sorted(missing))}")


def build_recognizer(spec: ModelSpec, directory: Path, *, threads: int) -> Any:
    """A sherpa-onnx streaming recognizer over ``spec``'s files."""
    import sherpa_onnx

    def path(role: str) -> str:
        return str(directory / spec.files[role][0])

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=path("tokens"),
        encoder=path("encoder"),
        decoder=path("decoder"),
        joiner=path("joiner"),
        num_threads=max(1, int(threads)),
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


# ─── What a transcript would route to ──────────────────────────────────────

_UNITS = {
    w: i for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {
    w: 10 * (i + 2) for i, w in enumerate(
        "twenty thirty forty fifty sixty seventy eighty ninety".split()
    )
}
_HYPHENATED = re.compile(
    r"\b(" + "|".join(_TENS) + r")-(" + "|".join(list(_UNITS)[1:10]) + r")\b"
)


def _two_digits(toks: list[str], j: int) -> tuple[int | None, int]:
    w = toks[j] if j < len(toks) else None
    if w in _TENS:
        v = _TENS[w]
        j += 1
        if j < len(toks) and toks[j] in _UNITS and 0 < _UNITS[toks[j]] < 10:
            v += _UNITS[toks[j]]
            j += 1
        return v, j
    if w in _UNITS:
        return _UNITS[w], j + 1
    return None, j


def words_to_digits(text: str) -> str:
    """Spelled numbers (0-999) as digits: "set the volume to forty five" ->
    "set the volume to 45". The streaming model spells numbers; Whisper and
    most fast-path patterns use digits."""
    toks = _HYPHENATED.sub(r"\1 \2", text).split()
    out: list[str] = []
    i = 0
    while i < len(toks):
        v, j = _two_digits(toks, i)
        if v is None:
            out.append(toks[i])
            i += 1
            continue
        if j < len(toks) and toks[j] == "hundred" and 0 < v < 10:
            v *= 100
            j += 1
            k = j + 1 if j < len(toks) and toks[j] == "and" else j
            rest, k2 = _two_digits(toks, k)
            if rest is not None:
                v += rest
                j = k2
        out.append(str(v))
        i = j
    return " ".join(out)


# The streaming model was trained on British and American text alike; the
# fast-path patterns are written in American spelling.
_LANE_SPELLING = {"favourite": "favorite", "favourites": "favorites"}


def lane_spelling(text: str) -> str:
    """The recognizer's partial with the few spellings no pattern expects
    put the way Whisper writes them."""
    return " ".join(_LANE_SPELLING.get(w, w) for w in text.split())


def normalize(text: str) -> str:
    """What the router matches fast paths against: lower-cased, trimmed,
    trailing punctuation off, leading filler ("please", "can you") off."""
    from domovoi.router import _LEADING_FILLER_RE

    t = " ".join(text.lower().split()).rstrip(".,!?")
    return _LEADING_FILLER_RE.sub("", t)


def _canon(group: str | None) -> str | None:
    if group is None:
        return None
    return words_to_digits(" ".join(group.lower().split()))


@dataclass(frozen=True)
class Command:
    """A transcript's first fast-path match, as the router would find it."""

    handler: str
    method: str
    groups: tuple[str | None, ...]  # number words as digits, whitespace folded
    text: str                      # the normalised text that matched
    hold_ms: int | None            # None: never commits

    @property
    def path(self) -> str:
        return f"{self.handler}.{self.method}"

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.handler, self.method, self.groups)


def _first_fast_path(text: str) -> Command | None:
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import as_fast_path

    for handler in list(HANDLERS):  # a snapshot: plugins may (un)register
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            m = fp.pattern.match(text)
            if not m:
                continue
            name = getattr(fp.method, "__name__", "")
            hold = TIERS.get((handler.name, name))
            if hold is not None and (text in EXTENSIBLE_HEADS or len(text.split()) == 1):
                hold = max(hold, HOLD_B_MS)
            return Command(
                handler=handler.name,
                method=name,
                groups=tuple(_canon(g) for g in m.groups()),
                text=text,
                hold_ms=hold,
            )
    return None


def resolve(transcript: str) -> Command | None:
    """The fast path ``transcript`` routes to — the router's own walk — or
    None when it would go to the language model. Spelled numbers are tried
    as digits when the words themselves match nothing, and slot values
    compare with digits either way, so "set the volume to forty" and "Set
    the volume to 40." are the same command."""
    t = normalize(transcript)
    if not t:
        return None
    hit = _first_fast_path(t)
    if hit is None:
        digits = words_to_digits(t)
        if digits != t:
            hit = _first_fast_path(digits)
    return hit


# ─── Voiced frames ─────────────────────────────────────────────────────────


class _VoicedTracker:
    """Voiced or not, per 30 ms frame, relative to the capture's own floor.

    The floor starts at the first frame, drops at once to anything quieter
    and creeps up slowly (``FLOOR_RISE_DB_PER_FRAME``), so a few seconds of
    continuous speech can't raise it to the speech level and make the
    speech itself read as silence — the failure that would let the lane
    decide while someone is still talking. A frame is voiced when it is
    ``VOICED_MARGIN_DB`` above the floor. A capture that opens mid-word
    (floor starts high) simply isn't voiced until the first gap: the lane
    then decides nothing, which is the safe way to be wrong."""

    __slots__ = ("floor",)

    def __init__(self) -> None:
        self.floor: float | None = None

    def voiced(self, dbfs: float) -> bool:
        if self.floor is None or dbfs < self.floor:
            self.floor = dbfs
        else:
            self.floor = min(self.floor + FLOOR_RISE_DB_PER_FRAME, dbfs)
        return dbfs >= self.floor + VOICED_MARGIN_DB


def _frames_dbfs(frames: bytes) -> list[float]:
    import numpy as np

    x = np.frombuffer(frames, dtype="<i2").astype(np.float32).reshape(-1, FRAME_BYTES // 2)
    rms = np.sqrt(np.mean(x * x, axis=1))
    return [
        -120.0 if r < 1.0 else 20.0 * math.log10(r / 32768.0) for r in rms.tolist()
    ]


# ─── One capture ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    """The moment the lane would have committed."""

    command: Command
    lane_text: str        # the recognizer's partial, as it was
    decided_at: float     # time.perf_counter()
    last_voiced_at: float  # arrival of the last voiced frame before it


class LaneCapture:
    """The lane's view of one capture, from ``utterance_start`` to
    ``utterance_end``.

    The event loop calls :meth:`feed` per frame and :meth:`finish` at the
    end; everything else — the recognizer stream, the partial transcript,
    the decision — lives on the engine's worker thread and is handed back
    under ``_lock``. After :meth:`finish` nothing more is decided and the
    stream is released on the worker."""

    def __init__(self, engine: "FastLaneEngine", *, room_id: str, trigger: str) -> None:
        self._engine = engine
        self.room_id = room_id
        self.trigger = trigger
        self._lock = threading.Lock()
        self._pending: collections.deque[tuple[bytes, float]] = collections.deque()
        self._pending_bytes = 0
        self._scheduled = False
        self._finished = False
        self.ended_at: float | None = None
        # Why the lane stopped decoding early, if it did: "long" | "backlog"
        # | "error".
        self.gave_up: str | None = None
        # Worker-side state (only the worker thread touches these).
        self._stream: Any = None
        self._decoding = True
        self._carry = b""
        self._frames = 0
        self._voiced_frames = 0
        self._last_voiced_frame = -1
        self._last_voiced_at: float | None = None
        self._tracker = _VoicedTracker()
        self._text = ""
        self._command: Command | None = None
        self._busy_s = 0.0
        # Read by settle() under _lock.
        self.decision: Decision | None = None
        self.voiced_after_ms = 0
        self.lane_text = ""
        # The core ended the capture itself (early commit, part B of the
        # same design) before the lane had decided: not a miss, since the
        # lane never heard the quiet its hold waits for.
        self.preempted = False

    # ── event loop side ────────────────────────────────────────────────

    def feed(self, data: bytes) -> None:
        now = time.perf_counter()
        with self._lock:
            if self._finished:
                return
            self._pending.append((data, now))
            self._pending_bytes += len(data)
            if self._pending_bytes > MAX_BACKLOG_MS * SAMPLE_RATE * 2 // 1000:
                self.gave_up = "backlog"
                self._finished = True
                self._pending.clear()
            if self._scheduled:
                return
            self._scheduled = True
        if not self._engine.submit(self._drain):
            self._abandon("error")

    def finish(self, ended_at: float | None = None, *, preempted: bool = False) -> None:
        """No more audio: stop deciding and release the stream.
        ``preempted``: the core's early commit ended the capture, not the
        satellite's own silence timeout."""
        with self._lock:
            if preempted and self.ended_at is None:
                self.preempted = True
            if self._finished and self.ended_at is not None:
                return
            if self.ended_at is None:
                self.ended_at = ended_at if ended_at is not None else time.perf_counter()
            self._finished = True
            self._pending.clear()
            idle = not self._scheduled
            if idle:
                self._scheduled = True
        self._engine.forget(self)
        if idle and not self._engine.submit(self._drain):
            self._release()

    def _abandon(self, why: str) -> None:
        with self._lock:
            self._finished = True
            self._pending.clear()
            self._scheduled = False
            self.gave_up = self.gave_up or why
        self._engine.forget(self)
        self._release()

    @property
    def cpu_ms(self) -> int:
        return int(round(self._busy_s * 1000))

    # ── worker side ────────────────────────────────────────────────────

    def _drain(self) -> None:
        while True:
            with self._lock:
                if self._finished or not self._pending:
                    self._scheduled = False
                    finished = self._finished
                    batch = None
                else:
                    batch = list(self._pending)
                    self._pending.clear()
                    self._pending_bytes = 0
            if batch is None:
                if finished:
                    self._release()
                return
            try:
                self._process(batch)
            except Exception as e:
                log.warning("fastlane: decoding failed in room=%s: %s", self.room_id, e)
                self._abandon("error")
                return

    def _release(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            self._engine.stream_released()

    def _process(self, batch: list[tuple[bytes, float]]) -> None:
        # Frame by frame, whatever the batching: a worker that fell a few
        # frames behind must reach the same verdict it would have reached
        # on time, or a hold completed inside a batch that also holds the
        # next word would go unrecorded.
        for chunk, arrived in batch:
            buf = self._carry + chunk
            whole = len(buf) - len(buf) % FRAME_BYTES
            self._carry = buf[whole:]
            for off in range(0, whole, FRAME_BYTES):
                self._frame(buf[off:off + FRAME_BYTES], arrived)

    def _frame(self, frame: bytes, arrived: float) -> None:
        if self._tracker.voiced(_frames_dbfs(frame)[0]):
            self._voiced_frames += 1
            self._last_voiced_frame = self._frames
            self._last_voiced_at = arrived
            if self.decision is not None:
                with self._lock:
                    self.voiced_after_ms += FRAME_MS
        self._frames += 1
        # Nothing is decided after finish(), so the rest of a batch that was
        # in flight at utterance_end isn't decoded: that is the moment
        # Whisper starts on the same CPU.
        if not self._decoding or self._finished:
            return
        if self._frames * FRAME_MS > MAX_DECODE_MS:
            self._stop_decoding("long")
            return

        t0 = time.perf_counter()
        rec = self._engine.recognizer
        stream = self._stream
        if stream is None:
            stream = rec.create_stream()
            with self._lock:
                if self._finished:
                    return
                self._stream = stream
            self._engine.stream_opened()
        import numpy as np

        pcm = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        stream.accept_waveform(SAMPLE_RATE, pcm)
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        text = str(rec.get_result(stream) or "").strip()
        self._busy_s += time.perf_counter() - t0

        if text != self._text:
            self._text = text
            self._command = resolve(lane_spelling(text)) if text else None
            with self._lock:
                self.lane_text = text
        self._maybe_decide()

    def _maybe_decide(self) -> None:
        cmd = self._command
        if (
            cmd is None
            or cmd.hold_ms is None
            or self._voiced_frames < MIN_VOICED_FRAMES
            or self._last_voiced_at is None
        ):
            return
        quiet_ms = (self._frames - 1 - self._last_voiced_frame) * FRAME_MS
        if quiet_ms < cmd.hold_ms:
            return
        decision = Decision(
            command=cmd,
            lane_text=self._text,
            decided_at=time.perf_counter(),
            last_voiced_at=self._last_voiced_at,
        )
        with self._lock:
            if self._finished:
                return
            self.decision = decision
        # Decided: the rest of the capture only matters for whether the
        # person kept talking (voiced_after_ms), which needs no recognizer.
        self._stop_decoding(None)

    def _stop_decoding(self, why: str | None) -> None:
        self._decoding = False
        if why is not None:
            with self._lock:
                self.gave_up = self.gave_up or why
        self._release()


class FastLaneEngine:
    """The loaded recognizer and the one worker thread every room shares."""

    def __init__(self, recognizer: Any, *, model: str, threads: int) -> None:
        self.recognizer = recognizer
        self.model = model
        self.threads = threads
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fastlane")
        self._open: set[LaneCapture] = set()
        self._streams = 0
        self._streams_lock = threading.Lock()

    def submit(self, fn: Callable[[], None]) -> bool:
        try:
            self._pool.submit(fn)
            return True
        except RuntimeError:  # shut down
            return False

    def open_capture(self, *, room_id: str, trigger: str) -> LaneCapture:
        cap = LaneCapture(self, room_id=room_id, trigger=trigger)
        self._open.add(cap)
        return cap

    def forget(self, cap: LaneCapture) -> None:
        self._open.discard(cap)

    def stream_opened(self) -> None:
        with self._streams_lock:
            self._streams += 1

    def stream_released(self) -> None:
        with self._streams_lock:
            self._streams -= 1

    @property
    def open_captures(self) -> int:
        return len(self._open)

    @property
    def live_streams(self) -> int:
        with self._streams_lock:
            return self._streams

    def drain(self, timeout: float = 5.0) -> None:
        """Wait until every job queued so far has run (tests, shutdown)."""
        done = threading.Event()
        if self.submit(done.set):
            done.wait(timeout)

    def shutdown(self) -> None:
        for cap in list(self._open):
            cap.finish()
        self._pool.shutdown(wait=False, cancel_futures=False)


# ─── The lane's lifecycle ──────────────────────────────────────────────────

_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
_ENGINE: FastLaneEngine | None = None
_STATE = "off"        # off | loading | ready | unavailable
_GENERATION = 0


def _mode() -> str:
    mode = str(getattr(settings, "fastlane_mode", "off") or "off").lower()
    return mode if mode in MODES else "off"


def status() -> dict[str, Any]:
    """What the lane is doing now (numbers and names only)."""
    with _LOCK:
        engine = _ENGINE
        state = _STATE
    return {
        "mode": _mode(),
        "state": state,
        "model": engine.model if engine is not None else str(settings.fastlane_model),
        "cpu_threads": engine.threads if engine is not None else None,
        "active_captures": engine.open_captures if engine is not None else 0,
    }


def install_engine(engine: FastLaneEngine | None) -> None:
    """Put a ready engine (or none) in place — for tests and tools."""
    global _ENGINE, _STATE, _GENERATION
    with _LOCK:
        old, _ENGINE = _ENGINE, engine
        _GENERATION += 1
        _STATE = "ready" if engine is not None else "off"
    if old is not None and old is not engine:
        old.shutdown()


def start(*, retry: bool = False) -> None:
    """Begin loading the lane if ``fastlane_mode`` asks for it. Returns at
    once; the download and the model load run on a background thread. A
    load that failed is retried only when asked (a restart, or saving
    ``fastlane_mode`` again), never per utterance. Never under stubs: the
    suite installs its own engine."""
    global _STATE, _GENERATION
    if _mode() == "off" or settings.use_stubs:
        return
    with _LOCK:
        if _STATE in ("loading", "ready") or (_STATE == "unavailable" and not retry):
            return
        _GENERATION += 1
        gen = _GENERATION
        _STATE = "loading"
    _spawn_loader(gen)


def _spawn_loader(gen: int) -> None:
    threading.Thread(target=_load, args=(gen,), name="fastlane-load", daemon=True).start()


def apply_mode() -> None:
    """The ``fastlane_mode`` reapply hook: off drops the engine (its memory
    goes back), shadow starts loading it. Captures already open finish
    without deciding anything."""
    if _mode() == "off":
        install_engine(None)
        log.info("fastlane: mode off")
        return
    start(retry=True)


def _fail(gen: int, detail: str) -> None:
    global _STATE
    with _LOCK:
        if gen != _GENERATION:
            return
        # Logged before the state flips, so "unavailable" always has its
        # reason in the log already.
        log.warning(
            "fastlane: shadow mode is on but the lane is unavailable: %s. Voice "
            "turns are unaffected. Save fastlane_mode again (or restart) to retry.",
            detail,
        )
        _STATE = "unavailable"


def _load(gen: int) -> None:
    global _ENGINE, _STATE
    name = str(settings.fastlane_model or DEFAULT_MODEL)
    spec = MODELS.get(name)
    if spec is None:
        _fail(gen, f"unknown fastlane_model {name!r} (known: {', '.join(sorted(MODELS))})")
        return
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        _fail(gen, 'sherpa-onnx is not installed (pip install -e ".[fastlane]")')
        return
    except Exception as e:  # a wheel whose native library won't load (OSError)
        _fail(gen, f"sherpa-onnx is installed but won't load: {type(e).__name__}: {e}")
        return
    threads = max(1, int(getattr(settings, "fastlane_cpu_threads", 1) or 1))
    try:
        t0 = time.perf_counter()
        # One fetch at a time: turning the lane off and on again during the
        # first download starts a second loader on the same paths.
        with _FETCH_LOCK:
            directory = ensure_model(spec)
        recognizer = build_recognizer(spec, directory, threads=threads)
    except Exception as e:
        _fail(gen, f"{type(e).__name__}: {e}")
        return
    engine = FastLaneEngine(recognizer, model=spec.name, threads=threads)
    with _LOCK:
        stale = gen != _GENERATION or _mode() == "off"
        if not stale:
            _ENGINE = engine
            _STATE = "ready"
    if stale:
        engine.shutdown()
        return
    log.info(
        "fastlane: ready in shadow mode — %s, %d CPU thread(s), loaded in %.1f s. "
        "It logs what it would have done and changes nothing.",
        spec.name, threads, time.perf_counter() - t0,
    )


def shutdown() -> None:
    install_engine(None)


# ─── The streaming layer's hooks ───────────────────────────────────────────


def open_capture(
    room_id: str,
    trigger: str | None,
    *,
    previous: LaneCapture | None = None,
    chat: bool = False,
) -> LaneCapture | None:
    """``utterance_start``: close the room's previous capture, and open a
    new one when the lane is on, ready and the turn could be a command.
    Never raises: it runs inside the satellite's message loop."""
    try:
        close(previous)
        if _mode() != "shadow":
            return None
        engine = _ENGINE
        if engine is None:
            start()  # still loading, or unavailable (logged once): no lane
            return None
        if chat or trigger not in ELIGIBLE_TRIGGERS:
            return None
        return engine.open_capture(room_id=room_id, trigger=str(trigger))
    except Exception as e:  # shadow must never cost a turn
        log.warning("fastlane: could not open a capture in room=%s: %s", room_id, e)
        return None


def close(capture: LaneCapture | None) -> None:
    """Drop a capture without a verdict (disconnect, noisy capture, the
    next utterance). Always returns None, for ``x = close(x)``."""
    if capture is not None:
        capture.finish()
    return None


def settle(capture: LaneCapture | None, transcript: str, timings: Any) -> None:
    """Whisper has spoken: log what the lane would have done against it and
    put the shadow record on the turn's timings. Never raises, never
    changes the turn. Returns None, for ``x = settle(x, ...)``."""
    if capture is None:
        return None
    try:
        _settle(capture, transcript, timings)
    except Exception as e:  # shadow must never cost a turn
        log.warning("fastlane: could not settle room=%s: %s", capture.room_id, e)
    return None


def _settle(capture: LaneCapture, transcript: str, timings: Any) -> None:
    capture.finish()
    with capture._lock:
        decision = capture.decision
        after_ms = capture.voiced_after_ms
        ended_at = capture.ended_at
        gave_up = capture.gave_up
        preempted = capture.preempted
    whisper = resolve(transcript)
    record: dict[str, Any] = {"fastlane_seen": True, "fastlane_cpu_ms": capture.cpu_ms}
    if decision is not None:
        cmd = decision.command
        agree = whisper is not None and whisper.key == cmd.key
        ms = max(0, int(round((decision.decided_at - decision.last_voiced_at) * 1000)))
        lead = (
            max(0, int(round((ended_at - decision.decided_at) * 1000)))
            if ended_at is not None else None
        )
        record.update(
            fastlane_ms=ms,
            fastlane_lead_ms=lead,
            fastlane_text=decision.lane_text,
            fastlane_path=cmd.path,
            fastlane_agree=agree,
            fastlane_after_ms=after_ms,
        )
        log.info(
            "fastlane would commit %s at +%dms (%s ms before utterance_end, "
            "hold %d ms) in room=%s; lane heard %r; whisper later said %r; "
            "agree=%s%s",
            cmd.path, ms, lead, cmd.hold_ms, capture.room_id, decision.lane_text,
            transcript, agree,
            f"; {after_ms} ms of speech came after it" if after_ms else "",
        )
    elif preempted and whisper is not None and whisper.hold_ms is not None:
        # The core's early commit stopped listening before the lane's hold
        # ran out: with fast speech-to-text the two holds end together, so
        # counting these as misses would say the lane can't hear commands
        # it simply never got the silence for.
        record["fastlane_missed"] = False
        record["fastlane_preempted"] = True
        log.info(
            "fastlane had not decided %s in room=%s when the core ended the "
            "capture early; the lane heard %r", whisper.path, capture.room_id,
            capture.lane_text,
        )
    else:
        missed = whisper is not None and whisper.hold_ms is not None
        record["fastlane_missed"] = missed
        if missed:
            log.info(
                "fastlane missed %s in room=%s: whisper said %r, the lane heard "
                "%r%s", whisper.path, capture.room_id, transcript,
                capture.lane_text, f" (stopped: {gave_up})" if gave_up else "",
            )
    if timings is not None and hasattr(timings, "note"):
        timings.note(**record)


# ─── The numbers-only summary ──────────────────────────────────────────────


def _stat(values: list[float]) -> dict[str, Any]:
    from domovoi.turn_timings import percentile

    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    values = sorted(values)
    return {
        "count": len(values),
        "p50": int(math.floor(percentile(values, 0.50) + 0.5)),
        "p95": int(math.floor(percentile(values, 0.95) + 0.5)),
        "max": int(math.floor(values[-1] + 0.5)),
    }


def _num(doc: dict[str, Any], key: str) -> float | None:
    v = doc.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or v < 0:
        return None
    return float(v)


def shadow_summary(docs: Iterable[Any]) -> dict[str, Any] | None:
    """Counts and milliseconds over the ``fastlane_*`` keys of timing
    documents — never the lane's text or the path it matched. None when the
    lane is off and none of ``docs`` carries a shadow record, so a default
    install's latency summary is exactly what it was."""
    observed = commits = agree = disagree = missed = preempted = cut_off = 0
    ms: list[float] = []
    lead: list[float] = []
    cpu: list[float] = []
    for raw in docs:
        doc = raw
        if isinstance(doc, (str, bytes)):
            try:
                doc = json.loads(doc)
            except ValueError:
                continue
        if not isinstance(doc, dict) or doc.get("fastlane_seen") is not True:
            continue
        observed += 1
        c = _num(doc, "fastlane_cpu_ms")
        if c is not None:
            cpu.append(c)
        if doc.get("fastlane_missed") is True:
            missed += 1
        if doc.get("fastlane_preempted") is True:
            preempted += 1
        v = _num(doc, "fastlane_ms")
        if v is None:
            continue
        commits += 1
        ms.append(v)
        lv = _num(doc, "fastlane_lead_ms")
        if lv is not None:
            lead.append(lv)
        if doc.get("fastlane_agree") is True:
            agree += 1
        elif doc.get("fastlane_agree") is False:
            disagree += 1
        after = _num(doc, "fastlane_after_ms")
        if after:
            cut_off += 1
    if observed == 0 and _mode() == "off":
        return None
    runtime = status()
    return {
        "mode": runtime["mode"],
        "state": runtime["state"],
        "model": runtime["model"],
        "observed": observed,
        "would_commit": commits,
        "agree": agree,
        "disagree": disagree,
        "missed": missed,
        "preempted": preempted,
        "speech_after_commit": cut_off,
        "commit_ms": _stat(ms),
        "lead_ms": _stat(lead),
        "cpu_ms": _stat(cpu),
    }


# ─── python -m domovoi.fast_lane fetch ─────────────────────────────────────


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m domovoi.fast_lane")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and verify the fast-lane model now")
    f.add_argument("--model", default=None, help=f"default: fastlane_model ({DEFAULT_MODEL})")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    name = args.model or str(settings.fastlane_model or DEFAULT_MODEL)
    spec = MODELS.get(name)
    if spec is None:
        print(f"unknown model {name!r}; known: {', '.join(sorted(MODELS))}")
        return 2
    directory = ensure_model(spec)
    print(f"{spec.name}: verified in {directory}")
    print(f"source: {spec.source}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
