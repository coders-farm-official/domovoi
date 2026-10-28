"""Convert a document to Word in its own short-lived process.

The same shape as the document reader's runner (:mod:`domovoi.doc_extract.runner`):
one process per conversion, with a wall-clock limit and the worker's own
memory and CPU caps, and at most a couple at once. The input is somebody
else's markup, and the server's documents pool must never be the thing it
exhausts: a conversion that runs long or grows large costs that one
download or save, not the web process. Anyone who can read Documents can
ask for a Word copy, so this is the bound on what that costs.
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

from domovoi.doc_extract import limits

log = logging.getLogger(__name__)

TIMEOUT_SEC = 60.0
#: A Word copy larger than this is not handed back.
MAX_OUT_BYTES = 96 * 1024 * 1024
MAX_PARALLEL = limits.worker_slots()


class ConvertFailed(Exception):
    """The conversion failed; ``str()`` is a sentence for people."""


def worker_argv(work: Path) -> list[str]:
    """The worker's command line (a seam: tests can start a stand-in)."""
    return [sys.executable, "-m", "domovoi.richdoc.worker", str(work)]


_gates: "dict[int, asyncio.Semaphore]" = {}


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _gates.get(id(loop))
    if gate is None:
        if len(_gates) > 8:
            _gates.clear()
        gate = _gates[id(loop)] = asyncio.Semaphore(MAX_PARALLEL)
    return gate


async def to_docx(markup: str, title: str = "", *, timeout: float | None = None) -> bytes:
    """``markup`` (sanitised in the worker) as .docx bytes. Raises
    :class:`ConvertFailed`."""
    timeout = TIMEOUT_SEC if timeout is None else timeout
    async with _semaphore():
        work = Path(tempfile.mkdtemp(prefix="domovoi-docx-"))
        try:
            (work / "input.html").write_text(markup or "", encoding="utf-8")
            request = {"title": title, "memory_bytes": limits.worker_memory_bytes(),
                       "cpu_seconds": int(timeout)}
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
                log.warning("Word conversion took over %.0f s; stopped", timeout)
                raise ConvertFailed("Making the Word copy took too long, so Domovoi stopped.") from None
            out = work / "out.docx"
            if out.is_file():
                if out.stat().st_size > MAX_OUT_BYTES:
                    raise ConvertFailed("That Word copy would be too large.")
                return out.read_bytes()
            try:
                why = json.loads((work / "error.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("Word conversion worker exited %s without a result (%d bytes on stderr)",
                            proc.returncode, len(err or b""))
                raise ConvertFailed("That document couldn't be converted to Word.") from None
            if why.get("detail"):
                log.info("Word conversion failed: %s", str(why["detail"])[:60])
            raise ConvertFailed(str(why.get("error") or "That document couldn't be converted to Word."))
        finally:
            shutil.rmtree(work, ignore_errors=True)
