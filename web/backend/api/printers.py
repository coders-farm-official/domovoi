"""Printers, printing and scanning — the dashboard's and the app's door.

The core owns printing (``domovoi/printing/``, ``/v1/printers``,
``/v1/print``, ``/v1/scan``) because voice needs it; this router proxies
to it with the caller's credentials forwarded, as every live-state action
does. Each route also wears the same tier here as in the core, so a refused
caller is refused before anything is read or forwarded.

The two print routes are not a straight proxy: the dashboard and the app
name a file the way the Files and Documents pages know it (``library_id`` +
``path``), and THIS process resolves it with the Files tab's own
containment (``files._resolve_library`` + ``safe_join``) and secret-name
filter, then sends the bytes to the core. The core never sees a path from
a client.

A scan writes into Documents, so starting one applies the Documents write
policy (``assert_documents_write_allowed``: the per-device block) here,
and its finish drops the Documents listing cache.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_admin_mutation, require_device, require_device_read
from web.backend.api.csrf_guard import require_requested_with
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    domovoi_url,
    get_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["printers"])
READ = [Depends(require_device_read)]
DEVICE = [Depends(require_requested_with), Depends(require_device)]
ADMIN = [Depends(require_requested_with), Depends(require_admin_mutation)]

MAX_PRINT_BYTES = 100 * 1024 * 1024
DOCUMENTS_LIBRARY_ID = "core:documents"

# What a phone app registers itself as (POST /api/devices/register). Until
# 2026-10-28 this was the single string "android", written when Android was
# the only app there was; an iPhone registering honestly as "ios" was then
# labelled "dashboard", which is not where its job came from.
PHONE_APP_PLATFORMS = frozenset({"android", "ios"})

# The marks a phone app leaves on a user agent, for a device that has not
# registered (or whose row we could not read). OkHttp is Android's HTTP
# library; the two product names are ours.
PHONE_APP_USER_AGENTS = ("okhttp", "domovoi-android", "domovoi-ios")


def source_label(platform: str, user_agent: str) -> str:
    """`"app"` or `"dashboard"`: where a print or scan job came from.

    Pure, so the rule is tested as written rather than through a request, a
    database and a printer. Both arguments are taken already lowercased, as
    `_caller` has them.

    Cosmetic — it is the word shown beside a job in the print queue — so it
    guesses rather than refuses, and a device we know nothing about reads
    "dashboard".
    """
    return "app" if (platform in PHONE_APP_PLATFORMS
                     or any(mark in user_agent for mark in PHONE_APP_USER_AGENTS)) else "dashboard"


async def _caller(request: Request) -> tuple[str | None, str, str]:
    """(device id, its name, "app"|"dashboard") as far as the request says."""
    from web.backend.api.files import caller_device_id

    ident = caller_device_id(request)
    name = ""
    platform = ""
    if ident:
        try:
            from sqlalchemy import text

            from web.backend.db import session_scope

            async with session_scope() as s:
                row = (await s.execute(text("SELECT name, platform FROM devices WHERE device_id = :d"),
                                       {"d": ident})).first()
            if row is not None:
                name, platform = row[0] or "", (row[1] or "").lower()
        except Exception as e:  # noqa: BLE001 — the label is cosmetic
            log.debug("printers: device lookup failed: %s", e)
    ua = (request.headers.get("user-agent") or "").lower()
    return ident, name, source_label(platform, ua)


_SCAN_ID = re.compile(r"^[0-9a-f]{32}$")


def _scan_id(scan_id: str) -> str:
    if not _SCAN_ID.match(scan_id or ""):
        raise HTTPException(status_code=404, detail="No such scan.")
    return scan_id


def _headers(request: Request, device_id: str | None = None) -> dict[str, str]:
    h = auth_forward_headers(request)
    if device_id:
        h["X-Domovoi-Device-Id"] = device_id
    return h


# ─── Printers (straight proxies) ─────────────────────────────────────────


@router.get("/api/printers", dependencies=READ)
async def list_printers(request: Request, refresh: bool = Query(False)):
    status, body = await get_admin("/v1/printers" + ("?refresh=true" if refresh else ""), timeout=30.0,
                                   headers=_headers(request))
    return bridge_response(status, body)


class DiscoverBody(BaseModel):
    look_again: bool = False


@router.post("/api/printers/discover", dependencies=DEVICE)
async def discover(request: Request, body: DiscoverBody):
    status, payload = await post_admin("/v1/printers/discover", body.model_dump(), timeout=60.0,
                                       headers=_headers(request))
    return bridge_response(status, payload)


class AddBody(BaseModel):
    key: Optional[str] = Field(None, max_length=64)
    address: Optional[str] = Field(None, max_length=300)
    name: Optional[str] = Field(None, max_length=120)
    room_id: Optional[str] = Field(None, max_length=64)


@router.post("/api/printers", dependencies=DEVICE, status_code=201)
async def add_printer(request: Request, body: AddBody):
    status, payload = await post_admin("/v1/printers", body.model_dump(), timeout=45.0,
                                       headers=_headers(request))
    return bridge_response(status, payload)


async def _send(method: str, path: str, request: Request, body: Any = None, timeout: float = 30.0):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, domovoi_url().rstrip("/") + path, json=body,
                                     headers=_headers(request))
    except Exception as e:  # noqa: BLE001
        log.warning("printers: %s %s failed: %s", method, path, e)
        return bridge_response(0, None)
    try:
        return bridge_response(r.status_code, r.json())
    except ValueError:
        return bridge_response(r.status_code, r.text)


class PatchBody(BaseModel):
    is_default: Optional[bool] = None
    rooms: Optional[list[str]] = Field(None, max_length=100)
    two_sided_default: Optional[bool] = None
    color_default: Optional[bool] = None


@router.patch("/api/printers/{printer_id}", dependencies=DEVICE)
async def patch_printer(printer_id: int, request: Request, body: PatchBody):
    return await _send("PATCH", f"/v1/printers/{printer_id}", request, body.model_dump(exclude_unset=True))


class RenameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.post("/api/printers/{printer_id}/rename", dependencies=ADMIN)
async def rename_printer(printer_id: int, request: Request, body: RenameBody):
    status, payload = await post_admin(f"/v1/printers/{printer_id}/rename", body.model_dump(),
                                       headers=_headers(request))
    return bridge_response(status, payload)


@router.delete("/api/printers/{printer_id}", dependencies=ADMIN)
async def remove_printer(printer_id: int, request: Request):
    status, payload = await delete_admin(f"/v1/printers/{printer_id}", headers=_headers(request))
    return bridge_response(status, payload)


@router.post("/api/printers/{printer_id}/trust-certificate", dependencies=ADMIN)
async def trust_certificate(printer_id: int, request: Request):
    status, payload = await post_admin(f"/v1/printers/{printer_id}/trust-certificate", {},
                                       headers=_headers(request))
    return bridge_response(status, payload)


@router.post("/api/printers/{printer_id}/refresh", dependencies=DEVICE)
async def refresh_printer(printer_id: int, request: Request):
    status, payload = await post_admin(f"/v1/printers/{printer_id}/refresh", {}, headers=_headers(request))
    return bridge_response(status, payload)


@router.post("/api/printers/{printer_id}/identify", dependencies=DEVICE)
async def identify_printer(printer_id: int, request: Request):
    status, payload = await post_admin(f"/v1/printers/{printer_id}/identify", {}, headers=_headers(request))
    return bridge_response(status, payload)


@router.post("/api/printers/{printer_id}/test-page", dependencies=DEVICE)
async def test_page(printer_id: int, request: Request):
    ident, name, source = await _caller(request)
    status, payload = await post_admin(f"/v1/printers/{printer_id}/test-page", {"who": name, "source": source},
                                       timeout=120.0, headers=_headers(request, ident))
    return bridge_response(status, payload)


# ─── Printing a file ─────────────────────────────────────────────────────


class PrintBody(BaseModel):
    library_id: str = Field(..., max_length=200)
    path: str = Field(..., min_length=1, max_length=4096)
    printer_id: Optional[int] = None
    options: dict[str, Any] = Field(default_factory=dict)


async def _read_file(library_id: str, path: str) -> tuple[bytes, str]:
    """The file's bytes and its name, through the Files tab's containment."""
    from web.backend.api.files import _resolve_library
    from web.backend.api.files_security import has_sensitive_segment, is_sensitive_name, safe_join

    lib = await _resolve_library(library_id)
    if lib.view == "other":
        # Other's files are kept as they are and only downloaded (doc_types.py).
        raise HTTPException(status_code=409, detail="Files in Other can only be downloaded.")
    target = safe_join(lib.root_path, path)
    if is_sensitive_name(target.name) or has_sensitive_segment(path, target, lib.root_path):
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    size = target.stat().st_size
    if size > MAX_PRINT_BYTES:
        raise HTTPException(status_code=413, detail="That file is too big to print (over 100 MB).")
    return target.read_bytes(), target.name


async def _forward_print(core_path: str, request: Request, body: PrintBody, *, timeout: float):
    data, filename = await _read_file(body.library_id, body.path)
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return await forward_print_bytes(core_path, request, data, filename, title=stem, printer_id=body.printer_id,
                                     options=body.options, library_id=body.library_id, path=body.path,
                                     timeout=timeout)


async def forward_print_bytes(core_path: str, request: Request, data: bytes, filename: str, *, title: str,
                              printer_id: Optional[int], options: dict[str, Any] | None,
                              library_id: str | None = None, path: str | None = None, timeout: float = 120.0):
    """Sends ``data`` (a file this process made or read itself) to the
    core's preview or print route with the caller's credentials: the Files
    and Documents print, and a chat draft's PDF (chat_drafts.py)."""
    ident, name, source = await _caller(request)
    meta = {"filename": filename, "title": title, "printer_id": printer_id,
            "options": {k: v for k, v in (options or {}).items()
                        if k in ("pages", "copies", "sides", "color", "media")},
            "who": name, "source": source, "library_id": library_id, "path": path}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                domovoi_url().rstrip("/") + core_path,
                files={"file": (filename, data, "application/octet-stream")},
                data={"meta": json.dumps(meta)},
                headers=_headers(request, ident),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("printers: POST %s failed: %s", core_path, e)
        return bridge_response(0, None)
    try:
        return bridge_response(r.status_code, r.json())
    except ValueError:
        return bridge_response(r.status_code, r.text)


@router.post("/api/print/preview", dependencies=DEVICE)
async def print_preview(request: Request, body: PrintBody):
    return await _forward_print("/v1/print/preview", request, body, timeout=120.0)


@router.post("/api/print/jobs", dependencies=DEVICE, status_code=202)
async def print_submit(request: Request, body: PrintBody):
    return await _forward_print("/v1/print/jobs", request, body, timeout=120.0)


@router.get("/api/print/jobs", dependencies=READ)
async def list_jobs(request: Request, printer_id: Optional[int] = Query(None), limit: int = Query(20, ge=1, le=200)):
    q = f"?limit={limit}" + (f"&printer_id={printer_id}" if printer_id is not None else "")
    status, payload = await get_admin("/v1/print/jobs" + q, headers=_headers(request))
    return bridge_response(status, payload)


@router.get("/api/print/jobs/{job_id}", dependencies=READ)
async def get_job(job_id: int, request: Request):
    status, payload = await get_admin(f"/v1/print/jobs/{job_id}", timeout=20.0, headers=_headers(request))
    return bridge_response(status, payload)


@router.post("/api/print/jobs/{job_id}/cancel", dependencies=DEVICE)
async def cancel_job(job_id: int, request: Request):
    status, payload = await post_admin(f"/v1/print/jobs/{job_id}/cancel", {}, headers=_headers(request))
    return bridge_response(status, payload)


@router.delete("/api/print/jobs", dependencies=ADMIN)
async def clear_jobs(request: Request):
    status, payload = await delete_admin("/v1/print/jobs", headers=_headers(request))
    return bridge_response(status, payload)


@router.get("/api/print/limits", dependencies=READ)
async def get_limits(request: Request):
    status, payload = await get_admin("/v1/print/limits", headers=_headers(request))
    return bridge_response(status, payload)


class LimitsBody(BaseModel):
    max_pages_per_job: Optional[int] = Field(None, ge=1, le=2000)
    max_copies: Optional[int] = Field(None, ge=1, le=999)
    max_sheets_per_job: Optional[int] = Field(None, ge=1, le=5000)
    max_jobs_per_hour_per_device: Optional[int] = Field(None, ge=1, le=1000)
    max_sheets_per_day: Optional[int] = Field(None, ge=1, le=20000)
    max_scan_pages: Optional[int] = Field(None, ge=1, le=500)
    history_days: Optional[int] = None


@router.put("/api/print/limits", dependencies=ADMIN)
async def put_limits(request: Request, body: LimitsBody):
    return await _send("PUT", "/v1/print/limits", request, body.model_dump(exclude_unset=True))


# ─── Scanning ────────────────────────────────────────────────────────────


class ScanBody(BaseModel):
    printer_id: Optional[int] = None
    source: str = Field("auto", max_length=20)
    color: str = Field("color", max_length=10)
    resolution: int = Field(300, ge=75, le=1200)


async def _documents_write_allowed(request: Request) -> None:
    from web.backend.api.files import assert_documents_write_allowed

    await assert_documents_write_allowed(request)


def _documents_changed() -> None:
    try:
        from web.backend.api import listing_cache

        listing_cache.invalidate(DOCUMENTS_LIBRARY_ID)
    except Exception as e:  # noqa: BLE001
        log.debug("printers: listing invalidate failed: %s", e)


@router.post("/api/scan/jobs", dependencies=DEVICE, status_code=201)
async def scan_start(request: Request, body: ScanBody):
    await _documents_write_allowed(request)
    ident, name, source = await _caller(request)
    payload = body.model_dump() | {"who": name, "origin": source}
    status, out = await post_admin("/v1/scan/jobs", payload, timeout=300.0, headers=_headers(request, ident))
    return bridge_response(status, out)


@router.get("/api/scan/jobs/{scan_id}", dependencies=READ)
async def scan_get(scan_id: str, request: Request):
    status, out = await get_admin(f"/v1/scan/jobs/{_scan_id(scan_id)}", headers=_headers(request))
    return bridge_response(status, out)


@router.post("/api/scan/jobs/{scan_id}/next", dependencies=DEVICE)
async def scan_next(scan_id: str, request: Request):
    await _documents_write_allowed(request)
    ident, _name, _source = await _caller(request)
    status, out = await post_admin(f"/v1/scan/jobs/{_scan_id(scan_id)}/next", {}, timeout=300.0,
                                   headers=_headers(request, ident))
    return bridge_response(status, out)


@router.post("/api/scan/jobs/{scan_id}/finish", dependencies=DEVICE)
async def scan_finish(scan_id: str, request: Request):
    await _documents_write_allowed(request)
    ident, _name, _source = await _caller(request)
    status, out = await post_admin(f"/v1/scan/jobs/{_scan_id(scan_id)}/finish", {}, timeout=120.0,
                                   headers=_headers(request, ident))
    if status == 200:
        _documents_changed()
    return bridge_response(status, out)


@router.post("/api/scan/jobs/{scan_id}/cancel", dependencies=DEVICE)
async def scan_cancel(scan_id: str, request: Request):
    status, out = await post_admin(f"/v1/scan/jobs/{_scan_id(scan_id)}/cancel", {}, headers=_headers(request))
    return bridge_response(status, out)
