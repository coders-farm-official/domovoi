"""TTS engine router.

A short fallback chain that starts at the preferred engine. Output is always
a complete WAV file's bytes (int16 mono, native sample rate of the engine),
so HTTP clients can stream it directly without re-encoding. Every rung is
local: nothing Domovoi says leaves the house.

(The Microsoft Edge online voices, edge-tts, were dropped on 2026-10-12, by
the owner's decision: Microsoft's service had started refusing edge-tts,
every reply text went to Microsoft, and edge-tts is LGPL. An ``engine`` of
"edge" still named by an old setting or registry row is spoken by Piper;
the boot removes the rows, domovoi/voice_catalog.py.)

- `piper`  — local neural (offline, auto-downloads voice, 22 kHz): a Piper
  VITS model run directly on ONNX Runtime, fed by Domovoi's own permissive
  text-to-phoneme converter (``domovoi/phonemizer``; no espeak-ng, no
  piper-tts). It speaks voices trained on espeak's British ``"en"`` phoneme
  set (the three public-domain voices Domovoi offers, ``SHIPPED_VOICES``)
  or its American ``"en-us"`` set (most voices people train and upload):
  the converter writes the set the voice's ``.onnx.json`` names. A voice
  trained on any other set can't speak here and the chain carries on to
  `system` (:func:`piper_voice_problem` says why, for the UI). The
  one-time voice download goes through the egress gate, so it is refused
  under ``never`` and the chain carries on to `system`.
- `system` — the OS's own synthesizer: pyttsx3/SAPI5 on Windows,
  `espeak-ng` (or `espeak`) on Linux/BSD when the owner installed one (Domovoi
  no longer installs it), `say` on macOS. Robotic, but it
  is the floor of the chain — the thing that still talks when the network
  is down AND the Piper voice is missing or broken. Returns None when no
  system synthesizer is installed, which just ends the chain.

Per-engine failure (network drop, missing voice, etc.) advances to the next
rung. :meth:`RealTTSClient.synthesize_detailed` says which rung actually
produced the audio (:class:`SynthResult`), so a caller that stores audio
(the per-voice clips in ``domovoi/canned_sounds.py``) can tag it with the
voice it is really in. Synchronous engine work runs in a threadpool to keep
the event loop responsive.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from domovoi import egress, executors, net_safety
from domovoi.config import settings
from domovoi.speech_sanitize import sanitize_for_speech

log = logging.getLogger(__name__)

ENGINES: tuple[str, ...] = ("piper", "system")


@dataclass(frozen=True)
class SynthResult:
    """What a synthesis produced, and which rung of the chain produced it.

    ``engine`` is ``"piper"`` or ``"system"``, or ``""`` when every rung
    failed and ``wav`` is the empty WAV callers get so they don't crash.
    ``voice`` is the Piper model_ref the rung used, and
    None for ``system`` (the OS voice has no id here) and for the empty
    result."""

    wav: bytes
    engine: str
    voice: str | None
    # Why each rung tried before the one that answered gave nothing, in
    # words for the owner ("piper: en_GB-x.onnx can't speak here: ..."):
    # what a sample that can't play its own voice says. Not part of
    # equality.
    failures: tuple[str, ...] = field(default=(), compare=False)
    # The Piper voice the chain stopped for because its files aren't on
    # disk yet (only while downloads are deferred: see synthesize_detailed);
    # None otherwise. Not part of equality.
    missing_voice: str | None = field(default=None, compare=False)


def engine_failure_words(engine: str, exc: BaseException) -> str:
    """One rung's failure, short and in words (no URLs)."""
    if isinstance(exc, egress.InternetTurnedOff):
        return f"{engine}: {egress.TURNED_OFF_REASON}"
    text = str(exc).split(", url=")[0].strip() or type(exc).__name__
    return f"{engine}: {text[:160]}"


def engine_order(preferred: str | None) -> list[str]:
    """The rungs to try, in order, for a call that prefers ``preferred``:
    ``system`` first only when asked for, else Piper; anything else
    (``piper``, the dropped ``edge``, an unknown name) is Piper.

    =====================  ===========================
    preferred              order
    =====================  ===========================
    piper / edge / other   piper, system
    system                 system, piper
    =====================  ===========================
    """
    pref = (preferred or "").strip().lower()
    if pref == "system":
        return ["system", "piper"]
    return ["piper", "system"]


class TTSClient(Protocol):
    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes: ...

    async def synthesize_detailed(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> SynthResult: ...


class TTSStubClient:
    """Deterministic stub — returns an empty WAV so tests can assert length
    without actually running TTS."""

    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        return (await self.synthesize_detailed(text, engine=engine, voice=voice)).wav

    async def synthesize_detailed(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> SynthResult:
        # Parity with the real client — the scrub is a no-op on the stub's
        # empty-WAV output, but keeps behavior identical if a future stub ever
        # echoes text back.
        _ = sanitize_for_speech(text or "")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(b"")
        # The stub "renders" on the first rung the real router would try.
        requested = (engine or "piper").strip().lower()
        rung = engine_order(requested)[0]
        return SynthResult(
            wav=buf.getvalue(),
            engine=rung,
            voice=(voice if rung == requested else None) if rung != "system" else None,
        )


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _wav_is_silent(wav_bytes: bytes) -> bool:
    """True only if ``wav_bytes`` parses as a WAV with ZERO audio frames — a
    header-only render that's effectively silence. Non-WAV / unparseable bytes
    return False (benefit of the doubt — that's the test markers' shape and any
    future raw format). The engine router treats a zero-frame "success" (e.g. a
    bad uploaded voice model or an engine hiccup) as a FAILURE and falls through
    to the next engine, so a flaky primary engine degrades to audible local TTS
    instead of silence."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getnframes() == 0
    except (wave.Error, EOFError, OSError):
        return False


_piper_voice_cache: dict[str, "PiperVoice"] = {}
# Guards _piper_voice_locks only, and is never held across a download or
# a model load: one voice's first fetch (minutes on a slow line) must not
# hold up a reply in another voice. Each voice has its own lock for that.
_piper_load_lock = threading.Lock()
_piper_voice_locks: dict[str, threading.Lock] = {}


def _piper_voice_lock(voice: str) -> threading.Lock:
    """The lock that makes one voice's download and load happen once."""
    with _piper_load_lock:
        lock = _piper_voice_locks.get(voice)
        if lock is None:
            lock = _piper_voice_locks[voice] = threading.Lock()
        return lock


def _voices_dir() -> Path:
    return Path(settings.voice_models_dir)


# ─── Piper voice downloads (CORE-18) ──────────────────────────────────────
#
# A standard voice name downloads once from Hugging Face's rhasspy/piper-voices.
# The name must have the catalogue's shape (a value with "/" or ".." would
# otherwise name both a different remote file and a write outside the
# voices dir), every hop goes through net_safety (public addresses only,
# redirects re-checked) on an egress-hooked client, the body is capped, and
# both files are checked against a digest before either lands where the
# loader looks: the pins below for the voices Domovoi offers, otherwise the
# digests Hugging Face publishes for the files at ``main``.

PIPER_REPO_URL = "https://huggingface.co/rhasspy/piper-voices"
PIPER_API_URL = "https://huggingface.co/api/models/rhasspy/piper-voices"
# The rhasspy/piper-voices commit the pins were read from (2026-10-12).
PIPER_PINNED_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"


@dataclass(frozen=True)
class ShippedVoice:
    """One of the voices Domovoi offers: public domain (LibriVox / LJ Speech
    recordings, trained from scratch by Bryce Beattie, no lessac base), and
    trained on espeak's British "en" phoneme set, one of the two sets
    domovoi/phonemizer writes. ``onnx_sha256`` is the LFS oid of the model at
    PIPER_PINNED_REVISION; ``json_blob_sha1`` the git blob id of its config,
    sha1(b"blob <len>\\0" + content)."""

    model_ref: str
    name: str        # the spoken name ("switch to Norman")
    sex: str
    sample_rate: int
    onnx_sha256: str
    onnx_bytes: int
    json_blob_sha1: str
    json_bytes: int


SHIPPED_VOICES: tuple[ShippedVoice, ...] = (
    ShippedVoice("en_US-norman-medium", "Norman", "male", 22050,
                 "b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71", 63531379,
                 "64985a9c6620d5522c500dd42224be7998f83f39", 4968),
    ShippedVoice("en_US-kristin-medium", "Kristin", "female", 22050,
                 "5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4", 63531379,
                 "e94be84ab0dc3532b4b2b48cbb1f5d4a8746bedd", 4968),
    ShippedVoice("en_US-ljspeech-medium", "Linda", "female", 22050,
                 "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22", 63531379,
                 "73ecdec4f6ab17a437936652dfe3a925bb19e098", 4972),
)
# The house voice: Kristin since 2026-10-12 (the owner's pick), Norman
# before; an install still on Norman moves once (domovoi/voice_migration.py).
DEFAULT_PIPER_VOICE = "en_US-kristin-medium"
PREVIOUS_DEFAULT_PIPER_VOICE = "en_US-norman-medium"
# model_ref -> (.onnx SHA-256, .onnx bytes, .onnx.json git blob SHA-1,
# .onnx.json bytes), read from the Hub's tree API at PIPER_PINNED_REVISION.
PIPER_PINS: dict[str, tuple[str, int, str, int]] = {
    v.model_ref: (v.onnx_sha256, v.onnx_bytes, v.json_blob_sha1, v.json_bytes) for v in SHIPPED_VOICES
}
# The voices Domovoi offered before 2026-10-12 whose licences don't allow
# every use: lessac is research-only, amy and ryan have non-commercial data,
# and kusal, joe, kathleen, alan and alba were fine-tuned from lessac or
# ryan. An install configured for one of them moves to DEFAULT_PIPER_VOICE,
# and their registry rows and files go (domovoi/voice_migration.py,
# domovoi/voice_catalog.py). The old catalog's cori is public domain and
# trained on the same "en" phonemes, so an install that chose it keeps it;
# it is just no longer offered.
RETIRED_PIPER_VOICES: frozenset[str] = frozenset({
    "en_US-kusal-medium", "en_US-lessac-medium", "en_US-amy-medium", "en_US-ryan-high",
    "en_US-joe-medium", "en_US-kathleen-low", "en_GB-alan-medium", "en_GB-alba-medium",
})
# LANG_REGION-SPEAKER-QUALITY, the catalogue's naming ("en_US-lessac-medium",
# "en_GB-northern_english_male-medium", "zh_CN-huayan-x_low").
PIPER_MODEL_REF_RE = re.compile(
    r"^(?P<lang>[a-z]{2,3})_[A-Z]{2}-[A-Za-z0-9_]{1,64}-(?:x_low|low|medium|high)$"
)
# The largest published voice is ~120 MB; anything far past that is not one.
PIPER_ONNX_MAX_BYTES = 256 * 1024 * 1024
PIPER_JSON_MAX_BYTES = 1024 * 1024
_PIPER_TREE_MAX_BYTES = 1024 * 1024
_PIPER_CHUNK = 1 << 20


class PiperDownloadError(ValueError):
    """A Piper voice download refused or failed its check; nothing was kept."""


def _piper_http_client():
    """The client every Piper fetch uses: egress-hooked (each request is
    refused under ``never``, redirects included). Tests replace it."""
    import httpx

    return egress.sync_client(timeout=httpx.Timeout(30.0, read=120.0))


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _inside(directory: Path, path: Path) -> bool:
    try:
        return path.resolve().parent == directory.resolve()
    except OSError:
        return False


def _piper_expected(model_ref: str, rel_dir: str) -> tuple[str, tuple[str, int, str, int]]:
    """(revision, digests) for ``model_ref``: the pins for a catalogue
    voice, else what the Hub's tree API publishes for its files at
    ``main`` (the LFS SHA-256 of the model, the blob id of the config)."""
    pinned = PIPER_PINS.get(model_ref)
    if pinned is not None:
        return PIPER_PINNED_REVISION, pinned
    url = f"{PIPER_API_URL}/tree/main/{rel_dir}"
    with _piper_http_client() as client:
        result = net_safety.fetch_bytes_sync(url, max_bytes=_PIPER_TREE_MAX_BYTES, client=client)
    if result.status_code != 200:
        raise PiperDownloadError(
            f"Hugging Face lists no files for {model_ref} (HTTP {result.status_code})"
        )
    import json

    try:
        entries = {
            str(e.get("path", "")).rsplit("/", 1)[-1]: e
            for e in json.loads(result.content)
            if isinstance(e, dict)
        }
        onnx = entries[f"{model_ref}.onnx"]
        cfg = entries[f"{model_ref}.onnx.json"]
        digests = (
            str(onnx["lfs"]["oid"]), int(onnx["lfs"]["size"]),
            str(cfg["oid"]), int(cfg["size"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise PiperDownloadError(f"{model_ref} is not a published Piper voice: {e}") from None
    if not re.fullmatch(r"[0-9a-f]{64}", digests[0]) or not re.fullmatch(r"[0-9a-f]{40}", digests[2]):
        raise PiperDownloadError(f"Hugging Face published no usable digest for {model_ref}")
    return "main", digests


def _piper_fetch_to_file(url: str, dest: Path, *, size: int) -> str:
    """Stream ``url`` into ``dest`` through net_safety, refusing to read
    past ``size`` bytes or a body of another length; the SHA-256 of what
    was written."""
    h = hashlib.sha256()
    written = 0
    with _piper_http_client() as client:
        response = net_safety.open_stream_sync(client, url)
        try:
            if response.status_code != 200:
                raise PiperDownloadError(f"{url} answered HTTP {response.status_code}")
            with open(dest, "wb") as f:
                for chunk in response.iter_bytes(_PIPER_CHUNK):
                    written += len(chunk)
                    if written > size:
                        raise PiperDownloadError(f"{url} is larger than the expected {size} bytes")
                    h.update(chunk)
                    f.write(chunk)
        finally:
            response.close()
    if written != size:
        raise PiperDownloadError(f"{url} gave {written} bytes, expected {size}")
    return h.hexdigest()


def _piper_download(model_ref: str, voices_dir: Path) -> Path:
    """Download, verify and install ``model_ref``'s two files into
    ``voices_dir``; the ``.onnx`` path. Nothing unverified is left where
    the loader looks."""
    m = PIPER_MODEL_REF_RE.fullmatch(model_ref)
    if m is None:
        raise ValueError(
            f"Piper voice must be an uploaded .onnx or a LANG_REGION-SPEAKER-QUALITY "
            f"name from the voice catalogue, got {model_ref!r}"
        )
    onnx = voices_dir / f"{model_ref}.onnx"
    jpath = voices_dir / f"{model_ref}.onnx.json"
    if not (_inside(voices_dir, onnx) and _inside(voices_dir, jpath)):
        raise ValueError(f"Piper voice {model_ref!r} would be written outside {voices_dir}")
    lang_full, speaker, quality = model_ref.split("-")
    rel_dir = f"{m.group('lang')}/{lang_full}/{speaker}/{quality}"
    # The internet answer first, before any request (the hooked client
    # refuses every hop too).
    egress.require_destination(f"{PIPER_REPO_URL}/resolve/main/{rel_dir}/{model_ref}.onnx")
    revision, (onnx_sha, onnx_size, cfg_blob, cfg_size) = _piper_expected(model_ref, rel_dir)
    if not (0 < onnx_size <= PIPER_ONNX_MAX_BYTES and 0 < cfg_size <= PIPER_JSON_MAX_BYTES):
        raise PiperDownloadError(f"{model_ref}: published sizes are out of range")
    base = f"{PIPER_REPO_URL}/resolve/{revision}/{rel_dir}/{model_ref}"
    log.info("downloading Piper voice %s (one-time, revision %s)", model_ref, revision[:12])
    part_onnx = voices_dir / f".{model_ref}.onnx.part"
    part_json = voices_dir / f".{model_ref}.onnx.json.part"
    try:
        with _piper_http_client() as client:
            cfg = net_safety.fetch_bytes_sync(base + ".onnx.json", max_bytes=cfg_size, client=client)
        if cfg.status_code != 200:
            raise PiperDownloadError(f"{base}.onnx.json answered HTTP {cfg.status_code}")
        if len(cfg.content) != cfg_size or _git_blob_sha1(cfg.content) != cfg_blob:
            raise PiperDownloadError(f"{model_ref}.onnx.json does not match its published digest")
        part_json.write_bytes(cfg.content)
        got = _piper_fetch_to_file(base + ".onnx", part_onnx, size=onnx_size)
        if got != onnx_sha:
            raise PiperDownloadError(
                f"{model_ref}.onnx checksum mismatch: expected {onnx_sha}, got {got}"
            )
        # The model first: a voice counts as on disk once its .onnx.json is.
        os.replace(part_onnx, onnx)
        os.replace(part_json, jpath)
    except net_safety.ResponseTooLarge as e:
        raise PiperDownloadError(f"{model_ref}: {e}") from None
    finally:
        part_onnx.unlink(missing_ok=True)
        part_json.unlink(missing_ok=True)
    return onnx


def _piper_local_path(model_ref: str) -> Path | None:
    """The ``.onnx`` a Piper ``model_ref`` loads from WITHOUT a download —
    an uploaded model's own path, or ``<name>.onnx`` + ``.onnx.json`` in the
    voice-models dir — or None. Creates nothing. A bare name is looked up
    in the voice-models dir only, never beside it."""
    cand = Path(model_ref)
    if cand.suffix == ".onnx" and cand.is_file():
        return cand
    voices_dir = _voices_dir()
    onnx = voices_dir / f"{model_ref}.onnx"
    jpath = voices_dir / f"{model_ref}.onnx.json"
    if not (_inside(voices_dir, onnx) and _inside(voices_dir, jpath)):
        return None
    if onnx.exists() and jpath.exists():
        return onnx
    return None


def piper_voice_on_disk(model_ref: str) -> bool:
    """Whether a Piper voice can speak without fetching anything: already
    loaded, or its model files are on disk."""
    return model_ref in _piper_voice_cache or _piper_local_path(model_ref) is not None


def _piper_voice_path(model_ref: str) -> Path:
    """Resolve a Piper ``model_ref`` to its ``.onnx`` file.

    Three forms, checked in order:
      1. A direct path to an uploaded ``.onnx`` (absolute or relative) that
         exists on disk — load it as-is (web-uploaded voices store this).
      2. A bare name whose ``<name>.onnx`` + ``.onnx.json`` already sit in
         the voice-models dir (uploaded-by-name, or a previously downloaded
         HF voice).
      3. A standard HF voice name (LANG_REGION-SPEAKER-QUALITY) —
         downloaded into the voice-models dir, one time, and checked
         against its digest first (:func:`_piper_download`). The download
         goes through the egress gate: under ``INTERNET_ACCESS=never`` it
         raises ``egress.InternetTurnedOff`` without a request, and the
         router moves on to the next rung.
    """
    # (1) Direct path to an uploaded model, (2) already under the voices dir.
    local = _piper_local_path(model_ref)
    if local is not None:
        return local

    voices_dir = _voices_dir()
    voices_dir.mkdir(parents=True, exist_ok=True)
    # (3) HF auto-download.
    return _piper_download(model_ref, voices_dir)


class UnsupportedPiperVoice(ValueError):
    """A Piper model this engine can't speak: its config asks for phonemes
    other than espeak's English sets ("en", "en-us": the ones Domovoi's
    converter writes), or it is not a Piper model at all."""


def piper_config_problem(cfg: object) -> str | None:
    """Why a voice with this ``.onnx.json`` (parsed) can't speak here, in
    words for the owner; None when it can (espeak "en" or "en-us"
    phonemes and a phoneme id map)."""
    from domovoi.phonemizer import dialect_for_espeak_voice

    if not isinstance(cfg, dict) or not isinstance(cfg.get("phoneme_id_map"), dict):
        return "its config has no phoneme map, so it is not a Piper voice config"
    phoneme_type = str(cfg.get("phoneme_type") or "espeak")
    espeak = cfg.get("espeak")
    espeak_voice = str((espeak.get("voice") if isinstance(espeak, dict) else None) or "")
    if phoneme_type != "espeak":
        return (f"it was trained on {phoneme_type!r} phonemes; Domovoi writes espeak's English phonemes "
                f"(\"en\" or \"en-us\") only")
    if dialect_for_espeak_voice(espeak_voice) is None:
        return (f"it was trained on espeak's {espeak_voice or 'unnamed'!r} phonemes; Domovoi writes "
                f"English (\"en\" or \"en-us\") only")
    return None


def piper_voice_problem(model_ref: str) -> str | None:
    """Why the Piper voice ``model_ref`` can't speak, read from its config
    on disk without loading the model; None when it can, or when it is not
    on disk (a catalogue voice downloads on first use)."""
    local = _piper_local_path(model_ref)
    if local is None:
        return None
    try:
        cfg = json.loads(Path(f"{local}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"its config can't be read ({e.__class__.__name__})"
    return piper_config_problem(cfg)


def piper_voice_dialect(model_ref: str) -> str:
    """The phoneme set ("en" or "en-us") the Piper voice ``model_ref``
    speaks: the loaded voice's, else its config's on disk, else "en" (the
    catalogue voices). Loads nothing."""
    from domovoi.phonemizer import DEFAULT_DIALECT, dialect_for_espeak_voice

    loaded = _piper_voice_cache.get(model_ref)
    if loaded is not None:
        return loaded.dialect
    local = _piper_local_path(model_ref)
    if local is not None:
        try:
            cfg = json.loads(Path(f"{local}.json").read_text(encoding="utf-8"))
            return dialect_for_espeak_voice((cfg.get("espeak") or {}).get("voice")) or DEFAULT_DIALECT
        except (OSError, ValueError, AttributeError):
            pass
    return DEFAULT_DIALECT


# Silence between the sentences of one reply, as the Piper voices' original
# command line put it (and as the owner heard them on 2026-10-12).
SENTENCE_SILENCE_SEC = 0.2
# Said, in the voice, when a text has words but none the converter can read
# (no Latin letters: another script entirely), rather than silence.
UNREADABLE_TEXT_FALLBACK = "Sorry, I can't read that out loud."
# The most text one synthesis call speaks (a few minutes of speech). Replies
# are spoken a sentence or a paragraph at a time; a longer text is cut at
# the last sentence end (else the last space) before this and the rest
# dropped with a log line, so one runaway reply can't hold a voice turn.
MAX_SPOKEN_CHARS = 5000


# Some voices run away on a short sentence said on its own: Norman (the
# shipped default until 2026-10-12) says "Cameron." as four to six seconds
# of babble in no language (and "Okay.", "Done.", "Hi, I'm Norman." much
# the same), while
# the very same ids come out right after a sentence of context, and Kristin
# and Linda say them right either way (measured 2026-10-12, Whisper and
# by length). A voice is checked once, the first time it says a short
# sentence (:meth:`PiperVoice.runs_away`; the boot warm-up's "Okay." is
# one): if one of the probe words alone comes out longer than
# RUNAWAY_SECONDS, its short sentences are said after the lead-in sentence
# in the same call and cut off again at the pause between the two
# (:func:`cut_after_lead_in`). The Kotlin engine on the phone does the same
# with the same numbers (PiperSpeechOut.kt, LeadIn.kt).
LEAD_IN_TEXT = "The weather today is sunny with a high of seventy two degrees."
# A sentence with fewer phonemes than this (letters of the phoneme string,
# not stress or length marks) counts as short. Norman failed on sentences
# of up to 12 and was right from about 14, in a 32-sentence sample.
LEAD_IN_MAX_PHONEMES = 20
RUNAWAY_PROBES = ("Cameron.", "Norman.")
# Norman says each probe in 3.5 to 6 s; Kristin and Linda in about 0.5 s.
RUNAWAY_SECONDS = 1.5
# The cut: a pause is 10 ms frames quieter than this (dB under the loudest
# frame) for at least LEAD_IN_MIN_PAUSE_SEC (the lead-in's end is 0.3 to
# 0.9 s; the stops inside it at most 0.14 s), the first one that starts
# after LEAD_IN_SEARCH_FROM of the lead-in's own length (said before a
# sentence it lasts 0.8 to 0.9 of that); the sentence's audio starts
# LEAD_IN_KEEP_SEC before that pause ends, so a soft first sound (a vowel,
# an n) is not clipped. No such pause: the call is made once more, then
# the sentence is said alone.
LEAD_IN_PAUSE_DB = -40.0
LEAD_IN_MIN_PAUSE_SEC = 0.16
LEAD_IN_SEARCH_FROM = 0.6
LEAD_IN_KEEP_SEC = 0.1
LEAD_IN_FADE_SEC = 0.005
LEAD_IN_TRIES = 2


def phoneme_count(phonemes: str) -> int:
    """How many phonemes a phoneme string holds: its letters, without the
    stress and length marks (ˈ ˌ ː), spaces or punctuation."""
    return sum(1 for c in phonemes if c.isalpha() and c not in "ˈˌː")


# A short sentence with no punctuation at its end is out of what the voices
# were trained on, and Kristin (the default since 2026-10-12) says one as a
# held first sound or a stretched word: a lone "seven" lasts 1.7 to 2.9 s
# against 0.7 s for "seven.", "three" is heard as "B", "nine" as a hum
# (measured 2026-10-17 over the setup digit clips, Whisper and by length;
# owner's satellite spoke its approval code that way). Such a sentence is
# said with a full stop at its end. Longer ones are left alone: a reply
# chunk cut mid-sentence keeps its rising, unfinished sound.
SENTENCE_END_MARKS = ".!?,;:"
_CLOSING_MARKS = "\"')]}"


def end_short_sentence(phonemes: str) -> str:
    """``phonemes`` with a full stop added when it is a short sentence
    (fewer than LEAD_IN_MAX_PHONEMES phonemes) that has none of
    SENTENCE_END_MARKS at its end (closing quotes and brackets aside)."""
    body = phonemes.rstrip()
    if not body or phoneme_count(body) >= LEAD_IN_MAX_PHONEMES:
        return phonemes
    words = body.rstrip(_CLOSING_MARKS)
    if not words or words[-1] in SENTENCE_END_MARKS:
        return phonemes
    return body + "."


def cut_after_lead_in(audio, sample_rate: int, lead_in_seconds: float):
    """The part of ``audio`` (the lead-in sentence, then a short sentence,
    from one model call) after the pause that ends the lead-in, faded in;
    None when no such pause is found. ``lead_in_seconds``: how long the
    lead-in alone lasts at this call's speed."""
    import numpy as np

    hop = max(1, int(sample_rate * 0.01))
    n = len(audio) // hop
    if n < 3:
        return None
    frames = np.asarray(audio[: n * hop], dtype=np.float32).reshape(n, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1)) + 1e-12
    quiet = 20 * np.log10(rms / float(np.max(rms))) < LEAD_IN_PAUSE_DB
    start = int(LEAD_IN_SEARCH_FROM * lead_in_seconds / 0.01)
    min_frames = int(round(LEAD_IN_MIN_PAUSE_SEC / 0.01))
    k = max(1, start)
    while k < n:
        if not quiet[k]:
            k += 1
            continue
        j = k
        while j < n and quiet[j]:
            j += 1
        if j - k >= min_frames and j < n:
            cut = max(k, j - int(round(LEAD_IN_KEEP_SEC / 0.01))) * hop
            out = np.array(audio[cut:], dtype=np.float32)
            fade = min(len(out), max(1, int(sample_rate * LEAD_IN_FADE_SEC)))
            out[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            return out
        k = j
    return None


def _cap_text(text: str) -> str:
    if len(text) <= MAX_SPOKEN_CHARS:
        return text
    head = text[:MAX_SPOKEN_CHARS]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut < MAX_SPOKEN_CHARS // 2:
        cut = head.rfind(" ")
    cut = cut + 1 if cut > 0 else MAX_SPOKEN_CHARS
    log.warning("TTS: a %d-character text was cut to %d characters", len(text), cut)
    return text[:cut]


class PiperVoice:
    """A Piper VITS voice on ONNX Runtime, fed by domovoi.phonemizer.

    Loads ``<model>.onnx`` and its ``.onnx.json``: the phoneme set
    (``espeak.voice``: "en" or "en-us", :attr:`dialect`), the phoneme id
    map, the sample rate and the inference scales come from the config.
    Thread-safe to share (ONNX Runtime's ``run`` is)."""

    def __init__(self, onnx_path: Path, *, threads: int | None = None) -> None:
        """``threads``: ONNX Runtime's intra-op threads (default: its own
        choice; scripts that need repeatable output pass 1)."""
        import numpy as np
        import onnxruntime as ort

        from domovoi.phonemizer import dialect_for_espeak_voice

        cfg_path = Path(f"{onnx_path}.json")
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise UnsupportedPiperVoice(f"{cfg_path.name}: not a readable Piper config ({e})") from None
        problem = piper_config_problem(cfg)
        if problem is not None:
            raise UnsupportedPiperVoice(f"{onnx_path.name} can't speak here: {problem}")
        inf = cfg.get("inference") or {}
        self.name = onnx_path.name
        self.np = np
        self.dialect: str = dialect_for_espeak_voice(cfg["espeak"]["voice"])
        self.id_map: dict[str, list[int]] = cfg["phoneme_id_map"]
        # Symbols the converter writes that this voice's map lacks -> what
        # is said instead (phoneme_ids.fit_to_map), filled as they turn up.
        self._missing: dict[str, str] = {}
        self.sample_rate = int((cfg.get("audio") or {}).get("sample_rate") or 22050)
        self.noise_scale = float(inf.get("noise_scale", 0.667))
        self.length_scale = float(inf.get("length_scale", 1.0))
        self.noise_w = float(inf.get("noise_w", 0.8))
        self.multi_speaker = int(cfg.get("num_speakers") or 1) > 1
        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        if threads:
            so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
        # Whether short sentences are said after the lead-in (runs_away),
        # and the lead-in's phonemes and length alone: checked on first need.
        self._lead_in: tuple[bool, str, float] | None = None
        self._lead_in_lock = threading.Lock()

    def _raw_audio(self, ids: list[int], speed: float):
        """The model's float output for ``ids``, not normalized."""
        np = self.np
        length = self.length_scale / speed if speed > 0 else self.length_scale
        args = {
            "input": np.array([ids], dtype=np.int64),
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": np.array([self.noise_scale, length, self.noise_w], dtype=np.float32),
        }
        if self.multi_speaker:
            args["sid"] = np.array([0], dtype=np.int64)
        return self.session.run(None, args)[0].squeeze()

    def _lead_in_state(self) -> tuple[bool, str, float]:
        """(runs away, lead-in phonemes, lead-in seconds at speed 1),
        measured once: each probe word said alone, then the lead-in."""
        if self._lead_in is None:
            with self._lead_in_lock:
                if self._lead_in is None:
                    from domovoi.phonemizer import get_phonemizer

                    ph = get_phonemizer()
                    longest = 0.0
                    for word in RUNAWAY_PROBES:
                        ids = self.sentence_ids(" ".join(ph.phonemize(word, dialect=self.dialect)))
                        if ids:
                            longest = max(longest, self._raw_audio(ids, 1.0).size / self.sample_rate)
                    runs_away = longest > RUNAWAY_SECONDS
                    lead = " ".join(ph.phonemize(LEAD_IN_TEXT, dialect=self.dialect))
                    seconds = 0.0
                    if runs_away:
                        ids = self.sentence_ids(lead)
                        seconds = self._raw_audio(ids, 1.0).size / self.sample_rate if ids else 0.0
                        log.info("TTS: %s says a short sentence alone in %.1f s; its short sentences "
                                 "are said after a lead-in", self.name, longest)
                    self._lead_in = (runs_away, lead, seconds)
        return self._lead_in

    def runs_away(self) -> bool:
        """Whether this voice garbles a short sentence said on its own (a
        probe word alone comes out longer than RUNAWAY_SECONDS)."""
        return self._lead_in_state()[0]

    def sentence_ids(self, phonemes: str) -> list[int] | None:
        """One sentence's phoneme string -> this voice's ids, a symbol its
        map lacks said as the nearest one it has (logged once); None when
        nothing in it is a sound the voice knows."""
        from domovoi.phonemizer import fit_to_map, phoneme_ids, speaks_anything

        seen = len(self._missing)
        fitted = fit_to_map(phonemes, self.id_map, self._missing)
        if len(self._missing) > seen:
            new = list(self._missing)[seen:]
            log.warning("TTS: %s has no %s in its phoneme map; said as %s", self.name,
                        ", ".join(repr(c) for c in new),
                        ", ".join(repr(self._missing[c]) if self._missing[c] else "nothing" for c in new))
        ids = phoneme_ids(fitted, self.id_map)
        return ids if speaks_anything(ids, self.id_map) else None

    def sentence_audio(self, phonemes: str, speed: float) -> "object":
        """One sentence's phoneme string -> float32 samples in [-1, 1]
        (none when nothing in it is a sound the voice knows). A short
        sentence in a voice that runs away is said after the lead-in and
        cut off from it. A short sentence with no end mark gets a full
        stop (:func:`end_short_sentence`)."""
        np = self.np
        phonemes = end_short_sentence(phonemes)
        ids = self.sentence_ids(phonemes)
        if ids is None:
            return np.zeros(0, dtype=np.float32)
        audio = None
        if phoneme_count(phonemes) < LEAD_IN_MAX_PHONEMES:
            runs_away, lead, lead_seconds = self._lead_in_state()
            if runs_away and lead_seconds > 0:
                both = self.sentence_ids(f"{lead} {phonemes}")
                for _ in range(LEAD_IN_TRIES if both is not None else 0):
                    audio = cut_after_lead_in(self._raw_audio(both, speed), self.sample_rate,
                                              lead_seconds / (speed if speed > 0 else 1.0))
                    if audio is not None:
                        break
                if audio is None:
                    log.warning("TTS: no pause after the lead-in; %s says the sentence alone", self.name)
        if audio is None:
            audio = self._raw_audio(ids, speed)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 1e-8:
            return np.zeros_like(audio, dtype=np.float32)
        return np.clip(audio / peak, -1.0, 1.0).astype(np.float32)

    def synthesize_pcm(self, sentences: list[str], speed: float) -> bytes:
        """int16 mono PCM for the sentences, a short silence between them."""
        np = self.np
        gap = np.zeros(int(SENTENCE_SILENCE_SEC * self.sample_rate), dtype=np.int16)
        parts = []
        for ph in sentences:
            audio = self.sentence_audio(ph, speed)
            if not audio.size:
                continue
            if parts:
                parts.append(gap)
            parts.append((audio * 32767).astype(np.int16))
        return b"".join(p.tobytes() for p in parts)


# How a voice that isn't on disk is met in the thread that wants it: None,
# downloaded there (the old way, for callers outside the speech chain);
# "defer", the chain stops and says so (SynthResult.missing_voice) so the
# caller fetches it in the downloads pool, holding no speech thread; "fail",
# a failure of the Piper rung (the fetch failed already), and the chain
# falls back. Set by RealTTSClient.synthesize_detailed around its call;
# the speech pool copies it into the thread (domovoi/executors.py).
_DOWNLOADS_IN_THREAD: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tts_downloads_in_thread", default=None)


class _PiperVoiceNotOnDisk(Exception):
    def __init__(self, voice: str) -> None:
        super().__init__(f"Piper voice {voice} is not on disk yet")
        self.voice = voice


def _piper_voice(voice: str) -> PiperVoice:
    """The loaded voice for ``voice`` (a model_ref), loading it once.
    Blocking — worker threads only. The voice's own lock makes a boot or
    connect-time preload and a turn racing it download and load the model
    once between them; a turn in another voice never waits on it (until
    2026-10-14 one global lock was held through the download, and every
    reply in every voice queued behind a slow one, a thread each: see
    domovoi/executors.py). In the speech chain a voice that isn't on disk
    is never downloaded here (:data:`_DOWNLOADS_IN_THREAD`)."""
    v = _piper_voice_cache.get(voice)
    if v is None:
        mode = _DOWNLOADS_IN_THREAD.get()
        if mode is not None and _piper_local_path(voice) is None:
            if mode == "defer":
                raise _PiperVoiceNotOnDisk(voice)
            raise PiperDownloadError(
                f"Piper voice {voice} is not on disk: its download failed or was refused")
        with _piper_voice_lock(voice):
            v = _piper_voice_cache.get(voice)
            if v is None:
                v = PiperVoice(_piper_voice_path(voice))
                _piper_voice_cache[voice] = v
    return v


def piper_voice_loaded(voice: str) -> bool:
    return voice in _piper_voice_cache


def preload_piper_voice_sync(voice: str) -> bool:
    """Load a Piper voice (and the text-to-phoneme converter) and
    synthesize one short word with it, so the first reply in that voice
    pays neither the model load nor the first inference. Blocking — run it
    in a worker thread (``domovoi/speech_warmup.py``, which fetches the
    voice first with :func:`ensure_piper_voice`). True when the voice
    rendered audio."""
    return _synth_piper_sync("Okay.", voice, 1.0) is not None


# One fetch per voice at a time, per event loop: every reply waiting for
# the same voice awaits it, and none holds a thread meanwhile.
_DOWNLOADS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Task]]" = (
    weakref.WeakKeyDictionary()
)


def _fetch_piper_voice_sync(voice: str) -> Path:
    with _piper_voice_lock(voice):
        return _piper_voice_path(voice)


async def ensure_piper_voice(voice: str) -> None:
    """Make sure a Piper voice's files are on disk, downloading them first
    if they aren't: in the "downloads" pool (domovoi/executors.py), never
    in the speech pool, so a reply in a voice that is loaded never waits
    behind a voice that is still coming (review 2026-10-15: four turns
    waiting on one download took every speech thread). Replies waiting for
    the same voice share the one fetch. Raises what the fetch raised."""
    if piper_voice_on_disk(voice):
        return
    loop = asyncio.get_running_loop()
    tasks = _DOWNLOADS.setdefault(loop, {})
    task = tasks.get(voice)
    if task is None or task.done():
        task = tasks[voice] = loop.create_task(
            executors.run("downloads", _fetch_piper_voice_sync, voice))
    await asyncio.shield(task)


def _phonemize(text: str, dialect: str = "en") -> list[str]:
    from domovoi.phonemizer import get_phonemizer

    return get_phonemizer().phonemize(text, dialect=dialect)


def _synth_piper_sync(text: str, voice: str, speed: float) -> bytes | None:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return None

    v = _piper_voice(voice)
    text = _cap_text(text)
    dialect = getattr(v, "dialect", "en")   # the voice's phoneme set
    sentences = _phonemize(text, dialect)
    if not sentences and any(ch.isalnum() for ch in text):
        # Words, but none in a script the converter reads (Cyrillic, CJK,
        # ...): say so in the voice instead of going silent.
        log.warning("TTS: no pronounceable words in a %d-character text; saying the fallback line", len(text))
        sentences = _phonemize(UNREADABLE_TEXT_FALLBACK, dialect)
    pcm = v.synthesize_pcm(sentences, speed) if sentences else b""
    if not pcm:
        # Nothing to say (punctuation only), or a voice that rendered no
        # audio: a failure, not a silent success — return None so the
        # router falls through to the next engine instead of emitting a
        # zero-frame WAV.
        return None
    return _pcm_to_wav_bytes(pcm, v.sample_rate)


def _synth_system_windows(text: str) -> bytes | None:
    """pyttsx3 → SAPI5."""
    try:
        import pyttsx3
    except ImportError:
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        engine = pyttsx3.init()
        engine.save_to_file(text, path)
        engine.runAndWait()
        with open(path, "rb") as f:
            data = f.read()
        Path(path).unlink(missing_ok=True)
        return data
    except Exception:
        return None


# Non-Windows system synthesizers, tried in PATH order. Each entry renders
# `text` (fed on stdin) to a WAV at `path`. Reading the text from stdin
# rather than argv keeps a leading "-" from being parsed as an option and
# sidesteps command-line length limits.
def _espeak_argv(exe: str, path: str) -> list[str]:
    return [exe, "--stdin", "-w", path]


def _say_argv(exe: str, path: str) -> list[str]:
    # macOS `say` writes AIFF by default; pin WAVE/LEI16 so the bytes we
    # return are a real WAV. `-f -` reads the text from stdin.
    return [exe, "-o", path, "--file-format=WAVE",
            "--data-format=LEI16@22050", "-f", "-"]


_SYSTEM_TTS_POSIX: tuple[tuple[str, Callable[[str, str], list[str]]], ...] = (
    # espeak-ng is the near-universal Linux/BSD choice: tiny, no daemon,
    # and in every distro's default repos.
    ("espeak-ng", _espeak_argv),
    ("espeak", _espeak_argv),
    ("say", _say_argv),
)


def _synth_system_posix(text: str) -> bytes | None:
    """First available OS synthesizer on PATH, rendered to a WAV file."""
    for name, build_argv in _SYSTEM_TTS_POSIX:
        exe = shutil.which(name)
        if exe is None:
            continue
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            subprocess.run(
                build_argv(exe, path),
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=settings.tts_system_timeout_sec,
                check=True,
            )
            data = Path(path).read_bytes()
            # A synthesizer that produced no audio is a failure, not a
            # silent success — end the chain rather than emit a header-only
            # WAV (mirrors the Piper guard above; 44 = WAV header size).
            return data if len(data) > 44 else None
        except Exception as e:  # noqa: BLE001 — any failure just ends the chain
            log.debug("system TTS via %s failed: %s", name, e)
            return None
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
    return None


def _synth_system_sync(text: str) -> bytes | None:
    if sys.platform == "win32":
        return _synth_system_windows(text)
    return _synth_system_posix(text)


class RealTTSClient:
    """Engine router with per-engine fallback. Returns full WAV bytes."""

    def __init__(
        self,
        *,
        preferred_engine: str,
        piper_voice: str,
        speed: float,
    ) -> None:
        self.preferred_engine = preferred_engine
        self.piper_voice = piper_voice
        self.speed = speed

    def _engine_order(self, preferred: str) -> list[str]:
        return engine_order(preferred)

    async def synthesize(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        return (await self.synthesize_detailed(text, engine=engine, voice=voice)).wav

    async def synthesize_detailed(
        self, text: str, *, engine: str | None = None, voice: str | None = None
    ) -> SynthResult:
        """Like :meth:`synthesize`, but also says which rung of the chain
        rendered the audio and in which voice (:class:`SynthResult`)."""
        # Strip emoji + Markdown so the engines never verbalize an asterisk or
        # narrate an emoji's Unicode name. Single choke point: every spoken
        # path in the core lands here (see domovoi/speech_sanitize).
        text = sanitize_for_speech(text or "")
        # A Piper voice that isn't on disk stops the chain in the speech
        # thread (its download deferred), is fetched in the downloads pool
        # holding no speech thread, and the chain runs again: a reply in a
        # voice already loaded never waits behind one still coming (review
        # 2026-10-15: four turns waiting on one download took every speech
        # thread). If the fetch fails, the chain runs without it.
        token = _DOWNLOADS_IN_THREAD.set("defer")
        try:
            res = await executors.run("tts", self._synth_detailed_blocking, text, engine, voice)
        finally:
            _DOWNLOADS_IN_THREAD.reset(token)
        if res.missing_voice is None:
            return res
        try:
            await ensure_piper_voice(res.missing_voice)
        except Exception as e:  # noqa: BLE001 - the chain says it, and falls back
            log.warning("TTS: Piper voice %s isn't available: %s", res.missing_voice, e)
        token = _DOWNLOADS_IN_THREAD.set("fail")
        try:
            return await executors.run("tts", self._synth_detailed_blocking, text, engine, voice)
        finally:
            _DOWNLOADS_IN_THREAD.reset(token)

    def _synth_blocking(
        self, text: str, engine: str | None = None, voice: str | None = None
    ) -> bytes:
        return self._synth_detailed_blocking(text, engine, voice).wav

    def _synth_detailed_blocking(
        self, text: str, engine: str | None = None, voice: str | None = None
    ) -> SynthResult:
        # Per-call overrides win, else the construct-time globals. The voice
        # override only applies to the preferred engine (the registry pairs a
        # voice with its engine); fallback engines use their own default voice.
        preferred = (engine or self.preferred_engine or "").strip().lower()
        failures: list[str] = []
        for eng in self._engine_order(preferred):
            v = voice if (voice and eng == preferred) else None
            used: str | None = None
            try:
                if eng == "piper":
                    used = v or self.piper_voice
                    out = _synth_piper_sync(text, used, self.speed)
                elif eng == "system":
                    out = _synth_system_sync(text)
                else:
                    continue
                # Reject a zero-frame WAV (engine "succeeded" but produced no
                # audio) so we fall through to the next engine rather than
                # returning silence — the local-first safety net working.
                if out and not _wav_is_silent(out):
                    return SynthResult(wav=out, engine=eng, voice=used, failures=tuple(failures))
                failures.append(f"{eng}: no audio")
            except _PiperVoiceNotOnDisk as e:
                # Not a failure yet: the caller fetches it, then asks again.
                return SynthResult(wav=b"", engine="", voice=None, failures=tuple(failures),
                                   missing_voice=e.voice)
            except Exception as e:
                log.warning("TTS engine %s failed: %s", eng, e)
                failures.append(engine_failure_words(eng, e))
        # If every engine fails, return an empty WAV so callers don't crash.
        log.error("all TTS engines failed for text len=%d", len(text))
        return SynthResult(wav=_pcm_to_wav_bytes(b"", 16_000), engine="", voice=None, failures=tuple(failures))


_client: TTSClient | None = None


def get_tts_client() -> TTSClient:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = TTSStubClient()
    else:
        _client = RealTTSClient(
            preferred_engine=settings.tts_engine,
            piper_voice=settings.tts_piper_voice,
            speed=settings.tts_speed,
        )
    return _client


def reset_tts_client() -> None:
    """Drop the cached TTS client so the next ``get_tts_client()`` rebuilds
    from the current settings (engine / speed). Backs the config editor's
    'reapply' tier. An in-flight synth keeps its old client; the change is
    picked up on the next turn."""
    global _client
    _client = None


# Backward-compat alias.
tts_client: TTSClient = TTSStubClient()  # replaced at startup in main.py
