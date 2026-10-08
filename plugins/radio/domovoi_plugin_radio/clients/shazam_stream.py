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

The URL is a station row's, so ffmpeg never sees it (A2-03). The stream
is opened HERE, through the shared outbound-URL fetcher
(``net_safety.open_stream``: http(s) only, never an address inside the
house or on the box, every redirect hop re-checked), and its bytes are fed
to ffmpeg on stdin under ``-protocol_whitelist pipe``. Left to open the URL
itself, ffmpeg would resolve the name on its own, follow redirects
unchecked and open every segment URL an HLS playlist lists; on a pipe it
can open nothing else. A body that is not audio, or is a playlist
(whatever type it claims), is refused before ffmpeg is spawned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from domovoi.sdk import egress, net_safety

from domovoi_plugin_radio import USER_AGENT, stream_types

log = logging.getLogger(__name__)

# The only protocol ffmpeg may open for a grab: its own stdin, which this
# module feeds from a stream it opened itself. Notably NOT http(s) (ffmpeg
# would resolve, redirect and follow playlists on its own), file:, or
# concat:.
FFMPEG_PROTOCOL_WHITELIST = "pipe"
FFMPEG_INPUT = "pipe:0"

# Bytes per read from the station. Small enough that a finished ffmpeg is
# noticed within about a second of a 128 kbps stream.
_FEED_CHUNK = 16 * 1024
# The most a grab will pull from a station, per second of audio asked
# for (plus a few seconds for ffmpeg's probe): 256 KiB/s is above any
# lossless radio stream, so this bounds bandwidth, not quality.
_FEED_BYTES_PER_SEC = 256 * 1024
_FEED_PROBE_SEC = 5
# Connect / per-read limits on the station fetch; the whole grab is
# still bounded by ``timeout_sec``.
_FETCH_TIMEOUT_SEC = 10.0


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
    """Record ``duration_sec`` of audio from ``url`` into a mono 16 kHz
    16-bit WAV tempfile. Returns the path, or None on any failure
    (timeout, ffmpeg missing, stream refused, not an audio stream).

    16 kHz mono is enough for both identify tiers and produces a
    tempfile ~10× smaller than 44.1 kHz stereo — that matters when the
    sampler is grabbing several streams at once.

    The stream is opened here, through ``net_safety.open_stream`` (every
    redirect hop re-checked), and fed to ffmpeg on stdin; ffmpeg may open
    nothing but that pipe. The whole grab, fetch included, is bounded by
    ``timeout_sec``.

    Returns None without spawning anything when ``url`` (or any hop it
    redirects through) is not an http(s) URL the server may fetch, when
    the answer is not audio or is a playlist, or when it is on the
    internet and the box is set to stay off it (``INTERNET_ACCESS=never``).
    """
    if egress.check_destination(url) is not None:
        try:
            egress.require_internet("song recognition")
        except egress.InternetTurnedOff:
            log.debug("radio: not sampling %s — internet access is turned off", url)
            return None
    reason = await net_safety.acheck_outbound_url(url)
    if reason is not None:
        log.warning("radio: refusing to sample %s — %s", url, reason)
        return None
    try:
        return await asyncio.wait_for(_grab(url, duration_sec), timeout=timeout_sec)
    except asyncio.TimeoutError:
        log.debug("radio: sample grab timed out after %ss for %s", timeout_sec, url)
        return None
    except Exception as e:
        log.debug("radio: sample grab raised for %s: %s", url, e)
        return None


async def _grab(url: str, duration_sec: int) -> str | None:
    """Open the station through the checked fetcher, vet what answered,
    and hand the body to :func:`_feed_ffmpeg`."""
    import httpx

    headers = {
        "User-Agent": USER_AGENT,
        # Interleaved ICY metadata would corrupt the audio ffmpeg reads.
        "Icy-MetaData": "0",
        "Accept": "*/*",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(_FETCH_TIMEOUT_SEC)) as client:
        try:
            resp = await net_safety.open_stream(client, url, headers=headers)
        except net_safety.OutboundFetchError as e:
            # The first URL passed, but a redirect hop did not.
            log.warning("radio: refusing to sample %s — %s", url, e)
            return None
        except httpx.HTTPError as e:
            log.debug("radio: sample fetch failed for %s: %s", url, e)
            return None
        try:
            if resp.status_code >= 400:
                log.debug("radio: sample fetch for %s answered %s", url, resp.status_code)
                return None
            declared = resp.headers.get("content-type")
            if not stream_types.samplable_audio_type(declared):
                log.warning(
                    "radio: refusing to sample %s — not an audio stream (%s)",
                    url, _shown_type(declared),
                )
                return None
            return await _feed_ffmpeg(resp, url, duration_sec)
        finally:
            await resp.aclose()


def _shown_type(declared: str | None) -> str:
    base = (declared or "").split(";", 1)[0].strip().lower()[:60]
    if base and all(c.isalnum() or c in "+-./" for c in base):
        return base
    return "unrecognised type"


async def _feed_ffmpeg(resp: Any, url: str, duration_sec: int) -> str | None:
    """Spawn ffmpeg reading stdin and pump the station's body into it
    until ffmpeg has its ``duration_sec`` (it exits), the station hangs
    up, or the byte budget is spent. Cleans up the process and the
    tempfile on every way out, cancellation by the caller's timeout
    included."""
    chunks = resp.aiter_bytes(chunk_size=_FEED_CHUNK)
    head = b""
    async for chunk in chunks:
        if chunk:
            head = chunk
            break
    if not head:
        log.debug("radio: %s sent no audio", url)
        return None
    if stream_types.looks_like_playlist(head):
        log.warning("radio: refusing to sample %s — it is a playlist, not a stream", url)
        return None

    # Close the fd immediately: ffmpeg writes by path, not fd, and a
    # lingering open handle confuses Windows.
    fd, path = tempfile.mkstemp(prefix="radio-sample-", suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        # Before -i: it governs what the INPUT may be — the pipe only.
        "-protocol_whitelist", FFMPEG_PROTOCOL_WHITELIST,
        "-i", FFMPEG_INPUT,
        "-t", str(duration_sec),
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
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

    budget = (int(duration_sec) + _FEED_PROBE_SEC) * _FEED_BYTES_PER_SEC
    keep = False
    stderr_task = asyncio.ensure_future(proc.stderr.read())
    pump = asyncio.ensure_future(_pump(head, chunks, proc.stdin, budget))
    exited = asyncio.ensure_future(proc.wait())
    try:
        await asyncio.wait({pump, exited}, return_when=asyncio.FIRST_COMPLETED)
        if not exited.done():
            # The pump is done (budget spent or the station hung up) and
            # has closed stdin, so ffmpeg finishes with what it has.
            await exited
        stderr = await stderr_task
        if proc.returncode != 0:
            tail = (stderr or b"").decode(errors="replace").strip().splitlines()
            last_line = tail[-1] if tail else "(no stderr)"
            log.debug("ffmpeg grab rc=%s for %s: %s", proc.returncode, url, last_line)
            return None
        # A successful rc with a near-empty file would crash shazamio.
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size < 1024:
            log.debug(
                "ffmpeg grab produced suspiciously small file (%d bytes) for %s",
                size, url,
            )
            return None
        keep = True
        return path
    finally:
        for task in (pump, exited, stderr_task):
            if not task.done():
                task.cancel()
        if proc.returncode is None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
        if not keep:
            _safe_unlink(path)


async def _pump(head: bytes, chunks: Any, stdin: Any, budget: int) -> None:
    """Write ``head`` and then the rest of the body to ffmpeg's stdin
    until ``budget`` bytes have gone, the body ends, or ffmpeg stops
    reading. Always closes stdin, which is ffmpeg's end of input."""
    sent = 0
    try:
        stdin.write(head)
        await stdin.drain()
        sent += len(head)
        async for chunk in chunks:
            if sent >= budget:
                break
            if not chunk:
                continue
            stdin.write(chunk)
            await stdin.drain()
            sent += len(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        # ffmpeg exited: it has its duration (or failed; its rc says which).
        pass
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # The station dropped mid-stream: ffmpeg finishes with what it has.
        log.debug("radio: sample stream ended early: %s", e)
    finally:
        try:
            stdin.close()
        except Exception:
            pass


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ─── shazamio recognize ──────────────────────────────────────────────────


async def _shazam_recognize(file_path: Path) -> TrackIdentity | None:
    try:
        # Shazam is an internet service: nothing goes out under
        # INTERNET_ACCESS=never.
        egress.require_internet("song recognition")
    except egress.InternetTurnedOff:
        return None
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
