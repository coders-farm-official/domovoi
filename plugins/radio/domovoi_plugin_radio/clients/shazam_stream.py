"""Shazam-from-stream identifier for the radio sampler.

Two public coroutines on the client:

* ``identify_stream(url)`` — capture ``duration_sec`` of audio via
  ffmpeg to a tempfile, ask shazamio to identify it.
* ``identify_wav(path)`` — same identification path from an
  already-grabbed WAV, so the sampler grabs the audio once and runs
  multiple identifiers against it (local fingerprints first, shazamio
  second) without re-fetching the stream.

If shazamio isn't installed, ``identify_wav`` returns ``None`` cleanly
so the sampler degrades to fingerprint-only matching instead of
crashing. The ffmpeg invocation is minimal: URL → mono 16 kHz 16-bit
PCM WAV (~480 KB for 15 s), the shape both the local fingerprinter and
shazamio handle well.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass
class TrackIdentity:
    """Minimal "what song is this" tuple. Both identify tiers return
    enough for the dedup/download flow; richer metadata lives elsewhere."""

    title: str
    artist: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "artist": self.artist}


class ShazamStreamClient(Protocol):
    async def identify_stream(
        self, url: str, duration_sec: int = 15
    ) -> TrackIdentity | None: ...

    async def identify_wav(
        self, wav_path: str | Path
    ) -> TrackIdentity | None: ...


# ─── Real client ─────────────────────────────────────────────────────────


class RealShazamStreamClient:
    """ffmpeg + shazamio implementation. ffmpeg via subprocess (must be
    on PATH — the manifest declares it as a required system tool);
    shazamio from the plugin's pinned requirements."""

    def __init__(self, *, ffmpeg_timeout_sec: float = 20.0) -> None:
        self.ffmpeg_timeout_sec = ffmpeg_timeout_sec

    async def identify_stream(
        self, url: str, duration_sec: int = 15
    ) -> TrackIdentity | None:
        wav_path = await grab_to_tempfile(
            url, duration_sec, timeout_sec=self.ffmpeg_timeout_sec
        )
        if wav_path is None:
            return None
        try:
            return await self.identify_wav(wav_path)
        finally:
            _safe_unlink(wav_path)

    async def identify_wav(
        self, wav_path: str | Path
    ) -> TrackIdentity | None:
        return await _shazam_recognize(Path(wav_path))


class ShazamStreamStubClient:
    """Deterministic stub — the returned title encodes the input so
    tests can assert which sample was passed."""

    def __init__(self, **_: Any) -> None:
        pass

    async def identify_stream(
        self, url: str, duration_sec: int = 15
    ) -> TrackIdentity | None:
        return TrackIdentity(title=f"stub-stream-{url[-12:]}", artist="Stub Artist")

    async def identify_wav(
        self, wav_path: str | Path
    ) -> TrackIdentity | None:
        return TrackIdentity(
            title=f"stub-wav-{Path(wav_path).stem}", artist="Stub Artist"
        )


# ─── ffmpeg grab ─────────────────────────────────────────────────────────


async def grab_to_tempfile(
    url: str, duration_sec: int, *, timeout_sec: float = 20.0
) -> str | None:
    """Spawn ffmpeg to record ``duration_sec`` of audio from ``url``
    into a mono 16 kHz 16-bit WAV tempfile. Returns the path, or None on
    any failure (timeout, ffmpeg missing, stream refused).

    16 kHz mono is enough for both identify tiers and produces a
    tempfile ~10× smaller than 44.1 kHz stereo — that matters when the
    sampler is grabbing several streams at once.
    """
    # Close the fd immediately: ffmpeg writes by path, not fd, and a
    # lingering open handle confuses Windows.
    fd, path = tempfile.mkstemp(prefix="radio-sample-", suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", url,
        "-t", str(duration_sec),
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        log.warning(
            "ffmpeg not on PATH; the radio sampler can't grab streams. "
            "(Install ffmpeg or set RADIO_SAMPLER_ENABLED=false.)"
        )
        _safe_unlink(path)
        return None

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        log.debug("ffmpeg grab timed out after %ss for %s", timeout_sec, url)
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        _safe_unlink(path)
        return None
    except Exception as e:
        log.debug("ffmpeg grab raised for %s: %s", url, e)
        _safe_unlink(path)
        return None

    if proc.returncode != 0:
        tail = (stderr or b"").decode(errors="replace").strip().splitlines()
        last_line = tail[-1] if tail else "(no stderr)"
        log.debug("ffmpeg grab rc=%s for %s: %s", proc.returncode, url, last_line)
        _safe_unlink(path)
        return None

    # A successful rc with a near-empty file would crash shazamio.
    try:
        if os.path.getsize(path) < 1024:
            log.debug(
                "ffmpeg grab produced suspiciously small file (%d bytes) for %s",
                os.path.getsize(path), url,
            )
            _safe_unlink(path)
            return None
    except OSError:
        return None

    return path


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ─── shazamio recognize ──────────────────────────────────────────────────


async def _shazam_recognize(file_path: Path) -> TrackIdentity | None:
    try:
        from shazamio import Shazam
    except ImportError:
        log.debug("shazamio not installed; skipping online identify")
        return None

    try:
        shazam = Shazam()
        result = await shazam.recognize(str(file_path))
    except Exception as e:
        # shazamio raises a grab-bag of network/parsing errors; debug
        # level because the sampler ticks frequently.
        log.debug("shazam recognize failed for %s: %s", file_path, e)
        return None

    track = (result or {}).get("track") if isinstance(result, dict) else None
    if not track:
        return None
    title = (track.get("title") or "").strip()
    artist = (track.get("subtitle") or "").strip() or None
    if not title:
        return None
    return TrackIdentity(title=title, artist=artist)


_client: ShazamStreamClient | None = None


def get_shazam_stream_client(
    *, use_stubs: bool = False, ffmpeg_timeout_sec: float = 20.0
) -> ShazamStreamClient:
    global _client
    if _client is None:
        _client = (
            ShazamStreamStubClient()
            if use_stubs
            else RealShazamStreamClient(ffmpeg_timeout_sec=ffmpeg_timeout_sec)
        )
    return _client


def _set_shazam_stream_client_for_tests(client: ShazamStreamClient | None) -> None:
    global _client
    _client = client
