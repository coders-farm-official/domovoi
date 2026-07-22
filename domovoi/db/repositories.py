from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TimerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        expires_at: datetime,
        label: str | None,
        message: str | None,
        room_id: str | None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO timers (label, message, expires_at, room_id)
                VALUES (:label, :message, :expires_at, :room_id)
                RETURNING id
                """
            ),
            {"label": label, "message": message, "expires_at": expires_at, "room_id": room_id},
        )
        return int(row.scalar_one())

    async def cancel_by_label(self, label: str | None, room_id: str | None) -> int:
        if label is None:
            result = await self.s.execute(
                text("DELETE FROM timers WHERE room_id IS NOT DISTINCT FROM :room_id"),
                {"room_id": room_id},
            )
        else:
            result = await self.s.execute(
                text("DELETE FROM timers WHERE label = :label"),
                {"label": label},
            )
        return result.rowcount or 0

    async def next_active(self, room_id: str | None) -> tuple[int, datetime, str | None] | None:
        row = await self.s.execute(
            text(
                """
                SELECT id, expires_at, label
                FROM timers
                WHERE room_id IS NOT DISTINCT FROM :room_id
                ORDER BY expires_at ASC
                LIMIT 1
                """
            ),
            {"room_id": room_id},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def pop_expired(self) -> list[tuple[int, str | None, str | None, str | None]]:
        """Atomically select + delete all expired timers. Returns (id, label, message, room_id)."""
        result = await self.s.execute(
            text(
                """
                DELETE FROM timers
                WHERE expires_at <= NOW()
                RETURNING id, label, message, room_id
                """
            )
        )
        return [(int(r[0]), r[1], r[2], r[3]) for r in result.all()]


class IntentLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def log(
        self,
        *,
        room_id: str | None,
        transcript: str | None,
        matched_handler: str | None,
        matched_path: str | None,
        online: bool | None,
        latency_ms: int | None,
        person_id: int | None = None,
        presence_tier: str | None = None,
    ) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO intents_log
                    (room_id, transcript, matched_handler, matched_path,
                     online, latency_ms, person_id, presence_tier)
                VALUES
                    (:room_id, :transcript, :matched_handler, :matched_path,
                     :online, :latency_ms, :person_id, :presence_tier)
                """
            ),
            {
                "room_id": room_id,
                "transcript": transcript,
                "matched_handler": matched_handler,
                "matched_path": matched_path,
                "online": online,
                "latency_ms": latency_ms,
                "person_id": person_id,
                "presence_tier": presence_tier,
            },
        )


class ConversationLogRepository:
    """Flat audit log of every routed turn: user transcript + assistant
    response side-by-side, plus the routing metadata for joins back to
    `intents_log` if you need it.

    Lives independent of `sessions.context.recent_turns` (which is
    capped by ``session_recent_turns_cap`` for prompt-budget reasons
    and gets truncated as turns roll off). This table is the
    full-fidelity history — useful for debugging multi-turn issues,
    auditing what the bot said in any room over the last week, or
    feeding a future training dataset.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def latest_assistant_text_in_room(
        self, *, room_id: str | None, within_seconds: int
    ) -> str | None:
        """Most recent non-empty assistant_text spoken in ``room_id`` within
        the trailing ``within_seconds`` window. Used by RepeatHandler as a
        fallback when ``sessions.context.last_assistant_response`` is empty
        (typically because the prior session aged out between turns).
        Returns None if no qualifying row exists.
        """
        result = await self.s.execute(
            text(
                """
                SELECT assistant_text
                FROM conversation_log
                WHERE room_id IS NOT DISTINCT FROM :room_id
                  AND assistant_text IS NOT NULL
                  AND assistant_text <> ''
                  AND at >= NOW() - make_interval(secs => :within_seconds)
                ORDER BY at DESC
                LIMIT 1
                """
            ),
            {"room_id": room_id, "within_seconds": within_seconds},
        )
        row = result.first()
        if row is None:
            return None
        value = row[0]
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    async def record_turn(
        self,
        *,
        session_id: UUID | None,
        room_id: str | None,
        person_id: int | None,
        user_text: str | None,
        assistant_text: str | None,
        matched_handler: str | None,
        matched_path: str | None,
        presence_tier: str | None,
        online: bool | None,
        latency_ms: int | None,
    ) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO conversation_log
                    (session_id, room_id, person_id, user_text,
                     assistant_text, matched_handler, matched_path,
                     presence_tier, online, latency_ms)
                VALUES
                    (:session_id, :room_id, :person_id, :user_text,
                     :assistant_text, :matched_handler, :matched_path,
                     :presence_tier, :online, :latency_ms)
                """
            ),
            {
                "session_id": str(session_id) if session_id else None,
                "room_id": room_id,
                "person_id": person_id,
                "user_text": user_text,
                "assistant_text": assistant_text,
                "matched_handler": matched_handler,
                "matched_path": matched_path,
                "presence_tier": presence_tier,
                "online": online,
                "latency_ms": latency_ms,
            },
        )


class PeopleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(self, *, name: str, notes: str | None = None) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO people (name, notes, last_seen_at)
                VALUES (:name, :notes, NOW())
                RETURNING id
                """
            ),
            {"name": name, "notes": notes},
        )
        return int(row.scalar_one())

    async def touch_last_seen(self, person_id: int) -> None:
        await self.s.execute(
            text("UPDATE people SET last_seen_at = NOW() WHERE id = :id"),
            {"id": person_id},
        )

    async def get(self, person_id: int) -> tuple[int, str, datetime | None] | None:
        row = await self.s.execute(
            text("SELECT id, name, last_seen_at FROM people WHERE id = :id"),
            {"id": person_id},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def list_recently_seen(
        self, since: datetime
    ) -> list[tuple[int, datetime | None]]:
        """Return ``(id, last_extracted_at)`` for every person whose
        ``last_seen_at`` is at-or-after ``since``. Used by the implicit
        memory extractor to decide who's active enough to bother
        scanning — there's no point burning Ollama time on someone
        who hasn't spoken in months."""
        row = await self.s.execute(
            text(
                """
                SELECT id, last_extracted_at
                FROM people
                WHERE last_seen_at IS NOT NULL
                  AND last_seen_at >= :since
                """
            ),
            {"since": since},
        )
        return [(int(r[0]), r[1]) for r in row.all()]

    async def touch_last_extracted(self, person_id: int) -> None:
        """Stamp ``last_extracted_at = NOW()`` after a successful
        extraction pass (whether or not it produced any rows). Failed
        passes (Ollama down, JSON broke, etc.) deliberately do NOT
        stamp, so the next loop tick retries the same window instead
        of silently dropping it."""
        await self.s.execute(
            text(
                """
                UPDATE people SET last_extracted_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": person_id},
        )

    async def latest_seen(self) -> datetime | None:
        """Most recent `last_seen_at` across every known person.

        Drives the presence-tier ladder: if nobody known has spoken in
        ages, an unrecognized voice is far more likely to be a stranger
        and warrants a direct identity prompt.
        """
        row = await self.s.execute(text("SELECT MAX(last_seen_at) FROM people"))
        result = row.scalar()
        return result  # may be None when the people table is empty

    async def delete(self, person_id: int) -> int:
        """Cascades through voice_profiles via FK; intents_log.person_id
        becomes NULL via the FK's ON DELETE SET NULL."""
        result = await self.s.execute(
            text("DELETE FROM people WHERE id = :id"),
            {"id": person_id},
        )
        return result.rowcount or 0

    # ─── Preferences JSONB ─────────────────────────────────────
    # Scalar/atom prefs for the speaker. Anything list-shaped goes in
    # the favorites table; anything free-form goes in memories.

    async def get_preferences(self, person_id: int) -> dict:
        row = await self.s.execute(
            text("SELECT preferences FROM people WHERE id = :id"),
            {"id": person_id},
        )
        result = row.scalar()
        if result is None:
            return {}
        return dict(result) if result else {}

    async def set_preference(self, person_id: int, key: str, value) -> None:
        """Merge a single (key, value) into ``people.preferences``.

        Read-modify-write rather than ``jsonb_set`` because the values
        we store are heterogeneous (strings, bools, small ints) and
        jsonb_set's type discipline is awkward to thread through the
        asyncpg driver. At household scale (a few dozen people max,
        one or two writes per turn) the extra round-trip is invisible.
        """
        import json

        prefs = await self.get_preferences(person_id)
        prefs[key] = value
        await self.s.execute(
            text(
                """
                UPDATE people
                SET preferences = CAST(:prefs AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": person_id, "prefs": json.dumps(prefs)},
        )

    async def delete_preference(self, person_id: int, key: str) -> bool:
        prefs = await self.get_preferences(person_id)
        if key not in prefs:
            return False
        prefs.pop(key, None)
        import json

        await self.s.execute(
            text(
                """
                UPDATE people
                SET preferences = CAST(:prefs AS JSONB)
                WHERE id = :id
                """
            ),
            {"id": person_id, "prefs": json.dumps(prefs)},
        )
        return True


class FavoritesRepository:
    """List-shaped favorites by (person_id, kind).

    Multiple values per (person, kind) supported — a person can have
    several favorite teams without a JSON-array read-modify-write
    dance. Handlers query by kind ("any favorite genres for this
    user? → suggest a playlist"); the web UI lists by person.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        kind: str,
        value: str,
        rank: int = 0,
    ) -> int:
        """Insert (or no-op if the (person, kind, value) already exists).

        Returns the row id either way. Idempotent so handlers don't
        have to special-case "user told me twice" — saying "my favorite
        team is the Mariners" a second time silently succeeds.
        """
        row = await self.s.execute(
            text(
                """
                INSERT INTO favorites (person_id, kind, value, rank)
                VALUES (:person_id, :kind, :value, :rank)
                ON CONFLICT (person_id, kind, value) DO UPDATE
                    SET rank = EXCLUDED.rank
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "kind": kind.strip().lower(),
                "value": value.strip(),
                "rank": rank,
            },
        )
        return int(row.scalar_one())

    async def list_for_person(
        self, person_id: int, kind: str | None = None
    ) -> list[tuple[int, str, str, int]]:
        """Return ``(id, kind, value, rank)`` rows ordered by rank
        then created_at. ``kind=None`` returns every favorite."""
        if kind is None:
            row = await self.s.execute(
                text(
                    """
                    SELECT id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid
                    ORDER BY kind, rank, created_at
                    """
                ),
                {"pid": person_id},
            )
        else:
            row = await self.s.execute(
                text(
                    """
                    SELECT id, kind, value, rank
                    FROM favorites
                    WHERE person_id = :pid AND kind = :kind
                    ORDER BY rank, created_at
                    """
                ),
                {"pid": person_id, "kind": kind.strip().lower()},
            )
        return [(int(r[0]), r[1], r[2], int(r[3])) for r in row.all()]

    async def delete(self, favorite_id: int, person_id: int) -> int:
        """Scoped to (id, person_id) so a web bug can't blow away
        another person's row by ID-guessing."""
        result = await self.s.execute(
            text(
                """
                DELETE FROM favorites
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": favorite_id, "pid": person_id},
        )
        return result.rowcount or 0

    async def delete_by_value(
        self, person_id: int, kind: str, value: str
    ) -> int:
        result = await self.s.execute(
            text(
                """
                DELETE FROM favorites
                WHERE person_id = :pid
                  AND kind = :kind
                  AND lower(value) = lower(:value)
                """
            ),
            {
                "pid": person_id,
                "kind": kind.strip().lower(),
                "value": value.strip(),
            },
        )
        return result.rowcount or 0


class MemoriesRepository:
    """Free-form facts about a speaker.

    Three sources:
      * ``explicit`` — captured via voice ("remember that ___")
      * ``manual``   — captured via the web UI
      * ``implicit`` — surfaced by the (future) extractor worker;
                       lands as ``status='pending'`` so the next turn
                       can prompt for confirmation before going active.

    Only ``status='active'`` rows feed into the QA prompt prefix —
    pending and rejected memories are tracked for audit / dedup.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        body: str,
        source: str,
        status: str = "active",
        topic: str | None = None,
        confidence: float | None = None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO memories
                    (person_id, body, topic, source, status, confidence)
                VALUES
                    (:person_id, :body, :topic, :source, :status, :confidence)
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "body": body.strip(),
                "topic": topic,
                "source": source,
                "status": status,
                "confidence": confidence,
            },
        )
        return int(row.scalar_one())

    async def list_active(
        self, person_id: int, limit: int = 100
    ) -> list[tuple[int, str, str | None, str]]:
        """Return ``(id, body, topic, source)`` rows for the prompt-
        prefix injection. Excludes pending / rejected memories."""
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic, source
                FROM memories
                WHERE person_id = :pid AND status = 'active'
                ORDER BY created_at
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [(int(r[0]), r[1], r[2], r[3]) for r in row.all()]

    async def list_all(
        self, person_id: int, limit: int = 200
    ) -> list[tuple[int, str, str | None, str, str, datetime]]:
        """``(id, body, topic, source, status, created_at)`` — every
        row regardless of status; powers the web UI audit view."""
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic, source, status, created_at
                FROM memories
                WHERE person_id = :pid
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [
            (int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in row.all()
        ]

    async def set_status(
        self, memory_id: int, person_id: int, status: str
    ) -> int:
        """Scoped to person_id so cross-person ID guessing can't flip
        states. Returns rowcount (0 = not found / wrong owner)."""
        result = await self.s.execute(
            text(
                """
                UPDATE memories
                SET status = :status
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": memory_id, "pid": person_id, "status": status},
        )
        return result.rowcount or 0

    async def touch_used(self, memory_ids: list[int]) -> None:
        """Stamp ``last_used_at = NOW()`` on each row that ended up in
        the prompt prefix. Drives later relevance-ranking once we
        cross the prefix-budget threshold."""
        if not memory_ids:
            return
        await self.s.execute(
            text(
                """
                UPDATE memories
                SET last_used_at = NOW()
                WHERE id = ANY(CAST(:ids AS INT[]))
                """
            ),
            {"ids": memory_ids},
        )

    async def delete(self, memory_id: int, person_id: int) -> int:
        result = await self.s.execute(
            text(
                """
                DELETE FROM memories
                WHERE id = :id AND person_id = :pid
                """
            ),
            {"id": memory_id, "pid": person_id},
        )
        return result.rowcount or 0

    async def next_pending_for_offer(
        self,
        person_id: int,
        offer_cooldown_sec: int,
    ) -> tuple[int, str, str | None] | None:
        """Pick the oldest pending memory eligible to surface.

        Eligibility: ``status='pending'`` AND
        ``last_offered_at IS NULL OR last_offered_at < NOW() -
        cooldown``. Returns ``(id, body, topic)`` or None.

        Ordered by created_at so memories the extractor surfaced first
        get asked about first — keeps the conversational ordering
        sensible when several have piled up across a long session.
        """
        row = await self.s.execute(
            text(
                """
                SELECT id, body, topic
                FROM memories
                WHERE person_id = :pid
                  AND status = 'pending'
                  AND (last_offered_at IS NULL
                       OR last_offered_at < NOW() - make_interval(secs => :cooldown))
                ORDER BY created_at
                LIMIT 1
                """
            ),
            {"pid": person_id, "cooldown": offer_cooldown_sec},
        )
        result = row.first()
        if result is None:
            return None
        return int(result[0]), result[1], result[2]

    async def mark_offered(self, memory_id: int) -> None:
        await self.s.execute(
            text(
                """
                UPDATE memories
                SET last_offered_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": memory_id},
        )

    async def count_new_turns_since(
        self, person_id: int, since: datetime | None
    ) -> int:
        """Count ``conversation_log`` rows attributed to ``person_id``
        with ``at > since`` (or all rows if ``since`` is None — first
        extraction pass for this person). Drives the threshold check
        in the extractor worker."""
        if since is None:
            row = await self.s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM conversation_log
                    WHERE person_id = :pid
                    """
                ),
                {"pid": person_id},
            )
        else:
            row = await self.s.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM conversation_log
                    WHERE person_id = :pid AND at > :since
                    """
                ),
                {"pid": person_id, "since": since},
            )
        return int(row.scalar() or 0)

    async def recent_turns_for_extraction(
        self, person_id: int, limit: int
    ) -> list[tuple[str | None, str | None]]:
        """Return up to ``limit`` recent (user_text, assistant_text)
        pairs for the implicit extractor's prompt. Newest first so
        the truncation drops oldest first; the worker reverses the
        list before formatting so the model reads chronologically."""
        row = await self.s.execute(
            text(
                """
                SELECT user_text, assistant_text
                FROM conversation_log
                WHERE person_id = :pid
                ORDER BY at DESC
                LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [(r[0], r[1]) for r in row.all()]

    async def has_similar_active_or_pending(
        self, person_id: int, fragment: str
    ) -> bool:
        """Cheap dedup gate before inserting a pending memory.

        Substring ILIKE comparison both directions — catches the
        common "I have a dog named Max" vs "user has a dog named
        Max" type duplicates without bringing in pg_trgm. Misses
        semantic dupes ("loves jazz" vs "is a jazz fan") — fine; the
        user will see them in the offer flow and can reject."""
        if not fragment.strip():
            return False
        row = await self.s.execute(
            text(
                """
                SELECT 1
                FROM memories
                WHERE person_id = :pid
                  AND status IN ('active', 'pending')
                  AND (
                       body ILIKE :pat
                       OR :fragment ILIKE '%' || body || '%'
                  )
                LIMIT 1
                """
            ),
            {
                "pid": person_id,
                "pat": f"%{fragment.strip()}%",
                "fragment": fragment.strip(),
            },
        )
        return row.first() is not None

    async def find_fuzzy(
        self, person_id: int, fragment: str, limit: int = 5
    ) -> list[tuple[int, str]]:
        """Substring search for the voice "forget that ___" path.

        Case-insensitive ILIKE — at household scale (typically a few
        dozen memories per person max) a full-table ILIKE scan is
        cheap; pg_trgm is overkill here. If memory counts ever climb
        into the thousands per person, swap in trgm similarity
        scoring without changing the call site."""
        if not fragment.strip():
            return []
        row = await self.s.execute(
            text(
                """
                SELECT id, body
                FROM memories
                WHERE person_id = :pid
                  AND status = 'active'
                  AND body ILIKE :pat
                ORDER BY length(body), id
                LIMIT :limit
                """
            ),
            {
                "pid": person_id,
                "pat": f"%{fragment.strip()}%",
                "limit": limit,
            },
        )
        return [(int(r[0]), r[1]) for r in row.all()]


class VoiceProfilesRepository:
    """All speaker embeddings live here. A person can have several rows
    (re-enrollment, different mics / rooms / colds). Match against MAX
    similarity rather than an averaged centroid so a single stable
    sample isn't dragged off-axis by a noisy one.
    """

    EMBEDDING_MODEL_TAG = "resemblyzer-v1"

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        person_id: int,
        embedding: bytes,
        model: str,
        room_id: str | None,
        sample_seconds: float | None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_profiles
                    (person_id, embedding, model, room_id, sample_seconds)
                VALUES
                    (:person_id, :embedding, :model, :room_id, :sample_seconds)
                RETURNING id
                """
            ),
            {
                "person_id": person_id,
                "embedding": embedding,
                "model": model,
                "room_id": room_id,
                "sample_seconds": sample_seconds,
            },
        )
        return int(row.scalar_one())

    async def all_for_model(
        self, model: str
    ) -> list[tuple[int, int, bytes]]:
        """Return ``(profile_id, person_id, embedding_bytes)`` for every
        profile matching ``model``.

        Pulls every row on every match call. At household scale (a few
        dozen samples max) this is microseconds; if it ever becomes hot
        we can move the cosine sim into a postgres extension or cache.
        """
        result = await self.s.execute(
            text(
                """
                SELECT id, person_id, embedding
                FROM voice_profiles
                WHERE model = :model
                """
            ),
            {"model": model},
        )
        return [(int(r[0]), int(r[1]), bytes(r[2])) for r in result.all()]


class VoiceDenylistRepository:
    """Persistent voice opt-out. Embeddings here belong to people
    who explicitly told us not to save their voice. The identifier
    consults this list on every match attempt and suppresses the
    identification result when an embedding falls within match
    threshold of any denylist row, so no downstream prompt fires.

    Schema-wise this is intentionally NOT a relation to ``people`` —
    a denylisted user has explicitly opted out of being a person row.
    """

    EMBEDDING_MODEL_TAG = "resemblyzer-v1"

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        embedding: bytes,
        model: str,
        notes: str | None = None,
    ) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_denylist (embedding, model, notes)
                VALUES (:embedding, :model, :notes)
                RETURNING id
                """
            ),
            {"embedding": embedding, "model": model, "notes": notes},
        )
        return int(row.scalar_one())

    async def all_for_model(self, model: str) -> list[tuple[int, bytes]]:
        """Return ``(id, embedding_bytes)`` for every denylist row at
        the given model tag. Same flat-list pattern as
        VoiceProfilesRepository.all_for_model — household-scale, fast."""
        result = await self.s.execute(
            text(
                """
                SELECT id, embedding
                FROM voice_denylist
                WHERE model = :model
                """
            ),
            {"model": model},
        )
        return [(int(r[0]), bytes(r[1])) for r in result.all()]


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_or_create(self, session_id: UUID | None, room_id: str | None) -> UUID:
        if session_id is not None:
            existing = await self.s.execute(
                text("SELECT id FROM sessions WHERE id = :id"),
                {"id": str(session_id)},
            )
            if existing.first() is not None:
                await self.s.execute(
                    text("UPDATE sessions SET last_activity = NOW() WHERE id = :id"),
                    {"id": str(session_id)},
                )
                return session_id

        new_id = uuid4()
        await self.s.execute(
            text(
                """
                INSERT INTO sessions (id, room_id)
                VALUES (:id, :room_id)
                """
            ),
            {"id": str(new_id), "room_id": room_id},
        )
        return new_id

    async def get_context(self, session_id: UUID) -> dict:
        row = await self.s.execute(
            text("SELECT context FROM sessions WHERE id = :id"),
            {"id": str(session_id)},
        )
        result = row.first()
        if result is None:
            return {}
        return dict(result[0]) if result[0] else {}

    async def record_exchange(
        self,
        session_id: UUID,
        user_text: str,
        assistant_text: str,
        recent_turns_cap: int,
    ) -> None:
        """Append user + assistant turns and update last_assistant_response.

        Read-modify-write on `sessions.context` JSONB. At our QPS this is fine;
        if it ever becomes hot, switch to Postgres jsonb_set/jsonb_array_append.
        """
        from datetime import datetime, timezone
        import json

        now = datetime.now(tz=timezone.utc).isoformat()
        ctx = await self.get_context(session_id)
        recent = list(ctx.get("recent_turns") or [])
        recent.append({"role": "user", "text": user_text, "at": now})
        recent.append({"role": "assistant", "text": assistant_text, "at": now})
        if len(recent) > recent_turns_cap:
            recent = recent[-recent_turns_cap:]

        ctx["recent_turns"] = recent
        ctx["last_assistant_response"] = assistant_text

        await self.s.execute(
            text(
                """
                UPDATE sessions
                SET context = CAST(:ctx AS JSONB), last_activity = NOW()
                WHERE id = :id
                """
            ),
            {"id": str(session_id), "ctx": json.dumps(ctx)},
        )

    async def set_context_key(self, session_id: UUID, key: str, value) -> None:
        """Merge a single key into `sessions.context`. Used for e.g. last_played_track."""
        import json

        ctx = await self.get_context(session_id)
        ctx[key] = value
        await self.s.execute(
            text(
                """
                UPDATE sessions
                SET context = CAST(:ctx AS JSONB), last_activity = NOW()
                WHERE id = :id
                """
            ),
            {"id": str(session_id), "ctx": json.dumps(ctx)},
        )


class VoiceNotesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(self, *, room_id: str | None, body: str) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO voice_notes (room_id, text)
                VALUES (:room_id, :body)
                RETURNING id
                """
            ),
            {"room_id": room_id, "body": body},
        )
        return int(row.scalar_one())

    async def added_within(
        self, *, since: datetime, limit: int = 20
    ) -> list[tuple[int, str | None, str, datetime]]:
        """Return notes (id, room_id, body, created_at) added at-or-after ``since``."""
        result = await self.s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                WHERE created_at >= :since
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"since": since, "limit": limit},
        )
        return [(int(r[0]), r[1], r[2], r[3]) for r in result.all()]

    async def latest(self) -> tuple[int, str | None, str, datetime] | None:
        result = await self.s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        )
        row = result.first()
        if row is None:
            return None
        return int(row[0]), row[1], row[2], row[3]


class WebSearchPrefsRepository:
    """Per-speaker prefs for the proactive web-search offer.

    A row exists for a (person_id, category) once the speaker has
    answered yes or no to at least one offer in that category.
    Anonymous speakers (``person_id IS NULL``) never get a row — the
    caller short-circuits and we offer every time.

    Two pieces of state:
      * ``auto_search`` — once true, future questions in that category
        skip the offer entirely and route straight to the SearxNG
        synth path.
      * ``yes_count`` / ``no_count`` — counters that drive the meta-
        offer ("want me to always check these?") after a threshold of
        yeses. ``prefs_offered_at`` is stamped once we ask, so we
        don't pester the user every turn after the threshold is hit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(
        self, person_id: int, category: str
    ) -> tuple[bool, int, int, datetime | None] | None:
        """Return ``(auto_search, yes_count, no_count, prefs_offered_at)``
        or None if no row exists for this (person, category)."""
        row = await self.s.execute(
            text(
                """
                SELECT auto_search, yes_count, no_count, prefs_offered_at
                FROM web_search_prefs
                WHERE person_id = :person_id AND category = :category
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.first()
        if result is None:
            return None
        return bool(result[0]), int(result[1]), int(result[2]), result[3]

    async def is_auto(self, person_id: int, category: str) -> bool:
        row = await self.s.execute(
            text(
                """
                SELECT auto_search FROM web_search_prefs
                WHERE person_id = :person_id AND category = :category
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.scalar()
        return bool(result) if result is not None else False

    async def record_offer_response(
        self, person_id: int, category: str, affirmative: bool
    ) -> tuple[int, int]:
        """Upsert + bump the matching counter. Returns the new
        ``(yes_count, no_count)`` so the caller can decide whether to
        surface the auto-search meta-offer."""
        column = "yes_count" if affirmative else "no_count"
        row = await self.s.execute(
            text(
                f"""
                INSERT INTO web_search_prefs (person_id, category, {column})
                VALUES (:person_id, :category, 1)
                ON CONFLICT (person_id, category) DO UPDATE
                SET {column} = web_search_prefs.{column} + 1,
                    updated_at = NOW()
                RETURNING yes_count, no_count
                """
            ),
            {"person_id": person_id, "category": category},
        )
        result = row.first()
        if result is None:
            return (0, 0)
        return int(result[0]), int(result[1])

    async def mark_prefs_offered(self, person_id: int, category: str) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO web_search_prefs (person_id, category, prefs_offered_at)
                VALUES (:person_id, :category, NOW())
                ON CONFLICT (person_id, category) DO UPDATE
                SET prefs_offered_at = NOW(),
                    updated_at = NOW()
                """
            ),
            {"person_id": person_id, "category": category},
        )

    async def set_auto(
        self, person_id: int, category: str, auto_search: bool
    ) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO web_search_prefs (person_id, category, auto_search)
                VALUES (:person_id, :category, :auto_search)
                ON CONFLICT (person_id, category) DO UPDATE
                SET auto_search = :auto_search,
                    updated_at = NOW()
                """
            ),
            {
                "person_id": person_id,
                "category": category,
                "auto_search": auto_search,
            },
        )


class ConnectivityEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def log_transition(self, *, connectivity_state: str, target: str) -> None:
        await self.s.execute(
            text(
                """
                INSERT INTO connectivity_events (connectivity_state, target)
                VALUES (:state, :target)
                """
            ),
            {"state": connectivity_state, "target": target},
        )


class ClientGreetingsRepository:
    """Wake-word greetings the satellites play (table client_greetings).
    ``all_enabled`` feeds the canned-sound renderer; the rest back
    the web dashboard's Greetings management page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def all_enabled(self) -> list[tuple[str, str]]:
        """``(text, category)`` for every enabled greeting — what the
        core renders to MP3."""
        row = await self.s.execute(
            text(
                """
                SELECT text, category
                FROM client_greetings
                WHERE enabled
                ORDER BY category, id
                """
            )
        )
        return [(r[0], r[1]) for r in row.all()]

    async def list_all(self) -> list[tuple[int, str, str, bool]]:
        """``(id, text, category, enabled)`` for the management UI."""
        row = await self.s.execute(
            text(
                """
                SELECT id, text, category, enabled
                FROM client_greetings
                ORDER BY category, id
                """
            )
        )
        return [(int(r[0]), r[1], r[2], bool(r[3])) for r in row.all()]

    async def create(self, *, greeting_text: str, category: str, enabled: bool = True) -> int:
        row = await self.s.execute(
            text(
                """
                INSERT INTO client_greetings (text, category, enabled)
                VALUES (:text, :category, :enabled)
                RETURNING id
                """
            ),
            {"text": greeting_text, "category": category, "enabled": enabled},
        )
        return int(row.scalar_one())

    async def update(
        self,
        greeting_id: int,
        *,
        greeting_text: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
    ) -> bool:
        """Patch only the provided fields. Returns True if a row changed."""
        sets: list[str] = []
        params: dict[str, object] = {"id": greeting_id}
        if greeting_text is not None:
            sets.append("text = :text")
            params["text"] = greeting_text
        if category is not None:
            sets.append("category = :category")
            params["category"] = category
        if enabled is not None:
            sets.append("enabled = :enabled")
            params["enabled"] = enabled
        if not sets:
            return False
        row = await self.s.execute(
            text(f"UPDATE client_greetings SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, greeting_id: int) -> bool:
        row = await self.s.execute(
            text("DELETE FROM client_greetings WHERE id = :id"),
            {"id": greeting_id},
        )
        return bool(row.rowcount)


class VoicesRepository:
    """The voice registry (table ``voices``). Each row is a
    user-selectable TTS voice: a spoken ``name``, the ``engine`` that
    renders it (``piper``|``edge``), and a ``model_ref`` locating the
    model (an uploaded ``.onnx`` path or a bare HF name for Piper, the
    Edge voice id for Edge). Exactly one row is ``is_default`` — the voice
    a satellite uses until it reports its own selection.

    Backs the streaming voice resolver, the per-voice clip renderer, the
    VoiceHandler, and the web Voices page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "name": r[1],
            "engine": r[2],
            "model_ref": r[3],
            "is_default": bool(r[4]),
        }

    async def all(self) -> list[dict]:
        """Every registered voice, default first then alphabetical."""
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                ORDER BY is_default DESC, lower(name)
                """
            )
        )
        return [self._row(r) for r in row.all()]

    async def get_by_name(self, name: str) -> dict | None:
        """Case-insensitive name lookup — what people say rarely matches
        the stored casing exactly."""
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                WHERE lower(name) = lower(:name)
                """
            ),
            {"name": name},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_default(self) -> dict | None:
        row = await self.s.execute(
            text(
                """
                SELECT id, name, engine, model_ref, is_default
                FROM voices
                WHERE is_default
                """
            )
        )
        r = row.first()
        return self._row(r) if r else None

    async def count(self) -> int:
        row = await self.s.execute(text("SELECT COUNT(*) FROM voices"))
        return int(row.scalar_one())

    async def create(
        self,
        *,
        name: str,
        engine: str,
        model_ref: str,
        is_default: bool = False,
    ) -> int:
        if is_default:
            await self.s.execute(text("UPDATE voices SET is_default = FALSE WHERE is_default"))
        row = await self.s.execute(
            text(
                """
                INSERT INTO voices (name, engine, model_ref, is_default)
                VALUES (:name, :engine, :model_ref, :is_default)
                RETURNING id
                """
            ),
            {"name": name, "engine": engine, "model_ref": model_ref, "is_default": is_default},
        )
        return int(row.scalar_one())

    async def set_default(self, voice_id: int) -> bool:
        """Make ``voice_id`` the sole default. Clears the flag everywhere
        first so the partial unique index never trips."""
        exists = await self.s.execute(
            text("SELECT 1 FROM voices WHERE id = :id"), {"id": voice_id}
        )
        if exists.first() is None:
            return False
        await self.s.execute(text("UPDATE voices SET is_default = FALSE WHERE is_default"))
        await self.s.execute(
            text(
                "UPDATE voices SET is_default = TRUE, updated_at = NOW() WHERE id = :id"
            ),
            {"id": voice_id},
        )
        return True

    async def update(
        self,
        voice_id: int,
        *,
        name: str | None = None,
        engine: str | None = None,
        model_ref: str | None = None,
    ) -> bool:
        """Patch only the provided fields (default is set via
        :meth:`set_default`). Returns True if a row changed."""
        sets: list[str] = ["updated_at = NOW()"]
        params: dict[str, object] = {"id": voice_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if engine is not None:
            sets.append("engine = :engine")
            params["engine"] = engine
        if model_ref is not None:
            sets.append("model_ref = :model_ref")
            params["model_ref"] = model_ref
        if len(sets) == 1:
            return False
        row = await self.s.execute(
            text(f"UPDATE voices SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, voice_id: int) -> dict | None:
        """Delete and return the removed row (so the caller can clean up an
        uploaded ``.onnx``). Returns None if nothing was deleted. Refuses to
        delete the default voice — callers should set a new default first."""
        row = await self.s.execute(
            text(
                """
                DELETE FROM voices
                WHERE id = :id AND NOT is_default
                RETURNING id, name, engine, model_ref, is_default
                """
            ),
            {"id": voice_id},
        )
        r = row.first()
        return self._row(r) if r else None


class WakeWordsRepository:
    """The custom wake-word registry (table ``wake_words``). Each row
    is a household-trained openWakeWord model: a user-facing ``name``, the
    load-bearing ``slug`` (= ``voice_slug(name)`` — the served
    ``<slug>.onnx``, the Pi's effective wake word, the openWakeWord
    prediction-dict key, and the model file stem all equal it), the spoken
    ``phrase`` the model detects, a detection ``threshold``, and a
    ``model_ref`` locating the trained ``.onnx``.

    A row moves through ``status`` ``recording`` → ``training`` →
    ``ready`` | ``failed`` as positive clips are captured on a satellite,
    handed to the external trainer, and a model lands (or errors). Exactly
    one row may be ``is_default``.

    Backs the wake-recording streaming path, the trainer worker, the
    /v1/wake-models channel, and the web Wake Words page."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "name": r[1],
            "slug": r[2],
            "phrase": r[3],
            "threshold": float(r[4]),
            "model_ref": r[5],
            "is_default": bool(r[6]),
            "status": r[7],
            "source_room_id": r[8],
            "clip_count": int(r[9]),
            "error": r[10],
        }

    _COLS = (
        "id, name, slug, phrase, threshold, model_ref, is_default, "
        "status, source_room_id, clip_count, error"
    )

    async def all(self) -> list[dict]:
        """Every wake word, default first then alphabetical."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                ORDER BY is_default DESC, lower(name)
                """
            )
        )
        return [self._row(r) for r in row.all()]

    async def get(self, wake_word_id: int) -> dict | None:
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE id = :id"),
            {"id": wake_word_id},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_by_name(self, name: str) -> dict | None:
        """Case-insensitive name lookup — what people type rarely matches
        the stored casing exactly."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                WHERE lower(name) = lower(:name)
                """
            ),
            {"name": name},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_by_slug(self, slug: str) -> dict | None:
        """Exact slug lookup. Unlike :class:`VoicesRepository`, wake words
        carry a real ``slug`` column (the load-bearing runtime identifier),
        so the match is exact, not case-folded."""
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE slug = :slug"),
            {"slug": slug},
        )
        r = row.first()
        return self._row(r) if r else None

    async def get_default(self) -> dict | None:
        row = await self.s.execute(
            text(f"SELECT {self._COLS} FROM wake_words WHERE is_default")
        )
        r = row.first()
        return self._row(r) if r else None

    async def count(self) -> int:
        row = await self.s.execute(text("SELECT COUNT(*) FROM wake_words"))
        return int(row.scalar_one())

    async def create(
        self,
        *,
        name: str,
        slug: str,
        phrase: str,
        threshold: float = 0.5,
        source_room_id: str | None = None,
    ) -> int:
        """Insert a fresh wake word in the ``recording`` status. A brand-new
        recording row is never the default (a model must reach ``ready``
        before it can be set default), so there is no default flag to clear
        here. Raises ``IntegrityError`` on a name/slug collision — the caller
        maps that to a 409."""
        row = await self.s.execute(
            text(
                """
                INSERT INTO wake_words (name, slug, phrase, threshold, source_room_id)
                VALUES (:name, :slug, :phrase, :threshold, :source_room_id)
                RETURNING id
                """
            ),
            {
                "name": name,
                "slug": slug,
                "phrase": phrase,
                "threshold": threshold,
                "source_room_id": source_room_id,
            },
        )
        return int(row.scalar_one())

    async def set_default(self, wake_word_id: int) -> bool:
        """Make ``wake_word_id`` the sole default. Refuses (returns False) a
        row that is not yet ``ready`` — you can't default a wake word with no
        trained model. Clears the flag everywhere first so the partial unique
        index never trips."""
        existing = await self.s.execute(
            text("SELECT status FROM wake_words WHERE id = :id"),
            {"id": wake_word_id},
        )
        r = existing.first()
        if r is None or r[0] != "ready":
            return False
        await self.s.execute(text("UPDATE wake_words SET is_default = FALSE WHERE is_default"))
        await self.s.execute(
            text(
                "UPDATE wake_words SET is_default = TRUE, updated_at = NOW() WHERE id = :id"
            ),
            {"id": wake_word_id},
        )
        return True

    async def update(
        self,
        wake_word_id: int,
        *,
        name: str | None = None,
        threshold: float | None = None,
    ) -> bool:
        """Patch only the provided fields (default is set via
        :meth:`set_default`, status via the transition helpers). Returns True
        if a row changed."""
        sets: list[str] = ["updated_at = NOW()"]
        params: dict[str, object] = {"id": wake_word_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if threshold is not None:
            sets.append("threshold = :threshold")
            params["threshold"] = threshold
        if len(sets) == 1:
            return False
        row = await self.s.execute(
            text(f"UPDATE wake_words SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        return bool(row.rowcount)

    async def delete(self, wake_word_id: int) -> dict | None:
        """Delete and return the removed row (so the caller can clean up the
        trained ``.onnx`` and the recorded clips). Returns None if nothing was
        deleted. Refuses to delete the default wake word — callers should set
        a new default first."""
        row = await self.s.execute(
            text(
                f"""
                DELETE FROM wake_words
                WHERE id = :id AND NOT is_default
                RETURNING {self._COLS}
                """
            ),
            {"id": wake_word_id},
        )
        r = row.first()
        return self._row(r) if r else None

    async def bump_clip_count(self, wake_word_id: int) -> int:
        """Record one more captured positive clip and return the new total.
        Called from the streaming clip-write as each clip lands."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET clip_count = clip_count + 1, updated_at = NOW()
                WHERE id = :id
                RETURNING clip_count
                """
            ),
            {"id": wake_word_id},
        )
        return int(row.scalar_one())

    async def reset_for_recording(self, wake_word_id: int) -> bool:
        """Start a FRESH recording take: flip the row back to ``recording``,
        zero ``clip_count``, and clear any prior error. Returns True if a row
        changed. The caller pairs this with truncating the on-disk clip dir so
        the file set and ``clip_count`` start a take in lockstep (each new clip
        then bumps both together) — without it, a re-record would overwrite
        ``clip_001.wav`` while ``clip_count`` kept climbing. Gated by the caller
        to only-``recording``/``failed`` rows so a trained/default word isn't
        silently demoted."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'recording', clip_count = 0, error = NULL,
                    updated_at = NOW()
                WHERE id = :id
                RETURNING id
                """
            ),
            {"id": wake_word_id},
        )
        return row.first() is not None

    async def mark_training(self, wake_word_id: int, min_clips: int) -> bool:
        """Promote a ``recording`` row to ``training`` — only if it still
        holds enough captured clips (``clip_count >= min_clips``). Returns
        False if the row is missing, already past ``recording``, or has too
        few clips, so the caller can surface a 409."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'training', error = NULL, updated_at = NOW()
                WHERE id = :id AND status = 'recording' AND clip_count >= :min
                RETURNING id
                """
            ),
            {"id": wake_word_id, "min": min_clips},
        )
        return row.first() is not None

    async def next_training(self) -> dict | None:
        """Oldest ``training`` row — the trainer worker's queue head."""
        row = await self.s.execute(
            text(
                f"""
                SELECT {self._COLS}
                FROM wake_words
                WHERE status = 'training'
                ORDER BY updated_at
                LIMIT 1
                """
            )
        )
        r = row.first()
        return self._row(r) if r else None

    async def mark_ready(self, wake_word_id: int, model_ref: str) -> bool:
        """The trainer produced a model — flip to ``ready`` and record where
        it landed. Returns True if a row changed."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'ready', model_ref = :model_ref, error = NULL,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": wake_word_id, "model_ref": model_ref},
        )
        return bool(row.rowcount)

    async def mark_failed(self, wake_word_id: int, error: str) -> bool:
        """The trainer errored (or training is unconfigured) — flip to
        ``failed`` and store the reason. Returns True if a row changed."""
        row = await self.s.execute(
            text(
                """
                UPDATE wake_words
                SET status = 'failed', error = :error, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": wake_word_id, "error": error},
        )
        return bool(row.rowcount)


class DropInCallsRepository:
    """Audit trail for two-way drop-in calls. The live pairing lives
    in app.state; this records just the start/end metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def start(self, initiator_room: str, target_room: str) -> int:
        row = await self.s.execute(
            text(
                "INSERT INTO dropin_calls (initiator_room, target_room, status) "
                "VALUES (:i, :t, 'active') RETURNING id"
            ),
            {"i": initiator_room, "t": target_room},
        )
        return int(row.scalar_one())

    async def finish(
        self, call_id: int, *, status: str = "ended", ended_by: str | None = None
    ) -> None:
        await self.s.execute(
            text(
                "UPDATE dropin_calls SET status = :st, ended_at = NOW(), "
                "ended_by = :by, "
                "duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::int "
                "WHERE id = :id AND ended_at IS NULL"
            ),
            {"st": status, "by": ended_by, "id": call_id},
        )


class SatellitePairingRepository:
    """CRUD for `satellite_pairings` — the WS trust-on-first-use rows (V002).

    Only the sha256 of a satellite's pairing token is stored (``token_hash``);
    the raw token lives solely in the Pi's ``~/.domovoi/pairing_token`` sidecar.
    The streaming layer's hello-frame validation drives every method here."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_pairing(
        self, room_id: str
    ) -> tuple[str, datetime, datetime | None] | None:
        """The (token_hash, paired_at, last_seen_at) for a room, or None when
        the room has never paired."""
        row = (
            await self.s.execute(
                text(
                    "SELECT token_hash, paired_at, last_seen_at "
                    "FROM satellite_pairings WHERE room_id = :r"
                ),
                {"r": room_id},
            )
        ).first()
        if row is None:
            return None
        return row.token_hash, row.paired_at, row.last_seen_at

    async def pair(self, room_id: str, token_hash: str) -> None:
        """Claim a room for a token (trust-on-first-use). Idempotent on the
        (room_id, token_hash) pair: a re-pair with the SAME hash refreshes
        paired_at/last_seen_at rather than erroring; a DIFFERENT hash would
        only reach here after the row was reset, so ON CONFLICT overwrites."""
        await self.s.execute(
            text(
                "INSERT INTO satellite_pairings (room_id, token_hash, last_seen_at) "
                "VALUES (:r, :h, now()) "
                "ON CONFLICT (room_id) DO UPDATE SET "
                "token_hash = EXCLUDED.token_hash, paired_at = now(), "
                "last_seen_at = now()"
            ),
            {"r": room_id, "h": token_hash},
        )

    async def touch_last_seen(self, room_id: str) -> None:
        """Stamp last_seen_at=now() for an accepted, already-paired room."""
        await self.s.execute(
            text(
                "UPDATE satellite_pairings SET last_seen_at = now() "
                "WHERE room_id = :r"
            ),
            {"r": room_id},
        )

    async def reset_pairing(self, room_id: str) -> bool:
        """Delete a room's pairing row so the next connect re-pairs (after a
        re-flash / new device). True when a row was actually removed."""
        row = (
            await self.s.execute(
                text(
                    "DELETE FROM satellite_pairings WHERE room_id = :r RETURNING 1"
                ),
                {"r": room_id},
            )
        ).first()
        return row is not None


_SATELLITE_COLS = (
    "room_id, sat_type, room_label, hardware, board, mac, "
    "adopted_via, adopted_at, created_at, updated_at"
)


class SatellitesRepository:
    """CRUD for `satellites` — the inventory/metadata rows (V003).

    A row means "this satellite is known": preseeded by the USB-adoption flow
    (before the device's first WS connect) or self-registered by a `hello`
    frame carrying an explicit ``sat_type``. `room_id` is the satellite's
    identity; `room_label` is optional display grouping for satellites that
    share a physical room. sat_type / adopted_via are app-validated open
    enums (registered_values domains)."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def preseed_upsert(
        self,
        room_id: str,
        *,
        sat_type: str = "voice",
        room_label: str | None = None,
        hardware: str | None = None,
        board: str | None = None,
        mac: str | None = None,
        adopted_via: str = "usb",
    ) -> None:
        """Full-row upsert from the adoption flow; stamps adopted_at=now()."""
        await self.s.execute(
            text(
                "INSERT INTO satellites "
                "(room_id, sat_type, room_label, hardware, board, mac, "
                " adopted_via, adopted_at) "
                "VALUES (:r, :t, :l, :h, :b, :m, :v, now()) "
                "ON CONFLICT (room_id) DO UPDATE SET "
                "sat_type = EXCLUDED.sat_type, room_label = EXCLUDED.room_label, "
                "hardware = EXCLUDED.hardware, board = EXCLUDED.board, "
                "mac = EXCLUDED.mac, adopted_via = EXCLUDED.adopted_via, "
                "adopted_at = now(), updated_at = now()"
            ),
            {
                "r": room_id,
                "t": sat_type,
                "l": room_label,
                "h": hardware,
                "b": board,
                "m": mac,
                "v": adopted_via,
            },
        )

    async def upsert_type(self, room_id: str, sat_type: str) -> None:
        """Self-registration from a `hello` frame that EXPLICITLY carried
        sat_type. Only ever called for explicit frames — an old client that
        omits the field must never funnel through here and reset an adopted
        row back to 'voice'."""
        await self.s.execute(
            text(
                "INSERT INTO satellites (room_id, sat_type, adopted_via) "
                "VALUES (:r, :t, 'hello') "
                "ON CONFLICT (room_id) DO UPDATE SET "
                "sat_type = EXCLUDED.sat_type, updated_at = now()"
            ),
            {"r": room_id, "t": sat_type},
        )

    async def set_room_label(self, room_id: str, room_label: str | None) -> None:
        """Set (or clear, with None) a satellite's display room label.
        Upserts so pre-V003 satellites (mpd_rooms row only) can be labeled."""
        await self.s.execute(
            text(
                "INSERT INTO satellites (room_id, room_label, adopted_via) "
                "VALUES (:r, :l, 'manual') "
                "ON CONFLICT (room_id) DO UPDATE SET "
                "room_label = EXCLUDED.room_label, updated_at = now()"
            ),
            {"r": room_id, "l": room_label},
        )

    async def get(self, room_id: str) -> dict | None:
        row = (
            await self.s.execute(
                text(f"SELECT {_SATELLITE_COLS} FROM satellites WHERE room_id = :r"),
                {"r": room_id},
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def get_by_mac(self, mac: str) -> dict | None:
        """The satellite previously seen with this wlan MAC (lowercased) —
        correlates a re-plugged device with its existing adoption row."""
        row = (
            await self.s.execute(
                text(
                    f"SELECT {_SATELLITE_COLS} FROM satellites "
                    "WHERE mac = :m ORDER BY room_id LIMIT 1"
                ),
                {"m": mac.lower()},
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def all(self) -> list[dict]:
        rows = (
            await self.s.execute(
                text(f"SELECT {_SATELLITE_COLS} FROM satellites ORDER BY room_id")
            )
        ).mappings()
        return [dict(r) for r in rows]

    async def delete(self, room_id: str) -> bool:
        """Remove an inventory row (adopt rollback / remove a never-connected
        satellite). True when a row was actually removed."""
        row = (
            await self.s.execute(
                text("DELETE FROM satellites WHERE room_id = :r RETURNING 1"),
                {"r": room_id},
            )
        ).first()
        return row is not None


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
