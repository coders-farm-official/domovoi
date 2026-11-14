"""Calendar API — the household's events, and the calendars it reads.

``/events``: full CRUD against ``calendar_events`` for the household's own
events (``source='local'``). The voice CalendarHandler only reads them.

Calendars from elsewhere (V055, 1.3.0; domovoi/calendar_feeds.py) are
READ-ONLY here: an event with a ``calendar_source_id`` came from a link or
a phone, carries that calendar's name and colour, and a PATCH or DELETE of
it answers 409 ("change it there").

* ``/sources`` — the calendars the house reads. Listing is household
  (device read tier); adding, changing, removing and "refresh now" are
  admin. A link's address is a secret: stored, never returned (only its
  host, as ``host/…``), never logged.
* ``/phones/{device_id}`` — a household phone's snapshot of the calendars
  its owner chose to share (device tier, at most once a minute per phone,
  8 MB; web/backend/middleware.py BODY_LIMITS). One row per (phone,
  calendar), so one phone never overwrites another's. A device id is
  self-asserted, so the phone's first snapshot is handed a calendar key
  and every later PUT or DELETE must carry it (``X-Domovoi-Calendar-Key``,
  V057; calendar_feeds.admit_phone): only that phone, or an admin (DELETE),
  touches its calendars. A late upload is refused by its ``generation``.
  A phone not heard from for a day shows ``stale``; after 7 days its
  calendars are deleted (the refresher worker).
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text

from domovoi import calendar_feeds, household_time
from domovoi.admin_auth import require_admin_mutation, require_device, require_device_read
from web.backend.db import session_scope
from web.backend.schemas import (
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventPatch,
    CalendarSource,
    CalendarSourceCreate,
    CalendarSourcePatch,
    CalendarSourceRefresh,
    PhoneCalendarSnapshot,
    PhoneCalendarStored,
)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# ─── Auth tier (REV-1) ─────────────────────────────────────────────────────
# Keeping the household calendar is an ordinary household action, so every write here takes the
# DEVICE tier: a valid ``X-Device-Token`` or an admin Bearer, with the
# pre-setup LAN grace kept so a fresh install still works.
DEVICE = [Depends(require_device)]

# READING it is household data as well (owner decision 2026-10-08, WEB-15):
# titles, places and descriptions of the household's appointments. The
# READ half of the device tier — the household token or an admin Bearer,
# the dashboard cookie, or ``?device_token=`` — with the pre-setup grace;
# a bare LAN request is 401. The ``/ws/state`` push of the same entries was
# gated first; this closes the HTTP poll beside it.
READ = [Depends(require_device_read)]

# Calendars from elsewhere: adding a link (a secret address the server will
# fetch), changing, removing and refreshing one is an admin's (owner
# decision 2026-11-14: "admin to add/remove, household to see").
ADMIN = [Depends(require_admin_mutation)]

_EVENT_COLUMNS = """
    e.id, e.title, e.starts_at, e.ends_at, e.location, e.description,
    e.source, e.external_id, e.last_synced_at, e.all_day, e.calendar_source_id,
    s.name, s.color, s.kind,
    COALESCE(sp.name, dp.name, d.name),
    e.busy,
    (SELECT array_agg(DISTINCT o_s.name || COALESCE(' · ' || COALESCE(o_sp.name, o_dp.name, o_d.name), ''))
       FROM calendar_events o
       JOIN calendar_sources o_s ON o_s.id = o.calendar_source_id
       LEFT JOIN people o_sp ON o_sp.id = o_s.person_id
       LEFT JOIN devices o_d ON o_s.kind = 'phone' AND o_d.device_id = o_s.device_id
       LEFT JOIN people o_dp ON o_dp.id = o_d.owner_person_id
      WHERE e.ical_uid IS NOT NULL AND o.ical_uid = e.ical_uid
        AND o.starts_at = e.starts_at AND o.id <> e.id)
"""
# The same event in two calendars (two phones sharing one family calendar,
# or a phone and a link to it): same iCalendar UID and start. Listed once,
# as the row with the lowest id; ``also_in`` names the others.
_NOT_A_DUPLICATE = """
    NOT EXISTS (SELECT 1 FROM calendar_events o
                WHERE e.ical_uid IS NOT NULL AND o.ical_uid = e.ical_uid
                  AND o.starts_at = e.starts_at AND o.id < e.id)
"""
# GET /events with neither bound: from a day ago to 60 days ahead.
DEFAULT_BACK = timedelta(days=1)
DEFAULT_AHEAD = timedelta(days=60)


def _now() -> datetime:
    return datetime.now(timezone.utc)
_EVENT_FROM = """
    FROM calendar_events e
    LEFT JOIN calendar_sources s ON s.id = e.calendar_source_id
    LEFT JOIN people sp ON sp.id = s.person_id
    LEFT JOIN devices d ON s.kind = 'phone' AND d.device_id = s.device_id
    LEFT JOIN people dp ON dp.id = d.owner_person_id
"""


@router.get("/events", response_model=list[CalendarEvent], dependencies=READ)
async def list_events(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[CalendarEvent]:
    """Events in ``[start, end)``: starting in it, or (from 1.3.0) begun
    before ``start`` and still under way at it (a multi-day event), in
    start order, at most ``limit``.

    With NEITHER bound the window is a day ago to 60 days ahead (until
    1.3.0 it was every event ever, oldest first, so the cap cut off what
    was coming). One bound alone is open on the other side, as before.
    The same event in two calendars (same iCalendar UID and start) is
    listed once, with ``also_in`` naming the others.
    """
    if start is None and end is None:
        now = _now()
        start, end = now - DEFAULT_BACK, now + DEFAULT_AHEAD
    where_clauses: list[str] = [_NOT_A_DUPLICATE]
    params: dict[str, Any] = {"limit": limit}
    if start is not None:
        where_clauses.append(
            "(e.starts_at >= :start OR COALESCE(e.ends_at, e.starts_at) > :start)"
        )
        params["start"] = start
    if end is not None:
        where_clauses.append("e.starts_at < :end")
        params["end"] = end
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    async with session_scope() as s:
        rows = await s.execute(
            text(
                f"""
                SELECT {_EVENT_COLUMNS}
                {_EVENT_FROM}
                {where}
                ORDER BY e.starts_at ASC, e.id ASC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_row_to_event(r) for r in rows.all()]


@router.get("/events/{event_id}", response_model=CalendarEvent, dependencies=READ)
async def get_event(event_id: int) -> CalendarEvent:
    async with session_scope() as s:
        result = await _fetch_event(s, event_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return _row_to_event(result)


@router.post(
    "/events", response_model=CalendarEvent, status_code=201,
    dependencies=DEVICE,
)
async def create_event(payload: CalendarEventCreate) -> CalendarEvent:
    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                INSERT INTO calendar_events
                    (title, starts_at, ends_at, location, description,
                     source, created_via)
                VALUES
                    (:title, :starts_at, :ends_at, :location, :description,
                     'local', 'manual')
                RETURNING id
                """
            ),
            {
                "title": payload.title,
                "starts_at": payload.starts_at,
                "ends_at": payload.ends_at,
                "location": payload.location,
                "description": payload.description,
            },
        )
        inserted = row.first()
        result = await _fetch_event(s, int(inserted[0])) if inserted is not None else None
        # Wake any other browser tabs watching the calendar within the
        # same Postgres NOTIFY round-trip (tens of ms) instead of
        # waiting for the next 1.5 s poll. Same transaction as the
        # INSERT so a rolled-back commit doesn't deliver a phantom event.
        await s.execute(text("SELECT pg_notify('calendar_changed', 'created')"))
    if result is None:
        raise HTTPException(status_code=500, detail="insert returned no row")
    return _row_to_event(result)


@router.patch(
    "/events/{event_id}", response_model=CalendarEvent, dependencies=DEVICE
)
async def patch_event(event_id: int, payload: CalendarEventPatch) -> CalendarEvent:
    """Partial update. Only fields explicitly set on the request body
    are written; anything left unset stays as-is. An event from a linked
    or phone calendar is 409: it is changed where it lives."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields provided")

    set_fragments = [f"{k} = :{k}" for k in updates]
    params: dict[str, Any] = {"id": event_id, **updates}

    async with session_scope() as s:
        await _refuse_read_only(s, event_id)
        row = await s.execute(
            text(
                f"""
                UPDATE calendar_events
                SET {', '.join(set_fragments)}
                WHERE id = :id AND calendar_source_id IS NULL
                RETURNING id
                """
            ),
            params,
        )
        result = row.first()
        if result is not None:
            result = await _fetch_event(s, event_id)
            # Don't fire NOTIFY for a 404 PATCH — nothing actually
            # changed.
            await s.execute(text("SELECT pg_notify('calendar_changed', 'updated')"))
    if result is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return _row_to_event(result)


@router.delete("/events/{event_id}", status_code=204, dependencies=DEVICE)
async def delete_event(event_id: int) -> None:
    async with session_scope() as s:
        await _refuse_read_only(s, event_id)
        result = await s.execute(
            text("DELETE FROM calendar_events WHERE id = :id AND calendar_source_id IS NULL"),
            {"id": event_id},
        )
        if (result.rowcount or 0) > 0:
            await s.execute(text("SELECT pg_notify('calendar_changed', 'deleted')"))
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")


# ─── Calendars from elsewhere (V055) ───────────────────────────────────────


async def _is_admin(request: Request) -> bool:
    from web.backend.api.devices import caller_is_admin

    return await caller_is_admin(request)


@router.get("/sources", response_model=list[CalendarSource], dependencies=READ)
async def list_sources(request: Request) -> list[CalendarSource]:
    """Every calendar the house reads besides its own: links (address
    shown as ``host/…`` only) and phones' shared calendars, with when each
    was last read and, for a link, what went wrong in plain words; a phone
    not heard from for a day is ``stale`` ("last heard from …"). A phone's
    ``device_id`` is in it for an admin only; ``account_name`` never."""
    admin = await _is_admin(request)
    async with session_scope() as s:
        rows = await calendar_feeds.list_sources(s, admin=admin)
    return [CalendarSource(**r) for r in rows]


async def _source_row(s: Any, source_id: int) -> dict[str, Any]:
    for r in await calendar_feeds.list_sources(s, admin=True):
        if r["id"] == source_id:
            return r
    raise HTTPException(status_code=404, detail=f"calendar {source_id} not found")


async def _check_person(s: Any, person_id: int | None) -> None:
    if person_id is None:
        return
    found = (await s.execute(text("SELECT 1 FROM people WHERE id = :id"), {"id": person_id})).first()
    if found is None:
        raise HTTPException(status_code=422, detail="no such person")


@router.post("/sources", response_model=CalendarSourceRefresh, status_code=201, dependencies=ADMIN)
async def add_source(payload: CalendarSourceCreate) -> CalendarSourceRefresh:
    """Add a calendar by its private ICS / webcal link, then read it once
    (when the internet answer allows). The calendar is kept even when that
    first read fails: the answer says why, in plain words, and the
    refresher keeps trying."""
    try:
        url = calendar_feeds.normalize_feed_url(payload.url)
    except calendar_feeds.FeedUrlRefused as e:
        raise HTTPException(status_code=422, detail=e.words) from None
    host = calendar_feeds.url_host(url)
    async with session_scope() as s:
        await _check_person(s, payload.person_id)
        dup = (
            await s.execute(
                text("SELECT 1 FROM calendar_sources WHERE kind = 'link' AND url = :url"),
                {"url": url},
            )
        ).first()
        if dup is not None:
            raise HTTPException(status_code=409, detail="that calendar is already added")
        source_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO calendar_sources (kind, name, color, person_id, url, url_host)
                    VALUES ('link', :name, :color, :pid, :url, :host)
                    RETURNING id
                    """
                ),
                {"name": payload.name.strip()[: calendar_feeds.MAX_NAME_LEN],
                 "color": calendar_feeds.valid_color(payload.color),
                 "pid": payload.person_id, "url": url, "host": host},
            )
        ).scalar_one()
    async with session_scope() as s:
        result = await calendar_feeds.refresh_link(s, int(source_id))
    async with session_scope() as s:
        row = await _source_row(s, int(source_id))
    return CalendarSourceRefresh(source=CalendarSource(**row), ok=result.ok, events=result.events)


@router.patch("/sources/{source_id}", response_model=CalendarSource, dependencies=ADMIN)
async def patch_source(source_id: int, payload: CalendarSourcePatch) -> CalendarSource:
    """Rename, recolour or re-own a calendar; for a link, also replace its
    address (after the calendar company reset it). A phone's calendar takes
    its name and colour from the phone, so only its owner label changes."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields provided")
    async with session_scope() as s:
        kind = (
            await s.execute(text("SELECT kind FROM calendar_sources WHERE id = :id"), {"id": source_id})
        ).scalar()
        if kind is None:
            raise HTTPException(status_code=404, detail=f"calendar {source_id} not found")
        sets: dict[str, Any] = {}
        if "person_id" in updates:
            await _check_person(s, updates["person_id"])
            sets["person_id"] = updates["person_id"]
        if kind == "phone" and ({"name", "color", "url"} & set(updates)):
            raise HTTPException(
                status_code=409,
                detail="a phone's calendar is named and coloured on that phone",
            )
        if "name" in updates and updates["name"] is not None:
            sets["name"] = updates["name"].strip()[: calendar_feeds.MAX_NAME_LEN]
        if "color" in updates and updates["color"] is not None:
            sets["color"] = calendar_feeds.valid_color(updates["color"])
        if updates.get("url"):
            try:
                url = calendar_feeds.normalize_feed_url(updates["url"])
            except calendar_feeds.FeedUrlRefused as e:
                raise HTTPException(status_code=422, detail=e.words) from None
            sets.update(url=url, url_host=calendar_feeds.url_host(url), etag=None,
                        last_modified=None, failures=0, next_refresh_at=None, last_error=None)
        if sets:
            frag = ", ".join(f"{k} = :{k}" for k in sets)
            await s.execute(
                text(f"UPDATE calendar_sources SET {frag}, updated_at = now() WHERE id = :id"),
                {"id": source_id, **sets},
            )
            await calendar_feeds.notify_changed(s, "source")
    if updates.get("url"):
        async with session_scope() as s:
            await calendar_feeds.refresh_link(s, source_id)
    async with session_scope() as s:
        return CalendarSource(**await _source_row(s, source_id))


@router.delete("/sources/{source_id}", status_code=204, dependencies=ADMIN)
async def delete_source(source_id: int) -> None:
    """Remove a calendar and its events from the house (the calendar
    itself, wherever it lives, is untouched). For a phone's calendar this
    is how one left behind goes (the app uninstalled, the phone lost); a
    phone still sharing it sends it again with its next update."""
    async with session_scope() as s:
        kind = (
            await s.execute(text("SELECT kind FROM calendar_sources WHERE id = :id"), {"id": source_id})
        ).scalar()
        if kind is None:
            raise HTTPException(status_code=404, detail=f"calendar {source_id} not found")
        await s.execute(text("DELETE FROM calendar_sources WHERE id = :id"), {"id": source_id})
        await calendar_feeds.notify_changed(s, "source")


@router.post("/sources/{source_id}/refresh", response_model=CalendarSourceRefresh, dependencies=ADMIN)
async def refresh_source(source_id: int) -> CalendarSourceRefresh:
    """Read a linked calendar now ("refresh now"). Under internet access
    "never" nothing is fetched and the answer says so."""
    async with session_scope() as s:
        kind = (
            await s.execute(text("SELECT kind FROM calendar_sources WHERE id = :id"), {"id": source_id})
        ).scalar()
    if kind is None:
        raise HTTPException(status_code=404, detail=f"calendar {source_id} not found")
    if kind != "link":
        raise HTTPException(status_code=409, detail="a phone's calendar is updated by the phone")
    async with session_scope() as s:
        result = await calendar_feeds.refresh_link(s, source_id)
    if result.busy:
        raise HTTPException(status_code=409, detail="this calendar is being read right now")
    async with session_scope() as s:
        row = await _source_row(s, source_id)
    return CalendarSourceRefresh(source=CalendarSource(**row), ok=result.ok, events=result.events)


# ─── Phones (the Android app's "Share this phone's calendars") ─────────────

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
PHONE_MIN_GAP_SEC = 60.0
# device id -> monotonic time of its last accepted snapshot (per process).
_last_snapshot: dict[str, float] = {}


def _device_id(device_id: str) -> str:
    ident = (device_id or "").strip()
    if not _DEVICE_ID_RE.match(ident):
        raise HTTPException(status_code=422, detail="invalid device id")
    return ident


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _all_day_bound(ms: int) -> datetime:
    """CalendarContract keeps an all-day instance at UTC midnights: the
    DATE is read in UTC and stored as the household's local midnight, like
    every all-day row."""
    d: date = _ms_to_utc(ms).date()
    return household_time.to_utc(datetime.combine(d, dtime()))


def _calendar_key(request: Request) -> str | None:
    raw = (request.headers.get(calendar_feeds.CALENDAR_KEY_HEADER) or "").strip()
    return raw[:200] or None


@router.put("/phones/{device_id}", response_model=PhoneCalendarStored, dependencies=DEVICE)
async def put_phone_snapshot(
    device_id: str, payload: PhoneCalendarSnapshot, request: Request, response: Response
) -> PhoneCalendarStored:
    """The complete set of calendars this phone shares, with their events
    in a rolling window. Each listed calendar's events are replaced;
    a calendar of this phone that is not listed is deleted (unpicked), and
    ``calendars: []`` deletes them all.

    The first snapshot from a phone answers ``calendar_key`` (once); every
    later one must send it in ``X-Domovoi-Calendar-Key`` (403 without).
    410: sharing was turned off after this snapshot's ``generation``; 409:
    an update with a higher ``generation`` is already stored."""
    ident = _device_id(device_id)
    now = time.monotonic()
    last = _last_snapshot.get(ident)
    if last is not None and now - last < PHONE_MIN_GAP_SEC:
        wait = int(PHONE_MIN_GAP_SEC - (now - last)) + 1
        raise HTTPException(
            status_code=429, detail="too soon: one update a minute per phone",
            headers={"Retry-After": str(wait)},
        )
    if len(payload.calendars) > calendar_feeds.PHONE_MAX_CALENDARS:
        raise HTTPException(
            status_code=413,
            detail=f"too many calendars (at most {calendar_feeds.PHONE_MAX_CALENDARS})",
        )
    if any(len(c.events) > calendar_feeds.PHONE_MAX_EVENTS_PER_CALENDAR for c in payload.calendars):
        raise HTTPException(
            status_code=413,
            detail=f"too many events in one calendar (at most {calendar_feeds.PHONE_MAX_EVENTS_PER_CALENDAR})",
        )
    total = sum(len(c.events) for c in payload.calendars)
    if total > calendar_feeds.PHONE_MAX_EVENTS:
        raise HTTPException(
            status_code=413,
            detail=f"too many events (at most {calendar_feeds.PHONE_MAX_EVENTS})",
        )
    seen: set[str] = set()
    calendars: list[calendar_feeds.PhoneCalendar] = []
    for c in payload.calendars:
        if c.calendar_id in seen:
            continue
        seen.add(c.calendar_id)
        events = []
        for ev in c.events:
            if ev.all_day:
                starts = _all_day_bound(ev.starts_at_ms)
                ends = _all_day_bound(ev.ends_at_ms) if ev.ends_at_ms is not None else None
            else:
                starts = _ms_to_utc(ev.starts_at_ms)
                ends = _ms_to_utc(ev.ends_at_ms) if ev.ends_at_ms is not None else None
            if ends is not None and ends < starts:
                ends = None
            events.append(calendar_feeds.EventRow(
                external_id=ev.uid, title=(ev.title or "").strip() or "(no title)",
                starts_at=starts, ends_at=ends, all_day=ev.all_day,
                location=(ev.location or "").strip() or None,
                ical_uid=(ev.ical_uid or "").strip() or None, busy=ev.busy,
            ))
        calendars.append(calendar_feeds.PhoneCalendar(
            calendar_id=c.calendar_id, name=c.name.strip(), color=c.color, events=events,
        ))
    async with session_scope() as s:
        try:
            minted = await calendar_feeds.admit_phone(
                s, ident, key=_calendar_key(request), generation=payload.generation,
            )
        except calendar_feeds.PhoneRefused as e:
            raise HTTPException(status_code=e.status, detail=e.detail) from None
        if payload.phone_name:
            # The name it registers with, for a phone this server hasn't
            # met under that id yet (never renames a known one).
            await s.execute(
                text(
                    "INSERT INTO devices (device_id, name, platform) VALUES (:id, :name, 'android') "
                    "ON CONFLICT (device_id) DO NOTHING"
                ),
                {"id": ident, "name": payload.phone_name.strip()[:80] or "phone"},
            )
        n_cal, n_ev = await calendar_feeds.store_phone_snapshot(
            s, ident, calendars, generation=payload.generation,
        )
    _last_snapshot[ident] = now
    return PhoneCalendarStored(calendars=n_cal, events=n_ev, calendar_key=minted)


@router.delete("/phones/{device_id}", status_code=204, dependencies=DEVICE)
async def delete_phone_calendars(
    device_id: str, request: Request,
    generation: int | None = Query(default=None, ge=0),
) -> None:
    """Every calendar a phone shared, and their events, leave the house.

    From the phone (sharing turned off): with its calendar key (403
    without, once it has one) and, optionally, the ``generation`` it was
    at, so an upload made before this and arriving after it is refused
    (410). From an admin (Settings → Household → Calendars, "remove all of
    this phone's"): no key needed, and the phone's key is forgotten."""
    ident = _device_id(device_id)
    admin = await _is_admin(request)
    async with session_scope() as s:
        try:
            await calendar_feeds.delete_phone(
                s, ident, key=_calendar_key(request), generation=generation, admin=admin,
            )
        except calendar_feeds.PhoneRefused as e:
            raise HTTPException(status_code=e.status, detail=e.detail) from None
    _last_snapshot.pop(ident, None)


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _fetch_event(s: Any, event_id: int) -> Any:
    row = await s.execute(
        text(f"SELECT {_EVENT_COLUMNS} {_EVENT_FROM} WHERE e.id = :id"), {"id": event_id}
    )
    return row.first()


async def _refuse_read_only(s: Any, event_id: int) -> None:
    """409 for an event that came from a link or a phone: the house only
    shows it; it is changed where it lives."""
    row = (
        await s.execute(
            text(
                "SELECT s.name, s.kind FROM calendar_events e "
                "JOIN calendar_sources s ON s.id = e.calendar_source_id WHERE e.id = :id"
            ),
            {"id": event_id},
        )
    ).first()
    if row is not None:
        where = "on that phone" if row[1] == "phone" else "there"
        raise HTTPException(
            status_code=409, detail=f"this event comes from {row[0]}; change it {where}",
        )


def _local_aware(instant: datetime | None) -> datetime | None:
    """An all-day bound with the household's own offset, so its ISO text
    carries the DATE it is ("2026-11-26T00:00:00-05:00"): the same instant
    as stored (a local midnight), readable by date on any client."""
    if instant is None:
        return None
    local = household_time.to_local(instant)
    offset = local - instant.astimezone(timezone.utc).replace(tzinfo=None)
    return local.replace(tzinfo=timezone(offset))


def _row_to_event(r: Any) -> CalendarEvent:
    all_day = bool(r[9])
    starts_at, ends_at = r[2], r[3]
    if all_day:
        starts_at, ends_at = _local_aware(starts_at), _local_aware(ends_at)
    imported = r[10] is not None
    return CalendarEvent(
        id=int(r[0]),
        title=r[1],
        starts_at=starts_at,
        ends_at=ends_at,
        location=r[4],
        description=r[5],
        source=r[6],
        external_id=r[7],
        last_synced_at=r[8],
        all_day=all_day,
        calendar_id=r[10],
        calendar_name=r[11],
        calendar_color=r[12],
        calendar_kind=r[13],
        owner_name=r[14] if imported else None,
        read_only=imported,
        busy=bool(r[15]),
        also_in=sorted(r[16] or []),
    )
