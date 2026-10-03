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
import io
import logging
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

from domovoi import egress
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


def _piper_local_path(model_ref: str) -> Path | None:
    """The ``.onnx`` a Piper ``model_ref`` loads from WITHOUT a download —
    an uploaded model's own path, or ``<name>.onnx`` + ``.onnx.json`` in the
    voice-models dir — or None. Creates nothing."""
    cand = Path(model_ref)
    if cand.suffix == ".onnx" and cand.is_file():
        return cand
    voices_dir = _voices_dir()
    onnx = voices_dir / f"{model_ref}.onnx"
    if onnx.exists() and (voices_dir / f"{model_ref}.onnx.json").exists():
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
      3. A standard HF voice name (LANG-SPEAKER-QUALITY) — auto-download
         into the voice-models dir, one time. The download goes through the
         egress gate: under ``INTERNET_ACCESS=never`` it raises
         ``egress.InternetTurnedOff`` without a request, and the router
         moves on to the next rung.
    """
    import requests

    # (1) Direct path to an uploaded model, (2) already under the voices dir.
    local = _piper_local_path(model_ref)
    if local is not None:
        return local

    voices_dir = _voices_dir()
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{model_ref}.onnx"
    jpath = voices_dir / f"{model_ref}.onnx.json"

    # (3) HF auto-download.
    parts = model_ref.split("-")
    if len(parts) != 3:
        raise ValueError(
            f"Piper voice must be an uploaded .onnx or a LANG-SPEAKER-QUALITY "
            f"HF name, got {model_ref!r}"
        )
    lang_full, speaker, quality = parts
    lang = lang_full.split("_")[0]
    base = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"{lang}/{lang_full}/{speaker}/{quality}/{model_ref}"
    )
    egress.require_destination(base + ".onnx")
    log.info("downloading Piper voice %s (one-time)", model_ref)
    for suffix, path in ((".onnx", onnx), (".onnx.json", jpath)):
        r = requests.get(base + suffix, stream=True, timeout=60)
        r.raise_for_status()
        with open(path, "wb") as f:
            for block in r.iter_content(32768):
                f.write(block)
    return onnx


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
