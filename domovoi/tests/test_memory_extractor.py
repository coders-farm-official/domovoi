"""Implicit memory extractor (PR-B) tests.

Covers:
  * ``_parse_extracted_memories`` — well-formed, prose-wrapped, bare
    list, garbage, confidence coercion / clamping.
  * Worker tick:
      - skips people below the threshold
      - runs once threshold crossed and writes pending rows above
        ``memory_extractor_min_confidence``
      - filters low-confidence candidates
      - dedups against existing active/pending rows
      - stamps ``people.last_extracted_at`` on success, leaves it
        alone on Ollama failure
  * ``MemoryHandler.handle_confirmation`` for ``pending_memory_offer``:
    yes flips status to active, no flips to rejected.
  * Router surfacing:
      - pending memory parks ``pending_memory_offer`` with the right
        memory_id
      - self-doubt offer wins ordering when both could fire
      - cooldown suppresses re-surfacing
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.clients.ollama import (
    ExtractedMemory,
    _parse_extracted_memories,
)
from domovoi.db.repositories import (
    ConversationLogRepository,
    MemoriesRepository,
    PeopleRepository,
    SessionRepository,
)
from domovoi.handlers.memory import MemoryHandler
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db
from domovoi.workers.memory_extractor import (
    MemoryExtractor,
    _format_transcript,
)


# ─── Parser ──────────────────────────────────────────────────────────────


def test_parse_extracted_memories_envelope() -> None:
    raw = (
        '{"memories": ['
        '{"fact": "I have a dog named Max", "confidence": 0.9, "topic": "pet"},'
        '{"fact": "I work nights", "confidence": 0.65, "topic": "work"}]}'
    )
    rows = _parse_extracted_memories(raw)
    assert len(rows) == 2
    assert rows[0].fact == "I have a dog named Max"
    assert rows[0].confidence == 0.9
    assert rows[0].topic == "pet"


def test_parse_extracted_memories_bare_list() -> None:
    """Some models drop the envelope and return the array directly."""
    raw = '[{"fact": "I like jazz", "confidence": 0.8, "topic": ""}]'
    rows = _parse_extracted_memories(raw)
    assert len(rows) == 1
    assert rows[0].fact == "I like jazz"
    assert rows[0].topic == ""


def test_parse_extracted_memories_prose_wrapped() -> None:
    raw = (
        "Here are the facts I found: "
        '{"memories": [{"fact": "vegetarian", "confidence": 0.75}]}'
        " Hope that helps."
    )
    rows = _parse_extracted_memories(raw)
    assert len(rows) == 1
    assert rows[0].fact == "vegetarian"


def test_parse_extracted_memories_clamps_confidence() -> None:
    raw = (
        '{"memories": ['
        '{"fact": "out of range high", "confidence": 1.7},'
        '{"fact": "out of range low", "confidence": -0.3},'
        '{"fact": "stringy conf", "confidence": "0.5"}]}'
    )
    rows = _parse_extracted_memories(raw)
    confs = [r.confidence for r in rows]
    assert confs == [1.0, 0.0, 0.5]


def test_parse_extracted_memories_garbage_returns_empty() -> None:
    assert _parse_extracted_memories("not json") == []
    assert _parse_extracted_memories("") == []
    assert _parse_extracted_memories('{"memories": "wrong shape"}') == []


def test_parse_extracted_memories_skips_blank_facts() -> None:
    raw = (
        '{"memories": ['
        '{"fact": "", "confidence": 0.9},'
        '{"fact": "real one", "confidence": 0.9}]}'
    )
    rows = _parse_extracted_memories(raw)
    assert len(rows) == 1
    assert rows[0].fact == "real one"


def test_format_transcript_reverses_to_chronological() -> None:
    """Repo returns newest-first; the formatter reverses to chronological."""
    turns = [
        ("third utterance", "third reply"),
        ("second utterance", "second reply"),
        ("first utterance", "first reply"),
    ]
    rendered = _format_transcript(turns)
    lines = rendered.splitlines()
    assert lines[0].startswith("User: first")
    assert lines[-1].startswith("Assistant: third")


# ─── Worker — extraction flow ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_tick_skips_person_below_threshold(db_session) -> None:
    """No extraction should run when a person has fewer than
    ``memory_extractor_threshold_turns`` new conversation_log rows."""
    person_id = await PeopleRepository(db_session).create(name="Alex")
    # Touch last_seen so they qualify for the active window. The
    # active-window query needs last_seen_at recently set.
    await PeopleRepository(db_session).touch_last_seen(person_id)
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    log_repo = ConversationLogRepository(db_session)
    # Just 5 turns — under the default threshold of 20.
    for i in range(5):
        await log_repo.record_turn(
            session_id=sid,
            room_id="kitchen",
            person_id=person_id,
            user_text=f"turn {i}",
            assistant_text=f"reply {i}",
            matched_handler=None,
            matched_path="qa",
            presence_tier="low",
            online=True,
            latency_ms=10,
        )
    await db_session.commit()

    extract_called = {"count": 0}

    class _PatchedOllama:
        async def extract_memories(self, transcript_block):
            extract_called["count"] += 1
            return []

    with patch(
        "domovoi.workers.memory_extractor.get_ollama_client",
        lambda: _PatchedOllama(),
    ):
        extractor = MemoryExtractor()
        passes = await extractor.tick()

    assert passes == 0
    assert extract_called["count"] == 0


@requires_db
@pytest.mark.asyncio
async def test_tick_extracts_and_writes_pending_above_confidence(
    db_session,
) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await PeopleRepository(db_session).touch_last_seen(person_id)
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    log_repo = ConversationLogRepository(db_session)
    for i in range(25):  # over the default threshold of 20
        await log_repo.record_turn(
            session_id=sid,
            room_id="kitchen",
            person_id=person_id,
            user_text=f"i mentioned a dog at turn {i}",
            assistant_text="ok",
            matched_handler=None,
            matched_path="qa",
            presence_tier="low",
            online=True,
            latency_ms=10,
        )
    await db_session.commit()

    class _PatchedOllama:
        async def extract_memories(self, transcript_block):
            return [
                ExtractedMemory(
                    fact="has a dog named Max", confidence=0.9, topic="pet"
                ),
                ExtractedMemory(
                    fact="low confidence", confidence=0.4, topic=""
                ),
            ]

    with patch(
        "domovoi.workers.memory_extractor.get_ollama_client",
        lambda: _PatchedOllama(),
    ):
        extractor = MemoryExtractor()
        passes = await extractor.tick()

    assert passes == 1

    # Pending rows for the person.
    rows = (
        await db_session.execute(
            text(
                """
                SELECT body, source, status, confidence, topic
                FROM memories WHERE person_id = :pid
                """
            ),
            {"pid": person_id},
        )
    ).all()
    bodies = [r[0] for r in rows]
    assert "has a dog named Max" in bodies
    assert "low confidence" not in bodies  # filtered out
    for r in rows:
        assert r[1] == "implicit"
        assert r[2] == "pending"

    # last_extracted_at stamped.
    stamp = (
        await db_session.execute(
            text("SELECT last_extracted_at FROM people WHERE id = :pid"),
            {"pid": person_id},
        )
    ).scalar()
    assert stamp is not None


@requires_db
@pytest.mark.asyncio
async def test_tick_dedups_against_existing_active_or_pending(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await PeopleRepository(db_session).touch_last_seen(person_id)
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    log_repo = ConversationLogRepository(db_session)
    for i in range(25):
        await log_repo.record_turn(
            session_id=sid,
            room_id="kitchen",
            person_id=person_id,
            user_text="seed transcript",
            assistant_text="ok",
            matched_handler=None,
            matched_path="qa",
            presence_tier="low",
            online=True,
            latency_ms=10,
        )
    # Pre-existing active row that should block the extractor's dupe.
    await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="has a dog named Max",
        source="explicit",
    )
    await db_session.commit()

    class _PatchedOllama:
        async def extract_memories(self, transcript_block):
            return [
                ExtractedMemory(
                    fact="has a dog named Max", confidence=0.95, topic="pet"
                ),
                ExtractedMemory(
                    fact="works nights", confidence=0.85, topic="work"
                ),
            ]

    with patch(
        "domovoi.workers.memory_extractor.get_ollama_client",
        lambda: _PatchedOllama(),
    ):
        await MemoryExtractor().tick()

    rows = (
        await db_session.execute(
            text(
                "SELECT body, status FROM memories WHERE person_id = :pid"
            ),
            {"pid": person_id},
        )
    ).all()
    # The dupe should NOT have been inserted as a new row.
    matches = [r for r in rows if r[0] == "has a dog named Max"]
    assert len(matches) == 1  # only the original active row
    assert matches[0][1] == "active"
    # The novel one DID land as pending.
    assert any(r[0] == "works nights" and r[1] == "pending" for r in rows)


@requires_db
@pytest.mark.asyncio
async def test_tick_does_not_stamp_on_ollama_failure(db_session) -> None:
    """Hard failure should leave last_extracted_at NULL so the next
    tick retries the same window."""
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await PeopleRepository(db_session).touch_last_seen(person_id)
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    log_repo = ConversationLogRepository(db_session)
    for i in range(25):
        await log_repo.record_turn(
            session_id=sid,
            room_id="kitchen",
            person_id=person_id,
            user_text="hello",
            assistant_text="hi",
            matched_handler=None,
            matched_path="qa",
            presence_tier="low",
            online=True,
            latency_ms=10,
        )
    await db_session.commit()

    class _ExplodingOllama:
        async def extract_memories(self, transcript_block):
            raise RuntimeError("ollama down")

    with patch(
        "domovoi.workers.memory_extractor.get_ollama_client",
        lambda: _ExplodingOllama(),
    ):
        await MemoryExtractor().tick()

    stamp = (
        await db_session.execute(
            text("SELECT last_extracted_at FROM people WHERE id = :pid"),
            {"pid": person_id},
        )
    ).scalar()
    assert stamp is None  # NOT stamped — retry next tick


# ─── Confirmation handler ───────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_handle_confirmation_yes_promotes_to_active(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    memory_id = await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="vegetarian",
        source="implicit",
        status="pending",
        confidence=0.85,
    )
    await db_session.commit()

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    response = await handler.handle_confirmation(
        "core.pending_memory_offer",
        {"memory_id": memory_id},
        True,
        ctx,
        db_session,
    )
    assert "remember" in response.text.lower()
    status = (
        await db_session.execute(
            text("SELECT status FROM memories WHERE id = :id"),
            {"id": memory_id},
        )
    ).scalar()
    assert status == "active"


@requires_db
@pytest.mark.asyncio
async def test_handle_confirmation_no_marks_rejected(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    memory_id = await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="works nights",
        source="implicit",
        status="pending",
    )
    await db_session.commit()

    handler = MemoryHandler()
    ctx = Context(room_id="kitchen", person_id=person_id)
    response = await handler.handle_confirmation(
        "core.pending_memory_offer",
        {"memory_id": memory_id},
        False,
        ctx,
        db_session,
    )
    assert "won't save" in response.text.lower()
    status = (
        await db_session.execute(
            text("SELECT status FROM memories WHERE id = :id"),
            {"id": memory_id},
        )
    ).scalar()
    assert status == "rejected"


# ─── Router surfacing ───────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_router_surfaces_pending_memory_on_qa(db_session) -> None:
    """A QA turn for a known speaker with an eligible pending memory
    should append the offer and park a ``pending_memory_offer``
    confirmation."""
    person_id = await PeopleRepository(db_session).create(name="Alex")
    memory_id = await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="has a dog named Max",
        source="implicit",
        status="pending",
    )
    await db_session.commit()

    intent = Intent(transcript="tell me a joke", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=person_id, online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert response.matched_path == "qa"
    assert "should i remember" in response.text.lower()
    pending = (
        await SessionRepository(db_session).get_context(response.session_id)
    ).get("pending_confirmation")
    assert pending and pending["kind"] == "core.pending_memory_offer"
    assert pending["memory_id"] == memory_id


@requires_db
@pytest.mark.asyncio
async def test_router_pending_memory_yields_to_self_doubt_offer(
    db_session,
) -> None:
    """When both could fire, the self-doubt web-search offer wins —
    we never park two pending_confirmations at once."""
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="has a dog named Max",
        source="implicit",
        status="pending",
    )
    await db_session.commit()

    # A current-events volatile question → categorizer flags current_events,
    # triggering the self-doubt offer path. ("what's the latest news" now
    # routes to the dedicated NewsHandler, so use a non-news volatile phrase.)
    intent = Intent(transcript="who is the current president", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=person_id, online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    # Self-doubt won — memory offer didn't surface.
    pending = (
        await SessionRepository(db_session).get_context(response.session_id)
    ).get("pending_confirmation")
    assert pending and pending["kind"] == "core.self_doubt_offer"
    assert "should i remember" not in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_pending_memory_cooldown_suppresses_resurfacing(db_session) -> None:
    """A pending memory that was just offered (last_offered_at = NOW)
    should NOT surface again until the cooldown elapses."""
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await MemoriesRepository(db_session).add(
        person_id=person_id,
        body="vegetarian",
        source="implicit",
        status="pending",
    )
    await db_session.commit()

    intent = Intent(transcript="tell me a joke", room_id="kitchen")
    ctx = Context(room_id="kitchen", person_id=person_id, online=True)

    # First turn surfaces it.
    response1 = await route(intent, ctx, db_session)
    await db_session.commit()
    assert "should i remember" in response1.text.lower()

    # Clear pending_confirmation so the yes/no pre-empt doesn't fire
    # on the next turn. (The Pi's user said something else, not yes/no.)
    await SessionRepository(db_session).set_context_key(
        response1.session_id, "pending_confirmation", None
    )
    await db_session.commit()

    # Second turn: cooldown is in effect (just stamped) — should NOT surface.
    ctx2 = Context(
        room_id="kitchen",
        person_id=person_id,
        online=True,
        session_id=response1.session_id,
    )
    response2 = await route(intent, ctx2, db_session)
    await db_session.commit()
    assert "should i remember" not in response2.text.lower()


# ─── Offers: only at a natural point, rarely, never old news ──────────────
# A live evening: a blood-pressure answer ended "By the way, Waffle and
# Plum are your pets…", and a moon-distance answer minutes later ended
# "By the way, your name is …": each pending fact had its own cooldown,
# so they took turns riding on factual answers, and facts the house knew
# already came back as news.


async def _person_with_pending(db_session, *bodies: str) -> tuple[int, list[int]]:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    ids = [
        await MemoriesRepository(db_session).add(
            person_id=person_id, body=b, source="implicit", status="pending",
        )
        for b in bodies
    ]
    await db_session.commit()
    return person_id, ids


@requires_db
@pytest.mark.asyncio
async def test_no_memory_offer_on_an_answer_to_a_question(db_session) -> None:
    person_id, _ = await _person_with_pending(db_session, "I work nights")
    for asked in (
        "Tell me, how far away is the moon from Earth?",
        "what is a normal blood pressure",
        "tell me about the moon",
    ):
        response = await route(
            Intent(transcript=asked, room_id="kitchen"),
            Context(room_id="kitchen", person_id=person_id, online=True),
            db_session,
        )
        await db_session.commit()
        assert response.matched_path == "qa"
        assert "should i remember" not in response.text.lower(), asked
    offered = (
        await db_session.execute(
            text("SELECT count(*) FROM memories WHERE person_id = :p AND last_offered_at IS NOT NULL"),
            {"p": person_id},
        )
    ).scalar()
    assert offered == 0


@requires_db
@pytest.mark.asyncio
async def test_one_memory_offer_per_person_per_cooldown(db_session) -> None:
    """Two pending facts: the second waits out the person's cooldown
    instead of riding on the very next turn."""
    person_id, _ = await _person_with_pending(db_session, "I work nights", "I like jazz")
    first = await route(
        Intent(transcript="tell me a joke", room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=True),
        db_session,
    )
    await db_session.commit()
    assert "should i remember" in first.text.lower()
    await SessionRepository(db_session).set_context_key(
        first.session_id, "pending_confirmation", None
    )
    await db_session.commit()
    second = await route(
        Intent(transcript="tell me a joke", room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=True,
                session_id=first.session_id),
        db_session,
    )
    await db_session.commit()
    assert "should i remember" not in second.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_known_facts_are_never_offered(db_session) -> None:
    person_id, (name_id, pets_id, news_id) = await _person_with_pending(
        db_session, "My name is Alex", "Waffle and Plum are my pets", "I work nights",
    )
    await MemoriesRepository(db_session).add(
        person_id=person_id, body="My pets are Waffle and Plum", source="explicit",
    )
    await db_session.commit()

    response = await route(
        Intent(transcript="tell me a joke", room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=True),
        db_session,
    )
    await db_session.commit()
    assert "you work nights" in response.text.lower()
    assert "name" not in response.text.lower() and "waffle" not in response.text.lower()
    statuses = dict(
        (
            await db_session.execute(
                text("SELECT id, status FROM memories WHERE person_id = :p"),
                {"p": person_id},
            )
        ).all()
    )
    assert statuses[name_id] == "rejected" and statuses[pets_id] == "rejected"
    assert statuses[news_id] == "pending"
    # The history the model reads next turn has the answer, not the offer.
    ctx_data = await SessionRepository(db_session).get_context(response.session_id)
    said = [t["text"] for t in ctx_data["recent_turns"] if t["role"] == "assistant"]
    assert said and "by the way" not in said[-1].lower()


@requires_db
@pytest.mark.asyncio
async def test_yes_to_a_memory_offer_counts_without_voice_id(db_session) -> None:
    """A one-word "yes" is too short for voice ID: the reply turn has no
    speaker, and the offer used to end in "Got it." with nothing saved."""
    person_id, (memory_id,) = await _person_with_pending(db_session, "I work nights")
    offered = await route(
        Intent(transcript="tell me a joke", room_id="kitchen"),
        Context(room_id="kitchen", person_id=person_id, online=True),
        db_session,
    )
    await db_session.commit()
    assert "should i remember" in offered.text.lower()
    reply = await route(
        Intent(transcript="yes", room_id="kitchen"),
        Context(room_id="kitchen", person_id=None, online=True,
                session_id=offered.session_id),
        db_session,
    )
    await db_session.commit()
    assert "remember" in reply.text.lower()
    status = (
        await db_session.execute(
            text("SELECT status FROM memories WHERE id = :id"), {"id": memory_id}
        )
    ).scalar()
    assert status == "active"


@requires_db
@pytest.mark.asyncio
async def test_tick_skips_known_and_rejected_facts(db_session) -> None:
    person_id = await PeopleRepository(db_session).create(name="Alex")
    await PeopleRepository(db_session).touch_last_seen(person_id)
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    log_repo = ConversationLogRepository(db_session)
    for _ in range(25):
        await log_repo.record_turn(
            session_id=sid, room_id="kitchen", person_id=person_id,
            user_text="seed transcript", assistant_text="ok",
            matched_handler=None, matched_path="qa", presence_tier="low",
            online=True, latency_ms=10,
        )
    await MemoriesRepository(db_session).add(
        person_id=person_id, body="I work nights", source="implicit", status="rejected",
    )
    await db_session.commit()

    class _PatchedOllama:
        async def extract_memories(self, transcript_block):
            return [
                ExtractedMemory(fact="My name is Alex", confidence=0.95, topic=""),
                ExtractedMemory(fact="I work nights", confidence=0.95, topic="work"),
                ExtractedMemory(fact="I like jazz", confidence=0.95, topic="music"),
            ]

    with patch(
        "domovoi.workers.memory_extractor.get_ollama_client",
        lambda: _PatchedOllama(),
    ):
        await MemoryExtractor().tick()

    pending = (
        await db_session.execute(
            text("SELECT body FROM memories WHERE person_id = :p AND status = 'pending'"),
            {"p": person_id},
        )
    ).scalars().all()
    assert pending == ["I like jazz"]
