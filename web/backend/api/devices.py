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
device tier the rename takes, so a device cannot un-share itself through
this route. Every device answer carries ``shared_screen``, the device's own
registration included, which is how the dashboard knows to leave personal
content off Home and off its launchers. Presentational, and thin:

* what Home hides on a shared screen (calendar titles and locations, a
  reminder's text, problem rows) is readable with NO credential at all,
  and Home never masks a browser that can't register, a private window on
  the tablet itself included;
* the flag hangs off the SELF-ASSERTED device id above, kept in the
  browser's own storage: clearing it, or registering under a fresh id,
  gives an unmarked device — ``register`` upserts any valid id and never
  touches the flag;
* the tablet holds the household token, so it can read any device-tier
  page too.

Enforcement would take a restricted display pairing of its own (V014).

**Whose device this is** (``devices.owner_person_id``, V035; "claim a
person", Up next P1, owner decisions 2026-11-12). A phone or browser can
say "this is me" for a person on the People page (and in the apps): from
then on what it plays is that person's listening history, and later their
favourites. Because a claimed device may also CLEAR its person's listening
history (web/backend/api/music_listens.py), a claim is not free-for-all.
The rules (:func:`claim_decision`, the only place they live; also applied,
quietly, to the ``owner_person_id`` the Android app sends with its
registration):

* an ADMIN (Bearer, or a box before setup) may set or clear any device's
  person, except on a shared screen; that wipes the device's claim key
  (below) — except an admin's own browser saying "this is me", which gets
  one like any device;
* a SHARED SCREEN is nobody's: it cannot be claimed (409), by an admin
  either — unmark it first; marking a device shared drops its person and
  key;
* otherwise only the device ITSELF may change whose it is (the id in the
  path is the id the caller names itself by: the registration cookie,
  ``X-Device-Id``, ``X-Domovoi-Device-Id`` or ``?device_id=``), and:

  * claiming a person nobody has claimed yet, from nobody, is allowed;
  * claiming a person another device has already claimed needs an admin
    (403, which the dashboard answers with the sign-in prompt) — so a
    second phone of the same person takes an admin's yes once;
  * anything a CLAIMED device does about its claim — let go, switch to
    someone unclaimed, or claim the same person again (a new key) — acts
    as its person, so it must present its claim key (below); a claim from
    before keys (V035, or one an admin set) holds none, and may only say
    "this is me" again for the same person, which mints one;
  * changing ANOTHER device's person (claiming or releasing) needs an
    admin.

**The claim key** (review 2026-11-12). A device id is self-asserted, so a
claim alone can't prove a request comes from the claiming device. When the
device itself claims successfully (``PUT /{device_id}/person``, or a
registration that changes its person), the answer carries ``claim_key``
once — 32 random bytes, url-safe — and only its SHA-256 is stored
(``devices.claim_key_hash``, V051). Acting as the person — clearing their
listening history, switching or releasing the claim, later their
favourites — needs the key in ``X-Domovoi-Claim-Key``, compared in constant
time (an admin skips the check). A re-claim issues a new key; an admin
setting or clearing the person, marking the device shared, or the device
releasing its claim wipes it. A plain registration (an app start) never
touches it.

A guest (no household credential) never gets this far: the route is device
tier. Who can still act as a person: whoever holds that device's key
(it sits in the browser's storage or the app's), and anyone who claims a
person nobody has claimed yet, ten minutes later (music_listens.py) —
docs/SECURITY_PRIVACY.md, "Whose phone or browser this is".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.admin_auth import (
    check_admin_request,
    require_admin_mutation,
    require_admin_read,
    require_device,
)
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
    # "This phone belongs to" (V035): the person (people.id) the app's
    # Settings → Connection names, so voice help can send steps to the
    # asker's own phones. Only applied when SENT (null clears it; leaving it
    # out keeps it): a browser, or an app from before, never touches it. An
    # id no person has is stored as nobody. Self-asserted like the rest of
    # the row: it decides where help steps go, nothing more.
    owner_person_id: int | None = Field(default=None, ge=1)


class DeviceRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=_MAX_NAME)


class DevicePerson(BaseModel):
    # The person this device is ("this is me"), or null: nobody in
    # particular (the household). Rules: claim_decision.
    person_id: int | None = Field(default=None, ge=1)


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
    # The person this device says it is (V035, claimed on the People page
    # or in the app), with their current name; null for nobody in
    # particular. What the browser player's "listening as" starts from.
    owner_person_id: int | None = None
    owner_person_name: str | None = None
    # When this device last said "this is me" itself (it holds a claim key
    # since then); null after an admin set its person, or for nobody.
    owner_claimed_at: Any = None
    # The claim key, ONLY in the answer to the claim that minted it: the
    # device keeps it and sends it as X-Domovoi-Claim-Key to act as its
    # person. The server keeps its SHA-256 alone and never shows it again.
    claim_key: str | None = None


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


def _owner_of(r: Any) -> int | None:
    return int(r[7]) if r is not None and len(r) > 7 and r[7] is not None else None


def _row_to_device(r: Any) -> Device:
    return Device(
        device_id=r[0], name=r[1], platform=r[2], user_agent=r[3],
        first_seen_at=r[4], last_seen_at=r[5], shared_screen=bool(r[6]),
        owner_person_id=int(r[7]) if len(r) > 7 and r[7] is not None else None,
        owner_person_name=r[8] if len(r) > 8 else None,
        owner_claimed_at=r[9] if len(r) > 9 else None,
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


_HAS_OWNER = False


async def _has_owner(s: Any) -> bool:
    """Whether V035's ``owner_person_id`` is there (cached once it is)."""
    global _HAS_OWNER
    if not _HAS_OWNER:
        row = (
            await s.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'devices'
                      AND column_name = 'owner_person_id'
                    """
                )
            )
        ).first()
        _HAS_OWNER = row is not None
    return _HAS_OWNER


_HAS_CLAIM_KEYS = False


async def _has_claim_keys(s: Any) -> bool:
    """Whether V051's ``claim_key_hash`` / ``owner_claimed_at`` are there
    (cached once they are). Without them a device registers as before and
    no claim can be made (503)."""
    global _HAS_CLAIM_KEYS
    if not _HAS_CLAIM_KEYS:
        row = (
            await s.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'devices'
                      AND column_name = 'claim_key_hash'
                    """
                )
            )
        ).first()
        _HAS_CLAIM_KEYS = row is not None
    return _HAS_CLAIM_KEYS


async def _columns(s: Any) -> str:
    """The select / RETURNING list for a :class:`Device`, ``shared_screen``
    last — read as FALSE until V014 is applied."""
    flag = "shared_screen" if await _has_shared_screen(s) else "FALSE AS shared_screen"
    owner = (
        "owner_person_id, "
        "(SELECT p.name FROM people p WHERE p.id = devices.owner_person_id) AS owner_person_name, "
        + ("owner_claimed_at" if await _has_claim_keys(s) else "NULL AS owner_claimed_at")
        if await _has_owner(s)
        else "NULL AS owner_person_id, NULL AS owner_person_name, NULL AS owner_claimed_at"
    )
    return f"{_BASE_COLUMNS}, {flag}, {owner}"


# ─── Whose device this is: the claim rules ───────────────────────────────


def caller_device(request: Request) -> str | None:
    """The id the caller names itself by: the registration cookie,
    ``X-Device-Id``, ``?device_id=`` (files.caller_device_id), then the
    ``X-Domovoi-Device-Id`` header the dashboard puts on every call.
    Self-asserted, like every device id."""
    # Inside the call: files imports music, which imports this module's
    # neighbours at import time.
    from web.backend.api import files as files_api

    return files_api.caller_device_id(request, None) or valid_device_id(
        request.headers.get("X-Domovoi-Device-Id")
    )


async def caller_is_admin(request: Request) -> bool:
    """A live admin Bearer, or a box nobody has claimed yet (everything is
    the household's before setup). The dashboard cookie alone is not: it
    renders state and never authorizes a change."""
    return await check_admin_request(request) in ("ok", "pre-setup")


SHARED_SCREEN_CLAIM_DETAIL = (
    "this is a shared screen, so it is the whole household's and can't be "
    "anyone's. An admin can unmark it in Settings → Phones & browsers first."
)

#: The header a device proves its claim with (the key it was handed when it
#: said "this is me"). Never logged, never forwarded to the core.
CLAIM_KEY_HEADER = "X-Domovoi-Claim-Key"

RECLAIM_DETAIL = (
    "this phone or browser said it was {name}'s before claims had keys: "
    "say \"this is me\" again on {name}'s People page first"
)
WRONG_KEY_DETAIL = (
    "this phone or browser can't prove it is {name}'s (its claim key is "
    "missing or out of date). An admin can change it, or set it to nobody so "
    "it can say \"this is me\" again."
)


def claim_key_hash(key: str) -> str:
    """What is stored of a claim key: its SHA-256, hex."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def presented_claim_key(request: Request) -> str | None:
    raw = (request.headers.get(CLAIM_KEY_HEADER) or "").strip()
    return raw[:200] or None


def claim_key_matches(stored_hash: str | None, presented: str | None) -> bool:
    """Constant-time: does the presented key hash to the stored one?"""
    if not stored_hash or not presented:
        return False
    return hmac.compare_digest(stored_hash, claim_key_hash(presented))


@dataclass
class ClaimDecision:
    """What :func:`claim_decision` decided: ``refusal`` ``(status, detail)``
    or None; on a yes, ``mint`` (hand the device a new claim key) or not
    (the stored key is wiped)."""

    refusal: tuple[int, str] | None
    mint: bool = False


async def claim_decision(
    s: Any, device_id: str, person_id: int | None, *, admin: bool, caller: str | None,
    key: str | None,
) -> ClaimDecision:
    """Whether ``caller`` may set ``device_id``'s person to ``person_id``
    (None = release). The rules are the module docstring's; this is the
    only place they live.

    Locks the device row and the person row (FOR UPDATE), so a claim and an
    admin marking the device shared, or two devices claiming one person,
    take turns instead of both passing their checks."""
    shared_col = "shared_screen" if await _has_shared_screen(s) else "FALSE"
    row = (
        await s.execute(
            text(
                f"SELECT owner_person_id, {shared_col}, claim_key_hash "
                "FROM devices WHERE device_id = :id FOR UPDATE"
            ),
            {"id": device_id},
        )
    ).first()
    if row is None:
        return ClaimDecision((404, f"device {device_id!r} hasn't registered yet"))
    current = int(row[0]) if row[0] is not None else None
    stored = row[2]
    if row[1] and person_id is not None:
        return ClaimDecision((409, SHARED_SCREEN_CLAIM_DETAIL))
    name = None
    if person_id is not None:
        found = (
            await s.execute(
                text("SELECT name FROM people WHERE id = :p FOR UPDATE"), {"p": person_id},
            )
        ).first()
        if found is None:
            return ClaimDecision((404, f"person {person_id} not found"))
        name = found[0]
    self_claim = caller == device_id and person_id is not None
    if admin:
        # An admin on its own browser saying "this is me" gets a key like
        # anyone; an admin setting or clearing a device's person wipes it.
        return ClaimDecision(None, mint=self_claim)
    if caller != device_id:
        return ClaimDecision((403, "only that phone or browser itself, or an admin, can change whose it is"))
    if current is not None:
        # Acting as the person it is now: releasing, switching, or claiming
        # them again (a new key) needs the key it holds.
        cur_name = (
            await s.execute(text("SELECT name FROM people WHERE id = :p"), {"p": current})
        ).scalar_one_or_none() or "that person"
        if not stored:
            # A claim from before keys: saying "this is me" again for the
            # same person is how it gets one; anything else waits for that.
            if person_id != current:
                return ClaimDecision((403, RECLAIM_DETAIL.format(name=cur_name)))
        elif not claim_key_matches(stored, key):
            return ClaimDecision((403, WRONG_KEY_DETAIL.format(name=cur_name)))
    if person_id is None:
        return ClaimDecision(None, mint=False)
    if person_id != current:
        others = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM devices "
                    "WHERE owner_person_id = :p AND device_id <> :id"
                ),
                {"p": person_id, "id": device_id},
            )
        ).scalar_one()
        if others:
            return ClaimDecision((403, (
                f"{name} is already claimed by another phone or browser. "
                "An admin can add this one too."
            )))
    return ClaimDecision(None, mint=True)


async def apply_claim(
    s: Any, device_id: str, person_id: int | None, decision: ClaimDecision,
) -> str | None:
    """Write a decided claim. Returns the new claim key when one was minted
    (shown to the device once; only its hash is kept), else None and the
    stored hash is wiped. Never on a shared screen (``AND NOT
    shared_screen``: the mark won the race)."""
    key = secrets.token_urlsafe(32) if decision.mint else None
    shared_guard = " AND NOT shared_screen" if await _has_shared_screen(s) else ""
    result = await s.execute(
        text(
            "UPDATE devices SET owner_person_id = (SELECT id FROM people WHERE id = :o), "
            "claim_key_hash = :h, "
            "owner_claimed_at = CASE WHEN :minted THEN now() ELSE NULL END "
            "WHERE device_id = :id"
            + (shared_guard if person_id is not None else "")
        ),
        {"o": person_id, "id": device_id, "h": claim_key_hash(key) if key else None,
         "minted": key is not None},
    )
    if not (result.rowcount or 0):
        # Marked a shared screen between the decision and the write.
        raise HTTPException(status_code=409, detail=SHARED_SCREEN_CLAIM_DETAIL)
    return key


async def _read_device(s: Any, device_id: str) -> Any:
    columns = await _columns(s)
    return (
        await s.execute(
            text(f"SELECT {columns} FROM devices WHERE device_id = :id"), {"id": device_id}
        )
    ).first()


@router.post("/register", response_model=Device, dependencies=DEVICE)
async def register_device(
    payload: DeviceRegistration, response: Response, request: Request,
) -> Device:
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
        minted: str | None = None
        if (
            "owner_person_id" in payload.model_fields_set
            and await _has_claim_keys(s)
            and _owner_of(result) != payload.owner_person_id
        ):
            # "This phone belongs to" from the app's registration, when it
            # CHANGES whose the phone is: the same rules as PUT
            # /{device_id}/person, with the registering device as the caller
            # and the claim key it presents. A plain registration (the same
            # person, or none sent) never touches the claim or its key. A
            # change the rules refuse is NOT an error here (the app registers
            # on every start): the row keeps the person it had, and the
            # answer says which that is.
            owner = payload.owner_person_id
            decision = await claim_decision(
                s, ident, owner, admin=await caller_is_admin(request), caller=ident,
                key=presented_claim_key(request),
            )
            if decision.refusal is None:
                try:
                    minted = await apply_claim(s, ident, owner, decision)
                except HTTPException as e:
                    log.info("device %s: owner from its registration lost a race: %s", ident, e.detail)
                result = await _read_device(s, ident)
            else:
                log.info("device %s: owner %s from its registration not applied: %s",
                         ident, owner, decision.refusal[1])
    if result is None:  # pragma: no cover — RETURNING always yields on upsert
        raise HTTPException(status_code=500, detail="upsert returned no row")
    # From here the browser carries its own name on every request, including
    # the ones whose body has no field for it. See DEVICE_ID_COOKIE.
    _remember_device(response, ident)
    out = _row_to_device(result)
    out.claim_key = minted
    return out


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


@router.put("/{device_id}/person", response_model=Device, dependencies=DEVICE)
async def set_device_person(device_id: str, payload: DevicePerson, request: Request) -> Device:
    """"This is me": set the person this phone or browser is, or
    ``person_id: null`` for nobody in particular. Device tier, with the
    claim rules on top (:func:`claim_decision`, the module docstring): the
    device itself may claim a person nobody has claimed yet and release its
    own claim; anything else is an admin's. 403 names what an admin can do
    (the dashboard answers it with the sign-in prompt and sends it again),
    409 a shared screen, 404 an unknown device or person, 503 a database
    without V035."""
    ident = _validate_id(device_id)
    async with session_scope() as s:
        if not await _has_claim_keys(s):
            raise HTTPException(
                status_code=503,
                detail="this database has no devices.claim_key_hash column yet — run the migrations (V051)",
            )
        decision = await claim_decision(
            s, ident, payload.person_id,
            admin=await caller_is_admin(request), caller=caller_device(request),
            key=presented_claim_key(request),
        )
        if decision.refusal is not None:
            raise HTTPException(status_code=decision.refusal[0], detail=decision.refusal[1])
        minted = await apply_claim(s, ident, payload.person_id, decision)
        result = await _read_device(s, ident)
    if result is None:  # pragma: no cover — claim_decision found the row
        raise HTTPException(status_code=404, detail=f"device {ident!r} hasn't registered yet")
    out = _row_to_device(result)
    out.claim_key = minted
    return out


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
        # A shared screen is nobody's: marking one drops the person it was
        # claimed for (claim_decision keeps it that way).
        owner_reset = (
            ", owner_person_id = CASE WHEN :shared THEN NULL ELSE owner_person_id END"
            + (", claim_key_hash = CASE WHEN :shared THEN NULL ELSE claim_key_hash END"
               ", owner_claimed_at = CASE WHEN :shared THEN NULL ELSE owner_claimed_at END"
               if await _has_claim_keys(s) else "")
            if await _has_owner(s) else ""
        )
        row = await s.execute(
            text(
                f"""
                UPDATE devices
                SET shared_screen = :shared{owner_reset}
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


@router.get("/android-app")
async def android_app_for_household_phones() -> dict[str, Any]:
    """Settings → Phones & browsers → "get the Domovoi app": the server's join page
    without a room code (``join_url``, ``<core>/join``, for the card's QR
    code and link) and what that page will offer, from the core's public
    ``GET /v1/android-app`` (``release`` null before the first release;
    ``ready`` once the server holds a checked copy of every APK).

    Open, like the page it points at: a phone that has not joined the
    household yet opens it. ``join_url`` is null with ``join_problem``
    when this server's address is not one a phone on the home network can
    reach (a box that found no LAN address)."""
    from satellite import enrolment

    from web.backend.api.satellites import _advertised_core_http
    from web.backend.domovoi_client import get_admin

    join_url: str | None = None
    problem: str | None = None
    try:
        core, _ = enrolment.normalize_core(_advertised_core_http())
        join_url = core + enrolment.JOIN_PATH
    except enrolment.EnrolmentError as e:
        problem = str(e)
    # The core's public view, so deliberately no credential: an open route
    # forwards none (headers={} says so to the every-hop-forwards guard,
    # domovoi/tests/test_web_proxy_credentials.py).
    status, app = await get_admin("/v1/android-app", headers={})
    if status != 200 or not isinstance(app, dict):
        app = {"release": None, "apks": [], "ready": False, "unreachable": True}
    return {"join_url": join_url, "join_problem": problem, **app}
