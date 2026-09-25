"""Models page API — the LLM/STT model-management surface.

The Models page is a one-stop hub: which model is active in each role
(Q&A / tool-routing / speech-to-text), what's installed on disk, a curated
catalog to browse + install from, live host hardware, and per-model fit
badges. This module backs it.

Where each thing comes from (respecting the two-process split):

  * **Installed / loaded Ollama models** — read straight from the local
    Ollama server (``localhost:11434`` on the same host) via the httpx
    helpers in ``domovoi.clients.ollama``. No domovoi hop needed;
    Ollama is a local service both processes can reach. Local-first: these
    views render whenever Ollama is up (no internet required).
  * **Curated catalog** — a static JSON shipped next to this file. Works
    fully offline; only *pulling* a listed model needs network.
  * **Active model slots** — proxied from the Domovoi server's editable-config
    registry (``/v1/admin/config``) so the values are LIVE (the web process
    holds a separate, stale ``settings`` copy). Switching a slot PATCHes the
    same config path — ``ollama_model``/``ollama_tool_model`` are reapply-tier
    (instant), ``whisper_model`` is restart-tier (badge shown).
  * **Hardware** — proxied from the Domovoi server's ``/v1/admin/hardware``
    (it owns the CUDA context), the denominator for every fit badge.
  * **Pulls** — a long streamed download modeled as a durable background job
    (``model_jobs``) with live progress over the LISTEN/NOTIFY → web
    WebSocket bus, exactly like ``download_jobs``.

The pull task runs in THIS (web) process: Ollama is local, and keeping the
transfer here means the Domovoi server's single-process voice pipeline is never
tied up by a multi-GB download.

Pulling, cancelling and deleting are admin-tier (``Authorization: Bearer``):
a pull writes multi-GB files to the host from a registry named in the
request, and a delete removes a model the household depends on. A model
reference may carry its own registry host (``host/ns/model:tag``); that
host goes through ``domovoi.net_safety`` so a pull can't be pointed at a
service inside the house.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import net_safety
from domovoi.admin_auth import require_admin_mutation
from domovoi.clients import ollama as ollama_client
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    post_admin,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

# Role slot → the Settings field that stores it. The page renders one row
# per role; switching writes the mapped field through the config path.
ROLE_TO_FIELD = {
    "qa": "ollama_model",
    "tool": "ollama_tool_model",
    "vision": "ollama_vision_model",
    "stt": "whisper_model",
}

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "model_catalog.json"

# In-flight pull tasks, keyed by model_jobs.id. Process-local — a job whose
# task isn't here (after a web restart mid-pull) is reconciled by the reaper
# note below: on GET /jobs we leave stale 'running' rows as-is; Ollama itself
# resumes/dedups a re-issued pull, so re-clicking install is safe.
_pull_tasks: dict[int, asyncio.Task] = {}
_cancelled: set[int] = set()


# ─── Catalog ────────────────────────────────────────────────────────────────


def _load_catalog() -> dict[str, Any]:
    try:
        return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover — shipped file should always parse
        log.warning("model catalog load failed: %s", e)
        return {"version": 0, "ollama": [], "whisper": []}


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    """The curated model catalog (static JSON). Works offline — powers the
    browse-and-install cards, the STT list, and every fit badge's VRAM
    estimate."""
    return _load_catalog()


# ─── Installed / loaded ──────────────────────────────────────────────────────


@router.get("/installed")
async def get_installed() -> dict[str, Any]:
    """Installed-on-disk Ollama models (``/api/tags``) joined with which are
    loaded in VRAM right now (``/api/ps``). ``ollama_reachable`` is false when
    the local Ollama server is down — the page then shows an offline notice
    instead of an empty 'no models' state (they're different situations)."""
    tags = await ollama_client.list_models()
    loaded = await ollama_client.ps()
    loaded_names = {m.get("name") or m.get("model") for m in loaded}
    # Ollama unreachable ⇒ both come back empty; distinguish by re-checking ps
    # only isn't reliable, so treat a successful (even empty) tags call as
    # reachable unless BOTH calls yielded nothing AND ps confirmed nothing.
    reachable = bool(tags) or bool(loaded)
    installed = []
    for m in tags:
        name = m.get("name") or m.get("model")
        details = m.get("details") or {}
        installed.append(
            {
                "name": name,
                "size_bytes": m.get("size"),
                "modified_at": m.get("modified_at"),
                "quant": details.get("quantization_level"),
                "family": details.get("family"),
                "param_size": details.get("parameter_size"),
                "loaded": name in loaded_names,
            }
        )
    installed.sort(key=lambda x: (x["name"] or "").lower())
    return {"ollama_reachable": reachable, "installed": installed}


# ─── Active role slots ───────────────────────────────────────────────────────


@router.get("/active")
async def get_active(request: Request) -> Any:
    """The three role slots (Q&A / tool / STT) with their LIVE current model
    and change-tier, derived from the Domovoi server's editable-config registry.
    Proxied so the values reflect the Domovoi server's singleton, not the web
    process's stale copy."""
    # Forward the caller's credentials: /v1/admin/config is admin-gated on
    # the core, and both processes validate against the same admin_sessions
    # table (design §7.3). Without this the hop is anonymous and the core
    # answers 401 no matter how many times the user logs in.
    status, payload = await get_admin(
        "/v1/admin/config", headers=auth_forward_headers(request)
    )
    if status != 200 or not isinstance(payload, dict):
        return bridge_response(status, payload)
    by_name = {f["name"]: f for f in payload.get("fields", [])}
    roles = []
    for role, field_name in ROLE_TO_FIELD.items():
        spec = by_name.get(field_name) or {}
        row = {
            "role": role,
            "field": field_name,
            "model": spec.get("value"),
            "tier": spec.get("tier"),
        }
        if role == "stt":
            # Whisper is a model AND a device/compute pair; the STT catalog
            # marks the row that matches all of it as active.
            row["device"] = (by_name.get("whisper_device") or {}).get("value")
            row["compute_type"] = (by_name.get("whisper_compute_type") or {}).get("value")
        roles.append(row)
    return {"roles": roles}


class SetActiveBody(BaseModel):
    role: str
    model: str = Field(..., min_length=1, max_length=200)
    # STT only: the Whisper compute type the chosen catalog row runs at
    # ("auto", "int8", ...). Written alongside whisper_model so a row that
    # says int8 actually runs int8; the core validates it against the
    # device. Omitted = leave the compute type as it is.
    compute_type: str | None = Field(default=None, min_length=1, max_length=40)


@router.post("/active")
async def set_active(body: SetActiveBody, request: Request):
    """Switch the model in a role slot. Writes the mapped config field through
    the Domovoi server (validate → persist → live-apply where the tier allows).
    ``ollama_model``/``ollama_tool_model`` apply instantly (reapply);
    ``whisper_model`` needs a restart (surfaced in ``restart_required``).

    Guard rail: refuse to activate an Ollama model that isn't installed —
    the caller should pull it first. Whisper is exempt (faster-whisper cold-
    downloads from HuggingFace on first use; the page offers an explicit
    pre-fetch, but activation without it is still valid).

    For ``stt``, ``compute_type`` (optional) is written with the model as
    ``whisper_compute_type`` in the same config write, so the core checks
    it against the device and refuses the pair together (``rejected``)."""
    field = ROLE_TO_FIELD.get(body.role)
    if field is None:
        raise HTTPException(status_code=400, detail=f"unknown role {body.role!r}")
    if body.compute_type is not None and body.role != "stt":
        raise HTTPException(
            status_code=400, detail="compute_type only applies to the stt role"
        )

    if field in ("ollama_model", "ollama_tool_model", "ollama_vision_model"):
        tags = await ollama_client.list_models()
        installed_names = {m.get("name") or m.get("model") for m in tags}
        # Only enforce when Ollama is reachable (non-empty) — if it's down we
        # can't verify, so don't block a legitimate switch.
        if installed_names and body.model not in installed_names:
            raise HTTPException(
                status_code=409,
                detail=f"{body.model} is not installed — pull it first, then switch.",
            )

    changes: dict[str, str] = {field: body.model}
    if body.compute_type is not None:
        changes["whisper_compute_type"] = body.compute_type
    status, payload = await post_admin(
        "/v1/admin/config",
        {"changes": changes},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


# ─── Hardware ────────────────────────────────────────────────────────────────


@router.get("/hardware")
async def get_hardware(request: Request):
    """Live host hardware (GPUs + CPU/RAM/disk), proxied from the Domovoi server
    which owns the CUDA context. The denominator for fit badges."""
    return bridge_response(
        *await get_admin("/v1/admin/hardware", headers=auth_forward_headers(request))
    )


# ─── Delete ──────────────────────────────────────────────────────────────────


@router.delete("/{name:path}", dependencies=[Depends(require_admin_mutation)])
async def delete_installed(name: str):
    """Delete an installed Ollama model from disk (``/api/delete``).
    Admin tier: it removes a model the household is using."""
    try:
        await ollama_client.delete_model(name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"delete failed: {e}")
    return {"deleted": name}


# ─── Pull jobs ───────────────────────────────────────────────────────────────


@router.get("/jobs")
async def get_jobs() -> dict[str, Any]:
    """Active + recently-finished pull jobs (``model_jobs``). The page shows a
    progress bar for pending/running and reconciles a just-completed one."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, model, status, pct, status_text, error,
                       requested_at, updated_at, completed_at
                FROM model_jobs
                WHERE status IN ('pending', 'running')
                   OR completed_at > now() - interval '30 seconds'
                ORDER BY requested_at DESC
                """
            )
        )
        jobs = [_job_row(r) for r in rows.all()]
    return {"jobs": jobs}


class PullBody(BaseModel):
    model: str = Field(..., min_length=1, max_length=200)


@router.post("/pull", dependencies=[Depends(require_admin_mutation)])
async def start_pull(body: PullBody) -> dict[str, Any]:
    """Start (or attach to) a background pull for ``model``. Idempotent per
    model: if a pull is already in flight the existing job is returned rather
    than starting a duplicate transfer (enforced by the partial unique
    index).

    Admin tier, and a model reference that names its own registry host is
    checked against the outbound-URL rules first."""
    model = body.model.strip()
    await _check_registry_host(model)
    async with session_scope() as s:
        # Attach to an existing in-flight job for the same model.
        existing = (
            await s.execute(
                text(
                    """
                    SELECT id, model, status, pct, status_text, error,
                           requested_at, updated_at, completed_at
                    FROM model_jobs
                    WHERE model = :m AND status IN ('pending', 'running')
                    """
                ),
                {"m": model},
            )
        ).first()
        if existing is not None:
            return {"job": _job_row(existing), "attached": True}

        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO model_jobs (model, status)
                    VALUES (:m, 'pending')
                    RETURNING id, model, status, pct, status_text, error,
                              requested_at, updated_at, completed_at
                    """
                ),
                {"m": model},
            )
        ).first()
        await s.execute(text("SELECT pg_notify('model_jobs_changed', 'created')"))
        await s.commit()
        job = _job_row(row)

    job_id = job["id"]
    _pull_tasks[job_id] = asyncio.create_task(
        _run_pull(job_id, model), name=f"model-pull-{job_id}"
    )
    return {"job": job, "attached": False}


@router.post(
    "/pull/{job_id}/cancel", dependencies=[Depends(require_admin_mutation)]
)
async def cancel_pull(job_id: int) -> dict[str, Any]:
    """Request cancellation of an in-flight pull. Cooperative: the streaming
    task checks the cancel flag between progress lines, aborts the transfer,
    and marks the job ``cancelled``. Admin tier, like the pull it stops."""
    _cancelled.add(job_id)
    task = _pull_tasks.get(job_id)
    if task is not None:
        task.cancel()
    # Best-effort immediate DB flip so the UI reacts even if the task already
    # exited; the task's own finally is idempotent.
    async with session_scope() as s:
        await s.execute(
            text(
                """
                UPDATE model_jobs
                   SET status = 'cancelled', updated_at = now(),
                       completed_at = now()
                 WHERE id = :id AND status IN ('pending', 'running')
                """
            ),
            {"id": job_id},
        )
        await s.execute(text("SELECT pg_notify('model_jobs_changed', 'cancelled')"))
        await s.commit()
    return {"cancelled": job_id}


# ─── Pull task ───────────────────────────────────────────────────────────────


def registry_host(model: str) -> str | None:
    """The registry host a model reference names, or None when it uses the
    default registry. ``example.com/ns/model:tag`` and ``host:5000/m`` name
    one; ``llama3.2:3b`` and ``library/llama3.2`` do not — the first
    segment is only a host if it looks like one (a dot or a port)."""
    head = (model or "").strip().split("/")[0]
    if "/" not in (model or "") or not head:
        return None
    if "." not in head and ":" not in head and head != "localhost":
        return None
    return head


async def _check_registry_host(model: str) -> None:
    """400 when the model reference points the pull at a host the server
    must not fetch from."""
    host = registry_host(model)
    if host is None:
        return
    reason = await net_safety.acheck_outbound_url(
        f"https://{host}/", require_resolution=False
    )
    if reason is not None:
        raise HTTPException(
            status_code=400,
            detail=f"refusing to pull from this registry — {reason}",
        )


def _normalize_model_name(name: str) -> str:
    """Canonical form for comparing a requested pull name with ``/api/tags``:
    Ollama lists ``llama3.2`` as ``llama3.2:latest`` and strips the default
    registry/namespace, so do the same to both sides before comparing."""
    n = name.strip().lower()
    for prefix in ("registry.ollama.ai/library/", "registry.ollama.ai/", "library/"):
        if n.startswith(prefix):
            n = n[len(prefix):]
    if ":" not in n.rsplit("/", 1)[-1]:
        n = f"{n}:latest"
    return n


async def _confirm_installed(model: str) -> bool | None:
    """After a pull stream ends: is ``model`` actually on disk? ``True`` /
    ``False`` from Ollama's installed list; ``None`` when the list is empty
    (Ollama unreachable — can't tell either way, same posture as
    :func:`set_active`)."""
    tags = await ollama_client.list_models()
    raw = [m.get("name") or m.get("model") for m in tags if isinstance(m, dict)]
    names = {_normalize_model_name(str(n)) for n in raw if n}
    if not names:
        return None
    return _normalize_model_name(model) in names


async def _run_pull(job_id: int, model: str) -> None:
    """Stream the Ollama pull and persist throttled progress to ``model_jobs``,
    pg_notify-ing each write so the browser's progress bar tracks live. Marks
    the job done / failed / cancelled at the end. Exceptions never escape —
    they land on the row as ``error``.

    A failed pull is NOT an HTTP failure: Ollama answers ``POST /api/pull``
    with 200 and reports the problem as an error LINE on the stream —
    ``{"error": "pull model manifest: file does not exist"}`` for a model
    that isn't in the registry. That line has to be read out here; before
    it was, the loop ran off the end of a failed stream and the job landed
    ``done / 100%`` for a model that was never installed (F-011). Belt and
    braces, the model must also show up in Ollama's installed list once the
    stream ends."""
    last_pct: int | None = None
    last_text: str | None = None
    try:
        await _set_running(job_id)
        async for chunk in ollama_client.pull_model(model):
            if job_id in _cancelled:
                raise asyncio.CancelledError()
            err = chunk.get("error")
            if err:
                raise RuntimeError(str(err))
            status_text = str(chunk.get("status") or "")[:200]
            pct = ollama_client.pct_from_progress(chunk)
            # Throttle: only write when the visible state actually moved.
            if pct != last_pct or status_text != last_text:
                last_pct, last_text = pct, status_text
                await _update_progress(job_id, pct, status_text)
        if await _confirm_installed(model) is False:
            raise RuntimeError(
                f"{model} is not in Ollama's installed list after the pull ended"
            )
        await _finish(job_id, "done", pct=100)
    except asyncio.CancelledError:
        await _finish(job_id, "cancelled")
    except Exception as e:
        log.warning("model pull %s (%s) failed: %s", job_id, model, e)
        await _finish(job_id, "failed", error=str(e)[:500])
    finally:
        _pull_tasks.pop(job_id, None)
        _cancelled.discard(job_id)


async def _set_running(job_id: int) -> None:
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE model_jobs SET status='running', updated_at=now() "
                "WHERE id=:id AND status='pending'"
            ),
            {"id": job_id},
        )
        await s.execute(text("SELECT pg_notify('model_jobs_changed', 'running')"))
        await s.commit()


async def _update_progress(job_id: int, pct: int | None, status_text: str) -> None:
    async with session_scope() as s:
        await s.execute(
            text(
                """
                UPDATE model_jobs
                   SET pct = :pct, status_text = :st, updated_at = now(),
                       status = 'running'
                 WHERE id = :id AND status IN ('pending', 'running')
                """
            ),
            {"id": job_id, "pct": pct, "st": status_text},
        )
        await s.execute(text("SELECT pg_notify('model_jobs_changed', 'progress')"))
        await s.commit()


async def _finish(
    job_id: int, status: str, pct: int | None = None, error: str | None = None
) -> None:
    async with session_scope() as s:
        # Don't clobber a row a concurrent cancel already finalized.
        await s.execute(
            text(
                """
                UPDATE model_jobs
                   SET status = :status,
                       pct = COALESCE(:pct, pct),
                       error = :error,
                       updated_at = now(),
                       completed_at = now()
                 WHERE id = :id AND status IN ('pending', 'running')
                """
            ),
            {"id": job_id, "status": status, "pct": pct, "error": error},
        )
        await s.execute(text("SELECT pg_notify('model_jobs_changed', :r)"), {"r": status})
        await s.commit()


def _job_row(r: Any) -> dict[str, Any]:
    return {
        "id": int(r[0]),
        "model": r[1],
        "status": r[2],
        "pct": int(r[3]) if r[3] is not None else None,
        "status_text": r[4],
        "error": r[5],
        "requested_at": _iso(r[6]),
        "updated_at": _iso(r[7]),
        "completed_at": _iso(r[8]),
    }


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v
