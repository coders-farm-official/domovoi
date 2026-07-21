"""People API — enrolled speakers, sessions, conversations, profiles.

People are resolved by the VoiceProfileHandler — each row in the
``people`` table is one identified speaker, and ``voice_profiles``
holds their embedding samples (multiple per person allowed for
re-enrollment / different rooms / etc.). The denylist is a
sibling concept and lives at ``/api/denylist`` via ``denylist.py``.

All endpoints here are read-only or destructive against Postgres; they
touch no live state in the Domovoi server, so they work whether the
Domovoi server process is running or not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from domovoi.config import settings as core_settings
from web.backend.db import session_scope
from web.backend.schemas import (
    ConversationTurn,
    Favorite,
    FavoriteCreate,
    Memory,
    MemoryCreate,
    MemoryPatch,
    Person,
    PreferencesPatch,
    Session,
    VoiceNote,
    VoiceProfile,
)

router = APIRouter(prefix="/api/people", tags=["people"])


# ─── List + detail ─────────────────────────────────────────────────────────


@router.get("", response_model=list[Person])
async def list_people() -> list[Person]:
    """All known speakers, sorted by most-recently-seen.

    Each row carries the count of voice-profile samples and the
    presence tier — same low/medium/high ladder
    VoiceProfileHandler uses to decide identity-prompt urgency.
    """
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT p.id, p.name, p.created_at, p.last_seen_at, p.notes,
                       COALESCE(vc.profile_count, 0) AS profile_count
                FROM people p
                LEFT JOIN (
                    SELECT person_id, COUNT(*) AS profile_count
                    FROM voice_profiles
                    GROUP BY person_id
                ) vc ON vc.person_id = p.id
                ORDER BY p.last_seen_at DESC NULLS LAST, p.id DESC
                """
            )
        )
        return [_row_to_person(r) for r in rows.all()]


@router.get("/{person_id}", response_model=Person)
async def get_person(person_id: int) -> Person:
    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                SELECT p.id, p.name, p.created_at, p.last_seen_at, p.notes,
                       COALESCE(vc.profile_count, 0) AS profile_count
                FROM people p
                LEFT JOIN (
                    SELECT person_id, COUNT(*) AS profile_count
                    FROM voice_profiles WHERE person_id = :id
                    GROUP BY person_id
                ) vc ON vc.person_id = p.id
                WHERE p.id = :id
                """
            ),
            {"id": person_id},
        )
        result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail=f"person {person_id} not found")
    return _row_to_person(result)


# ─── Sessions / conversations / notes / profiles ──────────────────────────


@router.get("/{person_id}/sessions", response_model=list[Session])
async def list_sessions(
    person_id: int, limit: int = Query(default=20, ge=1, le=200)
) -> list[Session]:
    """Sessions this person has appeared in.

    The ``sessions`` table doesn't have a ``person_id`` column —
    speaker identity is per-utterance, not per-session — so we join
    through ``conversation_log`` to find sessions that
    contain at least one turn attributed to this person.
    """
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT s.id::text, s.room_id, s.started_at, s.last_activity,
                       :pid AS person_id, COUNT(cl.id) AS intent_count
                FROM sessions s
                JOIN conversation_log cl ON cl.session_id = s.id
                WHERE cl.person_id = :pid
                GROUP BY s.id, s.room_id, s.started_at, s.last_activity
                ORDER BY s.last_activity DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [
            Session(
                id=r[0],
                room_id=r[1],
                started_at=r[2],
                last_activity=r[3],
                person_id=r[4],
                intent_count=int(r[5]),
            )
            for r in rows.all()
        ]


@router.get("/{person_id}/conversations", response_model=list[ConversationTurn])
async def list_conversations(
    person_id: int, limit: int = Query(default=50, ge=1, le=500)
) -> list[ConversationTurn]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, session_id::text, at, room_id, user_text,
                       assistant_text, matched_handler, matched_path
                FROM conversation_log
                WHERE person_id = :pid
                ORDER BY at DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [_row_to_turn(r) for r in rows.all()]


@router.get("/{person_id}/notes", response_model=list[VoiceNote])
async def list_notes_mentioning(person_id: int) -> list[VoiceNote]:
    """Voice notes that mention this person by name (heuristic ILIKE).

    The ``voice_notes`` table doesn't carry a person_id — notes are
    captured globally — so we surface notes whose body contains the
    person's name as a substring. False positives are likely with
    common names; this is a v1 best-effort search, not a guaranteed
    "notes about X" filter.
    """
    async with session_scope() as s:
        person_row = await s.execute(
            text("SELECT name FROM people WHERE id = :id"), {"id": person_id}
        )
        person = person_row.first()
        if person is None:
            raise HTTPException(status_code=404, detail=f"person {person_id} not found")
        name = person[0]

        rows = await s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                WHERE text ILIKE :name
                ORDER BY created_at DESC
                LIMIT 100
                """
            ),
            {"name": f"%{name}%"},
        )
        return [
            VoiceNote(id=int(r[0]), room_id=r[1], body=r[2], captured_at=r[3])
            for r in rows.all()
        ]


@router.get("/{person_id}/profiles", response_model=list[VoiceProfile])
async def list_profiles(person_id: int) -> list[VoiceProfile]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, person_id, model, enrolled_at, room_id, sample_seconds
                FROM voice_profiles
                WHERE person_id = :pid
                ORDER BY enrolled_at DESC
                """
            ),
            {"pid": person_id},
        )
        return [
            VoiceProfile(
                id=int(r[0]),
                person_id=int(r[1]),
                model=r[2],
                enrolled_at=r[3],
                room_id=r[4],
                sample_seconds=r[5],
            )
            for r in rows.all()
        ]


# ─── Destructive ───────────────────────────────────────────────────────────


@router.delete("/{person_id}", status_code=204)
async def delete_person(person_id: int) -> None:
    """Same SQL as the voice "forget me" path.

    Cascades to ``voice_profiles`` via FK; ``intents_log.person_id``
    and ``conversation_log.person_id`` lapse to NULL via
    ``ON DELETE SET NULL``. Historical audit trail
    stays — anonymized rather than deleted.
    """
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM people WHERE id = :id"), {"id": person_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"person {person_id} not found")


@router.delete("/{person_id}/profiles/{profile_id}", status_code=204)
async def delete_profile(person_id: int, profile_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text(
                """
                DELETE FROM voice_profiles
                WHERE id = :pfid AND person_id = :pid
                """
            ),
            {"pfid": profile_id, "pid": person_id},
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"voice profile {profile_id} not found for person {person_id}",
        )


# ─── Memories / favorites / preferences ────────────────────────────


@router.get("/{person_id}/memories", response_model=list[Memory])
async def list_memories(
    person_id: int,
    status: str | None = Query(default=None),
) -> list[Memory]:
    """List memories for this person. ``status`` filters when set
    (active / pending / rejected); default returns every row so the
    UI audit view can see implicit-pending alongside active."""
    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        if status:
            rows = await s.execute(
                text(
                    """
                    SELECT id, person_id, body, topic, source, status, created_at
                    FROM memories
                    WHERE person_id = :pid AND status = :status
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                ),
                {"pid": person_id, "status": status},
            )
        else:
            rows = await s.execute(
                text(
                    """
                    SELECT id, person_id, body, topic, source, status, created_at
                    FROM memories
                    WHERE person_id = :pid
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                ),
                {"pid": person_id},
            )
        return [
            Memory(
                id=int(r[0]),
                person_id=int(r[1]),
                body=r[2],
                topic=r[3],
                source=r[4],
                status=r[5],
                created_at=r[6],
            )
            for r in rows.all()
        ]


@router.post(
    "/{person_id}/memories", response_model=Memory, status_code=201
)
async def create_memory(person_id: int, payload: MemoryCreate) -> Memory:
    """Manual memory entry from the web UI — always ``source='manual',
    status='active'``. Voice-captured memories go through the
    Domovoi server's MemoryHandler instead."""
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is required")
    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        row = await s.execute(
            text(
                """
                INSERT INTO memories (person_id, body, topic, source, status)
                VALUES (:pid, :body, :topic, 'manual', 'active')
                RETURNING id, person_id, body, topic, source, status, created_at
                """
            ),
            {"pid": person_id, "body": body, "topic": payload.topic},
        )
        result = row.first()
    if result is None:
        raise HTTPException(status_code=500, detail="memory insert returned no row")
    return Memory(
        id=int(result[0]),
        person_id=int(result[1]),
        body=result[2],
        topic=result[3],
        source=result[4],
        status=result[5],
        created_at=result[6],
    )


@router.patch("/{person_id}/memories/{memory_id}", response_model=Memory)
async def patch_memory(
    person_id: int, memory_id: int, payload: MemoryPatch
) -> Memory:
    """Update status, body, or topic.

    The status patch is how the web UI promotes implicit-pending
    memories to active (or rejects them). Body/topic edits cover
    typo fixes and re-tagging.
    """
    sets: list[str] = []
    params: dict = {"id": memory_id, "pid": person_id}
    if payload.status is not None:
        sets.append("status = :status")
        params["status"] = payload.status
    if payload.body is not None:
        body = payload.body.strip()
        if not body:
            raise HTTPException(status_code=400, detail="body cannot be empty")
        sets.append("body = :body")
        params["body"] = body
    if payload.topic is not None:
        sets.append("topic = :topic")
        params["topic"] = payload.topic or None
    if not sets:
        raise HTTPException(status_code=400, detail="no fields to patch")
    sql = (
        "UPDATE memories SET "
        + ", ".join(sets)
        + " WHERE id = :id AND person_id = :pid "
        + "RETURNING id, person_id, body, topic, source, status, created_at"
    )
    async with session_scope() as s:
        row = await s.execute(text(sql), params)
        result = row.first()
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"memory {memory_id} not found for person {person_id}",
        )
    return Memory(
        id=int(result[0]),
        person_id=int(result[1]),
        body=result[2],
        topic=result[3],
        source=result[4],
        status=result[5],
        created_at=result[6],
    )


@router.delete("/{person_id}/memories/{memory_id}", status_code=204)
async def delete_memory(person_id: int, memory_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text(
                """
                DELETE FROM memories
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": memory_id, "pid": person_id},
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"memory {memory_id} not found for person {person_id}",
        )


@router.get("/{person_id}/favorites", response_model=list[Favorite])
async def list_favorites(
    person_id: int, kind: str | None = Query(default=None)
) -> list[Favorite]:
    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        if kind:
            rows = await s.execute(
                text(
                    """
                    SELECT id, person_id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid AND kind = :kind
                    ORDER BY rank, created_at
                    """
                ),
                {"pid": person_id, "kind": kind.strip().lower()},
            )
        else:
            rows = await s.execute(
                text(
                    """
                    SELECT id, person_id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid
                    ORDER BY kind, rank, created_at
                    """
                ),
                {"pid": person_id},
            )
        return [
            Favorite(
                id=int(r[0]),
                person_id=int(r[1]),
                kind=r[2],
                value=r[3],
                rank=int(r[4]),
            )
            for r in rows.all()
        ]


@router.post(
    "/{person_id}/favorites", response_model=Favorite, status_code=201
)
async def create_favorite(person_id: int, payload: FavoriteCreate) -> Favorite:
    kind = (payload.kind or "").strip().lower()
    value = (payload.value or "").strip()
    if not kind or not value:
        raise HTTPException(status_code=400, detail="kind and value are required")
    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        row = await s.execute(
            text(
                """
                INSERT INTO favorites (person_id, kind, value, rank)
                VALUES (:pid, :kind, :value, :rank)
                ON CONFLICT (person_id, kind, value) DO UPDATE
                    SET rank = EXCLUDED.rank
                RETURNING id, person_id, kind, value, rank
                """
            ),
            {
                "pid": person_id,
                "kind": kind,
                "value": value,
                "rank": payload.rank,
            },
        )
        result = row.first()
    if result is None:
        raise HTTPException(status_code=500, detail="favorite insert returned no row")
    return Favorite(
        id=int(result[0]),
        person_id=int(result[1]),
        kind=result[2],
        value=result[3],
        rank=int(result[4]),
    )


@router.delete(
    "/{person_id}/favorites/{favorite_id}", status_code=204
)
async def delete_favorite(person_id: int, favorite_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text(
                """
                DELETE FROM favorites
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": favorite_id, "pid": person_id},
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"favorite {favorite_id} not found for person {person_id}",
        )


@router.get("/{person_id}/preferences")
async def get_preferences(person_id: int) -> dict:
    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        row = await s.execute(
            text("SELECT preferences FROM people WHERE id = :id"),
            {"id": person_id},
        )
        result = row.scalar()
    return dict(result) if result else {}


@router.patch("/{person_id}/preferences")
async def patch_preferences(
    person_id: int, payload: PreferencesPatch
) -> dict:
    """Merge ``set`` and remove ``unset`` keys atomically.

    Read-modify-write inside a single transaction — at household scale
    the contention is zero and the alternative (chained jsonb_set +
    json_object minus) is harder to read than the equivalent dict ops.
    """
    import json

    async with session_scope() as s:
        await _ensure_person_exists(s, person_id)
        row = await s.execute(
            text("SELECT preferences FROM people WHERE id = :id"),
            {"id": person_id},
        )
        current = row.scalar()
        prefs: dict = dict(current) if current else {}
        for k, v in (payload.set or {}).items():
            prefs[k] = v
        for k in payload.unset or []:
            prefs.pop(k, None)
        await s.execute(
            text(
                """
                UPDATE people
                SET preferences = CAST(:prefs AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": person_id, "prefs": json.dumps(prefs)},
        )
    return prefs


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _ensure_person_exists(s, person_id: int) -> None:
    row = await s.execute(
        text("SELECT 1 FROM people WHERE id = :id"), {"id": person_id}
    )
    if row.first() is None:
        raise HTTPException(
            status_code=404, detail=f"person {person_id} not found"
        )


# ─── Helpers ───────────────────────────────────────────────────────────────


def _row_to_person(r: Any) -> Person:
    return Person(
        id=int(r[0]),
        name=r[1],
        created_at=r[2],
        last_seen_at=r[3],
        notes=r[4],
        voice_profile_count=int(r[5]),
        presence_tier=_presence_tier_for(r[3]),
    )


def _row_to_turn(r: Any) -> ConversationTurn:
    return ConversationTurn(
        id=int(r[0]),
        session_id=r[1],
        at=r[2],
        room_id=r[3],
        user_text=r[4],
        assistant_text=r[5],
        matched_handler=r[6],
        matched_path=r[7],
    )


def _presence_tier_for(last_seen_at: datetime | None) -> str:
    """Recompute the urgency ladder used by VoiceProfileHandler.

    Mirrors `voice_identifier._presence_tier` but isolated here so the
    web backend doesn't depend on the identifier (which pulls in the
    embedding stack). If thresholds drift between the two, it's the
    audit-side ledger that matters — fix the identifier, not this.
    """
    if last_seen_at is None:
        return "high"
    now = datetime.now(timezone.utc)
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
    elapsed = (now - last_seen_at).total_seconds()
    if elapsed < core_settings.voice_profile_soft_tier_sec:
        return "low"
    if elapsed < core_settings.voice_profile_hard_tier_sec:
        return "medium"
    return "high"
