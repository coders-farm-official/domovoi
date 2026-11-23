"""Faces and people — ``/api/faces`` (1.5.0, design MEDIA-UNDERSTANDING §6
and §8; owner answers 2026-11-22). The data and the grouping live in
:mod:`domovoi.media_understanding.faces`; this module holds the tiers.

**Who sees and trains what** (owner, Q8):

* **Guests** (no household credential) get nothing: every route is the
  household device tier (``require_device_read`` / ``require_device``), so
  401.
* **Shared screens** (a device an admin marked shared, ``devices.shared_screen``)
  get nothing either: 403 on every route, an admin's Bearer included. The
  mark hangs off the self-asserted device id (web/backend/api/devices.py
  says how thin that is); it keeps faces off the kitchen tablet's screen.
* **The household** views people and groups, names a group (an existing
  person or a new, face-only one), confirms a face, says "not this
  person", hides a stranger's group, and merges or splits UNNAMED groups.
* **Admins** also merge or split anything a named person is in (it changes
  whose faces are whose), rename a group that already has a person,
  unhide and list hidden groups.
* **Forget my face**: an admin, or a phone or browser claimed as that
  person (its claim key, not a shared screen, claimed over 10 minutes ago,
  as clearing listening history takes).

Every route but ``/status`` answers 409 while faces are off (no
misleading controls). No answer ever carries an embedding or a centroid,
and there is no export route: face data leaves the server only inside a
database backup.

Routes:

* ``GET  /api/faces/status``: the switch, whether the models are here,
  the fetch's last error, counts.
* ``GET  /api/faces/people``: named people with faces, and unnamed groups
  (``?more=true`` adds the small ones).
* ``GET  /api/faces/hidden`` (admin): hidden groups.
* ``GET  /api/faces/groups/{group_id}``: a group's faces (a hidden one: admin).
* ``GET  /api/faces/people/{person_id}/items``: photos and videos a person
  is in, with video moments (the search hook); paged by ``offset`` or
  ``cursor``, ``has_more`` / ``next_cursor`` say where it sits.
* ``POST /api/faces/groups/{group_id}/name``: ``{"person_id"}`` or ``{"name"}``.
* ``POST /api/faces/groups/{group_id}/hide``: ``{"hidden": bool}`` (false: admin).
* ``POST /api/faces/face/{face_id}/confirm``: optional ``{"person_id"}``.
* ``POST /api/faces/face/{face_id}/not-this-person``.
* ``POST /api/faces/merge``: ``{"into": group_id, "groups": [ids]}``.
* ``POST /api/faces/split``: ``{"face_ids": [ids]}``.
* ``POST /api/faces/people/{person_id}/forget``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import text

from domovoi.admin_auth import require_device, require_device_read
from domovoi.media_understanding import face_models, faces, switches
from web.backend.api import devices as devices_api
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/faces", tags=["faces"])

SHARED_SCREEN_DETAIL = "faces and people aren't shown on a shared screen"
FACES_OFF_DETAIL = "recognizing faces is turned off (an admin turns it on in Settings → Photos and videos)"
FRESH_DETAIL = ("this phone or browser said it was {name}'s less than 10 minutes ago, so until "
                "then only an admin can forget {name}'s face")
ADMIN_DETAIL = "only an admin can {what}"
NOT_YOURS_DETAIL = ("only an admin, or {name}'s own phone or browser, can forget {name}'s face. "
                    "Sign in as an admin, or say \"this is me\" on this phone first.")


async def refuse_shared_screen(request: Request) -> None:
    """403 for a device marked as a shared screen."""
    dev = devices_api.caller_device(request)
    if not dev:
        return
    async with session_scope() as s:
        if not await devices_api._has_shared_screen(s):
            return
        shared = (await s.execute(text("SELECT shared_screen FROM devices WHERE device_id = :d"),
                                  {"d": dev})).scalar()
    if shared:
        raise HTTPException(status_code=403, detail=SHARED_SCREEN_DETAIL)


READ = [Depends(require_device_read), Depends(refuse_shared_screen)]
WRITE = [Depends(require_device), Depends(refuse_shared_screen)]


async def _require_on(s: Any) -> None:
    if not (await switches.read(s))["faces"]:
        raise HTTPException(status_code=409, detail=FACES_OFF_DETAIL)


async def _require_admin(request: Request, what: str) -> None:
    if not await devices_api.caller_is_admin(request):
        raise HTTPException(status_code=403, detail=ADMIN_DETAIL.format(what=what))


def _http(e: faces.Refused) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


def _int(raw: Any, what: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise HTTPException(status_code=422, detail=f"{what} must be a positive whole number")
    return raw


def _ids(raw: Any, what: str, most: int = 500) -> list[int]:
    if not isinstance(raw, list) or not raw or len(raw) > most:
        raise HTTPException(status_code=422, detail=f"{what} must be a list of 1 to {most} ids")
    return [_int(x, what) for x in raw]


# ─── reads ───────────────────────────────────────────────────────────────


@router.get("/status", dependencies=READ)
async def status() -> dict[str, Any]:
    async with session_scope() as s:
        on = (await switches.read(s))["faces"]
        st = await faces.read_state(s)
        n = int((await s.execute(text("SELECT count(*) FROM media_faces"))).scalar() or 0)
        people = int((await s.execute(text(
            "SELECT count(DISTINCT person_id) FROM face_groups WHERE person_id IS NOT NULL"))).scalar() or 0)
    ready = face_models.ready()
    return {"enabled": on, "models_ready": ready,
            "fetch_error": st.get("fetch_error") if on and not ready else None,
            "faces": n, "people": people}


@router.get("/people", dependencies=READ)
async def people(more: bool = Query(False)) -> dict[str, Any]:
    async with session_scope() as s:
        await _require_on(s)
        named = await faces.people_with_faces(s)
        groups = await faces.unnamed_groups(s, more=more)
    return {"people": named, "groups": groups, "shown_min_faces": faces.SHOWN_MIN_FACES}


@router.get("/hidden", dependencies=READ)
async def hidden_groups(request: Request) -> dict[str, Any]:
    await _require_admin(request, "see hidden groups")
    async with session_scope() as s:
        await _require_on(s)
        return {"groups": await faces.unnamed_groups(s, hidden=True)}


@router.get("/groups/{group_id}", dependencies=READ)
async def group(group_id: int, request: Request, limit: int = Query(200, ge=1, le=500),
                offset: int = Query(0, ge=0)) -> dict[str, Any]:
    async with session_scope() as s:
        await _require_on(s)
        try:
            out = await faces.group_faces(s, group_id, limit=limit, offset=offset)
        except faces.Refused as e:
            raise _http(e) from None
    if out["hidden"]:
        await _require_admin(request, "see a hidden group")
    return out


@router.get("/people/{person_id}/items", dependencies=READ)
async def person_items(person_id: int, confirmed: bool = Query(False),
                       kind: str | None = Query(None, pattern="^(image|video)$"),
                       limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
                       cursor: str | None = Query(None, max_length=8)) -> dict[str, Any]:
    """``cursor`` (the ``next_cursor`` of the page before) stands in for
    ``offset``: the dashboard's grid loads as it scrolls (usePagedList)."""
    if cursor:
        if not cursor.isdigit():
            raise HTTPException(status_code=422, detail="cursor is the next_cursor of the page before")
        offset = int(cursor)
    async with session_scope() as s:
        await _require_on(s)
        items = await faces.items_for_person(s, person_id, confirmed_only=confirmed, kind=kind,
                                             limit=limit + 1, offset=offset)
    more = len(items) > limit
    items = items[:limit]
    return {"person_id": person_id, "items": items, "has_more": more,
            "next_cursor": str(offset + len(items)) if more else None}


# ─── training ────────────────────────────────────────────────────────────


@router.post("/groups/{group_id}/name", dependencies=WRITE)
async def name_group(group_id: int, request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with session_scope() as s:
        await _require_on(s)
        try:
            current = await faces.group_person(s, group_id)
            if "person_id" in body:
                pid = _int(body["person_id"], "person_id")
            elif isinstance(body.get("name"), str):
                pid = await faces.person_for_name(s, body["name"])
            else:
                raise HTTPException(status_code=422, detail="send person_id or name")
            if current is not None and current != pid:
                await _require_admin(request, "move a named person's faces to someone else")
            return await faces.name_group(s, group_id, pid)
        except faces.Refused as e:
            raise _http(e) from None


@router.post("/groups/{group_id}/hide", dependencies=WRITE)
async def hide_group(group_id: int, request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    hidden = body.get("hidden")
    if not isinstance(hidden, bool):
        raise HTTPException(status_code=422, detail="hidden must be true or false")
    if not hidden:
        await _require_admin(request, "show a hidden group again")
    async with session_scope() as s:
        await _require_on(s)
        try:
            return await faces.hide_group(s, group_id, hidden)
        except faces.Refused as e:
            raise _http(e) from None


@router.post("/face/{face_id}/confirm", dependencies=WRITE)
async def confirm(face_id: int, body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
    pid = _int(body["person_id"], "person_id") if body and "person_id" in body else None
    async with session_scope() as s:
        await _require_on(s)
        try:
            return await faces.confirm_face(s, face_id, pid)
        except faces.Refused as e:
            raise _http(e) from None


@router.post("/face/{face_id}/not-this-person", dependencies=WRITE)
async def not_this_person(face_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        await _require_on(s)
        try:
            return await faces.reject_face(s, face_id)
        except faces.Refused as e:
            raise _http(e) from None


@router.post("/merge", dependencies=WRITE)
async def merge(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    into = _int(body.get("into"), "into")
    others = _ids(body.get("groups"), "groups", most=50)
    async with session_scope() as s:
        await _require_on(s)
        try:
            named = [g for g in [into, *others] if await faces.group_person(s, g) is not None]
            if named:
                await _require_admin(request, "merge a named person's faces")
            return await faces.merge_groups(s, into, others)
        except faces.Refused as e:
            raise _http(e) from None


@router.post("/split", dependencies=WRITE)
async def split(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ids = _ids(body.get("face_ids"), "face_ids")
    async with session_scope() as s:
        await _require_on(s)
        named = (await s.execute(text(
            "SELECT 1 FROM media_faces f JOIN face_groups g ON g.id = f.group_id "
            "WHERE f.id = ANY(:ids) AND g.person_id IS NOT NULL LIMIT 1"), {"ids": ids})).first()
        if named:
            await _require_admin(request, "split a named person's faces")
        try:
            return await faces.split_faces(s, ids)
        except faces.Refused as e:
            raise _http(e) from None


# ─── forget my face ──────────────────────────────────────────────────────


@router.post("/people/{person_id}/forget", dependencies=WRITE)
async def forget(person_id: int, request: Request) -> dict[str, Any]:
    """Forget one person's face: an admin, or that person's own claimed
    phone or browser (claim key, not a shared screen, claimed over 10
    minutes ago). Works with faces on or off (off, there is nothing left
    but the denylist entry it adds when it can)."""
    from web.backend.api.music_listens import FRESH_CLAIM

    admin = await devices_api.caller_is_admin(request)
    caller = devices_api.caller_device(request)
    async with session_scope() as s:
        row = (await s.execute(text("SELECT name FROM people WHERE id = :p"), {"p": person_id})).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"person {person_id} not found")
        name = row[0]
        if not admin:
            device = (await s.execute(text(
                "SELECT owner_person_id, shared_screen, claim_key_hash, owner_claimed_at "
                "FROM devices WHERE device_id = :id"), {"id": caller})).first() if caller else None
            if device is None or device[0] != person_id or device[1]:
                raise HTTPException(status_code=403, detail=NOT_YOURS_DETAIL.format(name=name))
            if not device[2]:
                raise HTTPException(status_code=403, detail=devices_api.RECLAIM_DETAIL.format(name=name))
            if not devices_api.claim_key_matches(device[2], devices_api.presented_claim_key(request)):
                raise HTTPException(status_code=403, detail=devices_api.WRONG_KEY_DETAIL.format(name=name))
            claimed = device[3]
            if claimed is None or claimed > datetime.now(timezone.utc) - FRESH_CLAIM:
                raise HTTPException(status_code=403, detail=FRESH_DETAIL.format(name=name))
        out = await faces.forget_person(s, person_id)
    log.info("faces: person %s forgotten (%s)", person_id, "admin" if admin else "their device")
    return {"person_id": person_id, **out}
