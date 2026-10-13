"""The voice registry's seeding: the three public-domain Piper voices, and
the retired and Microsoft Edge voices taken out at boot."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import VoicesRepository
from domovoi.voice_catalog import (
    DROPPED_ENGINES,
    PIPER_CATALOG,
    piper_label,
    planned_voices,
    seed_voices,
)
from domovoi.tests.conftest import requires_db


def test_labels():
    assert piper_label("en_US-norman-medium") == "Norman"
    assert piper_label("en_US-kristin-medium") == "Kristin"
    assert piper_label("en_US-ljspeech-medium") == "Linda"   # a shipped voice's own name
    assert piper_label("en_US-custom-medium") == "Custom"


def test_catalog_names_unique():
    names = [n for n, _ in PIPER_CATALOG]
    assert len(names) == len(set(names)), "voice labels must be unique (DB UNIQUE(name))"


def test_planned_voices_dedupes_configured_into_catalog():
    plan = planned_voices(include_catalog=True, piper_voice="en_US-kristin-medium")
    refs = [(e, r) for e, _, r in plan]
    assert refs.count(("piper", "en_US-kristin-medium")) == 1
    assert len(plan) == len(PIPER_CATALOG)
    assert {e for e, _, _ in plan} == {"piper"}


def test_planned_voices_adds_custom_configured_voice():
    plan = planned_voices(include_catalog=False, piper_voice="en_US-custom-medium")
    assert plan == [("piper", "Custom", "en_US-custom-medium")]


def test_the_catalog_is_the_three_public_domain_voices():
    from domovoi.clients.tts import RETIRED_PIPER_VOICES, SHIPPED_VOICES

    assert [r for _, r in PIPER_CATALOG] == [v.model_ref for v in SHIPPED_VOICES] == [
        "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"]
    assert not {r for _, r in PIPER_CATALOG} & RETIRED_PIPER_VOICES
    assert {"en_US-lessac-medium", "en_US-amy-medium", "en_US-ryan-high"} <= RETIRED_PIPER_VOICES
    assert DROPPED_ENGINES == {"edge"}


class _FakeVoicesRepo:
    """The calls seed_voices makes, in memory (DB-free)."""

    def __init__(self, rows=None):
        self.rows: list[dict] = list(rows or [])

    async def all(self):
        return [dict(r) for r in self.rows]

    async def create(self, *, name, engine, model_ref, is_default=False):
        rid = max((r["id"] for r in self.rows), default=0) + 1
        self.rows.append({"id": rid, "name": name, "engine": engine,
                          "model_ref": model_ref, "is_default": is_default})
        return rid

    async def get_default(self):
        return next((dict(r) for r in self.rows if r["is_default"]), None)

    async def set_default(self, vid):
        for r in self.rows:
            r["is_default"] = r["id"] == vid

    async def clear_default(self):
        for r in self.rows:
            r["is_default"] = False

    async def delete(self, vid):
        row = next((r for r in self.rows if r["id"] == vid and not r["is_default"]), None)
        if row is not None:
            self.rows.remove(row)
        return row


async def test_seed_catalog_off_registers_the_configured_voice():
    repo = _FakeVoicesRepo()
    n = await seed_voices(repo, include_catalog=False, piper_voice="en_US-kristin-medium")
    assert n == 1
    assert [(r["engine"], r["model_ref"]) for r in repo.rows] == [("piper", "en_US-kristin-medium")]
    assert (await repo.get_default())["model_ref"] == "en_US-kristin-medium"


async def test_seed_removes_the_retired_voices_and_moves_their_default():
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Lessac", "engine": "piper", "model_ref": "en_US-lessac-medium", "is_default": True},
        {"id": 2, "name": "Ryan", "engine": "piper", "model_ref": "en_US-ryan-high", "is_default": False},
        {"id": 3, "name": "Mine", "engine": "piper", "model_ref": "mine", "is_default": False},
    ])
    await seed_voices(repo, include_catalog=True, piper_voice="en_US-kristin-medium")
    refs = {r["model_ref"] for r in repo.rows}
    assert not refs & {"en_US-lessac-medium", "en_US-ryan-high"}
    assert {"mine", "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"} <= refs
    assert (await repo.get_default())["model_ref"] == "en_US-kristin-medium"


async def test_seed_keeps_a_default_that_is_still_offered():
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Norman", "engine": "piper", "model_ref": "en_US-norman-medium", "is_default": True},
        {"id": 2, "name": "Amy", "engine": "piper", "model_ref": "en_US-amy-medium", "is_default": False},
    ])
    await seed_voices(repo, include_catalog=True, piper_voice="en_US-kristin-medium")
    # (Norman to Kristin is the default move's job, once Kristin is on disk:
    # domovoi/voice_migration.py, test_voice_migration.py.)
    assert (await repo.get_default())["model_ref"] == "en_US-norman-medium"
    assert "en_US-amy-medium" not in {r["model_ref"] for r in repo.rows}


# ─── 2026-10-12: the Microsoft Edge voices leave the registry ─────────────


@pytest.mark.parametrize("catalog", [False, True])
async def test_seed_removes_every_edge_voice_and_the_default_falls_to_the_house_voice(catalog):
    """A registered cloud voice is removed on the update; a default that was
    one falls back to the house voice (Kristin, the configured voice), and a
    room set to one speaks in the default (the resolver's fallback)."""
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Aria", "engine": "edge", "model_ref": "en-US-AriaNeural", "is_default": True},
        {"id": 2, "name": "Eric", "engine": "edge", "model_ref": "en-US-EricNeural", "is_default": False},
        {"id": 3, "name": "Cori", "engine": "piper", "model_ref": "en_GB-cori-high", "is_default": False},
        {"id": 4, "name": "Mine", "engine": "piper", "model_ref": "mine", "is_default": False},
    ])
    await seed_voices(repo, include_catalog=catalog, piper_voice="en_US-kristin-medium")
    assert {r["engine"] for r in repo.rows} == {"piper"}
    names = {r["name"] for r in repo.rows}
    assert {"Cori", "Mine", "Kristin"} <= names and not names & {"Aria", "Eric"}
    assert (await repo.get_default())["model_ref"] == "en_US-kristin-medium"
    # Seeding again changes nothing.
    assert await seed_voices(repo, include_catalog=catalog, piper_voice="en_US-kristin-medium") == 0


async def test_an_edge_engine_name_in_any_case_is_removed():
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium", "is_default": True},
        {"id": 2, "name": "Guy", "engine": " EDGE ", "model_ref": "en-US-GuyNeural", "is_default": False},
    ])
    await seed_voices(repo, include_catalog=False, piper_voice="en_US-kristin-medium")
    assert [r["name"] for r in repo.rows] == ["Kristin"]


async def test_a_removed_edge_room_voice_resolves_to_the_default(monkeypatch):
    """What a room still set to "Eric" speaks in once Eric is gone: the
    registry default (domovoi/streaming.py resolve_voice)."""
    import contextlib

    from domovoi import streaming

    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Eric", "engine": "edge", "model_ref": "en-US-EricNeural", "is_default": False},
        {"id": 2, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium", "is_default": True},
    ])
    await seed_voices(repo, include_catalog=False, piper_voice="en_US-kristin-medium")

    class _Repo:
        def __init__(self, _s):
            pass

        async def get_by_name(self, name):
            return next((dict(r) for r in repo.rows if r["name"].lower() == name.lower()), None)

        async def get_default(self):
            return await repo.get_default()

    @contextlib.asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(streaming, "session_scope", scope)
    import domovoi.db.repositories as repos

    monkeypatch.setattr(repos, "VoicesRepository", _Repo)
    assert await streaming.resolve_voice("Eric") == ("piper", "en_US-kristin-medium")


# ─── DB-backed seeding ──────────────────────────────────────────────────────

@pytest.fixture
async def repo(db_session):
    await db_session.execute(text("DELETE FROM voices"))
    return VoicesRepository(db_session)


@requires_db
@pytest.mark.asyncio
async def test_seed_fresh_creates_catalog_and_default(repo):
    n = await seed_voices(repo, include_catalog=True, piper_voice="en_US-kristin-medium")
    assert n == len(PIPER_CATALOG)
    default = await repo.get_default()
    assert default is not None and default["model_ref"] == "en_US-kristin-medium"


@requires_db
@pytest.mark.asyncio
async def test_seed_is_idempotent(repo):
    kw = dict(include_catalog=True, piper_voice="en_US-kristin-medium")
    first = await seed_voices(repo, **kw)
    second = await seed_voices(repo, **kw)
    assert first > 0
    assert second == 0  # nothing new on a re-run


@requires_db
@pytest.mark.asyncio
async def test_seed_preserves_existing_default(repo):
    ljs = await repo.create(name="Linda", engine="piper", model_ref="en_US-ljspeech-medium", is_default=True)
    await seed_voices(repo, include_catalog=True, piper_voice="en_US-kristin-medium")
    assert (await repo.get_default())["id"] == ljs  # existing default untouched


@requires_db
@pytest.mark.asyncio
async def test_seed_removes_edge_rows_against_the_db(repo):
    await repo.create(name="Aria", engine="edge", model_ref="en-US-AriaNeural", is_default=True)
    await repo.create(name="Eric", engine="edge", model_ref="en-US-EricNeural")
    n = await seed_voices(repo, include_catalog=False, piper_voice="en_US-kristin-medium")
    assert n == 1
    rows = await repo.all()
    assert {(r["engine"], r["name"]) for r in rows} == {("piper", "Kristin")}
    assert (await repo.get_default())["name"] == "Kristin"
