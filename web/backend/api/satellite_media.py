"""Satellite media preparation API — the dashboard's "prepare media" card.

Long builds run as background tasks tracked in ``satellite_media_jobs``
(V004, cloned from the model_jobs pattern): every progress write fires
``pg_notify('satellite_media_jobs_changed', ...)`` which the realtime layer
maps to the ``satellites.media`` channel, so the card's progress bar is
live. All mutations are admin-gated: preparing media writes bootstrap
scripts that run as root on a future satellite.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation
from domovoi.satellite_media import builder, cache, fetchers
from domovoi.satellite_media.boards import BOARDS, MIC_PROFILES, PI02W
from domovoi.satellite_payload import enabled_satellite_plugins, payload_files

from web.backend.api.files_security import detect_removable, drive_token
from web.backend.db import session_scope
from web.backend.satellite_adoption import _volume_label  # label pre-filter reuse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/satellites/media", tags=["satellite-media"])

_NOTIFY = "SELECT pg_notify('satellite_media_jobs_changed', :p)"


class PrepareRequest(BaseModel):
    board: str = "pi02w"
    mic_profile: str = "respeaker_2mic_hat_v2"
    target: dict = Field(default_factory=lambda: {"kind": "zip"})
    offline: bool = True


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
    """Removable drives that look like a flashed Pi boot partition."""
    from domovoi.satellite_media.overlay import looks_like_pi_boot
    import shutil as _shutil

    out: list[dict[str, Any]] = []
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
            "label": _volume_label(mount),
            "looks_like_pi_boot": looks,
            "free_bytes": free,
        })
    return out


@router.post("/prepare", dependencies=[Depends(require_admin_mutation)])
async def media_prepare(body: PrepareRequest) -> dict[str, Any]:
    """Start (or attach to) a build. One live build per target."""
    board = BOARDS.get(body.board)
    if board is None or not board.supported:
        raise HTTPException(status_code=422, detail=f"unsupported board {body.board!r}")
    if body.mic_profile not in MIC_PROFILES:
        raise HTTPException(status_code=422, detail=f"unknown mic profile {body.mic_profile!r}")
    kind = body.target.get("kind")
    if kind not in ("drive", "zip"):
        raise HTTPException(status_code=422, detail="target.kind must be drive|zip")
    mount: Path | None = None
    target_ref = "zip"
    if kind == "drive":
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

    asyncio.create_task(
        _run_build(job_id, body, mount), name=f"media-build-{job_id}"
    )
    return {"job": _public(await _job_row(job_id) or {}), "attached": False}


async def _run_build(job_id: int, body: PrepareRequest, mount: Path | None) -> None:
    async def progress(phase: str, pct: int, text_: str) -> None:
        await _update_job(job_id, phase=phase, pct=pct, status_text=text_)

    try:
        result = await builder.build(
            board_id=body.board,
            mic_profile=body.mic_profile,
            target_kind=body.target.get("kind", "zip"),
            target_mount=mount,
            job_id=str(job_id),
            offline=body.offline,
            progress=progress,
        )
        async with session_scope() as s:
            await s.execute(
                text(
                    "UPDATE satellite_media_jobs SET status = 'done', pct = 100, "
                    "status_text = 'ready', warnings = CAST(:w AS jsonb), "
                    "artifact_path = :a, completed_at = now(), updated_at = now() "
                    "WHERE id = :i"
                ),
                {
                    "w": json.dumps(result.get("warnings") or []),
                    "a": result.get("artifact_path"),
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
    can take minutes on a cold cache — the card shows a spinner)."""
    from domovoi.config import settings as core_settings

    repo_root = Path(core_settings.repo_dir)
    results = {
        "wheels": fetchers.fetch_wheels(
            repo_root, PI02W.python_version, PI02W.manylinux_platforms
        ),
        "debs": fetchers.fetch_debs(PI02W.os_release),
        "oww_models": fetchers.fetch_oww_models(),
    }
    return {
        k: {"ok": ok, "message": msg} for k, (ok, msg) in results.items()
    }
