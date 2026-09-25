"""Config / metadata endpoint.

Surfaces a subset of the Domovoi server's settings the UI needs to render
(bot name, configured TTS voice, currently provisioned rooms). Pulled
from ``domovoi.config.settings`` for the static bits and from the
``mpd_rooms`` table for the room list — that's the most authoritative
source the web backend can read without a live domovoi hop.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_read,
    require_admin_security,
    require_device,
)
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
        # The voice that actually speaks is the registry default
        # (`resolve_voice` in the core). `tts_edge_voice` only names Edge's
        # voice and is set — to Aria — on every box, including ones that have
        # never used Edge; reporting it here made a Piper-only box look like
        # it was using a cloud voice. Fall back to the configured engine's
        # own voice only when the registry is empty (pre-seed).
        default_row = (
            await s.execute(
                text("SELECT model_ref FROM voices WHERE is_default LIMIT 1")
            )
        ).first()
    if default_row is not None:
        tts_voice = str(default_row[0])
    elif core_settings.tts_engine == "piper":
        tts_voice = core_settings.tts_piper_voice
    else:
        tts_voice = core_settings.tts_edge_voice

    return ConfigResponse(
        bot_name=core_settings.bot_name,
        tts_voice=tts_voice,
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
async def get_editable_config(request: Request, section: str | None = None):
    """Editable domovoi settings — the FieldSpec registry joined with
    current values — for the settings gear. Passes through to the
    Domovoi server so values are LIVE; the web process holds its own separate
    ``settings`` copy that goes stale the moment a save mutates the
    Domovoi server's singleton, so we must not read it here. Credentials are
    forwarded — the core applies its own §7.3 gate, and decides from them
    what the answer contains: secret values (the database URL, API keys)
    read back masked, and the ``advanced`` section comes back only for a
    caller holding an admin Bearer (``advanced_available`` says which).
    ``section=advanced`` asks for that block alone and is a 401 for a
    cookie-only caller."""
    path = "/v1/admin/config"
    if section:
        path += f"?section={quote(section, safe='')}"
    status, payload = await get_admin(path, headers=auth_forward_headers(request))
    return bridge_response(status, payload)


@router.patch(
    "/config/editable",
    # §7.3: config writes are security-tier mutations — Bearer-only and
    # 501 before setup, at both hops.
    dependencies=[Depends(require_admin_security)],
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
async def get_version(request: Request):
    """What the Domovoi server is RUNNING, and what's checked out on disk.

    Returns ``sha``/``running_sha`` (captured at the core's boot, so it names
    the code actually loaded), ``checkout_sha`` (read live from the working
    tree), ``restart_required``, ``started_at`` and ``uptime_sec``. The two
    SHAs diverge after a ``git pull`` without a restart — the case this panel
    most needs to get right. On a Linux host with the update unit it also
    carries ``restart_mode``, ``last_update`` (that unit's last run) and
    ``bad_sha`` (a commit it rolled back). Read-only proxy to the Domovoi
    server, which owns the git working tree; the web process can't see it."""
    return bridge_response(
        *await get_admin("/v1/admin/version", headers=auth_forward_headers(request))
    )


@router.get("/config/server-identity")
async def get_server_identity(request: Request):
    """This server's fingerprint — the string a satellite is prepared with.

    Shown under Settings → About so a person can read it off the screen
    and compare it with the one printed on a prepared card, which is the
    whole point of a fingerprint: it turns "is this the right server?"
    into something a human can answer. Read-only proxy of the core's
    ``/v1/health`` identity block; the public key never implies anything
    the core did not already publish there."""
    status, payload = await get_admin(
        "/v1/health", headers=auth_forward_headers(request)
    )
    if status != 200 or not isinstance(payload, dict):
        return bridge_response(status, payload)
    return bridge_response(200, payload.get("identity") or {})


@router.post(
    "/config/version/check",
    # Device tier at both hops: the core's /v1/admin/version/check is on
    # the household tier — it fetches and reports, it never moves HEAD.
    dependencies=[Depends(require_device)],
)
async def check_version(request: Request):
    """Fetch upstream and report how far the Domovoi server's HEAD is
    behind/ahead. Best-effort: offline / no tracking branch comes back with
    upstream=False rather than an error status. Read-only — never pulls."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/check", {}, headers=auth_forward_headers(request)
        )
    )


@router.post(
    "/config/version/restart",
    # Security tier at both hops: Bearer-only, 501 before setup.
    dependencies=[Depends(require_admin_security)],
)
async def restart_version(request: Request):
    """Bounce the Domovoi services so pulled code actually loads (or, where
    domovoi-update.service is installed, start it: sync, migrate, restart,
    roll back on failure).

    Admin-gated at both hops. The response comes back before the restart
    fires, so a client that then sees the connection drop should treat that
    as the restart succeeding, not as an error. Hosts without the sudoers
    grant return ``ok: false`` with the reason instead."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/restart",
            {},
            headers=auth_forward_headers(request),
        )
    )


@router.post(
    "/config/version/pull",
    # Admin tier at both hops: this changes the code on disk, and the
    # core's /v1/admin/version/pull is admin-gated.
    dependencies=[Depends(require_admin_mutation)],
)
async def pull_version(request: Request):
    """`git pull --ff-only` on the Domovoi server — a deliberate, separate
    action never triggered by the check. Admin tier, like the core route it
    proxies to. A dirty or diverged tree returns pulled=False; the Domovoi
    server process is not restarted."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/pull", {}, headers=auth_forward_headers(request)
        )
    )
