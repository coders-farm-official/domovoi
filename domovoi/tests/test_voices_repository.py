"""VoicesRepository — the voice registry.

Exercises CRUD, the single-default invariant, case-insensitive name
lookup, and the refuse-to-delete-the-default guard. Starts from a clean
``voices`` table (it isn't in the per-test truncate list — it's reference
data — so the test clears it itself).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import VoicesRepository
from domovoi.tests.conftest import requires_db


@pytest.fixture
async def repo(db_session):
    await db_session.execute(text("DELETE FROM voices"))
    return VoicesRepository(db_session)


@requires_db
@pytest.mark.asyncio
async def test_create_and_lookup(repo):
    vid = await repo.create(
        name="Lessac", engine="piper", model_ref="en_US-lessac-medium", is_default=True
    )
    assert vid > 0

    got = await repo.get_by_name("lessac")  # case-insensitive
    assert got is not None
    assert got["name"] == "Lessac"
    assert got["engine"] == "piper"
    assert got["model_ref"] == "en_US-lessac-medium"
    assert got["is_default"] is True

    assert await repo.get_by_name("nope") is None


@requires_db
@pytest.mark.asyncio
async def test_single_default_invariant(repo):
    a = await repo.create(name="Aria", engine="edge", model_ref="en-US-AriaNeural", is_default=True)
    b = await repo.create(name="Lessac", engine="piper", model_ref="en_US-lessac-medium")

    # Creating B (non-default) leaves A the default.
    assert (await repo.get_default())["id"] == a

    # Flipping default to B clears A.
    assert await repo.set_default(b) is True
    default = await repo.get_default()
    assert default["id"] == b

    rows = await repo.all()
    assert sum(1 for r in rows if r["is_default"]) == 1
    # Default sorts first.
    assert rows[0]["id"] == b

    # set_default on an unknown id is a no-op returning False.
    assert await repo.set_default(999999) is False


@requires_db
@pytest.mark.asyncio
async def test_update_fields(repo):
    vid = await repo.create(name="Ryan", engine="piper", model_ref="ryan-v1")
    assert await repo.update(vid, name="Ryan HD", model_ref="ryan-v2") is True
    got = await repo.get_by_name("Ryan HD")
    assert got["model_ref"] == "ryan-v2"
    # No fields → no change.
    assert await repo.update(vid) is False


@requires_db
@pytest.mark.asyncio
async def test_delete_guards_default(repo):
    default_id = await repo.create(
        name="Aria", engine="edge", model_ref="en-US-AriaNeural", is_default=True
    )
    other_id = await repo.create(name="Lessac", engine="piper", model_ref="en_US-lessac-medium")

    # Can't delete the default.
    assert await repo.delete(default_id) is None
    assert await repo.get_by_name("Aria") is not None

    # A non-default deletes and returns the removed row.
    removed = await repo.delete(other_id)
    assert removed is not None
    assert removed["name"] == "Lessac"
    assert await repo.get_by_name("Lessac") is None
