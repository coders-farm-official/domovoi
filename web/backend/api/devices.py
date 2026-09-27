"""Named client devices — ``/api/devices``.

Every client already mints a stable per-install id for resume positions
(``browser-xxxx`` in localStorage, ``android-xxxx`` in DataStore). What it
never had was a NAME, which two features now need: the room queue's "added
by <device>" tag, and the admin blocklist that can take queue editing away
from a named device.

So a client introduces itself once per session: ``POST /register`` upserts
its row, seeding ``name`` from whatever the client can tell about itself
("Chrome on Windows", "Pixel 8") and refreshing ``last_seen_at``. The name
is editable afterwards — by the device itself, or by an admin from the
roster.

**Trust posture.** Registration and rename are DEVICE tier (REV-1): a valid
``X-Device-Token`` or an admin Bearer, with the pre-setup LAN grace kept so a
fresh install can introduce itself before an admin password exists. WITHIN
the household a device id is still SELF-ASSERTED: nothing stops one paired
client claiming another's id. That is why :mod:`web.backend.api.music_queue`
describes its blocklist as household policy rather than a security boundary,
and why renaming can't be used to escape a block (blocks match the id as well
as the name). What the credential changed is who gets as far as asserting an
id at all. Listing the roster is admin-gated, because it's an inventory of
who's on the network.

**Shared screens.** An admin can mark a device as a shared screen (the
kitchen tablet): ``PATCH /{device_id}/shared-screen`` at ADMIN tier, NOT the
device tier the rename takes, so a device cannot un-share itself. Every
device answer carries ``shared_screen``, the device's own registration
included, which is how the dashboard's Home page knows to leave personal
content off. Presentational, not a boundary: the tablet still holds the
household token (V014 says the same).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation, require_admin_read, require_device
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

DEVICE = [Depends(require_device)]

# Device ids are minted by clients, so the server decides what shape it will
# store: a conservative slug charset keeps them safe to echo into toasts and
# log lines, and bounds the column.
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")

# A name is displayed in the queue and matched by the blocklist. Collapse
# whitespace so " Kitchen  iPad " and "Kitchen iPad" can't become two
# different block targets.
_MAX_NAME = 60


class DeviceRegistration(BaseModel):
    device_id: str = Field(..., min_length=3, max_length=64)
    # Optional on purpose: a returning device keeps the name it (or an
    # admin) already set, so a client that sends its platform default on
    # every boot can't stomp a rename. Only the first registration seeds it.
    name: str | None = Field(default=None, max_length=_MAX_NAME)
    platform: str | None = Field(default=None, max_length=32)
    user_agent: str | None = Field(default=None, max_length=400)


class DeviceRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=_MAX_NAME)


class DeviceSharedScreen(BaseModel):
    shared_screen: bool


class Device(BaseModel):
    device_id: str
    name: str
    platform: str | None = None
    user_agent: str | None = None
    first_seen_at: Any = None
    last_seen_at: Any = None
    # An admin marked this device a shared screen (V014): the dashboard's
    # Home page shows a shared-screen view on it. False on a database that
    # has not had V014 yet.
    shared_screen: bool = False


def _clean_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    collapsed = " ".join(raw.split())
    return collapsed[:_MAX_NAME] or None


# ─── The id the server hands back to the browser ─────────────────────────────
#
# A cookie, set by the server on every registration, carrying nothing but the
# id the client just told us. It is NOT a credential: it confers no access, it
# is not checked for authenticity, and losing it only means the caller goes
# back to being anonymous. Its whole job is to make a browser CARRY ITS NAME on
# requests whose body does not mention it.
#
# That is what the per-device files block needed. Every ``/api/documents`` save
# takes ``device_id`` in the body as an optional field, no client sends it, and
# so a tablet an admin had blocked in Settings kept saving through that door by
# doing nothing at all. Requiring the field would 422 every Save in the
# dashboard and in the app. A cookie the SERVER sets asks the client for
# nothing: the dashboard already calls ``POST /api/devices/register`` on every
# load (web/static/index.html, in bootstrap()), and from then on the browser
# repeats the id on every request it makes, including the five documents saves.
#
# ``httponly`` because no page script has any reason to read it — the id is
# already in localStorage, where the client minted it. ``samesite=lax`` rather
# than the admin cookie's ``strict``: this value can only ever NARROW what a
# caller may do, so the failure to avoid is the cookie not arriving, and Lax
# arrives in every case Strict does plus the first navigation. No ``secure``,
# for the same reason the session cookie has none — v1 runs over plain LAN
# HTTP, and a Secure cookie would simply never be sent.
DEVICE_ID_COOKIE = "domovoi-device-id"

# Chrome caps cookie lifetime at 400 days; asking for more just gets trimmed.
DEVICE_ID_COOKIE_MAX_AGE_SEC = 400 * 86400


def valid_device_id(raw: str | None) -> str | None:
    """``raw`` if it is a well-formed device id, else ``None``.

    The non-raising twin of :func:`_validate_id`, for the callers that are
    READING an id out of a request (a cookie, a header, a query string)
    rather than being told one. A malformed value there means "this caller
    did not identify itself", never "400" — an old cookie must not be able
    to turn a legitimate save into an error.
    """
    if not raw:
        return None
    ident = raw.strip()
    return ident if _DEVICE_ID_RE.match(ident) else None


def _remember_device(response: Response, ident: str) -> None:
    """Hand the id back as :data:`DEVICE_ID_COOKIE` so the browser repeats
    it on requests that have nowhere to put it."""
    response.set_cookie(
        DEVICE_ID_COOKIE,
        ident,
        max_age=DEVICE_ID_COOKIE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _validate_id(device_id: str) -> str:
    ident = device_id.strip()
    if not _DEVICE_ID_RE.match(ident):
        raise HTTPException(
            status_code=400,
            detail=(
                "device_id must be 3–64 chars of letters, digits, dot, "
                "underscore, colon or hyphen"
            ),
        )
    return ident


def _row_to_device(r: Any) -> Device:
    return Device(
        device_id=r[0], name=r[1], platform=r[2], user_agent=r[3],
        first_seen_at=r[4], last_seen_at=r[5], shared_screen=bool(r[6]),
    )


_BASE_COLUMNS = "device_id, name, platform, user_agent, first_seen_at, last_seen_at"

# Whether `devices` has V014's `shared_screen` column. Registration is on the
# path EVERY dashboard load takes, so a web process started against a
# database Flyway has not caught up with must keep registering devices, not
# 500 — the same tolerance repositories.py extends to V011. Only a positive
# answer is cached: once the migration lands, the next request sees the
# column without a restart.
_HAS_SHARED_SCREEN = False


async def _has_shared_screen(s: Any) -> bool:
    global _HAS_SHARED_SCREEN
    if not _HAS_SHARED_SCREEN:
        row = (
            await s.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'devices'
                      AND column_name = 'shared_screen'
                    """
                )
            )
        ).first()
        _HAS_SHARED_SCREEN = row is not None
    return _HAS_SHARED_SCREEN


async def _columns(s: Any) -> str:
    """The select / RETURNING list for a :class:`Device`, ``shared_screen``
    last — read as FALSE until V014 is applied."""
    flag = "shared_screen" if await _has_shared_screen(s) else "FALSE AS shared_screen"
    return f"{_BASE_COLUMNS}, {flag}"


@router.post("/register", response_model=Device, dependencies=DEVICE)
async def register_device(payload: DeviceRegistration, response: Response) -> Device:
    """Upsert this client's row and bump ``last_seen_at``.

    Idempotent and cheap — clients call it on every boot. ``name`` is only
    applied when the row is NEW (``COALESCE`` on the existing value), so a
    client that always sends its platform default never overwrites a name a
    person chose.
    """
    ident = _validate_id(payload.device_id)
    name = _clean_name(payload.name)
    async with session_scope() as s:
        columns = await _columns(s)
        row = await s.execute(
            text(
                f"""
                INSERT INTO devices (device_id, name, platform, user_agent)
                VALUES (:id, :name, :platform, :ua)
                ON CONFLICT (device_id) DO UPDATE
                SET last_seen_at = now(),
                    platform   = COALESCE(EXCLUDED.platform, devices.platform),
                    user_agent = COALESCE(EXCLUDED.user_agent, devices.user_agent)
                RETURNING {columns}
                """
            ),
            {
                "id": ident,
                # Last-resort label so the column's NOT NULL holds even when a
                # client sends nothing it can introspect.
                "name": name or ident,
                "platform": payload.platform,
                "ua": payload.user_agent,
            },
        )
        result = row.first()
    if result is None:  # pragma: no cover — RETURNING always yields on upsert
        raise HTTPException(status_code=500, detail="upsert returned no row")
    # From here the browser carries its own name on every request, including
    # the ones whose body has no field for it. See DEVICE_ID_COOKIE.
    _remember_device(response, ident)
    return _row_to_device(result)


@router.patch("/{device_id}", response_model=Device, dependencies=DEVICE)
async def rename_device(device_id: str, payload: DeviceRename) -> Device:
    """Rename a device. Device tier, like registration — and harmless for
    the blocklist, which matches ids too."""
    ident = _validate_id(device_id)
    name = _clean_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="name can't be blank")
    async with session_scope() as s:
        columns = await _columns(s)
        row = await s.execute(
            text(
                f"""
                UPDATE devices
                SET name = :name, last_seen_at = now()
                WHERE device_id = :id
                RETURNING {columns}
                """
            ),
            {"id": ident, "name": name},
        )
        result = row.first()
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"device {ident!r} hasn't registered yet",
        )
    return _row_to_device(result)


@router.patch(
    "/{device_id}/shared-screen",
    response_model=Device,
    # ADMIN tier, not the device tier the rename above takes: the tablet
    # holds the household token, and a flag it could clear itself would
    # hide nothing from anybody standing at it.
    dependencies=[Depends(require_admin_mutation)],
)
async def set_shared_screen(device_id: str, payload: DeviceSharedScreen) -> Device:
    """Mark a device as a shared screen, or back to a personal one. Leaves
    ``last_seen_at`` alone: this is an admin's action, not the device being
    seen."""
    ident = _validate_id(device_id)
    async with session_scope() as s:
        if not await _has_shared_screen(s):
            raise HTTPException(
                status_code=503,
                detail=(
                    "this database has no devices.shared_screen column yet — "
                    "run the migrations (V014) to mark shared screens"
                ),
            )
        columns = await _columns(s)
        row = await s.execute(
            text(
                f"""
                UPDATE devices
                SET shared_screen = :shared
                WHERE device_id = :id
                RETURNING {columns}
                """
            ),
            {"id": ident, "shared": payload.shared_screen},
        )
        result = row.first()
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"device {ident!r} hasn't registered yet",
        )
    return _row_to_device(result)


@router.get("", response_model=list[Device], dependencies=[Depends(require_admin_read)])
async def list_devices(limit: int = 200) -> list[Device]:
    """The device roster, most-recently-seen first. Admin-gated: it's an
    inventory of what's on the network. Feeds the blocklist editor, so an
    admin picks a device from a list instead of typing an id."""
    async with session_scope() as s:
        columns = await _columns(s)
        rows = await s.execute(
            text(
                f"""
                SELECT {columns} FROM devices
                ORDER BY last_seen_at DESC
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 500))},
        )
        return [_row_to_device(r) for r in rows.all()]
