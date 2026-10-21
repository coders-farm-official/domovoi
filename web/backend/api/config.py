"""Config / metadata endpoint.

Surfaces a subset of the Domovoi server's settings the UI needs to render
(bot name, configured TTS voice, currently provisioned rooms). Pulled
from ``domovoi.config.settings`` for the static bits and from the
``mpd_rooms`` table for the room list — that's the most authoritative
source the web backend can read without a live domovoi hop.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

# Imported at module top on purpose: the web import guard refuses a NEW
# domovoi.* import once startup is done (web/backend/plugin_host.py).
from domovoi import egress
from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_read,
    require_admin_security,
    require_device,
    require_device_read,
)
from domovoi.config import HOME_PROBLEMS_VISIBILITY_CHOICES
from domovoi.config import settings as core_settings
from web.backend import core_restart
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    get_cached_snapshot,
    post_admin,
)
from web.backend.plugin_host import HOST
from web.backend.schemas import ConfigResponse, ConfigUpdateRequest

router = APIRouter(prefix="/api", tags=["config"])

WEB_VERSION = "1.0.0rc3"

# The core's git calls that reach the update server (the check, the pull,
# a channel switch) each stop at their own timeout, and one route can run
# a few of them in a row (fetch, tags, local reads, a merge): the web waits
# longer than all of them, so a slow or refused fetch comes back as the
# core's own words ("can't reach the update server ...") and never as
# "domovoi unreachable" while the core is fine (F-051).
_VERSION_GIT_TIMEOUT_SEC = 90.0


def home_problems_visibility() -> str:
    """The live HOME_PROBLEMS_VISIBILITY.

    The setting is edited through the core (Settings → Advanced, tier
    ``hot``), which mutates the CORE process's settings; this process's copy
    is whatever ``.env`` said when it booted. So the value comes from the
    core's admin snapshot the poll loop refreshes every 1.5 s, and falls back
    to this process's own copy only while the core has not answered (down,
    or a core from before the field)."""
    live = (get_cached_snapshot() or {}).get("home_problems_visibility")
    if live in HOME_PROBLEMS_VISIBILITY_CHOICES:
        return live
    return core_settings.home_problems_visibility


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    async with session_scope() as s:
        rows = await s.execute(text("SELECT room_id FROM mpd_rooms ORDER BY room_id"))
        rooms = [r[0] for r in rows.all()]
        # The voice that actually speaks is the registry default
        # (`resolve_voice` in the core). Fall back to the configured Piper
        # voice only when the registry is empty (pre-seed).
        default_row = (
            await s.execute(
                text("SELECT model_ref FROM voices WHERE is_default LIMIT 1")
            )
        ).first()
    tts_voice = str(default_row[0]) if default_row is not None else core_settings.tts_piper_voice

    return ConfigResponse(
        bot_name=core_settings.bot_name,
        tts_voice=tts_voice,
        rooms=rooms,
        web_version=WEB_VERSION,
        wake_word_min_clips=core_settings.wake_word_min_clips,
        # Open on purpose, like the rest of this response: the Home page
        # every browser lands on decides from it what to show. One of the
        # two household settings exposed here (internet_access, below, is
        # the other); nothing else from the config is.
        home_problems_visibility=home_problems_visibility(),
        # The internet answer, read the way both processes read it
        # (environment, then domovoi/.env — egress.policy), so a save in
        # Settings → Internet shows here on the next read.
        internet_access=egress.policy(),
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
    ``{applied, restart_required, rejected, normalized, followed}``. The web
    process never mutates its own settings copy — every write fans out to
    :6370. ``follow_internet`` (Settings → Internet's "follow the answer
    again") rides along only when it names something."""
    payload: dict = {"changes": body.changes}
    if body.follow_internet:
        payload["follow_internet"] = body.follow_internet
    status, answer = await post_admin(
        "/v1/admin/config",
        payload,
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, answer)


# The longest typed key the test forwards. Checked in the handler, not as a
# pydantic max_length: that 422 quotes the value back (its "input").
ACOUSTID_TEST_KEY_MAX = 256


class AcoustidTestRequest(BaseModel):
    # A key typed but not saved yet; absent or empty tests the saved one.
    key: str | None = None


@router.post(
    "/config/acoustid-test",
    # Security tier at both hops, like the config write beside it: it
    # sends the household's AcoustID key out of the house.
    dependencies=[Depends(require_admin_security)],
)
async def acoustid_key_test(body: AcoustidTestRequest, request: Request):
    """Settings → Advanced's "test the key": one AcoustID lookup with
    the typed key, else the saved one. Proxy of the core's
    ``POST /v1/admin/config/acoustid-test`` →
    ``{result: works|refused|unreachable|unavailable, detail,
    fingerprints}``; 409 when the internet answer is No (nothing sent),
    422 with no key to test. The key is never echoed or logged."""
    if body.key and len(body.key) > ACOUSTID_TEST_KEY_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"that is longer than an AcoustID key ({ACOUSTID_TEST_KEY_MAX} characters at most)",
        )
    payload: dict = {}
    if body.key:
        payload["key"] = body.key
    return bridge_response(
        *await post_admin(
            "/v1/admin/config/acoustid-test",
            payload,
            headers=auth_forward_headers(request),
        )
    )


@router.get(
    "/config/internet",
    # A read like /config/editable beside it: the dashboard cookie renders
    # it, a Bearer is accepted, and the core applies its own gate.
    dependencies=[Depends(require_admin_read)],
)
async def get_internet(request: Request):
    """Settings → Internet: the household's internet answer, the three
    choices, connectivity, and every setting whose default follows the
    answer. Read-only proxy of the core's ``GET /v1/admin/internet`` (the
    core owns the live settings). Changing the answer is a PATCH of
    ``/api/config/editable`` with ``{changes: {internet_access}}``."""
    status, payload = await get_admin(
        "/v1/admin/internet", headers=auth_forward_headers(request)
    )
    return bridge_response(status, payload)


@router.post(
    "/config/internet/search-helper",
    # An admin mutation at both hops (it starts or stops a container).
    dependencies=[Depends(require_admin_mutation)],
)
async def start_search_helper(request: Request):
    """Settings → Internet's "start it again": the core runs the search
    helper's start/stop for the current answer in the background and
    returns its state (proxy of ``POST /v1/admin/internet/search-helper``)."""
    status, payload = await post_admin(
        "/v1/admin/internet/search-helper",
        {},
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
    ``bad_sha`` (a commit it rolled back). ``restart_in_progress`` is true
    while a restart or update is under way, however it was started; the
    dashboard offers no other meanwhile. Read-only proxy to the Domovoi
    server, which owns the git working tree; the web process can't see it.

    ``plugins_pending_restart`` is the one addition this process makes: the
    core lists its upgrades staged for restart, and this process adds every
    plugin whose web module it imported at an older version than the
    registry now holds (``where`` names which process runs the old code).
    Either one makes ``restart_required`` true."""
    status, payload = await get_admin(
        "/v1/admin/version", headers=auth_forward_headers(request)
    )
    if status == 200 and isinstance(payload, dict):
        payload = merge_web_pending_restarts(payload, HOST.stale_mounts())
    return bridge_response(status, payload)


def merge_web_pending_restarts(
    version: dict, web_stale: list[dict]
) -> dict:
    """Fold this process's stale plugin mounts into the core's version
    answer. A slug both processes hold old code for is one entry."""
    pending = [dict(p) for p in (version.get("plugins_pending_restart") or [])]
    by_slug = {p.get("slug"): p for p in pending}
    for entry in web_stale:
        existing = by_slug.get(entry["slug"])
        if existing is None:
            pending.append(dict(entry))
            by_slug[entry["slug"]] = pending[-1]
        elif "web" not in (existing.get("where") or []):
            existing["where"] = [*(existing.get("where") or []), "web"]
    merged = {**version, "plugins_pending_restart": pending}
    if pending:
        merged["restart_required"] = True
    return merged


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
            "/v1/admin/version/check", {}, headers=auth_forward_headers(request),
            timeout=_VERSION_GIT_TIMEOUT_SEC,
        )
    )


@router.post(
    "/config/version/restart",
    # Security tier at both hops: Bearer-only, 501 before setup.
    dependencies=[Depends(require_admin_security)],
)
async def restart_version(request: Request):
    """Bounce the Domovoi services so pulled code actually loads, or just
    restart them (Settings → About & updates's "Restart Domovoi"); where
    domovoi-update.service is installed, start it instead: sync, migrate,
    restart, roll back on failure, and with nothing new to apply only the
    restart and its health check.

    Admin-gated at both hops. The response comes back before the restart
    fires, so a client that then sees the connection drop should treat that
    as the restart succeeding, not as an error. Hosts without the sudoers
    grant return ``ok: false`` with the reason instead, and so does a press
    while a restart or update is already under way (``in_progress: true``):
    the client follows that one rather than reporting a failure."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/restart",
            {},
            headers=auth_forward_headers(request),
        )
    )


@router.get(
    "/config/core/restart",
    # The device tier's read half, like the Version card's own read.
    dependencies=[Depends(require_device_read)],
)
async def core_restart_status():
    """Whether the core answers, and when it doesn't, what "restart the
    core" would do from here: ``{answering, capable, how, why, starting,
    manual_command}`` (web/backend/core_restart.py). The Version card and
    Home's "the Domovoi server isn't answering" row read it to offer the
    button, or the reason and the command to run by hand."""
    return await core_restart.status()


@router.post(
    "/config/core/restart",
    # Security tier, checked HERE against the shared admin_sessions table:
    # the core that would normally check it is the one not answering.
    # Bearer-only, 501 before setup.
    dependencies=[Depends(require_admin_security)],
)
async def core_restart_now():
    """Restart a core that doesn't answer (incident 2026-10-14): the web
    process ends it and systemd starts it again, or, where this process
    can use the restart grant, runs the core's own restart. Refused while
    the core answers (use ``/config/version/restart``) or is starting.
    ``{ok, how, error, answering, starting, manual_command}``; the client
    then follows ``/api/health`` until ``domovoi_reachable`` is true."""
    return await core_restart.restart_unanswering_core()


@router.post(
    "/config/version/pull",
    # Security tier at both hops (delta review N5, 2026-10-16): this changes
    # the code on disk, so it waits for the admin password like the core's
    # /v1/admin/version/pull (501 before setup).
    dependencies=[Depends(require_admin_security)],
)
async def pull_version(request: Request):
    """`git pull --ff-only` on the Domovoi server — a deliberate, separate
    action never triggered by the check. Security tier, like the core route
    it proxies to. A dirty or diverged tree returns pulled=False; the Domovoi
    server process is not restarted."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/pull", {}, headers=auth_forward_headers(request),
            timeout=_VERSION_GIT_TIMEOUT_SEC,
        )
    )


class VersionChannelRequest(BaseModel):
    channel: str = Field(..., max_length=40)


@router.post(
    "/config/version/channel",
    # Security tier at both hops: it decides what every later pull, and so
    # the update unit's root run, follows. Bearer-only, 501 before setup.
    dependencies=[Depends(require_admin_security)],
)
async def switch_version_channel(body: VersionChannelRequest, request: Request):
    """Settings → About & updates's channel: follow ``stable`` (releases only) or
    ``main`` (every change) from now on. HEAD never moves; the check and
    the pull then compare with, and move to, that branch. Proxy of the
    core's ``POST /v1/admin/version/channel`` →
    ``{ok, changed, channel, error}``."""
    return bridge_response(
        *await post_admin(
            "/v1/admin/version/channel",
            {"channel": body.channel},
            headers=auth_forward_headers(request),
            timeout=_VERSION_GIT_TIMEOUT_SEC,
        )
    )
