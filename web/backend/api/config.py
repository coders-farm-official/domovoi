"""Config / metadata endpoint.

Surfaces a subset of the Domovoi server's settings the UI needs to render
(bot name, configured TTS voice, currently provisioned rooms). Pulled
from ``domovoi.config.settings`` for the static bits and from the
``mpd_rooms`` table for the room list — that's the most authoritative
source the web backend can read without a live domovoi hop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation, require_admin_read
from domovoi.config import settings as core_settings
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    post_admin,
)
from web.backend.schemas import ConfigResponse, ConfigUpdateRequest

router = APIRouter(prefix="/api", tags=["config"])

WEB_VERSION = "0.1.0-dev"


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    async with session_scope() as s:
        rows = await s.execute(text("SELECT room_id FROM mpd_rooms ORDER BY room_id"))
        rooms = [r[0] for r in rows.all()]

    return ConfigResponse(
        bot_name=core_settings.bot_name,
        tts_voice=core_settings.tts_edge_voice,
        rooms=rooms,
        web_version=WEB_VERSION,
        wake_word_min_clips=core_settings.wake_word_min_clips,
    )


@router.get(
    "/config/editable",
    # §7.3: config READS are gated on both processes (plugin config
    # carries secrets). GETs may render via the dashboard cookie.
    dependencies=[Depends(require_admin_read)],
)
async def get_editable_config(request: Request):
    """Editable domovoi settings — the FieldSpec registry joined with
    current values — for the settings gear. Passes through to the
    Domovoi server so values are LIVE; the web process holds its own separate
    ``settings`` copy that goes stale the moment a save mutates the
    Domovoi server's singleton, so we must not read it here. Credentials are
    forwarded — the core applies its own §7.3 gate."""
    status, payload = await get_admin(
        "/v1/admin/config", headers=auth_forward_headers(request)
    )
    return bridge_response(status, payload)


@router.patch(
    "/config/editable",
    # §7.3: config writes are admin-tier mutations — Bearer-only.
    dependencies=[Depends(require_admin_mutation)],
)
async def patch_editable_config(body: ConfigUpdateRequest, request: Request):
    """Apply config changes through the Domovoi server (validate → persist to
    .env → live-apply where the tier allows). Returns
    ``{applied, restart_required, rejected}``. The web process never mutates
    its own settings copy — every write fans out to :6370."""
    status, payload = await post_admin(
        "/v1/admin/config",
        {"changes": body.changes},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.get("/config/version")
async def get_version():
    """What the Domovoi server is RUNNING, and what's checked out on disk.

    Returns ``sha``/``running_sha`` (captured at the core's boot, so it names
    the code actually loaded), ``checkout_sha`` (read live from the working
    tree), ``restart_required``, ``started_at`` and ``uptime_sec``. The two
    SHAs diverge after a ``git pull`` without a restart — the case this panel
    most needs to get right. Read-only proxy to the Domovoi server, which
    owns the git working tree; the web process can't see it."""
    return bridge_response(*await get_admin("/v1/admin/version"))


@router.post("/config/version/check")
async def check_version():
    """Fetch upstream and report how far the Domovoi server's HEAD is
    behind/ahead. Best-effort: offline / no tracking branch comes back with
    upstream=False rather than an error status. Read-only — never pulls."""
    return bridge_response(*await post_admin("/v1/admin/version/check", {}))


@router.post("/config/version/pull")
async def pull_version():
    """`git pull --ff-only` on the Domovoi server — a deliberate, separate
    action never triggered by the check. A dirty or diverged tree returns
    pulled=False; the Domovoi server process is not restarted."""
    return bridge_response(*await post_admin("/v1/admin/version/pull", {}))
