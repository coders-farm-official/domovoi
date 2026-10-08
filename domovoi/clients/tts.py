"""TTS engine router.

A short fallback chain that starts at the preferred engine. Output is always
a complete WAV file's bytes (int16 mono, native sample rate of the engine),
so HTTP clients can stream it directly without re-encoding.

- `edge`   — Microsoft Edge neural voices (online, very natural, ~24 kHz).
  The reply text goes to Microsoft, so Edge is an informed opt-in: it is
  used ONLY when it is the preferred engine for the call (``TTS_ENGINE=edge``
  or a voice registered on the edge engine) and the internet answer allows
  it. It is never a fallback rung, and under ``INTERNET_ACCESS=never`` it is
  never used at all (see :func:`engine_order`).
- `piper`  — local neural (offline, auto-downloads voice, 22 kHz). The
  one-time voice download goes through the egress gate, so it is refused
  under ``never`` and the chain carries on to `system`.
- `system` — the OS's own synthesizer: pyttsx3/SAPI5 on Windows,
  `espeak-ng` (or `espeak`) on Linux/BSD, `say` on macOS. Robotic, but it
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
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from domovoi import egress, net_safety
from domovoi.config import settings
from domovoi.speech_sanitize import sanitize_for_speech

log = logging.getLogger(__name__)

ENGINES: tuple[str, ...] = ("edge", "piper", "system")


@dataclass(frozen=True)
class SynthResult:
    """What a synthesis produced, and which rung of the chain produced it.

    ``engine`` is ``"edge"``, ``"piper"`` or ``"system"``, or ``""`` when
    every rung failed and ``wav`` is the empty WAV callers get so they don't
    crash. ``voice`` is the model_ref / Edge voice id the rung used, and
    None for ``system`` (the OS voice has no id here) and for the empty
    result."""

    wav: bytes
    engine: str
    voice: str | None


_edge_skip_logged = False


def engine_order(preferred: str | None) -> list[str]:
    """The rungs to try, in order, for a call that prefers ``preferred``.

    Edge appears ONLY when it is the preferred engine and the internet
    answer allows it: never as a fallback, so a broken Piper on an online
    box can't quietly send reply text to Microsoft, and never under
    ``INTERNET_ACCESS=never``. An unknown engine name is treated as Piper.

    =====================  ===========================
    preferred              order
    =====================  ===========================
    edge (allowed)         edge, piper, system
    edge (under never)     piper, system
    piper / unknown        piper, system
    system                 system, piper
    =====================  ===========================
    """
    global _edge_skip_logged
    pref = (preferred or "").strip().lower()
    if pref == "edge":
        if egress.internet_allowed():
            return ["edge", "piper", "system"]
        if not _edge_skip_logged:
            _edge_skip_logged = True
            log.info(
                "TTS: the Microsoft Edge voice is skipped (%s); speaking with "
                "Piper instead", egress.TURNED_OFF_REASON,
            )
        return ["piper", "system"]
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
        # The stub "renders" on the first rung the real router would try, so
        # what it reports follows the same Edge rules.
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


def _synth_edge_sync(text: str, voice: str, speed: float) -> bytes | None:
    """Run edge-tts (async under the hood) from a sync context.

    Refuses under ``INTERNET_ACCESS=never`` before anything is imported or
    sent (``egress.InternetTurnedOff``). :func:`engine_order` already keeps
    Edge out of the chain there; this is the defence in depth for any other
    caller."""
    egress.require_internet("Microsoft Edge voice")
    try:
        import edge_tts
        import miniaudio
    except ImportError:
        return None

    rate = f"{int(round((speed - 1.0) * 100)):+d}%"

    async def _go() -> bytes:
        chunks: list[bytes] = []
        async for c in edge_tts.Communicate(text, voice, rate=rate).stream():
            if c["type"] == "audio":
                chunks.append(c["data"])
        return b"".join(chunks)

    mp3 = asyncio.run(_go())
    if not mp3:
        return None
    # miniaudio.decode defaults to nchannels=2, sample_rate=44100 — silently
    # upmixes/resamples. Edge MP3 is mono 24 kHz; pin both so the WAV we
    # write matches the bytes (otherwise _pcm_to_wav_bytes writes a mono
    # header over stereo-interleaved data → playback at half speed).
    decoded = miniaudio.decode(
        mp3,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=24_000,
    )
    pcm_bytes = decoded.samples.tobytes()
    return _pcm_to_wav_bytes(pcm_bytes, decoded.sample_rate)


_piper_voice_cache: dict[str, object] = {}
_piper_load_lock = threading.Lock()


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
# The rhasspy/piper-voices commit the pins were read from (2026-09-17).
PIPER_PINNED_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
# model_ref -> (.onnx SHA-256, .onnx bytes, .onnx.json git blob SHA-1,
# .onnx.json bytes), from the Hub's tree API at PIPER_PINNED_REVISION: the
# LFS oid is the SHA-256 of the model; the small JSON config is a plain git
# blob, whose id is sha1(b"blob <len>\0" + content). Every voice in
# voice_catalog.PIPER_CATALOG is here.
PIPER_PINS: dict[str, tuple[str, int, str, int]] = {
    "en_US-kusal-medium": ("438ae25bb305b2a7f6d632327d6102df25011f793e8222fa9db876e7321df8f3", 63201294, "68eff4cb75e728b75ada72c3d1b8e54ba5cf0b3f", 4884),
    "en_US-lessac-medium": ("5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f", 63201294, "c67cea2c9a7a6501d89f7b2cdff411bc49e54a28", 4885),
    "en_US-amy-medium": ("b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18", 63201294, "5a1e0a2d94d3719de29a4771aa0e6c116552445f", 4882),
    "en_US-ryan-high": ("b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca", 120786792, "d50091e9aeb2e7b02517749d953ade2be28dcdb1", 4166),
    "en_US-joe-medium": ("58afce0321b8d9c46d7cdf9c16500cc55a793b4220212dba6b70fb788b3baf06", 63201294, "b4ab600fbd71cee986a8e7714bd88996f33be945", 4794),
    "en_US-kathleen-low": ("87adf17f5326bc0782282147a8b9788406236245f0f9b0e68dacb651bc1de8b6", 63104526, "95db36461a4f0479b68b06eb20a3f86e383a14db", 4169),
    "en_GB-alan-medium": ("0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330", 63201294, "31f864659bfb7678af373cf4e58a7a0866ff52af", 4888),
    "en_GB-alba-medium": ("401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b", 63201294, "c0969252c640fd7c2765baa62936a1106ca856d7", 4888),
    "en_GB-cori-high": ("470b4dd634c98f8a4850d7626ffc3dfc90774628eeef6605a6dd8f88f30a5903", 114219352, "ec08ff84191b2e7fe7dd91928b6068bca12ff81b", 4963),
}
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


def _piper_voice(voice: str) -> object:
    """The loaded PiperVoice for ``voice`` (a model_ref), loading it once.
    Blocking — worker threads only. The lock makes a boot or connect-time
    preload and a turn racing it load the model once between them."""
    v = _piper_voice_cache.get(voice)
    if v is None:
        with _piper_load_lock:
            v = _piper_voice_cache.get(voice)
            if v is None:
                from piper import PiperVoice

                v = PiperVoice.load(str(_piper_voice_path(voice)))
                _piper_voice_cache[voice] = v
    return v


def piper_voice_loaded(voice: str) -> bool:
    return voice in _piper_voice_cache


def preload_piper_voice_sync(voice: str) -> bool:
    """Load a Piper voice and synthesize one short word with it, so the
    first reply in that voice pays neither the model load nor the first
    inference (~600 ms on a CPU host, against ~50 ms warm). Blocking —
    run it in a worker thread (``domovoi/speech_warmup.py``). True when
    the voice rendered audio."""
    return _synth_piper_sync("Okay.", voice, 1.0) is not None


def _synth_piper_sync(text: str, voice: str, speed: float) -> bytes | None:
    try:
        from piper import SynthesisConfig
    except ImportError:
        return None

    v = _piper_voice(voice)

    syn_config = SynthesisConfig(length_scale=(1.0 / speed) if speed > 0 else 1.0)
    pcm_parts: list[bytes] = []
    sr: int | None = None
    for chunk in v.synthesize(text, syn_config=syn_config):
        if sr is None:
            sr = chunk.sample_rate
        pcm_parts.append(chunk.audio_int16_bytes)
    pcm = b"".join(pcm_parts)
    if not pcm or sr is None:
        # Defensive: if a voice ever renders no audio (a bad/empty uploaded
        # model, a degenerate phonemization) that's a failure, not a silent
        # success — return None so the router falls through to the next engine
        # instead of emitting a zero-frame WAV. (Verified 2026-06-13: piper-tts
        # 1.4.2 renders every current voice fine; this is a safety net, not a
        # known bug.)
        return None
    return _pcm_to_wav_bytes(pcm, sr)


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
        edge_voice: str,
        piper_voice: str,
        speed: float,
    ) -> None:
        self.preferred_engine = preferred_engine
        self.edge_voice = edge_voice
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
        return await asyncio.to_thread(self._synth_detailed_blocking, text, engine, voice)

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
        for eng in self._engine_order(preferred):
            v = voice if (voice and eng == preferred) else None
            used: str | None = None
            try:
                if eng == "edge":
                    used = v or self.edge_voice
                    out = _synth_edge_sync(text, used, self.speed)
                elif eng == "piper":
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
                    return SynthResult(wav=out, engine=eng, voice=used)
            except Exception as e:
                log.warning("TTS engine %s failed: %s", eng, e)
        # If every engine fails, return an empty WAV so callers don't crash.
        log.error("all TTS engines failed for text len=%d", len(text))
        return SynthResult(wav=_pcm_to_wav_bytes(b"", 16_000), engine="", voice=None)


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
            edge_voice=settings.tts_edge_voice,
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
