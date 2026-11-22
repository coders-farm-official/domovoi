"""The offline kit card's routes (Settings → System → Offline kit; release
1.5.0): the dashboard's side of ``domovoi/kit_builder/`` on the core.

The core makes the kit image and serves it, with its Raspberry Pi Imager
list, at ``/v1/offline-kit/<id>/…`` on the core's own address; this
router only forwards the card's reads and an admin's presses, and tells
the core the address phones and Imager reach it at (the one the join
page's QR code uses), so the Imager list it writes points at the image.
All admin: a kit takes many gigabytes of the server's disk and downloads.
A kit carrying household items downloads only with the admin's Bearer, so
the card fetches it here (:func:`offline_kit_download`), the caller's
credentials forwarded to the core, which checks them.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_admin_mutation, require_admin_read

from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    domovoi_url,
    get_admin,
    post_admin,
)

_ID_RE = re.compile(r"[A-Za-z0-9_-]{32}")
_IMAGE_RE = re.compile(r"domovoi-offline-kit-[0-9A-Za-z.+-]{1,64}\.img")

router = APIRouter(prefix="/api/offline-kit", tags=["offline-kit"])


def _core_address() -> str | None:
    from satellite import enrolment

    from web.backend.api.satellites import _advertised_core_http

    try:
        core, _ = enrolment.normalize_core(_advertised_core_http())
        return core
    except enrolment.EnrolmentError:
        return None


def _absolute(doc: Any) -> Any:
    """The finished kits' links as the browser reaches them."""
    core = _core_address()
    if not core or not isinstance(doc, dict):
        return doc
    for b in doc.get("builds") or []:
        for key in ("image_url", "os_list_url", "manifest_url"):
            url = b.get(key)
            if isinstance(url, str) and url.startswith("/"):
                b[key] = core.rstrip("/") + url
    return doc


@router.get("", dependencies=[Depends(require_admin_read)])
async def offline_kit_status(request: Request, targets: str | None = None, includes: str | None = None,
                             household: str | None = None, stick_gb: int | None = None):
    """The card for the boxes ticked: sizes, the fit check, the refusal or
    warning, the job and the finished kit."""
    q = {k: v for k, v in (("targets", targets), ("includes", includes), ("household", household),
                           ("stick_gb", stick_gb)) if v is not None}
    path = "/v1/admin/offline-kit" + (f"?{urlencode(q)}" if q else "")
    status, doc = await get_admin(path, timeout=120.0, headers=auth_forward_headers(request))
    return bridge_response(status, doc) if status != 200 else _absolute(doc)


class OfflineKitBuild(BaseModel):
    targets: list[str] = Field(default_factory=lambda: ["pc-x86_64"], max_length=4)
    includes: list[str] = Field(default_factory=lambda: ["ai", "vision", "rooms"], max_length=8)
    household: list[str] = Field(default_factory=list, max_length=64)
    stick_gb: int | None = None
    force: bool = False


@router.post("", dependencies=[Depends(require_admin_mutation)])
async def offline_kit_make(body: OfflineKitBuild, request: Request):
    """Start making the kit. 409 ``{reason, message}`` when it can't
    (``behind``: send again with ``force`` for "Make it anyway")."""
    payload = {**body.model_dump(), "core": _core_address()}
    status, doc = await post_admin("/v1/admin/offline-kit/build", payload, timeout=120.0,
                                   headers=auth_forward_headers(request))
    return bridge_response(status, doc) if status != 200 else _absolute(doc)


@router.post("/cancel", dependencies=[Depends(require_admin_mutation)])
async def offline_kit_cancel(request: Request):
    """Stop the kit being made; nothing it made is kept."""
    status, doc = await post_admin("/v1/admin/offline-kit/cancel", headers=auth_forward_headers(request))
    return bridge_response(status, doc) if status != 200 else doc


@router.get("/{build_id}/{name}", dependencies=[Depends(require_admin_read)])
async def offline_kit_download(build_id: str, name: str, request: Request):
    """A finished kit's image, streamed from the core with the caller's
    credentials: the way the card downloads a kit that carries household
    items (the core asks for the admin's Bearer there)."""
    if not _ID_RE.fullmatch(build_id) or not _IMAGE_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="no such kit")
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
    try:
        req = client.build_request("GET", f"{domovoi_url().rstrip('/')}/v1/offline-kit/{build_id}/{name}",
                                   headers=auth_forward_headers(request))
        r = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="the core didn't answer") from None
    if r.status_code != 200:
        await r.aclose()
        await client.aclose()
        code = r.status_code if r.status_code in (401, 403, 404, 423) else 502
        raise HTTPException(status_code=code, detail="the kit can't be downloaded")

    async def body():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    headers = {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
               "Cache-Control": "no-store"}
    if r.headers.get("content-length", "").isdigit():
        headers["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(body(), media_type="application/octet-stream", headers=headers)


@router.delete("/{build_id}", dependencies=[Depends(require_admin_mutation)])
async def offline_kit_delete(build_id: str, request: Request):
    """Remove a finished kit image from the server."""
    if not _ID_RE.fullmatch(build_id):
        raise HTTPException(status_code=404, detail="no such kit")
    status, doc = await delete_admin(f"/v1/admin/offline-kit/builds/{build_id}", timeout=60.0,
                                     headers=auth_forward_headers(request))
    return bridge_response(status, doc) if status != 200 else _absolute(doc)
