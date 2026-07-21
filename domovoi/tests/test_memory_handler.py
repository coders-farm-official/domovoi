"""MemoryHandler + profile personalization tests.

Covers:
  * Regex sanity for every fast path (negatives included so reminder /
    voice-profile / note vocabularies don't get poached).
  * Anon-speaker guard — no orphan rows ever get written.
  * Memory CRUD via the handler and via the repository directly.
  * Favorites add / list / forget.
  * Profile-prefix builder including the soft-cap truncation.
  * Router-side prompt-prefix injection (asserts the stub's tagged
    prefix lands in the response when a known speaker has memories).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import (
    FavoritesRepository,
    MemoriesRepository,
    PeopleRepository,
)
from domovoi.handlers.memory import (
    MemoryHandler,
    _FAVORITE_ADD_RE,
    _FAVORITE_FORGET_RE,
    _FORGET_MEMORY_RE,
    _LIST_FAVORITES_RE,
    _LIST_MEMORIES_RE,
    _REMEMBER_RE,
    _singularize,
)
from domovoi.models import Context, Intent
from domovoi.profile_context import (
    PROFILE_PREFIX_SOFT_CAP,
    build_profile_prefix,
)
from domovoi.router import route
from domovoi.tests.conftest import requires_db


# ─── Regex coverage ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "transcript,expected_body",
    [
        ("remember that i like jazz", "i like jazz"),
        ("remember i'm allergic to peanuts", "i'm allergic to peanuts"),
        ("remember this: my anniversary is june third", "my anniversary is june third"),
        (
            "i want you to remember that my dog is named max",
            "my dog is named max",
        ),
    ],
)
def test_remember_regex_positives(transcript: str, expected_body: str) -> None:
    m = _REMEMBER_RE.match(transcript)
    assert m, f"expected match: {transcript!r}"
    assert m.group("body").strip() == expected_body


@pytest.mark.parametrize(
    "transcript",
    [
        # Reminder territory, must NOT match memory.
        "remember to call mom",
        "remember to pick up milk",
        "remember to feed the dog",
        # Unrelated vocabulary.
        "what's the weather",
        "play some jazz",
    ],
)
def test_remember_regex_negatives(transcript: str) -> None:
    assert not _REMEMBER_RE.match(transcript), f"unexpected match: {transcript!r}"


@pytest.mark.parametrize(
    "transcript,expected_body",
    [
        ("forget that i like jazz", "i like jazz"),
        ("forget the fact about my dog", "my dog"),
        ("stop saving that i work nights", "i work nights"),
        ("stop remembering that i'm allergic", "i'm allergic"),
    ],
)
def test_forget_memory_regex_positives(transcript: str, expected_body: str) -> None:
    m = _FORGET_MEMORY_RE.match(transcript)
    assert m, f"expected match: {transcript!r}"
    assert m.group("body").strip() == expected_body


@pytest.mark.parametrize(
    "transcript",
    [
        # VoiceProfileHandler territory — "forget me" / "delete me",
        # no qualifier after.
        "forget me",
        # Reminder / generic phrasing without our qualifier.
        "forget about it",
    ],
)
def test_forget_memory_regex_negatives(transcript: str) -> None:
    assert not _FORGET_MEMORY_RE.match(transcript), f"unexpected match: {transcript!r}"


@pytest.mark.parametrize(
    "transcript,kind,value",
    [
        ("my favorite team is the mariners", "team", "the mariners"),
        ("favorite genre is jazz", "genre", "jazz"),
        ("my favorite sports are basketball and tennis", "sports", "basketball and tennis"),
        ("my favorite ice cream is chocolate", "ice cream", "chocolate"),
    ],
)
def test_favorite_add_regex(transcript: str, kind: str, value: str) -> None:
    m = _FAVORITE_ADD_RE.match(transcript)
    assert m, f"expected match: {transcript!r}"
    assert m.group("kind") == kind
    assert m.group("value") == value


@pytest.mark.parametrize(
    "transcript,kind",
    [
        ("forget my favorite team", "team"),
        ("forget my favorite genres", "genres"),
    ],
)
def test_favorite_forget_regex(transcript: str, kind: str) -> None:
    m = _FAVORITE_FORGET_RE.match(transcript)
    assert m, f"expected match: {transcript!r}"
    assert m.group("kind") == kind


@pytest.mark.parametrize(
    "transcript",
    [
        "what do you remember about me",
        "what do you know about me",
        "what did i tell you to remember",
    ],
)
def test_list_memories_regex(transcript: str) -> None:
    assert _LIST_MEMORIES_RE.match(transcript), f"expected match: {transcript!r}"


@pytest.mark.parametrize(
    "transcript,kind",
    [
        ("what are my favorites", None),
        ("what's my favorite team", "team"),
        ("what are my favorite genres", "genres"),
    ],
)
def test_list_favorites_regex(transcript: str, kind: str | None) -> None:
    m = _LIST_FAVORITES_RE.match(transcript)
    assert m, f"expected match: {transcript!r}"
    assert m.group("kind") == kind


def test_singularize_basic_vocab() -> None:
    assert _singularize("teams") == "team"
    assert _singularize("favorites") == "favorite"
    assert _singularize("genre") == "genre"
    assert _singularize("class") == "class"  # double-s preserved
    assert _singularize("Mariners") == "mariner"


# ─── Anon-speaker guard ───────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_anon_speaker_remember_returns_guidance(db_session) -> None:
    handler = MemoryHandler()
    intent = Intent(transcript="remember that i like jazz", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=None)

    m = _REMEMBER_RE.match(intent.transcript)
    assert m is not None
    response = await handler._add_memory_from_match(m, ctx, db_session)
    assert "don't know who you are" in response.text.lower()

    # And no orphan row.
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM memories"))
    ).scalar()
    assert count == 0


@requires_db
@pytest.mark.asyncio
async def test_anon_speaker_favorite_returns_guidance(db_session) -> None:
    handler = MemoryHandler()
    intent = Intent(
        transcript="my favorite team is the mariners", room_id="kitchen"
    )
    ctx = Context(room_id="kitchen", person_id=None)
    m = _FAVORITE_ADD_RE.match(intent.transcript)
    assert m is not None
    response = await handler._add_favorite_from_match(m, ctx, db_session)
    assert "don't know who you are" in response.text.lower()
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM favorites"))
    ).scalar()
    assert count == 0


# ─── Memory CRUD ──────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_remember_writes_memory_row(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)

    m = _REMEMBER_RE.match("remember that i'm allergic to peanuts")
    assert m is not None
    response = await handler._add_memory_from_match(m, ctx, db_session)

    assert "remember" in response.text.lower()
    rows = await MemoriesRepository(db_session).list_active(person_id)
    assert len(rows) == 1
    assert "allergic to peanuts" in rows[0][1]
    assert rows[0][3] == "explicit"


@requires_db
@pytest.mark.asyncio
async def test_forget_deletes_unique_match(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = MemoriesRepository(db_session)
    await repo.add(person_id=person_id, body="I like jazz", source="explicit")
    await repo.add(person_id=person_id, body="my dog is named Max", source="explicit")

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    m = _FORGET_MEMORY_RE.match("forget that i like jazz")
    assert m is not None
    response = await handler._forget_memory_from_match(m, ctx, db_session)

    assert "forgotten" in response.text.lower()
    remaining = await repo.list_active(person_id)
    bodies = [b for _, b, _, _ in remaining]
    assert any("max" in b.lower() for b in bodies)
    assert not any("jazz" in b.lower() for b in bodies)


@requires_db
@pytest.mark.asyncio
async def test_forget_with_ambiguous_match_asks_to_specify(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = MemoriesRepository(db_session)
    # Two memories both containing "dog".
    await repo.add(person_id=person_id, body="my dog is named Max", source="explicit")
    await repo.add(person_id=person_id, body="my dog is afraid of thunder", source="explicit")

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    m = _FORGET_MEMORY_RE.match("forget the fact about my dog")
    assert m is not None
    response = await handler._forget_memory_from_match(m, ctx, db_session)
    assert "more specific" in response.text.lower()
    # Nothing deleted.
    rows = await repo.list_active(person_id)
    assert len(rows) == 2


@requires_db
@pytest.mark.asyncio
async def test_list_memories_renders_known_facts(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = MemoriesRepository(db_session)
    await repo.add(person_id=person_id, body="I like jazz", source="explicit")
    await repo.add(person_id=person_id, body="I have a dog named Max", source="explicit")

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    m = _LIST_MEMORIES_RE.match("what do you remember about me")
    assert m is not None
    response = await handler._list_memories_from_match(m, ctx, db_session)
    text = response.text.lower()
    assert "jazz" in text
    assert "max" in text


# ─── Favorites ────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_favorite_add_then_list(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)

    m1 = _FAVORITE_ADD_RE.match("my favorite team is the mariners")
    assert m1 is not None
    await handler._add_favorite_from_match(m1, ctx, db_session)
    m2 = _FAVORITE_ADD_RE.match("my favorite team is the sounders")
    assert m2 is not None
    await handler._add_favorite_from_match(m2, ctx, db_session)
    m3 = _FAVORITE_ADD_RE.match("my favorite genre is jazz")
    assert m3 is not None
    await handler._add_favorite_from_match(m3, ctx, db_session)

    rows = await FavoritesRepository(db_session).list_for_person(person_id)
    kinds = sorted({r[1] for r in rows})
    assert kinds == ["genre", "team"]
    teams = [r[2] for r in rows if r[1] == "team"]
    assert "the mariners" in teams and "the sounders" in teams

    # List filtered by kind.
    m_list = _LIST_FAVORITES_RE.match("what are my favorite teams")
    assert m_list is not None
    response = await handler._list_favorites_from_match(m_list, ctx, db_session)
    assert "mariners" in response.text.lower()
    assert "sounders" in response.text.lower()
    # Genre shouldn't leak into a team-scoped listing.
    assert "jazz" not in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_favorite_forget_by_kind_clears_all_values(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = FavoritesRepository(db_session)
    await repo.add(person_id=person_id, kind="team", value="The Mariners")
    await repo.add(person_id=person_id, kind="team", value="The Sounders")
    await repo.add(person_id=person_id, kind="genre", value="jazz")

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    m = _FAVORITE_FORGET_RE.match("forget my favorite teams")
    assert m is not None
    response = await handler._forget_favorite_from_match(m, ctx, db_session)
    assert "cleared 2" in response.text.lower()
    remaining = await repo.list_for_person(person_id)
    # Only the genre should survive.
    assert len(remaining) == 1
    assert remaining[0][1] == "genre"


# ─── People preferences JSONB ─────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_people_preferences_set_get_delete(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = PeopleRepository(db_session)

    assert await repo.get_preferences(person_id) == {}
    await repo.set_preference(person_id, "timezone", "America/Los_Angeles")
    await repo.set_preference(person_id, "tts_voice", "en-US-AriaNeural")
    prefs = await repo.get_preferences(person_id)
    assert prefs["timezone"] == "America/Los_Angeles"
    assert prefs["tts_voice"] == "en-US-AriaNeural"

    removed = await repo.delete_preference(person_id, "tts_voice")
    assert removed is True
    prefs = await repo.get_preferences(person_id)
    assert "tts_voice" not in prefs
    assert prefs.get("timezone") == "America/Los_Angeles"


# ─── Profile prefix builder ───────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_build_profile_prefix_anon_returns_empty(db_session) -> None:
    assert (await build_profile_prefix(None, db_session)) == ""


@requires_db
@pytest.mark.asyncio
async def test_build_profile_prefix_includes_name_memories_favorites(
    db_session,
) -> None:
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    await MemoriesRepository(db_session).add(
        person_id=person_id, body="I have a dog named Max", source="explicit"
    )
    await FavoritesRepository(db_session).add(
        person_id=person_id, kind="genre", value="jazz"
    )
    await PeopleRepository(db_session).set_preference(
        person_id, "timezone", "America/Los_Angeles"
    )

    blob = await build_profile_prefix(person_id, db_session)
    assert "Kamron" in blob
    assert "Max" in blob
    assert "jazz" in blob
    assert "America/Los_Angeles" in blob


@requires_db
@pytest.mark.asyncio
async def test_build_profile_prefix_excludes_pending_memories(db_session) -> None:
    """Implicit-extracted memories sitting in pending must NOT leak
    into the QA prefix — they're awaiting user confirmation."""
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    repo = MemoriesRepository(db_session)
    await repo.add(
        person_id=person_id,
        body="confirmed fact about kamron",
        source="explicit",
        status="active",
    )
    await repo.add(
        person_id=person_id,
        body="unconfirmed guess about kamron",
        source="implicit",
        status="pending",
    )
    blob = await build_profile_prefix(person_id, db_session)
    assert "confirmed fact" in blob
    assert "unconfirmed guess" not in blob


@requires_db
@pytest.mark.asyncio
async def test_build_profile_prefix_truncates_over_soft_cap(db_session) -> None:
    """A very long memory should produce a truncated blob with the
    trailing ellipsis sentinel."""
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    big_body = "lorem ipsum " * 400  # well past 2000 chars
    await MemoriesRepository(db_session).add(
        person_id=person_id, body=big_body, source="explicit"
    )
    blob = await build_profile_prefix(person_id, db_session)
    assert len(blob) <= PROFILE_PREFIX_SOFT_CAP + 3  # trailing "..."
    assert blob.endswith("...")


# ─── Router prefix injection ─────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_router_threads_profile_prefix_into_qa(db_session) -> None:
    """End-to-end: a question that falls through to QA for a known
    speaker should have the profile prefix threaded into the stubbed
    qa_with_uncertainty call — the stub tags it so we can assert on
    the response text."""
    person_id = await PeopleRepository(db_session).create(name="Kamron")
    await MemoriesRepository(db_session).add(
        person_id=person_id, body="I like jazz", source="explicit"
    )

    intent = Intent(transcript="tell me about a song", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=person_id, online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_path == "qa"
    # The stub embeds the prefix in the answer when present.
    assert "[profile:" in response.text


@requires_db
@pytest.mark.asyncio
async def test_router_no_prefix_for_anon_speaker(db_session) -> None:
    intent = Intent(transcript="tell me a joke", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=None, online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert "[profile:" not in response.text
