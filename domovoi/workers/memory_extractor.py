"""Implicit-memory extractor.

Outer loop polls every ``memory_extractor_loop_sec`` (default 60s).
Per loop, for every person ``last_seen_at`` within the active window
(7 days), checks if ``conversation_log.at > people.last_extracted_at``
has accumulated at least ``memory_extractor_threshold_turns`` new
rows. If yes, runs one extraction pass:

1. Pull up to ``memory_extractor_max_turns_per_pass`` recent turns
   for that person from ``conversation_log``.
2. Format chronologically as a transcript block and call
   ``ollama_client.extract_memories(...)``.
3. Filter to ``confidence >= memory_extractor_min_confidence``.
4. Dedup each fact against existing active/pending memories.
5. Insert survivors as ``source='implicit', status='pending'``.
6. Stamp ``people.last_extracted_at = NOW()`` regardless of how many
   rows landed — the threshold window has been "consumed."

Stamping only happens on a SUCCESSFUL pass (Ollama returned a usable
list, even an empty one). Hard failures (Ollama unreachable, parse
broke) leave ``last_extracted_at`` alone so the next tick retries the
same window instead of silently dropping it.

The router surfaces these pending memories on the next QA turn from
the same speaker via ``MemoryHandler.handle_confirmation`` — see
``router._maybe_offer_pending_memory``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from domovoi.clients.ollama import get_ollama_client
from domovoi.config import settings
from domovoi.db.repositories import MemoriesRepository, PeopleRepository
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


# Don't bother extracting for people who haven't been heard from in a
# week. Reduces wasted Ollama time on guests / inactive household
# members and keeps the per-tick scan size bounded.
_ACTIVE_WINDOW = timedelta(days=7)


class MemoryExtractor(Worker):
    # Declarative registration (design §4.5): gated by
    # memory_extractor_enabled, suppressed under stubs so the suite never
    # kicks off Ollama calls (tick() is unit-tested with patched clients).
    name = "memory_extractor"
    enabled_setting = "memory_extractor_enabled"
    interval_setting = "memory_extractor_loop_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        """One pass over every active person. Returns the number of
        extraction passes that ran this tick (one per person who
        crossed the threshold) — useful for tests and instrumentation.
        """
        since = datetime.now(tz=timezone.utc) - _ACTIVE_WINDOW
        async with session_scope() as session:
            people_repo = PeopleRepository(session)
            people = await people_repo.list_recently_seen(since)
        passes = 0
        for person_id, last_extracted_at in people:
            try:
                ran = await self._maybe_extract_for_person(
                    person_id, last_extracted_at
                )
                if ran:
                    passes += 1
            except Exception as e:
                log.exception(
                    "memory extractor pass for person %s failed: %s",
                    person_id, e,
                )
        return passes

    async def _maybe_extract_for_person(
        self, person_id: int, last_extracted_at: datetime | None
    ) -> bool:
        """Run a single extraction pass for ``person_id`` if the new-
        conversation threshold has been crossed. Returns True if an
        extraction ran (success or no-op-result), False if the
        threshold wasn't met."""
        async with session_scope() as session:
            memories_repo = MemoriesRepository(session)
            new_turns = await memories_repo.count_new_turns_since(
                person_id, last_extracted_at
            )
            if new_turns < settings.memory_extractor_threshold_turns:
                return False
            turns = await memories_repo.recent_turns_for_extraction(
                person_id, limit=settings.memory_extractor_max_turns_per_pass
            )

        # Build the prompt outside the session; the Ollama call can
        # take several seconds and there's no reason to hold an open
        # transaction during it.
        transcript_block = _format_transcript(turns)
        if not transcript_block.strip():
            # Nothing usable to extract from. Still stamp so we don't
            # rescan the same empty window every tick.
            async with session_scope() as session:
                await PeopleRepository(session).touch_last_extracted(person_id)
            return True

        client = get_ollama_client()
        try:
            candidates = await client.extract_memories(transcript_block)
        except Exception as e:
            log.warning(
                "extract_memories Ollama call failed for person %s: %s",
                person_id, e,
            )
            return False  # don't stamp — retry next tick

        kept = [
            c for c in candidates
            if c.confidence >= settings.memory_extractor_min_confidence
            and c.fact.strip()
        ]

        async with session_scope() as session:
            memories_repo = MemoriesRepository(session)
            inserted = 0
            for cand in kept:
                if await memories_repo.has_similar_active_or_pending(
                    person_id, cand.fact
                ):
                    log.debug(
                        "memory dedup: skipping %r for person %s",
                        cand.fact, person_id,
                    )
                    continue
                await memories_repo.add(
                    person_id=person_id,
                    body=cand.fact,
                    source="implicit",
                    status="pending",
                    topic=cand.topic or None,
                    confidence=cand.confidence,
                )
                inserted += 1
            await PeopleRepository(session).touch_last_extracted(person_id)

        log.info(
            "memory extractor: person %s — %d candidates, %d kept above conf %.2f, %d new pending",
            person_id,
            len(candidates),
            len(kept),
            settings.memory_extractor_min_confidence,
            inserted,
        )
        return True


def _format_transcript(
    turns: list[tuple[str | None, str | None]],
) -> str:
    """Reverse newest-first rows into chronological order and render
    as ``User: ___\\nAssistant: ___`` blocks. Empty halves are skipped
    so a row that captured only the user side isn't padded with blank
    assistant lines that the model would treat as a refusal."""
    lines: list[str] = []
    for user_text, assistant_text in reversed(turns):
        u = (user_text or "").strip()
        a = (assistant_text or "").strip()
        if u:
            lines.append(f"User: {u}")
        if a:
            lines.append(f"Assistant: {a}")
    return "\n".join(lines)
