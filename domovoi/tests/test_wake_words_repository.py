"""WakeWordsRepository — the custom wake-word registry.

Exercises the recording→training→ready|failed lifecycle, the
single-default invariant (with the extra "default only when ready" guard
that the voices registry doesn't have), case-insensitive name lookup,
exact slug lookup, the clip-count bump, the training-eligibility gate
(``mark_training`` refuses below ``min_clips``), the ``next_training``
queue ordering, and the refuse-to-delete-the-default guard.

``wake_words`` is reference-ish data and is deliberately NOT in the
per-test truncate list (mirroring ``voices``), so this test clears the
table itself in a fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import WakeWordsRepository
from domovoi.tests.conftest import requires_db


@pytest.fixture
async def repo(db_session):
    await db_session.execute(text("DELETE FROM wake_words"))
    return WakeWordsRepository(db_session)


@requires_db
@pytest.mark.asyncio
async def test_create_and_lookup(repo):
    wid = await repo.create(
        name="Hey Domovoi",
        slug="hey_domovoi",
        phrase="hey domovoi",
        threshold=0.6,
        source_room_id="kitchen",
    )
    assert wid > 0

    # A brand-new row is in 'recording', non-default, zero clips.
    got = await repo.get(wid)
    assert got is not None
    assert got["name"] == "Hey Domovoi"
    assert got["slug"] == "hey_domovoi"
    assert got["phrase"] == "hey domovoi"
    assert got["threshold"] == pytest.approx(0.6)
    assert got["status"] == "recording"
    assert got["is_default"] is False
    assert got["clip_count"] == 0
    assert got["source_room_id"] == "kitchen"
    assert got["model_ref"] is None
    assert got["error"] is None

    # Case-insensitive name lookup; exact slug lookup.
    assert (await repo.get_by_name("hey domovoi"))["id"] == wid
    assert (await repo.get_by_slug("hey_domovoi"))["id"] == wid
    assert await repo.get_by_name("nope") is None
    # Slug match is exact, not case-folded.
    assert await repo.get_by_slug("HEY_DOMOVOI") is None

    assert await repo.count() == 1
    listing = await repo.all()
    assert [r["id"] for r in listing] == [wid]


@requires_db
@pytest.mark.asyncio
async def test_create_defaults_threshold(repo):
    """``threshold`` defaults to 0.5 when not supplied."""
    wid = await repo.create(name="Computer", slug="computer", phrase="computer")
    got = await repo.get(wid)
    assert got["threshold"] == pytest.approx(0.5)


@requires_db
@pytest.mark.asyncio
async def test_bump_clip_count_increments(repo):
    wid = await repo.create(name="Jarvis", slug="jarvis", phrase="hey jarvis")
    assert await repo.bump_clip_count(wid) == 1
    assert await repo.bump_clip_count(wid) == 2
    assert await repo.bump_clip_count(wid) == 3
    assert (await repo.get(wid))["clip_count"] == 3


@requires_db
@pytest.mark.asyncio
async def test_mark_training_eligibility(repo):
    """``mark_training`` only promotes a 'recording' row that holds
    ``>= min_clips`` captured clips. Too few → False, status unchanged."""
    wid = await repo.create(name="Athena", slug="athena", phrase="hey athena")

    # Only 2 clips so far, min is 5 → refused.
    await repo.bump_clip_count(wid)
    await repo.bump_clip_count(wid)
    assert await repo.mark_training(wid, 5) is False
    assert (await repo.get(wid))["status"] == "recording"

    # Reach the minimum → promoted.
    for _ in range(3):
        await repo.bump_clip_count(wid)
    assert (await repo.get(wid))["clip_count"] == 5
    assert await repo.mark_training(wid, 5) is True
    assert (await repo.get(wid))["status"] == "training"

    # Re-promoting an already-training row is a no-op (status guard).
    assert await repo.mark_training(wid, 5) is False


@requires_db
@pytest.mark.asyncio
async def test_next_training_ordering(repo):
    """``next_training`` returns the oldest (by updated_at) 'training' row —
    the queue head the trainer worker drains."""
    # No training rows yet.
    assert await repo.next_training() is None

    a = await repo.create(name="First", slug="first", phrase="first")
    b = await repo.create(name="Second", slug="second", phrase="second")
    for w in (a, b):
        for _ in range(3):
            await repo.bump_clip_count(w)

    # Promote A first, then B — A has the older updated_at, so it heads the queue.
    assert await repo.mark_training(a, 1) is True
    assert await repo.mark_training(b, 1) is True

    head = await repo.next_training()
    assert head is not None
    assert head["id"] == a

    # Finishing A surfaces B next.
    await repo.mark_ready(a, "first")
    head2 = await repo.next_training()
    assert head2["id"] == b


@requires_db
@pytest.mark.asyncio
async def test_mark_ready_and_failed(repo):
    wid = await repo.create(name="Bishop", slug="bishop", phrase="hey bishop")
    for _ in range(3):
        await repo.bump_clip_count(wid)
    await repo.mark_training(wid, 1)

    # mark_ready stamps status + model_ref and clears any prior error.
    assert await repo.mark_ready(wid, "bishop") is True
    got = await repo.get(wid)
    assert got["status"] == "ready"
    assert got["model_ref"] == "bishop"
    assert got["error"] is None

    # mark_failed flips to failed + records the reason.
    assert await repo.mark_failed(wid, "trainer exited 1") is True
    got = await repo.get(wid)
    assert got["status"] == "failed"
    assert got["error"] == "trainer exited 1"

    # Unknown id → no row changed.
    assert await repo.mark_ready(999999, "nope") is False
    assert await repo.mark_failed(999999, "nope") is False


@requires_db
@pytest.mark.asyncio
async def test_set_default_refuses_non_ready(repo):
    """A wake word can only become default once it has a trained model
    (status == 'ready'). set_default refuses anything else."""
    wid = await repo.create(name="Cortana", slug="cortana", phrase="hey cortana")

    # Still recording → refused, not made default.
    assert await repo.set_default(wid) is False
    assert (await repo.get(wid))["is_default"] is False

    # Promote to ready, then it can be made default.
    for _ in range(3):
        await repo.bump_clip_count(wid)
    await repo.mark_training(wid, 1)
    await repo.mark_ready(wid, "cortana")
    assert await repo.set_default(wid) is True
    assert (await repo.get(wid))["is_default"] is True

    # set_default on an unknown id is a no-op returning False.
    assert await repo.set_default(999999) is False


@requires_db
@pytest.mark.asyncio
async def test_single_default_invariant(repo):
    """Setting a new default clears the old one — at most one row is
    is_default, and the default sorts first in all()."""
    async def _ready(name: str, slug: str) -> int:
        wid = await repo.create(name=name, slug=slug, phrase=slug)
        for _ in range(3):
            await repo.bump_clip_count(wid)
        await repo.mark_training(wid, 1)
        await repo.mark_ready(wid, slug)
        return wid

    a = await _ready("Alpha", "alpha")
    b = await _ready("Bravo", "bravo")

    assert await repo.set_default(a) is True
    assert (await repo.get_default())["id"] == a

    # Flipping to B clears A.
    assert await repo.set_default(b) is True
    assert (await repo.get_default())["id"] == b

    rows = await repo.all()
    assert sum(1 for r in rows if r["is_default"]) == 1
    # Default sorts first.
    assert rows[0]["id"] == b


@requires_db
@pytest.mark.asyncio
async def test_update_fields(repo):
    wid = await repo.create(name="Vesper", slug="vesper", phrase="hey vesper")
    assert await repo.update(wid, name="Vesper 2", threshold=0.8) is True
    got = await repo.get(wid)
    assert got["name"] == "Vesper 2"
    assert got["threshold"] == pytest.approx(0.8)
    # No fields → no change.
    assert await repo.update(wid) is False


@requires_db
@pytest.mark.asyncio
async def test_delete_guards_default(repo):
    """delete refuses the default; a non-default deletes and returns the
    removed row (so the caller can clean up the .onnx + clips)."""
    async def _ready(name: str, slug: str) -> int:
        wid = await repo.create(name=name, slug=slug, phrase=slug)
        for _ in range(3):
            await repo.bump_clip_count(wid)
        await repo.mark_training(wid, 1)
        await repo.mark_ready(wid, slug)
        return wid

    default_id = await _ready("DefaultWord", "default_word")
    other_id = await _ready("OtherWord", "other_word")
    assert await repo.set_default(default_id) is True

    # Can't delete the default.
    assert await repo.delete(default_id) is None
    assert await repo.get(default_id) is not None

    # A non-default deletes and returns the removed row.
    removed = await repo.delete(other_id)
    assert removed is not None
    assert removed["name"] == "OtherWord"
    assert removed["slug"] == "other_word"
    assert await repo.get(other_id) is None
