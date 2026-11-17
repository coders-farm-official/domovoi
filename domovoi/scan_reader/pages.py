"""Page pictures for the reader, drawn in :mod:`.page_worker` (its own
short-lived, capped process), and the smaller JPEG the picture model is
sent (from Domovoi's own PNG, so that part is done here)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TIMEOUT_SEC = 90.0
DPI = 300
#: The picture model is sent the page at most this many pixels on its long side.
VISION_EDGE = 1280


class PageFailed(Exception):
    """The page couldn't be drawn; ``str()`` is a sentence for people."""


def worker_argv(work: Path) -> list[str]:
    """The worker's command line (a seam: tests may start a stand-in)."""
    return [sys.executable, "-m", "domovoi.scan_reader.page_worker", str(work)]


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    from domovoi.doc_extract import limits

    work = Path(tempfile.mkdtemp(prefix="domovoi-scanpage-"))
    try:
        req = dict(request, memory_bytes=limits.worker_memory_bytes(), cpu_seconds=int(TIMEOUT_SEC))
        (work / "request.json").write_text(json.dumps(req), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = await asyncio.create_subprocess_exec(
            *worker_argv(work), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env=env,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SEC)
        except asyncio.TimeoutError:
            raise PageFailed("Drawing a page of the scan took too long.") from None
        finally:
            if proc.returncode is None:
                proc.kill()
                await asyncio.shield(proc.wait())
        try:
            result = json.loads((work / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("scan page worker exited %s without a result", proc.returncode)
            raise PageFailed("The scan couldn't be opened.") from None
        if not result.get("ok"):
            if result.get("detail"):
                log.info("scan page worker refused: %s", result["detail"])
            raise PageFailed(str(result.get("error") or "The scan couldn't be opened."))
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def count(pdf: Path) -> int:
    return int((await _run({"action": "count", "path": str(Path(pdf).resolve())}))["pages"])


async def render(pdf: Path, page: int, out: Path, dpi: int = DPI) -> Path:
    await _run({"action": "render", "path": str(Path(pdf).resolve()), "page": page, "dpi": dpi,
                "out": str(Path(out).resolve())})
    return out


def vision_jpeg(png: Path, edge: int = VISION_EDGE) -> bytes:
    """The page as the picture model is sent it: at most ``edge`` pixels on
    its long side, JPEG."""
    from PIL import Image

    with Image.open(png) as im:
        im = im.convert("RGB")
        im.thumbnail((edge, edge))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return buf.getvalue()
