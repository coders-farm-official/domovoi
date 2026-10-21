"""Read a document in its own short-lived process (:mod:`.worker`).

One process per document, with a wall-clock limit and the worker's own
memory and CPU caps: PDFium is native code and the file is somebody
else's, so a document that hangs or crashes the reader costs that one
attachment, not the web process. At most :data:`MAX_PARALLEL` read at
once (one on a small box), so a burst of attachments can't eat the
server's memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from domovoi.doc_extract import limits

log = logging.getLogger(__name__)

TIMEOUT_SEC = 120.0
MAX_PARALLEL = limits.worker_slots()
#: The largest result read back: the 4,000,000-character text cap
#: (doc_extract.MAX_TEXT_CHARS) as JSON, with room for the markup.
MAX_RESULT_BYTES = 24 * 1024 * 1024



def worker_argv(work: Path) -> list[str]:
    """The worker's command line (a seam: the tests start a stand-in)."""
    return [sys.executable, "-m", "domovoi.doc_extract.worker", str(work)]


class ExtractFailed(Exception):
    """The read refused or failed; ``str()`` is a sentence for people."""


_gates: "dict[int, asyncio.Semaphore]" = {}


def _semaphore() -> asyncio.Semaphore:
    """The gate of the running event loop (one per loop: a semaphore is
    bound to the loop it first waited on)."""
    loop = asyncio.get_running_loop()
    gate = _gates.get(id(loop))
    if gate is None:
        if len(_gates) > 8:
            _gates.clear()
        gate = _gates[id(loop)] = asyncio.Semaphore(MAX_PARALLEL)
    return gate


async def extract(path: Path, kind: str, *, timeout: float | None = None,
                  single_pass: int | None = None) -> dict[str, Any]:
    """The extraction of ``path`` (see :func:`domovoi.doc_extract.extract`),
    read by the worker. Raises :class:`ExtractFailed`."""
    timeout = TIMEOUT_SEC if timeout is None else timeout
    # The temporary folder is made once this read's turn has come (review
    # round 2): a read queued behind others has none for the sweep to take.
    async with _semaphore():
        return await _extract_now(path, kind, timeout, single_pass)


async def _extract_now(path: Path, kind: str, timeout: float, single_pass: int | None) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="domovoi-docread-"))
    try:
        request = {"path": str(Path(path).resolve()), "kind": kind, "single_pass": single_pass,
                   "memory_bytes": limits.worker_memory_bytes(), "cpu_seconds": int(timeout)}
        (work / "request.json").write_text(json.dumps(request), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = await asyncio.create_subprocess_exec(
            *worker_argv(work),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("document read of %s took over %.0f s; stopped", Path(path).name, timeout)
            raise ExtractFailed("Reading that document took too long, so Domovoi stopped.")
        out = work / "result.json"
        try:
            if out.stat().st_size > MAX_RESULT_BYTES:
                raise ExtractFailed("That document has more text than Domovoi keeps for a chat.")
            result = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # The exit code only: the worker's stderr could quote the file.
            log.warning("document read worker exited %s without a result (%d bytes on stderr)",
                        proc.returncode, len(err or b""))
            raise ExtractFailed("That document couldn't be read.")
        if not isinstance(result, dict) or not result.get("ok"):
            if isinstance(result, dict) and result.get("detail"):
                log.info("document read failed: %s", str(result["detail"])[:60])
            raise ExtractFailed(str((result or {}).get("error") or "That document couldn't be read."))
        if not isinstance(result.get("parts"), list):
            raise ExtractFailed("That document couldn't be read.")
        result.pop("ok", None)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
