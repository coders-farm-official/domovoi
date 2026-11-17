"""Core routes for reading scans: ``/v1/scan/reads``.

The dashboard and the phone apps reach them through the web
(``/api/scan/reads``, web/backend/api/printers.py). Reads are
household-read (``require_device_read``) like the Documents folder the
scans live in; asking for a read is device tier.

* ``GET /v1/scan/reads?path=`` — the newest read of a scan in
  Documents/Scans (``read`` null when it was never read) and whether this
  server can read scans (``can_read``).
* ``POST /v1/scan/reads`` ``{"path"}`` — read a scan (an older one, or one
  whose read failed): the read already under way or done for those bytes
  when there is one.
* ``GET /v1/scan/reads/{id}`` — its state and progress.
* ``GET /v1/scan/reads/{id}/summary`` — "What does it say?": the short
  summary (made once, by the chat model, then kept; with AI features off
  the start of the text instead).
* ``GET /v1/scan/reads/{id}/text`` — the full text, page by page.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_device, require_device_read
from domovoi.scan_reader import runner, says, store

router = APIRouter(tags=["printing"])
READ = [Depends(require_device_read)]
DEVICE = [Depends(require_device)]

SCANS_FOLDER = "Scans"


def scan_path(path: str) -> str:
    """``path`` as a Documents-relative scan path, or 400: a PDF inside
    Documents/Scans, no ``..``, no hidden parts."""
    p = PurePosixPath((path or "").replace("\\", "/").strip("/"))
    parts = p.parts
    if (len(parts) < 2 or parts[0] != SCANS_FOLDER or p.suffix.lower() != ".pdf"
            or any(x in ("", ".", "..") or x.startswith(".") for x in parts)):
        raise HTTPException(status_code=400, detail="Only scans in Documents, Scans can be read.")
    return p.as_posix()


async def _read_or_404(read_id: str) -> dict[str, Any]:
    row = await store.get(read_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such scan reading.")
    return row


@router.get("/v1/scan/reads", dependencies=READ)
async def read_for_path(path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    rel = scan_path(path)
    return {"read": await store.read_json_for(await store.latest_for_path(rel)), "can_read": runner.can_read()}


class ReadBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


@router.post("/v1/scan/reads", dependencies=DEVICE, status_code=201)
async def start_read(body: ReadBody) -> dict[str, Any]:
    rel = scan_path(body.path)
    if not runner.can_read():
        raise HTTPException(status_code=409, detail="This server can't read scans yet.")
    path = runner.resolve(rel)
    if path is None:
        raise HTTPException(status_code=404, detail="That scan isn't in Documents any more.")
    have = await store.latest_for_path(rel)
    if have is not None and have["state"] in ("queued", "running", "done"):
        sha = await asyncio.to_thread(store.sha256_of, path)
        if have["sha256"] == sha:
            return await store.read_json_for(have)  # type: ignore[return-value]
    read_id = await runner.queue_scan(rel)
    if read_id is None:
        raise HTTPException(status_code=409, detail="That scan couldn't be read.")
    return await store.read_json_for(await store.get(read_id))  # type: ignore[return-value]


@router.get("/v1/scan/reads/{read_id}", dependencies=READ)
async def get_read(read_id: str) -> dict[str, Any]:
    return await store.read_json_for(await _read_or_404(read_id))  # type: ignore[return-value]


@router.get("/v1/scan/reads/{read_id}/summary", dependencies=READ)
async def read_summary(read_id: str) -> dict[str, Any]:
    row = await _read_or_404(read_id)
    if row["state"] != "done":
        raise HTTPException(status_code=409, detail="Domovoi is still reading this scan.")
    out = await says.summary(row)
    return {"read": await store.read_json_for(row), **out}


@router.get("/v1/scan/reads/{read_id}/text", dependencies=READ)
async def read_text(read_id: str) -> dict[str, Any]:
    row = await _read_or_404(read_id)
    return {"read": await store.read_json_for(row), **(await says.full(row))}
