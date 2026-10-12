"""Curated voice catalog + idempotent registry seeding."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.config import settings
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
    assert piper_label("en_US-norman-medium") == "Norman"
    assert piper_label("en_US-kristin-medium") == "Kristin"
    assert piper_label("en_US-ljspeech-medium") == "Linda"   # a shipped voice's own name
    assert piper_label("en_US-custom-medium") == "Custom"


def test_catalog_names_unique_across_engines():
    names = [n for n, _ in EDGE_CATALOG] + [n for n, _ in PIPER_CATALOG]
    assert len(names) == len(set(names)), "voice labels must be unique (DB UNIQUE(name))"


def test_planned_voices_dedupes_configured_into_catalog():
    plan = planned_voices(
        include_catalog=True, edge_voice="en-US-AriaNeural", piper_voice="en_US-kristin-medium",
        include_edge_voice=True,
    )
    refs = [(e, r) for e, _, r in plan]
    # Configured voices already in the catalog → not doubled.
    assert refs.count(("edge", "en-US-AriaNeural")) == 1
    assert refs.count(("piper", "en_US-kristin-medium")) == 1
    assert len(plan) == len(EDGE_CATALOG) + len(PIPER_CATALOG)


def test_planned_voices_adds_custom_configured_voice():
    plan = planned_voices(
        include_catalog=False, edge_voice="en-US-JennyNeural", piper_voice="en_US-custom-medium",
        include_edge_voice=True,
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
        piper_voice="en_US-kristin-medium", default_is_piper=True,
    )
    assert n == len(EDGE_CATALOG) + len(PIPER_CATALOG)
    default = await repo.get_default()
    assert default is not None and default["model_ref"] == "en_US-kristin-medium"  # configured engine


@requires_db
@pytest.mark.asyncio
async def test_seed_is_idempotent(repo):
    kw = dict(include_catalog=True, edge_voice="en-US-AriaNeural",
              piper_voice="en_US-kristin-medium", default_is_piper=True)
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
        piper_voice="en_US-kristin-medium", default_is_piper=True,
    )
    default = await repo.get_default()
    assert default["id"] == aria_id  # existing default untouched


@requires_db
@pytest.mark.asyncio
async def test_seed_catalog_off_only_configured(repo, monkeypatch):
    # An Edge household (TTS_ENGINE=edge): its configured Edge voice is
    # registered even with the catalog off (B2 keeps it only for that case).
    monkeypatch.setattr(settings, "tts_engine", "edge")
    n = await seed_voices(
        repo, include_catalog=False, edge_voice="en-US-AriaNeural",
        piper_voice="en_US-norman-medium", default_is_piper=False,
    )
    assert n == 2
    rows = await repo.all()
    assert {r["name"] for r in rows} == {"Aria", "Norman"}
    assert (await repo.get_default())["model_ref"] == "en-US-AriaNeural"


# ─── B2: the configured Edge voice is an opt-in, and never under "never" ───


def test_planned_voices_without_the_edge_voice():
    plan = planned_voices(
        include_catalog=False, edge_voice="en-US-AriaNeural", piper_voice="en_US-norman-medium",
        include_edge_voice=False,
    )
    assert plan == [("piper", "Norman", "en_US-norman-medium")]


def test_planned_voices_catalog_without_its_edge_half():
    plan = planned_voices(
        include_catalog=True, edge_voice="en-US-AriaNeural", piper_voice="en_US-kristin-medium",
        include_edge_voice=False, include_edge_catalog=False,
    )
    assert {e for e, _, _ in plan} == {"piper"}
    assert len(plan) == len(PIPER_CATALOG)


class _FakeVoicesRepo:
    """The four calls seed_voices makes, in memory (DB-free)."""

    def __init__(self, rows=None):
        self.rows: list[dict] = list(rows or [])

    async def all(self):
        return [dict(r) for r in self.rows]

    async def create(self, *, name, engine, model_ref, is_default=False):
        rid = len(self.rows) + 1
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


_BOOT_KW = dict(edge_voice="en-US-AriaNeural", piper_voice="en_US-norman-medium")


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
async def test_seed_catalog_off_with_piper_registers_no_edge_voice(monkeypatch, answer):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "piper")
    repo = _FakeVoicesRepo()
    with egress.override_policy(answer):
        n = await seed_voices(repo, include_catalog=False, default_is_piper=True, **_BOOT_KW)
    assert n == 1
    assert [(r["engine"], r["model_ref"]) for r in repo.rows] == [("piper", "en_US-norman-medium")]
    assert (await repo.get_default())["model_ref"] == "en_US-norman-medium"


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
async def test_seed_registers_the_edge_voice_when_tts_engine_is_edge(monkeypatch, answer):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "edge")
    repo = _FakeVoicesRepo()
    with egress.override_policy(answer):
        await seed_voices(repo, include_catalog=False, default_is_piper=False, **_BOOT_KW)
    assert {(r["engine"], r["model_ref"]) for r in repo.rows} == {
        ("edge", "en-US-AriaNeural"), ("piper", "en_US-norman-medium"),
    }
    assert (await repo.get_default())["model_ref"] == "en-US-AriaNeural"


async def test_seed_registers_the_edge_voice_with_the_catalog_on(monkeypatch):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "piper")
    repo = _FakeVoicesRepo()
    with egress.override_policy("always"):
        n = await seed_voices(repo, include_catalog=True, default_is_piper=True, **_BOOT_KW)
    assert n == len(EDGE_CATALOG) + len(PIPER_CATALOG)
    assert ("edge", "en-US-AriaNeural") in {(r["engine"], r["model_ref"]) for r in repo.rows}


@pytest.mark.parametrize("engine", ["edge", "piper", "system"])
@pytest.mark.parametrize("catalog", [False, True])
async def test_seed_under_never_registers_no_edge_voice_at_all(monkeypatch, engine, catalog):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", engine)
    repo = _FakeVoicesRepo()
    with egress.override_policy("never"):
        await seed_voices(
            repo, include_catalog=catalog, default_is_piper=engine == "piper", **_BOOT_KW
        )
        # Even an explicit ask can't register one under never.
        await seed_voices(
            repo, include_catalog=catalog, default_is_piper=engine == "piper",
            include_edge_voice=True, **_BOOT_KW,
        )
    assert {r["engine"] for r in repo.rows} == {"piper"}
    assert len(repo.rows) == (len(PIPER_CATALOG) if catalog else 1)
    # A registry with no default still gets one: the Piper voice.
    assert (await repo.get_default())["engine"] == "piper"


async def test_seed_never_removes_an_edge_voice_already_registered(monkeypatch):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "piper")
    repo = _FakeVoicesRepo([{"id": 1, "name": "Aria", "engine": "edge",
                             "model_ref": "en-US-AriaNeural", "is_default": True}])
    with egress.override_policy("never"):
        await seed_voices(repo, include_catalog=False, default_is_piper=True, **_BOOT_KW)
    assert ("edge", "en-US-AriaNeural") in {(r["engine"], r["model_ref"]) for r in repo.rows}
    assert (await repo.get_default())["name"] == "Aria"  # the existing default stands


@requires_db
@pytest.mark.asyncio
async def test_seed_catalog_off_piper_household_against_the_db(repo, monkeypatch):
    monkeypatch.setattr(settings, "tts_engine", "piper")
    n = await seed_voices(
        repo, include_catalog=False, edge_voice="en-US-AriaNeural",
        piper_voice="en_US-norman-medium", default_is_piper=True,
    )
    assert n == 1
    rows = await repo.all()
    assert {(r["engine"], r["name"]) for r in rows} == {("piper", "Norman")}


# ─── 2026-10: the voices Domovoi no longer offers leave the registry ──────


def test_the_catalog_is_the_three_public_domain_voices():
    from domovoi.clients.tts import RETIRED_PIPER_VOICES, SHIPPED_VOICES

    assert [r for _, r in PIPER_CATALOG] == [v.model_ref for v in SHIPPED_VOICES] == [
        "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"]
    assert not {r for _, r in PIPER_CATALOG} & RETIRED_PIPER_VOICES
    assert {"en_US-lessac-medium", "en_US-amy-medium", "en_US-ryan-high"} <= RETIRED_PIPER_VOICES


async def test_seed_removes_the_retired_voices_and_moves_their_default(monkeypatch):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "piper")
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Lessac", "engine": "piper", "model_ref": "en_US-lessac-medium", "is_default": True},
        {"id": 2, "name": "Ryan", "engine": "piper", "model_ref": "en_US-ryan-high", "is_default": False},
        {"id": 3, "name": "Mine", "engine": "piper", "model_ref": "mine", "is_default": False},
        {"id": 4, "name": "Aria", "engine": "edge", "model_ref": "en-US-AriaNeural", "is_default": False},
    ])
    with egress.override_policy("always"):
        await seed_voices(repo, include_catalog=True, default_is_piper=True, **_BOOT_KW)
    refs = {r["model_ref"] for r in repo.rows}
    assert not refs & {"en_US-lessac-medium", "en_US-ryan-high"}
    assert {"mine", "en-US-AriaNeural", "en_US-norman-medium", "en_US-kristin-medium",
            "en_US-ljspeech-medium"} <= refs   # an owner's own voice and Edge stay
    assert (await repo.get_default())["model_ref"] == "en_US-norman-medium"


async def test_seed_keeps_a_default_that_is_still_offered(monkeypatch):
    from domovoi import egress

    monkeypatch.setattr(settings, "tts_engine", "piper")
    repo = _FakeVoicesRepo([
        {"id": 1, "name": "Kristin", "engine": "piper", "model_ref": "en_US-kristin-medium", "is_default": True},
        {"id": 2, "name": "Amy", "engine": "piper", "model_ref": "en_US-amy-medium", "is_default": False},
    ])
    with egress.override_policy("always"):
        await seed_voices(repo, include_catalog=True, default_is_piper=True, **_BOOT_KW)
    assert (await repo.get_default())["model_ref"] == "en_US-kristin-medium"
    assert "en_US-amy-medium" not in {r["model_ref"] for r in repo.rows}
