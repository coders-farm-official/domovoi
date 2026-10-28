"""Starting, talking to and replacing document workers (worker.py).

One worker subprocess per open document, plus one "scratch" worker for
jobs that hold no document (a version preview, an offline copy). Every
call has a timeout; a worker that dies or overruns is killed and the call
raises :class:`WorkerFailed`, which the room turns into "refuse that
update, close the peer that sent it, start a new worker from Postgres".
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[3]

# Seconds. A keystroke is a millisecond's work; these are for documents with
# photos in them, on a busy small box.
TIMEOUTS = {
    "ping": 10.0, "apply": 10.0, "sv": 10.0, "diff": 20.0, "encode": 30.0,
    "compact": 30.0, "restore": 30.0, "load": 60.0, "merge_copy": 30.0, "preview": 20.0,
    "crash": 10.0, "sleep": 30.0,
}
START_TIMEOUT = 30.0
SCRATCH_IDLE_SEC = 60.0


class WorkerFailed(Exception):
    """The worker died or did not answer in time; it is gone."""


class WorkerRefused(Exception):
    """The worker said no (an update it would not take); it is still fine."""


class Worker:
    def __init__(self, label: str) -> None:
        self.label = label
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self.dead = False
        self.last_used = time.monotonic()

    async def start(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "web.backend.collab.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=str(_REPO), env=env,
        )
        try:
            await self.call("ping", timeout=START_TIMEOUT)
        except WorkerFailed:
            self.kill()
            raise

    async def call(
        self, op: str, args: Optional[dict] = None, blobs: tuple[bytes, ...] | list[bytes] = (),
        *, timeout: Optional[float] = None,
    ) -> tuple[dict, list[bytes]]:
        """One request, one answer; calls are serialised per worker."""
        if self.dead or self.proc is None:
            raise WorkerFailed(f"{self.label}: not running")
        async with self._lock:
            self.last_used = time.monotonic()
            rid = next(self._ids)
            header = {"id": rid, "op": op, "args": args or {}, "blobs": [len(b) for b in blobs]}
            raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
            try:
                return await asyncio.wait_for(
                    self._exchange(struct.pack(">I", len(raw)) + raw + b"".join(blobs), rid),
                    timeout or TIMEOUTS.get(op, 30.0),
                )
            except WorkerRefused:
                raise
            except Exception as e:  # noqa: BLE001 — dead, stalled, or talking nonsense
                self.kill()
                raise WorkerFailed(f"{self.label}: {op}: {type(e).__name__}") from None

    async def _exchange(self, data: bytes, rid: int) -> tuple[dict, list[bytes]]:
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()
        out = self.proc.stdout
        (hlen,) = struct.unpack(">I", await out.readexactly(4))
        if hlen > 64 * 1024:
            raise ValueError("answer header too large")
        header = json.loads((await out.readexactly(hlen)).decode("utf-8"))
        blobs = [await out.readexactly(int(n)) for n in header.get("blobs", [])]
        if header.get("id") != rid:
            raise ValueError("answer out of order")
        if not header.get("ok"):
            raise WorkerRefused(str(header.get("error") or "refused"))
        return header.get("result") or {}, blobs

    def kill(self) -> None:
        self.dead = True
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        self.dead = True
        p = self.proc
        if p is None or p.returncode is not None:
            return
        try:
            if p.stdin:
                p.stdin.close()
            await asyncio.wait_for(p.wait(), 5.0)
        except Exception:  # noqa: BLE001
            self.kill()


async def start_worker(label: str) -> Worker:
    w = Worker(label)
    await w.start()
    return w


# ─── The scratch worker ───────────────────────────────────────────────

_scratch: Optional[Worker] = None
_scratch_lock: Optional[asyncio.Lock] = None
_scratch_loop: Optional[asyncio.AbstractEventLoop] = None


async def scratch_call(op: str, blobs: list[bytes]) -> tuple[dict, list[bytes]]:
    """A job that holds no document, on the shared scratch worker (started
    on first use, replaced after a failure; one per event loop, which in the
    server is one)."""
    global _scratch, _scratch_lock, _scratch_loop
    loop = asyncio.get_running_loop()
    if _scratch_loop is not loop:
        if _scratch is not None:
            _scratch.kill()
        _scratch, _scratch_lock, _scratch_loop = None, asyncio.Lock(), loop
    async with _scratch_lock:
        if _scratch is None or _scratch.dead:
            _scratch = await start_worker("scratch")
        w = _scratch
    return await w.call(op, {}, blobs)


async def stop_scratch() -> None:
    global _scratch
    if _scratch is not None:
        await _scratch.stop()
        _scratch = None
