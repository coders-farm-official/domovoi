"""Core routes for printers, printing and scanning: ``/v1/printers``,
``/v1/print`` and ``/v1/scan``.

The core owns these because voice needs them; the web dashboard and the
app reach them through ``web/backend/api/printers.py``, which forwards the
caller's credentials. Tiers (owner decisions, plan part 7):

* **device read** (``require_device_read``): the printer list, status, ink,
  the job log, the limits — the household's own printing is a household
  read;
* **device** (``require_device``): look for printers (mDNS; the sweep on
  "Look again"), add one, defaults, room defaults, test page, identify,
  preview, print, cancel, scan. A satellite turn reaches the same code
  through the ``printing`` handler as its room;
* **admin** (``require_admin_mutation``): rename, remove, trust a changed
  certificate, change the limits, clear the job log.

Guests (no credential) get nothing. Every mutation also needs
``X-Requested-With`` on the web hop (middleware).
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from domovoi import admin_auth
from domovoi.admin_auth import require_admin_mutation, require_device, require_device_read
from domovoi.printing import devices, discovery, jobs, lan_target, scans, store

log = logging.getLogger(__name__)

router = APIRouter(tags=["printing"])
READ = [Depends(require_device_read)]
DEVICE = [Depends(require_device)]
ADMIN = [Depends(require_admin_mutation)]

MAX_UPLOAD = jobs.MAX_INPUT_BYTES


def _who(request: Request, label: str | None, source: str | None) -> jobs.Who:
    # The hourly limit is counted per source address (the web forwards the
    # caller's own): a self-asserted device id could be changed per job.
    key = "addr:" + admin_auth.request_source(request)
    src = source if source in ("dashboard", "app") else "dashboard"
    name = " ".join(str(label or "").split())[:80] or ("a phone" if src == "app" else "the dashboard")
    return jobs.Who(label=name, device_key=key, source=src)


def _scan_id(scan_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", scan_id or ""):
        raise scans.ScanRefused("No such scan.", 404)
    return scan_id


def _refuse(e: Exception) -> HTTPException:
    if isinstance(e, jobs.PrintRefused):
        return HTTPException(status_code=e.status, detail=str(e))
    if isinstance(e, scans.ScanRefused):
        return HTTPException(status_code=e.status, detail=str(e))
    if isinstance(e, lan_target.NotOnLan):
        return HTTPException(status_code=400, detail=e.reason)
    if isinstance(e, devices.DeviceError):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=500, detail="printing failed")


async def _rows_json(refresh: bool = False) -> list[dict[str, Any]]:
    limits = await store.get_limits()
    rows = await store.list_printers()
    active = {}
    for j in await store.active_jobs():
        active.setdefault(j.get("printer_id"), jobs.job_json(j))
    out = []
    for row in rows:
        if refresh and row.get("ipp_address"):
            try:
                row["status"] = await devices.refresh_status(row)
            except Exception as e:  # noqa: BLE001
                log.debug("printing: refresh %s: %s", row.get("name"), e)
        out.append(devices.printer_json(row, limits=limits, active_job=active.get(row["id"])))
    return out


async def _one(printer_id: int) -> dict[str, Any]:
    for p in await _rows_json():
        if p["id"] == printer_id:
            return p
    raise HTTPException(status_code=404, detail="That printer isn't set up.")


# ─── Printers ────────────────────────────────────────────────────────────


@router.get("/v1/printers", dependencies=READ)
async def list_printers(refresh: bool = Query(False)) -> dict[str, Any]:
    printers = await _rows_json(refresh=refresh)
    default = next((p["id"] for p in printers if p["is_default"]), None)
    return {
        "printers": printers,
        "has_printer": any(p["can_print"] for p in printers),
        "has_scanner": any(p["can_scan"] for p in printers),
        "default_printer_id": default,
        "rooms": await store.known_rooms(),
        "printable": jobs.printable_info(),
        "limits": await store.get_limits(),
        "discovery": discovery.last_diagnostics(),
    }


class DiscoverBody(BaseModel):
    look_again: bool = False


@router.post("/v1/printers/discover", dependencies=DEVICE)
async def discover(body: DiscoverBody) -> dict[str, Any]:
    return await discovery.look(look_again=body.look_again)


class AddBody(BaseModel):
    key: str | None = Field(None, max_length=64)
    address: str | None = Field(None, max_length=300)
    name: str | None = Field(None, max_length=120)
    room_id: str | None = Field(None, max_length=64)


async def _room_label(room_id: str | None) -> str | None:
    if not room_id:
        return None
    for r in await store.known_rooms():
        if r["id"] == room_id:
            return r["label"]
    return room_id.replace("_", " ").replace("-", " ")


@router.post("/v1/printers", dependencies=DEVICE, status_code=201)
async def add_printer(request: Request, body: AddBody) -> dict[str, Any]:
    if not body.key and not body.address:
        raise HTTPException(status_code=422, detail="Pick a printer, or type its address.")
    label = await _room_label(body.room_id)
    try:
        if body.key:
            row = await discovery.add_found(body.key, name=body.name, room_id=body.room_id, room_label=label)
        else:
            admin = await admin_auth.check_admin_request(request) in ("ok",)
            row = await discovery.add_typed(body.address or "", name=body.name, room_id=body.room_id,
                                            room_label=label, admin=admin)
    except (devices.DeviceError, lan_target.NotOnLan) as e:
        raise _refuse(e) from e
    return await _one(row["id"])


class PatchBody(BaseModel):
    is_default: bool | None = None
    rooms: list[str] | None = Field(None, max_length=100)
    two_sided_default: bool | None = None
    color_default: bool | None = None
    model_config = {"extra": "forbid"}


@router.patch("/v1/printers/{printer_id}", dependencies=DEVICE)
async def patch_printer(printer_id: int, body: PatchBody) -> dict[str, Any]:
    row = await store.get_printer(printer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    sent = body.model_dump(exclude_unset=True)
    if "is_default" in sent and body.is_default is not None:
        await store.set_default(printer_id, body.is_default)
    if "rooms" in sent and body.rooms is not None:
        rooms = [r[:64] for r in dict.fromkeys(body.rooms) if r and r.strip()]
        await store.set_rooms(printer_id, rooms)
    changes = {k: sent[k] for k in ("two_sided_default", "color_default") if k in sent}
    if changes:
        await store.update_printer(printer_id, changes)
    return await _one(printer_id)


class RenameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.post("/v1/printers/{printer_id}/rename", dependencies=ADMIN)
async def rename_printer(printer_id: int, body: RenameBody) -> dict[str, Any]:
    name = devices.clean_name(body.name)
    if not name:
        raise HTTPException(status_code=422, detail="Give it a name.")
    if await store.get_printer(printer_id) is None:
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    await store.update_printer(printer_id, {"name": name})
    return await _one(printer_id)


@router.delete("/v1/printers/{printer_id}", dependencies=ADMIN)
async def remove_printer(printer_id: int) -> dict[str, Any]:
    if not await store.delete_printer(printer_id):
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    return {"removed": printer_id}


@router.post("/v1/printers/{printer_id}/trust-certificate", dependencies=ADMIN)
async def trust_certificate(printer_id: int) -> dict[str, Any]:
    row = await store.get_printer(printer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    seen = row.get("cert_seen_sha256")
    if not seen:
        raise HTTPException(status_code=409, detail="Its certificate hasn't changed.")
    changes: dict[str, Any] = {"cert_seen_sha256": None, "misses": 0}
    if row.get("ipp_scheme") == "ipps":
        changes["cert_sha256"] = seen
    if row.get("escl_scheme") == "https":
        changes["escl_cert_sha256"] = seen
    await store.update_printer(printer_id, changes)
    log.warning("printing: an admin trusted the new certificate of %s", row.get("name"))
    return await _one(printer_id)


@router.post("/v1/printers/{printer_id}/refresh", dependencies=DEVICE)
async def refresh_printer(printer_id: int) -> dict[str, Any]:
    row = await store.get_printer(printer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    await devices.refresh_status(row, force=True)
    return await _one(printer_id)


@router.post("/v1/printers/{printer_id}/identify", dependencies=DEVICE)
async def identify_printer(printer_id: int) -> dict[str, Any]:
    from domovoi.printing.client import IppClient, IppError
    from domovoi.printing import http11

    row = await store.get_printer(printer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That printer isn't set up.")
    actions = (row.get("capabilities") or {}).get("identify_actions") or []
    try:
        client = IppClient(devices.ipp_printer(row), timeout=10.0)
        await client.identify([a for a in ("flash", "sound", "display") if a in actions][:2] or None)
    except devices.DeviceError as e:
        raise _refuse(e) from e
    except (IppError, http11.HttpError) as e:
        raise HTTPException(status_code=409, detail="%s didn't answer." % devices.The(row["name"])) from e
    return {"ok": True}


class TestPageBody(BaseModel):
    who: str | None = Field(None, max_length=120)
    source: str | None = Field(None, max_length=20)


@router.post("/v1/printers/{printer_id}/test-page", dependencies=DEVICE)
async def print_test_page(request: Request, printer_id: int, body: TestPageBody | None = None) -> dict[str, Any]:
    who = _who(request, body.who if body else None, body.source if body else None)
    try:
        return await jobs.test_page(printer_id, who)
    except (jobs.PrintRefused, devices.DeviceError) as e:
        raise _refuse(e) from e


# ─── Printing ────────────────────────────────────────────────────────────


async def _spool(upload: UploadFile) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="domovoi-print-in-", delete=False)
    size = 0
    try:
        while True:
            chunk = await upload.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(status_code=413, detail="That file is too big to print (over 100 MB).")
            tmp.write(chunk)
    except BaseException:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    tmp.close()
    return Path(tmp.name)


def _meta(raw: str) -> dict[str, Any]:
    try:
        meta = json.loads(raw or "{}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail="bad meta") from e
    if not isinstance(meta, dict):
        raise HTTPException(status_code=422, detail="bad meta")
    opts = meta.get("options") or {}
    if not isinstance(opts, dict):
        raise HTTPException(status_code=422, detail="bad options")
    clean = {}
    for k in ("pages", "copies", "sides", "color", "media"):
        v = opts.get(k)
        if v is not None:
            clean[k] = v if k == "copies" else str(v)[:100]
    meta["options"] = clean
    pid = meta.get("printer_id")
    meta["printer_id"] = int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else None
    meta["filename"] = str(meta.get("filename") or "")[:255]
    meta["title"] = str(meta.get("title") or Path(meta["filename"]).stem or "Document")[:200]
    return meta


@router.post("/v1/print/preview", dependencies=DEVICE)
async def print_preview(file: UploadFile = File(...), meta: str = Form("{}")) -> dict[str, Any]:
    m = _meta(meta)
    src = await _spool(file)
    try:
        return await jobs.preview(src, filename=m["filename"] or file.filename or "", title=m["title"],
                                  printer_id=m["printer_id"], wanted=m["options"])
    except (jobs.PrintRefused, devices.DeviceError) as e:
        raise _refuse(e) from e
    finally:
        src.unlink(missing_ok=True)


@router.post("/v1/print/jobs", dependencies=DEVICE, status_code=202)
async def print_submit(request: Request, file: UploadFile = File(...), meta: str = Form("{}")) -> dict[str, Any]:
    m = _meta(meta)
    src = await _spool(file)
    try:
        return await jobs.submit(
            src, filename=m["filename"] or file.filename or "", title=m["title"], printer_id=m["printer_id"],
            wanted=m["options"], who=_who(request, m.get("who"), m.get("source")),
            library_id=str(m.get("library_id") or "")[:100] or None, path=str(m.get("path") or "")[:1000] or None,
        )
    except (jobs.PrintRefused, devices.DeviceError) as e:
        raise _refuse(e) from e
    finally:
        src.unlink(missing_ok=True)


@router.get("/v1/print/jobs", dependencies=READ)
async def list_jobs(printer_id: int | None = Query(None), limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"jobs": [jobs.job_json(j) for j in await store.list_jobs(printer_id=printer_id, limit=limit)]}


@router.get("/v1/print/jobs/{job_id}", dependencies=READ)
async def get_job(job_id: int) -> dict[str, Any]:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such print job.")
    if job["state"] in ("queued", "printing") and job.get("ipp_job_id"):
        try:
            await jobs.follow(job_id)
        except Exception as e:  # noqa: BLE001 — the stored state still answers
            log.debug("printing: follow %s: %s", job_id, e)
        job = await store.get_job(job_id) or job
    return jobs.job_json(job)


@router.post("/v1/print/jobs/{job_id}/cancel", dependencies=DEVICE)
async def cancel_job(job_id: int) -> dict[str, Any]:
    try:
        return await jobs.cancel(job_id)
    except jobs.PrintRefused as e:
        raise _refuse(e) from e


@router.delete("/v1/print/jobs", dependencies=ADMIN)
async def clear_jobs() -> dict[str, Any]:
    return {"cleared": await store.clear_history()}


@router.get("/v1/print/limits", dependencies=READ)
async def get_limits() -> dict[str, Any]:
    return await store.get_limits()


class LimitsBody(BaseModel):
    max_pages_per_job: int | None = Field(None, ge=1, le=2000)
    max_copies: int | None = Field(None, ge=1, le=999)
    max_sheets_per_job: int | None = Field(None, ge=1, le=5000)
    max_jobs_per_hour_per_device: int | None = Field(None, ge=1, le=1000)
    max_sheets_per_day: int | None = Field(None, ge=1, le=20000)
    max_scan_pages: int | None = Field(None, ge=1, le=500)
    history_days: int | None = None


@router.put("/v1/print/limits", dependencies=ADMIN)
async def put_limits(body: LimitsBody) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True, exclude={"history_days"})
    return await store.set_limits({k: v for k, v in values.items() if v is not None})


# ─── Scanning ────────────────────────────────────────────────────────────


class ScanBody(BaseModel):
    printer_id: int | None = None
    source: str = Field("auto", max_length=20)
    color: str = Field("color", max_length=10)
    resolution: int = Field(300, ge=75, le=1200)
    who: str | None = Field(None, max_length=120)
    origin: str | None = Field(None, max_length=20)


@router.post("/v1/scan/jobs", dependencies=DEVICE, status_code=201)
async def scan_start(request: Request, body: ScanBody) -> dict[str, Any]:
    who = _who(request, body.who, body.origin)
    try:
        s = await scans.start(body.printer_id, source=body.source, color=body.color, resolution=body.resolution,
                              who=who.label, origin=who.source)
    except scans.ScanRefused as e:
        raise _refuse(e) from e
    return s.json()


@router.get("/v1/scan/jobs/{scan_id}", dependencies=READ)
async def scan_get(scan_id: str) -> dict[str, Any]:
    try:
        return scans.get(_scan_id(scan_id)).json()
    except scans.ScanRefused as e:
        raise _refuse(e) from e


@router.post("/v1/scan/jobs/{scan_id}/next", dependencies=DEVICE)
async def scan_next(scan_id: str) -> dict[str, Any]:
    try:
        return (await scans.more(_scan_id(scan_id))).json()
    except scans.ScanRefused as e:
        raise _refuse(e) from e


@router.post("/v1/scan/jobs/{scan_id}/finish", dependencies=DEVICE)
async def scan_finish(scan_id: str) -> dict[str, Any]:
    try:
        return (await scans.finish(_scan_id(scan_id))).json()
    except scans.ScanRefused as e:
        raise _refuse(e) from e


@router.post("/v1/scan/jobs/{scan_id}/cancel", dependencies=DEVICE)
async def scan_cancel(scan_id: str) -> dict[str, Any]:
    try:
        return (await scans.cancel(_scan_id(scan_id))).json()
    except scans.ScanRefused as e:
        raise _refuse(e) from e
