"""Run :mod:`domovoi.printing.render_worker` in its own short-lived process.

One process per step, with a time limit, killed when it runs over: PDFium
is native code and the document is somebody else's file, so a PDF that
hangs or crashes it costs one job, not the core (plan part 1). The work
directory holds the input copy and every output and is removed by the
caller (:class:`Workdir`).

At most :data:`MAX_PARALLEL` drawings run at once (sized for the box), so
a burst of previews can't eat the server's memory (a 600 dpi colour page is
~100 MB raw); each worker is memory- and CPU-capped and checks a pixel
budget before drawing (:mod:`domovoi.printing.limits`).
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

log = logging.getLogger(__name__)

from domovoi.printing import limits

PREPARE_TIMEOUT_SEC = 90.0
OUTPUT_TIMEOUT_SEC = 900.0
# Sized for the box: one drawing at a time on a small one, two otherwise
# (each worker is also memory-capped: limits.worker_memory_bytes).
MAX_PARALLEL = limits.worker_slots()
_gate = asyncio.Semaphore(MAX_PARALLEL)


class RenderFailed(Exception):
    """The drawing refused or failed; ``str()`` is a sentence for people."""


class Workdir:
    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="domovoi-print-"))

    def input(self) -> Path:
        return self.path / "input"

    def output(self) -> Path:
        return self.path / "out.bin"

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> "Workdir":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.cleanup()


async def run(workdir: Workdir, request: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    request = dict(request, memory_bytes=limits.worker_memory_bytes(), cpu_seconds=int(timeout))
    (workdir.path / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (workdir.path / "result.json").unlink(missing_ok=True)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    async with _gate:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "domovoi.printing.render_worker", str(workdir.path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RenderFailed("Drawing the pages took too long, so the job was stopped.")
    try:
        result = json.loads((workdir.path / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("print render worker exited %s without a result: %s", proc.returncode,
                    (err or b"")[-500:].decode("utf-8", "replace"))
        raise RenderFailed("That document couldn't be drawn for printing.")
    if not result.get("ok"):
        if result.get("detail"):
            log.info("print render refused: %s", result["detail"])
        raise RenderFailed(result.get("error") or "That document couldn't be drawn for printing.")
    return result


async def prepare(workdir: Workdir, **request: Any) -> dict[str, Any]:
    return await run(workdir, {"action": "prepare", **request}, timeout=PREPARE_TIMEOUT_SEC)


async def output(workdir: Workdir, **request: Any) -> dict[str, Any]:
    return await run(workdir, {"action": "output", **request}, timeout=OUTPUT_TIMEOUT_SEC)
