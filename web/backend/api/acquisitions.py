"""Generic media-acquisition queue readout (design §4.8).

The queue is provider-agnostic core state, so the web process reads the
``media_acquisitions`` table directly (rows survive the core being
down). Fulfiller availability — "is anything installed that can act on
these rows" — is live core registry state, fetched best-effort from the
core's ``GET /v1/acquisitions`` and degraded to ``null`` when the core
is unreachable, so the UI can distinguish "no provider installed" from
"can't tell right now".

Provider plugins render richer, provider-specific views from their own
routers; this endpoint is deliberately the lowest common denominator.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/acquisitions", tags=["acquisitions"])

_VALID_STATUS = {"pending", "claimed", "done", "failed", "unfulfillable", "cancelled"}


async def _availability() -> dict[str, Any]:
    core = os.environ.get("DOMOVOI_URL", "http://localhost:6370")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{core.rstrip('/')}/v1/acquisitions?limit=1")
        r.raise_for_status()
        data = r.json()
        return {
            "fulfillers": data.get("fulfillers") or [],
            "can_fulfill_query": data.get("can_fulfill_query"),
            "can_fulfill_url": data.get("can_fulfill_url"),
            "core_reachable": True,
        }
    except Exception as e:
        log.debug("core acquisition availability unreachable: %s", e)
        return {
            "fulfillers": [],
            "can_fulfill_query": None,
            "can_fulfill_url": None,
            "core_reachable": False,
        }


@router.get("")
async def list_acquisitions(
    status: str | None = Query(
        default=None,
        description="Filter by status "
                    "(pending|claimed|done|failed|unfulfillable|cancelled).",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    if status is not None and status not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"invalid status {status!r}")
    where = "WHERE status = :status" if status else ""
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    f"""
                    SELECT id, kind, text, metadata, requested_by, origin_ref,
                           attach_to_playlist_id, status, claimed_by, attempts,
                           error, requested_at, completed_at
                    FROM media_acquisitions {where}
                    ORDER BY requested_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).all()
    availability = await _availability()
    return {
        "acquisitions": [
            {
                "id": int(r.id),
                "kind": r.kind,
                "text": r.text,
                "metadata": dict(r.metadata or {}),
                "requested_by": r.requested_by,
                "origin_ref": r.origin_ref,
                "attach_to_playlist_id": r.attach_to_playlist_id,
                "status": r.status,
                "claimed_by": r.claimed_by,
                "attempts": int(r.attempts),
                "error": r.error,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ],
        **availability,
    }
