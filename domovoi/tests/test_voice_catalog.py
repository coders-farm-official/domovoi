"""Curated voice catalog + idempotent registry seeding."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import VoicesRepository
from domovoi.voice_catalog import (
    EDGE_CATALOG,
    PIPER_CATALOG,
    edge_label,
    piper_label,
    planned_voices,
    seed_voices,
)
from domovoi.tests.conftest import requires_db


def test_labels():
    assert edge_label("en-US-AriaNeural") == "Aria"
    assert edge_label("en-GB-SoniaNeural") == "Sonia"
    assert piper_label("en_US-lessac-medium") == "Lessac"
    assert piper_label("en_US-kusal-medium") == "Kusal"


def test_catalog_names_unique_across_engines():
    names = [n for n, _ in EDGE_CATALOG] + [n for n, _ in PIPER_CATALOG]
    assert len(names) == len(set(names)), "voice labels must be unique (DB UNIQUE(name))"


def test_planned_voices_dedupes_configured_into_catalog():
    plan = planned_voices(
        include_catalog=True, edge_voice="en-US-AriaNeural", piper_voice="en_US-kusal-medium"
    )
    refs = [(e, r) for e, _, r in plan]
    # Configured voices already in the catalog → not doubled.
    assert refs.count(("edge", "en-US-AriaNeural")) == 1
    assert refs.count(("piper", "en_US-kusal-medium")) == 1
    assert len(plan) == len(EDGE_CATALOG) + len(PIPER_CATALOG)


def test_planned_voices_adds_custom_configured_voice():
    plan = planned_voices(
        include_catalog=False, edge_voice="en-US-JennyNeural", piper_voice="en_US-custom-medium"
    )
    refs = {(e, r) for e, _, r in plan}
    # Catalog off → only the two configured voices.
    assert refs == {("edge", "en-US-JennyNeural"), ("piper", "en_US-custom-medium")}


# ─── DB-backed seeding ──────────────────────────────────────────────────────

@pytest.fixture
async def repo(db_session):
    await db_session.execute(text("DELETE FROM voices"))
    return VoicesRepository(db_session)


@requires_db
@pytest.mark.asyncio
async def test_seed_fresh_creates_catalog_and_default(repo):
    n = await seed_voices(
        repo, include_catalog=True, edge_voice="en-US-AriaNeural",
        piper_voice="en_US-kusal-medium", default_is_piper=True,
    )
    assert n == len(EDGE_CATALOG) + len(PIPER_CATALOG)
    default = await repo.get_default()
    assert default is not None and default["model_ref"] == "en_US-kusal-medium"  # configured engine


@requires_db
@pytest.mark.asyncio
async def test_seed_is_idempotent(repo):
    kw = dict(include_catalog=True, edge_voice="en-US-AriaNeural",
              piper_voice="en_US-kusal-medium", default_is_piper=True)
    first = await seed_voices(repo, **kw)
    second = await seed_voices(repo, **kw)
    assert first > 0
    assert second == 0  # nothing new on a re-run


@requires_db
@pytest.mark.asyncio
async def test_seed_preserves_existing_default(repo):
    # Pre-seed with Aria as default (mimics an install that chose edge).
    aria_id = await repo.create(
        name="Aria", engine="edge", model_ref="en-US-AriaNeural", is_default=True
    )
    # Now run the catalog seed with piper as the configured engine.
    await seed_voices(
        repo, include_catalog=True, edge_voice="en-US-AriaNeural",
        piper_voice="en_US-kusal-medium", default_is_piper=True,
    )
    default = await repo.get_default()
    assert default["id"] == aria_id  # existing default untouched


@requires_db
@pytest.mark.asyncio
async def test_seed_catalog_off_only_configured(repo):
    n = await seed_voices(
        repo, include_catalog=False, edge_voice="en-US-AriaNeural",
        piper_voice="en_US-lessac-medium", default_is_piper=False,
    )
    assert n == 2
    rows = await repo.all()
    assert {r["name"] for r in rows} == {"Aria", "Lessac"}
    assert (await repo.get_default())["model_ref"] == "en-US-AriaNeural"
