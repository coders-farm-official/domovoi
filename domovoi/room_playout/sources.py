"""PCM sources for the room streamer.

``FfmpegStreamSource`` decodes the room's own MPD http stream (MP3,
control 6650+N / stream 8050+N, ``domovoi/mpd_provisioner.py``) over
loopback with the system ffmpeg, run as its own program as the clip encoder
already does (``canned_sounds.py``). No MPD change, no new port.

MPD serves the stream at exactly playback rate from the same machine, so
the decoder's output keeps pace with the streamer's clock; the buffer cap
drops the oldest audio if it ever runs ahead.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from urllib.parse import urlsplit, urlunsplit

from domovoi.room_playout import protocol as P

log = logging.getLogger(__name__)

PREBUFFER_MS = 200
MAX_BUFFER_MS = 2000


def _ms_bytes(ms: int) -> int:
    return ms * P.RATE // 1000 * P.BYTES_PER_FRAME


class BufferSource:
    """A fixed piece of PCM (tests, chimes)."""

    def __init__(self, pcm: bytes) -> None:
        self._buf = bytearray(pcm)
        self._closed = False

    @property
    def active(self) -> bool:
        return bool(self._buf) and not self._closed

    @property
    def started(self) -> bool:
        return True

    def read(self, n: int) -> bytes:
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def close(self) -> None:
        self._closed = True
        self._buf.clear()


class FfmpegStreamSource:
    def __init__(self, url: str, *, exe: str | None = None) -> None:
        self.url = url
        self._exe = exe or shutil.which("ffmpeg")
        self._buf = bytearray()
        self._got = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._ended = False

    async def start(self) -> None:
        if self._exe is None:
            log.warning("room playout: ffmpeg not found on PATH; cannot decode %s", self.url)
            self._ended = True
            return
        self._proc = await asyncio.create_subprocess_exec(
            self._exe, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", self.url, "-vn", "-f", "s16le", "-ac", str(P.CHANNELS),
            "-ar", str(P.RATE), "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        cap = _ms_bytes(MAX_BUFFER_MS)
        try:
            while True:
                data = await self._proc.stdout.read(16384)
                if not data:
                    break
                self._buf += data
                self._got += len(data)
                if len(self._buf) > cap:
                    drop = len(self._buf) - cap
                    del self._buf[: drop - drop % P.BYTES_PER_FRAME]
        finally:
            self._ended = True

    @property
    def active(self) -> bool:
        return not self._ended or bool(self._buf)

    @property
    def started(self) -> bool:
        return self._got >= _ms_bytes(PREBUFFER_MS)

    def read(self, n: int) -> bytes:
        if not self.started:
            return b""
        n -= n % P.BYTES_PER_FRAME
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def close(self) -> None:
        self._ended = True
        self._buf.clear()
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        if self._task is not None:
            self._task.cancel()


def loopback_stream_url(room_id: str) -> str | None:
    """The room's MPD http stream, dialled over loopback (the stream the
    room's satellite would play: its shared queue's host's, if any)."""
    from domovoi.clients.mpd import mpd_stream_url_for

    url = mpd_stream_url_for(room_id)
    if url is None:
        return None
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, f"127.0.0.1:{parts.port}", parts.path, "", ""))
