"""In-memory ring buffer for the satellite's own log output.

A satellite is a headless Pi in another room. When something goes wrong
the evidence lives in `journalctl -u domovoi-satellite`, which means SSH,
which means the failure has to still be happening when you get there.
This keeps the most recent ``max_bytes`` of log output in RAM so the core
can pull it over the existing WebSocket and the dashboard can show it.

RAM only, deliberately: the Pi's SD card is the part most likely to die,
and a log ring is exactly the write pattern that kills one. A restart
loses the buffer — journald still has the durable copy for the cases
where that matters.

Kept out of ``client.py`` so it's unit-testable on any box: the client
imports sounddevice/webrtcvad at module scope and can't be imported off a
Pi. See ``devices.select_channel_int16`` for the same reasoning.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Iterator

# 10 MB of log text. At the satellite's INFO volume (a handful of lines
# per turn, plus wifi-watcher chatter every few seconds) this is days of
# history; at DEBUG on a busy box it's still hours.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

# A single record longer than this is truncated before it goes in, so one
# pathological line (a numpy array repr, a multi-MB traceback) can't evict
# the entire buffer on its own.
MAX_RECORD_BYTES = 64 * 1024

_TRUNCATED = b"... [truncated]"


class RingLogBuffer:
    """Byte-budgeted FIFO of formatted log lines.

    Thread-safe: log records arrive from the mic thread, the playback
    thread, the wifi watcher and the asyncio loop, so every mutation
    holds the lock. Reads copy under the lock and format outside it.
    """

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._lines: deque[bytes] = deque()
        self._size = 0
        self._dropped = 0
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        """Add one formatted record, evicting oldest lines to stay in budget."""
        raw = line.encode("utf-8", errors="replace")
        if len(raw) > MAX_RECORD_BYTES:
            # Cut on a character boundary — a mid-sequence slice of UTF-8
            # would decode into a replacement char at the seam.
            raw = raw[:MAX_RECORD_BYTES].decode(
                "utf-8", errors="ignore"
            ).encode("utf-8") + _TRUNCATED
        raw += b"\n"
        with self._lock:
            self._lines.append(raw)
            self._size += len(raw)
            while self._size > self.max_bytes and self._lines:
                self._size -= len(self._lines.popleft())
                self._dropped += 1

    def tail(self, max_bytes: int | None = None) -> str:
        """The most recent ``max_bytes`` of log text, whole lines only.

        Cutting at a line boundary rather than a byte offset keeps the
        first line of the returned text readable — a byte slice would
        start mid-record, and the console view would open on a fragment.
        """
        with self._lock:
            lines = list(self._lines)
        if max_bytes is None or max_bytes >= self._size:
            return b"".join(lines).decode("utf-8", errors="replace")
        if max_bytes <= 0:
            return ""
        out: list[bytes] = []
        total = 0
        for raw in reversed(lines):
            if total + len(raw) > max_bytes:
                break
            out.append(raw)
            total += len(raw)
        out.reverse()
        return b"".join(out).decode("utf-8", errors="replace")

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "bytes": self._size,
                "lines": len(self._lines),
                "dropped_lines": self._dropped,
                "max_bytes": self.max_bytes,
            }

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._size = 0
            self._dropped = 0


def chunk_text(text: str, chunk_chars: int) -> Iterator[tuple[int, str, bool]]:
    """Split ``text`` into ``(seq, piece, final)`` triples for transport.

    Lives here rather than in ``client.py`` so it can be tested off a Pi —
    the client module imports sounddevice at import time and can't be loaded
    on a dev box, which is exactly how an off-by-one in a chunker ships.

    Guarantees the receiver depends on:

      * at least one triple, even for empty text — the core resolves its
        pending request on ``final``, so a silent path would hang the caller
        until its timeout and tell the user nothing;
      * exactly one triple with ``final=True``, and it is the last;
      * ``seq`` starts at 0 and increments by 1, which the core enforces on
        arrival (a gap fails the request rather than stitching a log whose
        ordering can't be trusted);
      * concatenating the pieces in order reproduces ``text`` exactly.

    Chunks are counted in CHARACTERS, not bytes: the pieces are about to be
    JSON-escaped into a WebSocket frame, so the byte count is not knowable
    here anyway. Callers size ``chunk_chars`` with enough headroom that even
    4-bytes-per-character text plus escaping stays under the receiver's cap.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    total = len(text)
    seq = 0
    sent = 0
    while True:
        piece = text[sent:sent + chunk_chars]
        sent += len(piece)
        final = sent >= total
        yield seq, piece, final
        if final:
            return
        seq += 1


class RingLogHandler(logging.Handler):
    """``logging.Handler`` that writes into a :class:`RingLogBuffer`.

    Installed on the ROOT logger so it captures third-party output too —
    websockets' connection errors and sounddevice's PortAudio complaints
    are exactly the lines you want when a satellite is misbehaving, and
    they don't come from the ``satellite`` logger.
    """

    def __init__(self, buffer: RingLogBuffer) -> None:
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            self.handleError(record)
