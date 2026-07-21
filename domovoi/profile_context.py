"""Per-speaker profile context — assembled blob for QA prompt-prefix.

Pulls a known speaker's active memories + favorites + relevant
preferences from the personalization tables and renders them as a compact
``User context: ...`` string the router prepends to the QA system
prompt.

Anonymous speakers (``person_id`` is None) get ``""`` back so the
caller can no-op cleanly without branching.

Sizing:

* At household scale (a few dozen memories per person, ~10
  favorites) the rendered blob is well under 2000 characters and
  easily fits in any Ollama context window.
* The ``PROFILE_PREFIX_SOFT_CAP`` constant defines the soft ceiling.
  When the assembled blob exceeds it we log a warning and truncate;
  the longer-term plan is to switch to per-turn relevance retrieval
  past that cap. Keeping the truncation here means callers don't
  need to care.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import (
    FavoritesRepository,
    MemoriesRepository,
    PeopleRepository,
)

log = logging.getLogger(__name__)


# 2000 chars ≈ 500 tokens — small fraction of an 8k context.
# Past this we log a warning; over the longer term we'd swap in
# relevance-scored retrieval (see ``MemoriesRepository.touch_used``).
PROFILE_PREFIX_SOFT_CAP = 2000


# Preference keys the prefix surfaces. Other keys in the JSONB blob
# (UI-only knobs, etc.) are intentionally excluded from the prompt to
# keep the prefix compact and on-topic.
_PROMPT_RELEVANT_PREF_KEYS: tuple[str, ...] = (
    "timezone",
    "tts_voice",  # only here because the QA model occasionally riffs
                  # on it ("I'll keep using that voice")
)


async def build_profile_prefix(
    person_id: int | None,
    session: AsyncSession,
) -> str:
    """Render the speaker's profile as a single "User context: ___" blob.

    Returns ``""`` for anonymous speakers — callers can blindly
    concatenate without a None check.

    Truncates to PROFILE_PREFIX_SOFT_CAP with a warning log on
    overflow. Used memory ids are NOT stamped here — the router does
    that after the response is materialized so we don't double-write
    on the offline path.
    """
    if person_id is None:
        return ""

    memories_repo = MemoriesRepository(session)
    favorites_repo = FavoritesRepository(session)
    people_repo = PeopleRepository(session)

    # Pull sequentially — same async session, SQLAlchemy serializes
    # these under its session lock anyway, and at household scale the
    # round-trip cost is sub-10ms total.
    person_row = await people_repo.get(person_id)
    memory_rows = await memories_repo.list_active(person_id, limit=100)
    favorite_rows = await favorites_repo.list_for_person(person_id)
    preferences = await people_repo.get_preferences(person_id)

    person_name = person_row[1] if person_row else None

    if not memory_rows and not favorite_rows and not preferences and not person_name:
        return ""

    lines: list[str] = ["User context (the person speaking to you):"]
    if person_name:
        lines.append(f"  Name: {person_name}")
    if memory_rows:
        bodies = [b for _id, b, _t, _s in memory_rows]
        lines.append("  Memories: " + "; ".join(bodies))
    if favorite_rows:
        by_kind: dict[str, list[str]] = {}
        for _i, kind, value, _r in favorite_rows:
            by_kind.setdefault(kind, []).append(value)
        fav_parts = [
            f"{kind}={', '.join(values)}" for kind, values in by_kind.items()
        ]
        lines.append("  Favorites: " + "; ".join(fav_parts))
    pref_parts: list[str] = []
    for key in _PROMPT_RELEVANT_PREF_KEYS:
        if key in preferences and preferences[key] not in (None, ""):
            pref_parts.append(f"{key}={preferences[key]}")
    if pref_parts:
        lines.append("  Preferences: " + ", ".join(pref_parts))
    lines.append(
        "Use this context to personalize your response when it's "
        "relevant; ignore it when it isn't."
    )
    blob = "\n".join(lines)

    if len(blob) > PROFILE_PREFIX_SOFT_CAP:
        log.warning(
            "profile prefix for person_id=%s is %d chars "
            "(soft cap %d) — truncating. consider implicit-memory "
            "pruning or retrieval-mode switch.",
            person_id,
            len(blob),
            PROFILE_PREFIX_SOFT_CAP,
        )
        blob = blob[: PROFILE_PREFIX_SOFT_CAP].rstrip() + "..."
    return blob


def used_memory_ids(memory_rows: list[tuple[int, str, str | None, str]]) -> list[int]:
    """Helper for the router's ``touch_used`` post-step. Kept here so
    the prefix shape (which rows ended up in the prompt) and the
    "what was used" stamp stay co-located."""
    return [r[0] for r in memory_rows]
