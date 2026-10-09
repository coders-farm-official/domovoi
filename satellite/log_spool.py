"""The offline log spool and the live log push buffer.

The in-RAM log ring (``log_buffer``) is gone the moment the process dies,
and a satellite that dies has usually been disconnected for a while first
— exactly the lines nobody can read afterwards. So while the WebSocket is
down every formatted log line also goes to ``~/.domovoi/log-spool.txt``,
a small bounded ring FILE (256 KB, truncated from the front), and on the
next connect to a core that lists ``log_push`` the spool is sent as
``log_push`` frames flagged ``offline`` and then cleared. While connected
to such a core, new lines collect in a bounded in-memory buffer the
client pushes every 30 s (at most 32 KB a push; beyond that lines are
dropped and counted, never queued).

One ``logging.Handler`` on the root logger does both, switched by
:meth:`SpoolHandler.set_mode`: ``offline`` (spool to disk), ``online``
(collect for the push) or ``off`` (the core does not take pushes: nothing
accumulates). Kept out of ``client.py`` so it is testable on any box.

Writes to the SD card are the concern here, so: the spool is appended
line by line only while disconnected (a few lines a minute), a trim
rewrites it only once it is a quarter over budget, and a storm of more
than :data:`MAX_APPENDS_PER_SEC` lines in a second is counted and dropped
rather than written.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

#: The spool file's budget.
DEFAULT_MAX_BYTES = 256 * 1024
#: One ``log_push`` frame carries at most this much text.
CHUNK_BYTES = 32 * 1024
#: Beyond this many appends in one second, lines are dropped and counted.
MAX_APPENDS_PER_SEC = 50
#: A trim keeps this fraction of the budget (the newest lines).
_KEEP_FRACTION = 0.75


def chunk_lines(text: str, limit: int = CHUNK_BYTES) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` UTF-8 bytes, cutting
    at line boundaries where a line fits and mid-line only when one line is
    itself over the limit. Concatenating the pieces reproduces ``text``."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    pieces: list[str] = []
    cur: list[str] = []
    cur_bytes = 0
    for line in text.splitlines(keepends=True):
        size = len(line.encode("utf-8"))
        if size > limit:
            if cur:
                pieces.append("".join(cur))
                cur, cur_bytes = [], 0
            # A single oversized line: cut it on character boundaries.
            raw = line.encode("utf-8")
            start = 0
            while start < len(raw):
                piece = raw[start:start + limit].decode("utf-8", errors="ignore")
                if not piece:
                    break
                pieces.append(piece)
                start += len(piece.encode("utf-8"))
            continue
        if cur_bytes + size > limit:
            pieces.append("".join(cur))
            cur, cur_bytes = [], 0
        cur.append(line)
        cur_bytes += size
    if cur:
        pieces.append("".join(cur))
    return pieces


class LogSpool:
    """A bounded ring file of log lines, oldest dropped first."""

    def __init__(
        self, path: Path, max_bytes: int = DEFAULT_MAX_BYTES,
        max_per_sec: int = MAX_APPENDS_PER_SEC,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.max_per_sec = int(max_per_sec)
        self._lock = threading.Lock()
        self._size: int | None = None  # unknown until first touch
        self.dropped_lines = 0
        self.trims = 0
        self._second = 0
        self._in_second = 0

    def _current_size(self) -> int:
        if self._size is None:
            try:
                self._size = self.path.stat().st_size
            except OSError:
                self._size = 0
        return self._size

    def append(self, line: str) -> bool:
        raw = line.encode("utf-8", errors="replace")
        if not raw.endswith(b"\n"):
            raw += b"\n"
        with self._lock:
            now = int(time.monotonic())
            if now != self._second:
                self._second, self._in_second = now, 0
            self._in_second += 1
            if self._in_second > self.max_per_sec:
                self.dropped_lines += 1
                return False
            before = self._current_size()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "ab") as f:
                    f.write(raw)
            except OSError:
                self.dropped_lines += 1
                return False
            self._size = before + len(raw)
            if self._size > self.max_bytes:
                self._trim_locked()
            return True

    def _trim_locked(self) -> None:
        """Keep the newest ``_KEEP_FRACTION`` of the budget, whole lines."""
        keep = int(self.max_bytes * _KEEP_FRACTION)
        try:
            data = self.path.read_bytes()
        except OSError:
            return
        if len(data) > keep:
            cut = data[-keep:]
            nl = cut.find(b"\n")
            cut = cut[nl + 1:] if nl >= 0 else cut
        else:
            cut = data
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(cut)
            os.replace(tmp, self.path)
            self._size = len(cut)
            self.trims += 1
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def size(self) -> int:
        with self._lock:
            return self._current_size()

    def read(self) -> str:
        with self._lock:
            try:
                return self.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

    def drain(self, chunk_bytes: int = CHUNK_BYTES) -> list[str]:
        """Everything spooled, in send-sized pieces, and the spool cleared.
        Empty when there is nothing."""
        with self._lock:
            try:
                text = self.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._clear_locked()
        if not text:
            return []
        return chunk_lines(text, chunk_bytes)

    def _clear_locked(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        self._size = 0

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "bytes": self._current_size(),
                "max_bytes": self.max_bytes,
                "dropped_lines": self.dropped_lines,
                "trims": self.trims,
            }


class PushBuffer:
    """Lines since the last push, bounded to one frame's worth."""

    def __init__(self, max_bytes: int = CHUNK_BYTES) -> None:
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._bytes = 0
        self.dropped_lines = 0

    def append(self, line: str) -> bool:
        if not line.endswith("\n"):
            line += "\n"
        size = len(line.encode("utf-8", errors="replace"))
        with self._lock:
            if self._bytes + size > self.max_bytes:
                self.dropped_lines += 1
                return False
            self._lines.append(line)
            self._bytes += size
            return True

    def take(self) -> tuple[str, int]:
        """``(text, dropped_since_last_take)``; resets both."""
        with self._lock:
            text = "".join(self._lines)
            dropped = self.dropped_lines
            self._lines = []
            self._bytes = 0
            self.dropped_lines = 0
        return text, dropped

    def pending_bytes(self) -> int:
        with self._lock:
            return self._bytes


class SpoolHandler(logging.Handler):
    """Routes formatted records to the spool (offline) or the push buffer
    (online), or nowhere (off). ``logging.Handler.handle`` serialises
    ``emit`` under the handler's own lock."""

    MODES = ("off", "offline", "online")

    def __init__(self, spool: LogSpool, push: PushBuffer | None = None) -> None:
        super().__init__()
        self.spool = spool
        self.push = push or PushBuffer()
        self.mode = "off"

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"unknown spool mode {mode!r}")
        self.mode = mode

    def emit(self, record: logging.LogRecord) -> None:
        try:
            mode = self.mode
            if mode == "off":
                return
            line = self.format(record)
            if mode == "offline":
                self.spool.append(line)
            else:
                self.push.append(line)
        except Exception:  # noqa: BLE001 — a logging handler must never raise
            self.handleError(record)
