"""Satellite media preparation API — the dashboard's "prepare media" card.

Long builds run as background tasks tracked in ``satellite_media_jobs``
(V004, cloned from the model_jobs pattern): every progress write fires
``pg_notify('satellite_media_jobs_changed', ...)`` which the realtime layer
maps to the ``satellites.media`` channel, so the card's progress bar is
live.

**The whole router is admin-gated** (WEB-1). The mutations always were —
preparing media writes bootstrap scripts that run as root on a future
satellite — but the reads matter just as much: the artifact a build
produces is the code a Pi will run, ``/jobs`` lists the small integer ids
that name it, and ``/targets`` enumerates the drives plugged into the
server. Reads take ``require_admin_read`` (a Bearer or the dashboard
cookie), writes keep ``require_admin_mutation``.

One route sits outside that gate: ``POST /claim`` (:data:`claim_router`),
where the Windows tray's card helper, on the same PC, trades a zip's
one-time pass (``domovoi/claim.json``) for the setup network's key so
nobody types it. The pass is the credential: 256 random bits, good once,
for a day, answered to a loopback peer only and only while this process
still remembers the build (see :func:`media_claim`).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import egress
from domovoi.admin_auth import require_admin_mutation, require_admin_read
from domovoi.container_guard import freeze as container_freeze
from domovoi.host_kind import host_kind
from domovoi.satellite_media import builder, cache, fetchers, overlay
from domovoi.satellite_media.boards import BOARDS, MIC_PROFILES, PI02W
from domovoi.satellite_payload import enabled_satellite_plugins, payload_files

from web.backend.api.files_security import (
    detect_removable,
    drive_token,
    removable_drives_visible,
)
from web.backend.domovoi_client import auth_forward_headers, post_admin
from web.backend.db import session_scope
from web.backend.satellite_adoption import _volume_label  # label pre-filter reuse

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/satellites/media",
    tags=["satellite-media"],
    # Router-level, so a route added later is gated by default rather than
    # by remembering. The mutations add require_admin_mutation on top.
    dependencies=[Depends(require_admin_read)],
)

_NOTIFY = "SELECT pg_notify('satellite_media_jobs_changed', :p)"

# What a person needs to connect to the satellite they just prepared: the
# setup network's key, and the console login. Held in PROCESS MEMORY only —
# never written to satellite_media_jobs, never logged. A web restart drops
# them and the card remains the source of truth (domovoi/ap.json and
# domovoi/console.json), so nothing is lost, but they also can't outlive the
# card by sitting in a database backup.
_JOB_CREDENTIALS: dict[int, dict[str, Any]] = {}
_CREDENTIAL_CAP = 20


# The one-time pass a Wi-Fi setup card's zip carries (builder.CLAIM_JSON_PATH),
# by job: only the secret's SHA-256, when it stops working, and whether it
# has been used. Memory only, beside the credentials it unlocks, and
# forgotten with them.
_JOB_CLAIMS: dict[int, dict[str, Any]] = {}
CLAIM_TTL_SEC = 24 * 3600

# Seconds since the epoch; a module attribute so a test can move the clock.
_now = time.time


def _remember_credentials(job_id: int, creds: dict[str, Any]) -> None:
    _JOB_CREDENTIALS[job_id] = creds
    # Bounded so a long-lived process can't accumulate them indefinitely.
    while len(_JOB_CREDENTIALS) > _CREDENTIAL_CAP:
        _JOB_CREDENTIALS.pop(next(iter(_JOB_CREDENTIALS)))
    for stale in [j for j in _JOB_CLAIMS if j not in _JOB_CREDENTIALS]:
        _JOB_CLAIMS.pop(stale, None)


def _new_claim(job_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """A fresh one-time pass for a zip build: what goes in the zip, and
    what this process keeps (the secret's hash, never the secret)."""
    secret = secrets.token_urlsafe(32)
    expires = _now() + CLAIM_TTL_SEC
    in_zip = {
        "schema": 1,
        "job_id": job_id,
        "secret": secret,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    kept = {
        "sha256": hashlib.sha256(secret.encode()).hexdigest(),
        "expires": expires,
        "used": False,
    }
    return in_zip, kept


def default_setup_transport() -> str:
    """What a prepare request that names no transport gets: USB adoption
    where this server can see the device's drive, the Wi-Fi portal where
    it can't (inside WSL, whose host keeps USB drives to itself)."""
    return "usb" if removable_drives_visible() else "portal"


# Why a drive target is refused inside WSL. Worded for the person reading
# a 422, who is holding a card and needs the way forward.
_NO_DRIVES_DETAIL = (
    "this server runs inside WSL on Windows and can't see SD cards — "
    "choose the download for the card helper, then use Domovoi's tray icon "
    "→ Prepare a satellite card"
)


class PrepareRequest(BaseModel):
    board: str = "pi02w"
    mic_profile: str = "respeaker_2mic_hat_v2"
    target: dict = Field(default_factory=lambda: {"kind": "zip"})
    offline: bool = True
    # "usb"    — the device presents a DOMOVOI-SET drive to this server.
    # "portal" — the device raises its own Wi-Fi setup AP, and the customer
    #            onboards it from a phone. Portal cards deliberately skip the
    #            dwc2 peripheral-mode overlay, which a USB mic array can't
    #            share a single-port Pi with.
    setup_transport: str = Field(
        default_factory=default_setup_transport,
        description=(
            "usb | portal. Omitted: usb, or portal when the server runs "
            "inside WSL (no removable drives there)."
        ),
    )
    # No safe default exists — legal channels and power limits are per
    # market, so this is a deliberate choice per batch, not a fallback.
    wifi_country: str = "US"


async def _job_row(job_id: int) -> dict[str, Any] | None:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, board, mic_profile, target_kind, target_ref, "
                    "offline, status, phase, pct, status_text, error, warnings, "
                    "artifact_path, requested_at, completed_at "
                    "FROM satellite_media_jobs WHERE id = :i"
                ),
                {"i": job_id},
            )
        ).mappings().first()
    return dict(row) if row else None


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    out.pop("artifact_path", None)   # server-local; download endpoint serves it
    out["has_artifact"] = bool(job.get("artifact_path"))
    return out


async def _update_job(job_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with session_scope() as s:
        await s.execute(
            text(
                f"UPDATE satellite_media_jobs SET {sets}, updated_at = now() "
                f"WHERE id = :i"
            ),
            {**fields, "i": job_id},
        )
        await s.execute(text(_NOTIFY), {"p": str(job_id)})


@router.get("/status")
async def media_status() -> dict[str, Any]:
    """Everything the card needs to render: boards, cache state, docker
    availability, and what each enabled plugin would ship."""
    plugins = []
    for entry in await enabled_satellite_plugins():
        files = payload_files(entry["root"], entry["decl"])
        plugins.append({
            "slug": entry["slug"],
            "version": entry["version"],
            "files": len(files),
            "payload_bytes": sum(p.stat().st_size for p in files.values()),
            "satellite_root": bool(
                entry["decl"]["apt_packages"] or entry["decl"]["post_install"]
            ),
        })
    return {
        "docker_available": fetchers.docker_available(),
        # Inside WSL there are no drive targets and USB adoption can't
        # finish, so the card offers the zip and the Wi-Fi portal only.
        "host_kind": host_kind(),
        "drive_targets": removable_drives_visible(),
        "default_setup_transport": default_setup_transport(),
        "cache": cache.status(PI02W.python_version, PI02W.os_release),
        "boards": [
            {
                "id": b.id, "label": b.label, "supported": b.supported,
                "stock_os": b.stock_os, "note": b.note,
            }
            for b in BOARDS.values()
        ],
        "mic_profiles": list(MIC_PROFILES),
        "plugins": plugins,
    }


@router.get("/targets")
async def media_targets() -> list[dict[str, Any]]:
    """Removable drives that look like a flashed Pi boot partition. Always
    empty inside WSL, which sees no removable drives (``/status`` says
    so with ``drive_targets: false``)."""
    from domovoi.satellite_media.overlay import looks_like_pi_boot
    import shutil as _shutil

    out: list[dict[str, Any]] = []
    if not removable_drives_visible():
        return out
    for rm in detect_removable():
        mount = rm.get("mount")
        if not mount:
            continue
        p = Path(mount)
        try:
            looks = looks_like_pi_boot(p, PI02W.boot_marker)
            free = _shutil.disk_usage(p).free if looks else None
        except OSError:
            continue
        out.append({
            "kind": "drive",
            "token": drive_token(mount),
            "label": _volume_label(mount, rm.get("device")),
            "looks_like_pi_boot": looks,
            "free_bytes": free,
        })
    return out


@router.post("/prepare", dependencies=[Depends(require_admin_mutation)])
async def media_prepare(body: PrepareRequest, request: Request) -> dict[str, Any]:
    """Start (or attach to) a build. One live build per target. 423 while
    the container guard has an open tamper incident: the build writes the
    code a new satellite will run as root (docs/CONTAINER_GUARD.md)."""
    await container_freeze.require_unfrozen("satellite_media", session_scope=session_scope)
    board = BOARDS.get(body.board)
    if board is None or not board.supported:
        raise HTTPException(status_code=422, detail=f"unsupported board {body.board!r}")
    if body.mic_profile not in MIC_PROFILES:
        raise HTTPException(status_code=422, detail=f"unknown mic profile {body.mic_profile!r}")
    try:
        overlay.validate_wifi_country(body.wifi_country)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if body.setup_transport not in overlay.SETUP_TRANSPORTS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown setup transport {body.setup_transport!r}",
        )
    kind = body.target.get("kind")
    if kind not in ("drive", "zip"):
        raise HTTPException(status_code=422, detail="target.kind must be drive|zip")
    mount: Path | None = None
    target_ref = "zip"
    if kind == "drive":
        if not removable_drives_visible():
            raise HTTPException(status_code=422, detail=_NO_DRIVES_DETAIL)
        token = str(body.target.get("token") or "")
        match = next(
            (
                m
                for m in detect_removable()
                if m.get("mount") and drive_token(m["mount"]) == token
            ),
            None,
        )
        if match is None:
            raise HTTPException(status_code=410, detail="target drive not present")
        mount, target_ref = Path(match["mount"]), token

    async with session_scope() as s:
        existing = (
            await s.execute(
                text(
                    "SELECT id FROM satellite_media_jobs WHERE target_ref = :t "
                    "AND status IN ('pending','running')"
                ),
                {"t": target_ref},
            )
        ).first()
        if existing is not None:
            return {"job": _public(await _job_row(int(existing[0])) or {}), "attached": True}
        row = (
            await s.execute(
                text(
                    "INSERT INTO satellite_media_jobs "
                    "(board, mic_profile, target_kind, target_ref, offline, "
                    " status, status_text) "
                    "VALUES (:b, :m, :k, :t, :o, 'running', 'starting') "
                    "RETURNING id"
                ),
                {
                    "b": body.board, "m": body.mic_profile, "k": kind,
                    "t": target_ref, "o": body.offline,
                },
            )
        ).first()
        job_id = int(row[0])
        await s.execute(text(_NOTIFY), {"p": str(job_id)})

    # The build runs after this response, so it can't read the request
    # later: capture the operator's credentials NOW and hand them to the
    # job, which needs them for its own hop to the core (below).
    asyncio.create_task(
        _run_build(job_id, body, mount, auth_forward_headers(request)),
        name=f"media-build-{job_id}",
    )
    return {"job": _public(await _job_row(job_id) or {}), "attached": False}


async def _run_build(
    job_id: int,
    body: PrepareRequest,
    mount: Path | None,
    auth_headers: dict[str, str] | None = None,
) -> None:
    async def progress(phase: str, pct: int, text_: str) -> None:
        await _update_job(job_id, phase=phase, pct=pct, status_text=text_)

    # Ask the CORE to render the setup clips before the build copies
    # sounds_dir into the payload. This process may not import
    # domovoi.clients.tts (design §5.1) - it tried, the import was refused,
    # and every card shipped silent while the reason sat in a job warning
    # nobody reads. Over HTTP is how the web reaches every other core
    # capability, and this is no different.
    #
    # Best-effort: a card with no clips still provisions perfectly, it just
    # does it without speaking, and payload.assemble warns on the absent
    # files rather than on a failure here.
    try:
        await progress("assemble", 45, "rendering setup announcements")
        status, payload_ = await post_admin(
            "/v1/admin/sounds/setup-clips", {}, headers=auth_headers
        )
        if status != 200:
            log.warning("setup clips: core returned %s (%s)", status, payload_)
    except Exception as e:  # noqa: BLE001
        log.warning("setup clips: could not reach the core (%s)", e)

    # A zip of a Wi-Fi setup card carries a one-time pass the tray's card
    # helper trades for the setup key (POST /claim).
    claim_in_zip, claim_kept = (None, None)
    if body.target.get("kind", "zip") == "zip" and body.setup_transport == "portal":
        claim_in_zip, claim_kept = _new_claim(job_id)
    try:
        result = await builder.build(
            board_id=body.board,
            mic_profile=body.mic_profile,
            setup_transport=body.setup_transport,
            wifi_country=body.wifi_country,
            target_kind=body.target.get("kind", "zip"),
            target_mount=mount,
            job_id=str(job_id),
            offline=body.offline,
            progress=progress,
            claim=claim_in_zip,
        )
        creds = result.get("credentials") or {}
        if creds.get("ap") or creds.get("console"):
            if claim_kept is not None and creds.get("ap"):
                _JOB_CLAIMS[job_id] = claim_kept
            _remember_credentials(job_id, creds)
        async with session_scope() as s:
            await s.execute(
                text(
                    # `offline` is stored at INSERT from the REQUEST. The
                    # builder can downgrade it — an empty cache bucket makes
                    # it fall back to installing over the internet — so write
                    # the effective value back. Otherwise the jobs list shows
                    # what was asked for and calls it what was achieved,
                    # which is the one field an operator checks to confirm a
                    # card will work with no network.
                    "UPDATE satellite_media_jobs SET status = 'done', pct = 100, "
                    "status_text = 'ready', warnings = CAST(:w AS jsonb), "
                    "artifact_path = :a, offline = :off, "
                    "completed_at = now(), updated_at = now() "
                    "WHERE id = :i"
                ),
                {
                    "w": json.dumps(result.get("warnings") or []),
                    "a": result.get("artifact_path"),
                    "off": bool(result.get("offline", False)),
                    "i": job_id,
                },
            )
            await s.execute(text(_NOTIFY), {"p": str(job_id)})
    except Exception as e:  # noqa: BLE001 — job failure is a reported state
        log.warning("media build %s failed: %s", job_id, e)
        await _update_job(job_id, status="failed", error=str(e)[:500])


@router.get("/jobs")
async def media_jobs(limit: int = 10) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, board, mic_profile, target_kind, target_ref, "
                    "offline, status, phase, pct, status_text, error, warnings, "
                    "artifact_path, requested_at, completed_at "
                    "FROM satellite_media_jobs "
                    "ORDER BY requested_at DESC LIMIT :n"
                ),
                {"n": max(1, min(50, limit))},
            )
        ).mappings()
        return [_public(dict(r)) for r in rows]


@router.get(
    "/jobs/{job_id}/credentials",
    # Reading secrets, so gated — but a GET, so the dashboard cookie is
    # enough (mutations are the Bearer-only tier).
    dependencies=[Depends(require_admin_read)],
)
async def media_job_credentials(job_id: int) -> dict[str, Any]:
    """The setup-AP and console credentials for a prepared card.

    404 once the web process has restarted — they live in memory only. A
    card written straight to a drive still has them, so the message says
    where to look; a zip never carried them (WEB-1), so it says to build
    it again (or, for the setup key alone, ``POST /jobs/{id}/setup-key``
    makes a new one). ``console`` is null after that: the console
    password is in no file, only its hash."""
    creds = _JOB_CREDENTIALS.get(job_id)
    if creds is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "credentials aren't in memory any more (the dashboard was "
                "restarted). A card written from here has them in "
                "domovoi/ap.json and domovoi/console.json; a zip never "
                "did, so prepare it again."
            ),
        )
    return creds


# What a Domovoi setup network is called (overlay.generate_ap_credentials).
_SETUP_SSID = re.compile(r"^Domovoi-Setup-[0-9A-F]{4}$")

_CANT_RECOVER_DETAIL = (
    "Your Domovoi server can't recover this card's password. Prepare the "
    "card again on the dashboard."
)


class SetupKeyRequest(BaseModel):
    ssid: str = Field(
        max_length=64,
        description="the setup network's name, from the zip's domovoi/device-info.json",
    )


@router.post(
    "/jobs/{job_id}/setup-key",
    dependencies=[Depends(require_admin_mutation)],
)
async def media_job_setup_key(job_id: int, body: SetupKeyRequest) -> dict[str, Any]:
    """The setup network's key for a zip build, made again if this process
    has forgotten it (a restart): for an admin holding card files whose
    one-time pass no longer works (the tray's "Sign in as admin to fetch
    it").

    The key lives only in the card's ``domovoi/ap.json``: nothing on the
    server checks it later, so a NEW key for the same network name,
    written onto the card by whoever asked, works as well as the old one.
    It is remembered like the first (memory only), so "show setup details"
    shows the new one, and ``reissued`` says the one shown before no
    longer applies. While the build's key is still remembered it is
    answered unchanged. 404 when there is no finished zip build with that
    id: prepare the card again."""
    if not _SETUP_SSID.match(body.ssid):
        raise HTTPException(status_code=422, detail="that isn't a Domovoi setup network name")
    creds = _JOB_CREDENTIALS.get(job_id)
    if creds and creds.get("ap"):
        if creds["ap"].get("ssid") != body.ssid:
            raise HTTPException(status_code=409, detail=(
                "those card files name a different setup network from build "
                f"{job_id}. Prepare the card again on the dashboard."
            ))
        return {"ap": creds["ap"], "reissued": bool(creds.get("reissued"))}
    job = await _job_row(job_id)
    if job is None or job.get("target_kind") != "zip" or job.get("status") != "done":
        raise HTTPException(status_code=404, detail=_CANT_RECOVER_DETAIL)
    ap = {"ssid": body.ssid, "psk": overlay.generate_ap_credentials()["psk"]}
    # The console password is in the zip only as a hash: it can't be shown again.
    _remember_credentials(job_id, {"ap": ap, "console": None, "reissued": True})
    return {"ap": ap, "reissued": True}


# ─── The card helper's one-time pass (no admin session) ──────────────────

claim_router = APIRouter(prefix="/api/satellites/media", tags=["satellite-media"])

# Each refusal's status and words. The card helper words its own message
# from ``reason``; ``detail`` is for anyone else reading the answer.
_CLAIM_REFUSALS: dict[str, tuple[int, str]] = {
    "not_local": (403, "this is answered only to the Domovoi tray on the server's own PC"),
    "invalid": (403, "that isn't this card's one-time pass"),
    "used": (409, "this card's one-time pass was already used"),
    "expired": (410, "this card was prepared more than a day ago, so its pass expired"),
    "forgotten": (410, (
        "the server restarted since this card was prepared, so it no longer "
        "has the password"
    )),
}

# Headers a proxy adds to say who it relays for. The card helper talks to
# the dashboard directly and sends none of them. uvicorn rewrites the peer
# from X-Forwarded-For when a loopback peer sends one (its
# --forwarded-allow-ips default), so with any of them present the peer
# can't be taken at its word (round 4's WIN-7: loopback XFF trust on WSL).
_FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")


def _claim_refused(reason: str) -> JSONResponse:
    status, detail = _CLAIM_REFUSALS[reason]
    return JSONResponse(
        {"detail": detail, "reason": reason},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _from_this_pc(request: Request) -> bool:
    """Whether the TCP peer is this machine's loopback, with nothing in
    the request claiming to relay for someone else.

    On a Windows install the tray reaches the dashboard in WSL through
    mirrored networking's loopback (hostAddressLoopback), where a Windows
    process's 127.0.0.1 arrives as 127.0.0.1; a phone or a laptop on the
    LAN arrives as its own address. Every Windows account on the PC shares
    that loopback (the shared-PC trust boundary in docs/WINDOWS_HOST.md),
    and still needs the zip's 256-bit pass."""
    if any(request.headers.get(h) for h in _FORWARDING_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


class ClaimRequest(BaseModel):
    job_id: int
    secret: str = Field(max_length=200)


@claim_router.post("/claim")
async def media_claim(body: ClaimRequest, request: Request) -> JSONResponse:
    """Trade a zip's one-time pass (``domovoi/claim.json``) for the setup
    network's name and key, once.

    No admin session: the pass is the credential. Answered only to a
    loopback peer that sent no forwarding headers (403 ``not_local``),
    checked before anything else. Then, with ``reason`` in the body:
    410 ``forgotten`` when this process no longer has the build's
    credentials (a restart; the card helper then offers an admin sign-in
    or the typed password), 403 ``invalid`` for a secret that isn't the
    build's, 410 ``expired`` a day after the build, 409 ``used`` the second
    time. A success marks the pass used; "show setup details" in the
    dashboard keeps showing the key, which the person still types on their
    phone."""
    if not _from_this_pc(request):
        log.info("refused a card pass from a peer that isn't this PC's loopback")
        return _claim_refused("not_local")
    claim = _JOB_CLAIMS.get(body.job_id)
    creds = _JOB_CREDENTIALS.get(body.job_id)
    if claim is None or creds is None or not creds.get("ap"):
        return _claim_refused("forgotten")
    given = hashlib.sha256(body.secret.encode()).hexdigest()
    if not hmac.compare_digest(given, claim["sha256"]):
        return _claim_refused("invalid")
    if _now() >= claim["expires"]:
        return _claim_refused("expired")
    if claim["used"]:
        return _claim_refused("used")
    claim["used"] = True
    return JSONResponse(
        {"ap": {"ssid": creds["ap"]["ssid"], "psk": creds["ap"]["psk"]}},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_admin_mutation)])
async def media_cancel(job_id: int) -> dict[str, Any]:
    """Best-effort cancel: marks the row; the build task checks nothing
    mid-phase (phases are short except cold cache fetches), so this mostly
    matters for a wedged fetch — the UI reflects the cancelled state and a
    new build can start for the target."""
    job = await _job_row(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] in ("done", "failed", "cancelled"):
        return _public(job)
    await _update_job(job_id, status="cancelled", status_text="cancelled")
    return _public(await _job_row(job_id) or {})


@router.get("/jobs/{job_id}/download")
async def media_download(job_id: int) -> FileResponse:
    """The built overlay, for the operator to unzip onto a flashed card.

    Admin-gated with the rest of the router, and since WEB-1 the zip no
    longer carries the setup-AP or console passwords — those are shown
    once in the dashboard instead (see ``builder.build``)."""
    job = await _job_row(job_id)
    if job is None or not job.get("artifact_path"):
        raise HTTPException(status_code=404, detail="no artifact for this job")
    path = Path(job["artifact_path"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="artifact expired")
    return FileResponse(
        path,
        media_type="application/zip",
        filename="domovoi-satellite-overlay.zip",
    )


@router.post("/cache/refresh", dependencies=[Depends(require_admin_mutation)])
async def media_cache_refresh() -> dict[str, Any]:
    """Synchronous-ish cache refresh (wheels are the slow part; the call
    can take minutes on a cold cache — the card shows a spinner).

    409 under ``INTERNET_ACCESS=never``: every bucket downloads. Preparing
    a card (``/prepare``) stays allowed and uses what is cached. 423 while
    the container guard's freeze is on (it fills what a build copies)."""
    await container_freeze.require_unfrozen("satellite_media", session_scope=session_scope)
    if egress.internet_turned_off():
        raise egress.http_exception("satellite cache refresh")
    from domovoi.config import settings as core_settings

    repo_root = Path(core_settings.repo_dir)
    results = {
        "wheels": fetchers.fetch_wheels(
            repo_root, PI02W.python_version, PI02W.manylinux_platforms
        ),
        "debs": fetchers.fetch_debs(PI02W.os_release),
        "oww_models": fetchers.fetch_oww_models(),
        "xvf_host": fetchers.fetch_xvf_host(),
    }
    return {
        k: {"ok": ok, "message": msg} for k, (ok, msg) in results.items()
    }
