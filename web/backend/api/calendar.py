"""Calendar API — full CRUD against ``calendar_events``.

A voice CalendarHandler hasn't landed yet; until it does the web UI is the
sole producer of ``source='local'`` events. When the handler joins
later, it writes to the same table and this UI keeps working.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from web.backend.db import session_scope
from web.backend.schemas import CalendarEvent, CalendarEventCreate, CalendarEventPatch

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/events", response_model=list[CalendarEvent])
async def list_events(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[CalendarEvent]:
    """Events whose ``starts_at`` falls in ``[start, end)``.

    Both bounds optional: omit ``start`` for a "since the dawn of time"
    sweep, omit ``end`` for everything from ``start`` onwards. Default
    cap at 500 keeps the JSON response sane on a year-spanning query.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if start is not None:
        where_clauses.append("starts_at >= :start")
        params["start"] = start
    if end is not None:
        where_clauses.append("starts_at < :end")
        params["end"] = end
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    async with session_scope() as s:
        rows = await s.execute(
            text(
                f"""
                SELECT id, title, starts_at, ends_at, location, description,
                       source, external_id, last_synced_at
                FROM calendar_events
                {where}
                ORDER BY starts_at ASC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_row_to_event(r) for r in rows.all()]


@router.get("/events/{event_id}", response_model=CalendarEvent)
async def get_event(event_id: int) -> CalendarEvent:
    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                SELECT id, title, starts_at, ends_at, location, description,
                       source, external_id, last_synced_at
                FROM calendar_events WHERE id = :id
                """
            ),
            {"id": event_id},
        )
        result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return _row_to_event(result)


@router.post("/events", response_model=CalendarEvent, status_code=201)
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
                RETURNING id, title, starts_at, ends_at, location, description,
                          source, external_id, last_synced_at
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
        result = row.first()
        # Wake any other browser tabs watching the calendar within the
        # same Postgres NOTIFY round-trip (tens of ms) instead of
        # waiting for the next 1.5 s poll. Same transaction as the
        # INSERT so a rolled-back commit doesn't deliver a phantom event.
        await s.execute(text("SELECT pg_notify('calendar_changed', 'created')"))
    if result is None:
        raise HTTPException(status_code=500, detail="insert returned no row")
    return _row_to_event(result)


@router.patch("/events/{event_id}", response_model=CalendarEvent)
async def patch_event(event_id: int, payload: CalendarEventPatch) -> CalendarEvent:
    """Partial update. Only fields explicitly set on the request body
    are written; anything left unset stays as-is."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields provided")

    set_fragments = [f"{k} = :{k}" for k in updates]
    params: dict[str, Any] = {"id": event_id, **updates}

    async with session_scope() as s:
        row = await s.execute(
            text(
                f"""
                UPDATE calendar_events
                SET {', '.join(set_fragments)}
                WHERE id = :id
                RETURNING id, title, starts_at, ends_at, location, description,
                          source, external_id, last_synced_at
                """
            ),
            params,
        )
        result = row.first()
        if result is not None:
            # Don't fire NOTIFY for a 404 PATCH — nothing actually
            # changed.
            await s.execute(text("SELECT pg_notify('calendar_changed', 'updated')"))
    if result is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return _row_to_event(result)


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM calendar_events WHERE id = :id"), {"id": event_id}
        )
        if (result.rowcount or 0) > 0:
            await s.execute(text("SELECT pg_notify('calendar_changed', 'deleted')"))
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")


# ─── Helpers ───────────────────────────────────────────────────────────────


def _row_to_event(r: Any) -> CalendarEvent:
    return CalendarEvent(
        id=int(r[0]),
        title=r[1],
        starts_at=r[2],
        ends_at=r[3],
        location=r[4],
        description=r[5],
        source=r[6],
        external_id=r[7],
        last_synced_at=r[8],
    )
