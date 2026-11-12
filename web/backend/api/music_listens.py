"""Listening history API — ``/api/music/listens`` (V051, Up next P1, 2026-11-12).

* ``POST /api/music/listens`` — a browser's or phone's songs, in batches of
  up to 200, sent when each ends, or later (a phone that was offline sends
  its outbox when it is back; up to 7 days late). DEVICE tier. Each row is
  the player's own word: the song, the device, how long it played and how
  it ended. Whose it is: the ``listener_person_id`` the row names (the
  "listening as" choice), else the household when it says
  ``as_household`` (a claimed device told "nobody in particular"), else
  the person the device is claimed by unless it is a shared screen, else
  the household. Dropped, not refused: a start
  more than 7 days old or more than 10 minutes ahead, a song the library
  doesn't have, a row the table already has (a batch sent twice). Under
  "Keep listening history: Don't keep" nothing is recorded.
* ``GET /api/music/listens/summary`` — how long it is kept, and counts per
  person (never a song). The READ half of the device tier, like the
  People page's other reads.
* ``DELETE /api/music/listens/people/{person_id}`` — clear one person's
  listening history (owner decision 2026-11-12: each person clears their
  OWN). DEVICE tier, then: an admin (Bearer, or a box before setup), or a
  phone or browser claimed as that person (devices.owner_person_id) that is
  not a shared screen. Anyone else gets 403 saying so: the dashboard then
  offers the admin password, or claiming the person for this device first.
* ``POST /api/music/listens/delete-all`` — everyone's and the household's.
  Admin, SECURITY tier (Bearer only, 501 before setup), like "Delete all
  voice history".

The setting ("Keep listening history": 1 year, 90 days, don't keep) is
written like any other: ``PATCH /api/config/editable`` with
``music_listens_keep``. Its live value comes from the core's snapshot.

A room's listens are the core's (domovoi/workers/listen_logger.py). The
web's one part in them: when a phone or browser REPLACES a room's queue
(play, play-track, play-tracks, play-playlist), the room's music is that
device's person's from then on (:func:`note_room_start`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import music_listens
from domovoi.admin_auth import require_admin_security, require_device, require_device_read
from domovoi.config import (
    MUSIC_LISTENS_KEEP_CHOICES,
    MUSIC_LISTENS_KEEP_LABELS,
    normalize_music_listens_keep,
)
from domovoi.config import settings as core_settings
from web.backend.api import devices as devices_api
from web.backend.db import session_scope
from web.backend.domovoi_client import get_cached_snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/listens", tags=["music"])

_DEVICE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$"
#: One batch at most: an offline phone sends its outbox in pages of this.
MAX_BATCH = 200


def live_keep() -> str:
    """The live "Keep listening history": the core's snapshot, else this
    process's copy (the core hasn't answered yet)."""
    live = (get_cached_snapshot() or {}).get("music_listens_keep")
    if live in MUSIC_LISTENS_KEEP_CHOICES:
        return live
    return normalize_music_listens_keep(core_settings.music_listens_keep)


class ListenIn(BaseModel):
    track_id: int = Field(..., ge=1)
    device_id: str = Field(..., pattern=_DEVICE_ID)
    # The "listening as" person, when the player has one. Self-asserted
    # within the household, like the spoken-audio resume positions.
    listener_person_id: int | None = Field(default=None, ge=1)
    # The player was told "nobody in particular" on purpose (a claimed
    # phone or browser lent to a guest): the household's, not the device's
    # person's.
    as_household: bool = False
    session_id: UUID | None = None
    origin: Literal["explicit", "list", "auto"] = "explicit"
    started_at: datetime
    played_sec: float = Field(..., ge=0, le=music_listens.MAX_SECONDS)
    duration_sec: float | None = Field(default=None, ge=0, le=music_listens.MAX_SECONDS)
    outcome: Literal["finished", "skipped", "stopped"]
    # Left out: read from the device's registration (android / ios, else browser).
    player: Literal["browser", "android", "ios"] | None = None


class ListensIn(BaseModel):
    listens: list[ListenIn] = Field(..., min_length=1, max_length=MAX_BATCH)


async def _devices(s: Any, ids: set[str]) -> dict[str, tuple[int | None, bool, str | None]]:
    """device_id -> (claimed person, shared screen, platform)."""
    if not ids:
        return {}
    rows = (
        await s.execute(
            text(
                "SELECT device_id, owner_person_id, shared_screen, platform "
                "FROM devices WHERE device_id = ANY(:ids)"
            ),
            {"ids": sorted(ids)},
        )
    ).all()
    return {r[0]: (r[1], bool(r[2]), r[3]) for r in rows}


def _player(platform: str | None) -> str:
    p = (platform or "").strip().lower()
    return p if p in ("android", "ios") else "browser"


#: How many listens one device may add in a day. A phone playing music
#: all day long logs a few hundred; past this a client is misbehaving or
#: flooding, and the rest of the day's rows are dropped.
DEVICE_DAILY_CAP = 3000
#: Slack on the "no more played time than time passed" rule (clock
#: rounding, a song's last second).
PLAYED_SLACK_SEC = 60


async def _recent_count(s: Any, device_id: str) -> int:
    return int((
        await s.execute(
            text(
                "SELECT COUNT(*) FROM music_listens WHERE device_id = :d "
                "AND recorded_at > now() - interval '24 hours'"
            ),
            {"d": device_id},
        )
    ).scalar_one() or 0)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _impossible_device(items: list[ListenIn], now: datetime) -> str | None:
    """A device whose rows in this batch played longer, in total, than the
    time since its earliest row began: one player can't do that. None when
    every device's rows add up."""
    by_device: dict[str, list[ListenIn]] = {}
    for item in items:
        by_device.setdefault(item.device_id, []).append(item)
    for device_id, rows in by_device.items():
        span = (now - min(_aware(r.started_at) for r in rows)).total_seconds()
        if sum(r.played_sec for r in rows) > span + PLAYED_SLACK_SEC:
            return device_id
    return None


@router.post("", dependencies=[Depends(require_device)])
async def post_listens(body: ListensIn) -> dict[str, Any]:
    """Record a batch of a browser's or phone's listens. ``{accepted,
    dropped, recording}``: ``accepted`` the rows that were new, ``dropped``
    the rest (too old, too far ahead, unknown song, already recorded, or
    past the device's daily cap), ``recording`` false while the household
    keeps no listening history.

    Checked here, not taken on the player's word (review 2026-11-12): the
    outcome is worked out again from the seconds played and the length
    (only the player's "skipped" is kept: a skip is something only the
    player saw); a shared screen's rows are the household's whatever person
    they name; a batch in which one device played longer in total than the
    time since its first song began is refused (422); and a device adds at
    most ``DEVICE_DAILY_CAP`` listens a day."""
    keep = live_keep()
    if not music_listens.recording(keep):
        return {"accepted": 0, "dropped": len(body.listens), "recording": False}
    now = datetime.now(timezone.utc)
    bad = _impossible_device(body.listens, now)
    if bad is not None:
        raise HTTPException(
            status_code=422,
            detail=f"{bad}: more seconds played than have passed since its first song began",
        )
    async with session_scope() as s:
        devices = await _devices(s, {item.device_id for item in body.listens})
        room_left: dict[str, int] = {}
        for d in {item.device_id for item in body.listens}:
            room_left[d] = max(0, DEVICE_DAILY_CAP - await _recent_count(s, d))
        rows = []
        for item in body.listens:
            if room_left[item.device_id] <= 0:
                continue
            owner, shared, platform = devices.get(item.device_id, (None, False, None))
            if shared or item.as_household:
                # A shared screen is the household's: a "listening as" pick
                # there is ignored.
                person = None
            else:
                person = item.listener_person_id or owner
            row = music_listens.clean_listen(
                {
                    "track_id": item.track_id,
                    "person_id": person,
                    "room_id": None,
                    "device_id": item.device_id,
                    "player": item.player or _player(platform),
                    "session_id": item.session_id,
                    "origin": item.origin,
                    "started_at": item.started_at,
                    "played_sec": item.played_sec,
                    "duration_sec": item.duration_sec,
                    "outcome": music_listens.outcome_for(
                        item.played_sec, item.duration_sec, item.outcome == "skipped",
                    ),
                },
                now=now,
            )
            if row is not None:
                rows.append(row)
                room_left[item.device_id] -= 1
        accepted = await music_listens.insert_listens(s, rows)
    return {"accepted": accepted, "dropped": len(body.listens) - accepted, "recording": True}


@router.get("/summary", dependencies=[Depends(require_device_read)])
async def get_summary() -> dict[str, Any]:
    """``{keep, keep_label, keep_days, choices, total, oldest_at, household,
    people: [{person_id, name, listens}]}``. Counts only, never a song."""
    keep = live_keep()
    async with session_scope() as s:
        counts = await music_listens.summary(s)
    return {
        "keep": keep,
        "keep_label": MUSIC_LISTENS_KEEP_LABELS.get(keep, keep),
        "keep_days": music_listens.keep_days(keep),
        "choices": [
            {"value": c, "label": MUSIC_LISTENS_KEEP_LABELS[c]} for c in MUSIC_LISTENS_KEEP_CHOICES
        ],
        **counts,
    }


NOT_YOURS_DETAIL = (
    "only {name}'s own phone or browser, or an admin, can clear {name}'s listening history"
)
#: After a device says "this is me", clearing that person's history from it
#: waits this long; until then it is an admin's (review 2026-11-12: claiming
#: an unclaimed person is easy on purpose, so a claim alone must not be
#: enough to wipe their history at once).
FRESH_CLAIM = timedelta(minutes=10)
FRESH_CLAIM_DETAIL = (
    "this phone or browser said it was {name}'s less than 10 minutes ago, so "
    "until then only an admin can clear {name}'s listening history"
)


@router.delete("/people/{person_id}", dependencies=[Depends(require_device)])
async def clear_person(person_id: int, request: Request) -> dict[str, Any]:
    """Clear one person's listening history: ``{deleted}``. An admin, or
    a phone or browser claimed as that person that isn't a shared screen,
    proves it with its claim key (``X-Domovoi-Claim-Key``, compared in
    constant time) and claimed more than 10 minutes ago. 404 for a person
    nobody has; 403, saying why and who may, for anything else (the
    dashboard answers it with the admin sign-in)."""
    admin = await devices_api.caller_is_admin(request)
    caller = devices_api.caller_device(request)
    async with session_scope() as s:
        person = (
            await s.execute(text("SELECT name FROM people WHERE id = :p"), {"p": person_id})
        ).first()
        if person is None:
            raise HTTPException(status_code=404, detail=f"person {person_id} not found")
        name = person[0]
        if not admin:
            device = (
                await s.execute(
                    text(
                        "SELECT owner_person_id, shared_screen, claim_key_hash, owner_claimed_at "
                        "FROM devices WHERE device_id = :id"
                    ),
                    {"id": caller},
                )
            ).first() if caller else None
            if device is None or device[0] != person_id or device[1]:
                raise HTTPException(status_code=403, detail=NOT_YOURS_DETAIL.format(name=name))
            if not device[2]:
                raise HTTPException(
                    status_code=403, detail=devices_api.RECLAIM_DETAIL.format(name=name))
            if not devices_api.claim_key_matches(device[2], devices_api.presented_claim_key(request)):
                raise HTTPException(
                    status_code=403, detail=devices_api.WRONG_KEY_DETAIL.format(name=name))
            claimed = device[3]
            if claimed is None or claimed > datetime.now(timezone.utc) - FRESH_CLAIM:
                raise HTTPException(status_code=403, detail=FRESH_CLAIM_DETAIL.format(name=name))
        deleted = await music_listens.delete_person(s, person_id)
    log.info("listening history: %d listen%s of person %s cleared (%s)",
             deleted, "" if deleted == 1 else "s", person_id, "admin" if admin else "their device")
    return {"deleted": deleted}


@router.post("/delete-all", dependencies=[Depends(require_admin_security)])
async def delete_all() -> dict[str, Any]:
    """Delete all listening history: every person's and the household's,
    from every room, browser and phone, and who each room's music was for.
    Admin, security tier: 501 before setup. ``{deleted}``."""
    async with session_scope() as s:
        deleted = await music_listens.delete_all(s)
    log.info("listening history: all %d listen%s deleted by an admin",
             deleted, "" if deleted == 1 else "s")
    return {"deleted": deleted}


async def note_room_start(request: Request, body_device_id: str | None, room_id: str) -> None:
    """A phone or browser just replaced ``room_id``'s queue: the room's
    music is for that device's claimed person from now on (the household
    when it has none, is a shared screen, or didn't name itself).
    Best-effort: a failure here never fails the play."""
    from web.backend.api import files as files_api

    caller = files_api.caller_device_id(request, body_device_id) or devices_api.caller_device(request)
    try:
        async with session_scope() as s:
            person = None
            if caller:
                row = (
                    await s.execute(
                        text(
                            "SELECT owner_person_id, shared_screen FROM devices "
                            "WHERE device_id = :id"
                        ),
                        {"id": caller},
                    )
                ).first()
                if row is not None and not row[1]:
                    person = row[0]
            await music_listens.set_room_listener(s, room_id, person, caller)
    except Exception as e:  # noqa: BLE001 — attribution is a nicety
        log.debug("listens: room listener for %s not recorded: %s", room_id, e)
