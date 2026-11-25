"""SDK 1.17: plugins hear when voice history is deleted (security review M8).

``core.voice_history_deleted`` ``{person_id}`` is emitted in the core, only
after the deletion committed (domovoi/voice_history.py, V079
``voice_history_forgets``):

* "forget me" (the router's turn), once the turn's transaction commits,
  and never after a rollback;
* People -> delete person, which runs in the WEB process: recorded in the
  web's transaction, then the web asks the core to announce it; with the
  core unreachable the delete still succeeds and the forget waits, and the
  core announces it when it is back (its tick, or the web's next nudge);
* "Delete all voice history", with ``person_id`` None, when its job is done;
* never for the daily "Keep voice history" trim.

And the SDK side: the catalog name, ``core_config.voice_history_keep`` and
``API_VERSION`` 1.17.0.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from domovoi import voice_history
from domovoi.config import settings
from domovoi.events import CORE_EVENTS, EVENTS, Event
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_voice_history import (  # noqa: F401  (vh is a fixture)
    DAY,
    _core,
    _forget_scene,
    _now,
    _said,
    _scalar,
    _turn,
    vh,
)

EVENT = "core.voice_history_deleted"


# ─── The SDK surface (DB-free) ──────────────────────────────────────────────


def test_the_event_is_in_the_core_catalog() -> None:
    assert EVENT in CORE_EVENTS
    assert voice_history.FORGET_EVENT == EVENT


def test_sdk_is_1_17() -> None:
    from domovoi import sdk

    assert tuple(int(p) for p in sdk.API_VERSION.split(".")) >= (1, 17, 0)


@pytest.mark.parametrize(("raw", "read_as"), [
    ("30", "30"), ("90", "90"), ("365", "365"), ("forever", "forever"),
    ("45", "90"), ("1 year", "365"), ("always", "forever"),
])
def test_core_config_reads_voice_history_keep_normalized(raw, read_as, monkeypatch) -> None:
    from domovoi.sdk.coreconfig import CORE_CONFIG_WHITELIST, CoreConfigView

    assert "voice_history_keep" in CORE_CONFIG_WHITELIST
    monkeypatch.setattr(settings, "voice_history_keep", raw)
    assert CoreConfigView().voice_history_keep == read_as
    with pytest.raises(AttributeError):
        CoreConfigView().voice_history_keep = "30"


# ─── Helpers ────────────────────────────────────────────────────────────────


@pytest.fixture
def heard():
    """Subscribe to the event like a plugin would; yields what arrived."""
    got: list[dict] = []

    async def on_event(evt: Event) -> None:
        got.append(dict(evt.payload))

    sub = EVENTS.subscribe(EVENT, on_event, owner="test-forget")
    yield got
    EVENTS.unsubscribe(sub)


async def _settle() -> None:
    """Let background announces and the subscriber tasks run."""
    for _ in range(5):
        pending = list(voice_history._BACKGROUND)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0.01)


async def _waiting() -> list:
    from domovoi.db.session import engine
    from sqlalchemy import text

    async with engine.begin() as conn:
        return [r[0] for r in (await conn.execute(
            text("SELECT person_id FROM voice_history_forgets ORDER BY id"))).all()]


# ─── Subscribers get the payload ───────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_recorded_forget_reaches_subscribers_once(vh, heard) -> None:
    from domovoi.db.session import session_scope

    clean, _ = vh
    await clean()
    async with session_scope() as s:
        await voice_history.record_forget(s, 7)
        await voice_history.record_forget(s, None)
    assert await _waiting() == [7, None]
    assert await voice_history.announce_forgets() == [{"person_id": 7}, {"person_id": None}]
    await _settle()
    assert heard == [{"person_id": 7}, {"person_id": None}]
    # Announced rows are gone: a second drain tells nobody again.
    assert await _waiting() == []
    assert await voice_history.announce_forgets() == []
    await _settle()
    assert len(heard) == 2


@requires_db
@pytest.mark.asyncio
async def test_a_rolled_back_record_announces_nothing(vh, heard) -> None:
    from domovoi.db.session import session_scope

    clean, _ = vh
    await clean()
    with pytest.raises(RuntimeError):
        async with session_scope() as s:
            await voice_history.record_forget(s, 3)
            raise RuntimeError("the delete broke")
    assert await voice_history.announce_forgets() == []
    await _settle()
    assert heard == []


# ─── "forget me" ────────────────────────────────────────────────────────────


async def _forget_me(db_session, monkeypatch, scene):
    from domovoi.handlers.voice_profile import VoiceProfileHandler
    from domovoi.models import Context, Intent
    from domovoi.router import route

    async def own_voice(self, ctx, session):
        return "person", []

    monkeypatch.setattr(VoiceProfileHandler, "_forget_scope", own_voice)

    def ctx():
        return Context(room_id="kitchen", online=True, person_id=scene["sarah"],
                       presence_tier="low", session_id=UUID(scene["a"]))

    await route(Intent(transcript="forget me", room_id="kitchen"), ctx(), db_session)
    await db_session.commit()
    return await route(Intent(transcript="yes", room_id="kitchen"), ctx(), db_session)


@requires_db
@pytest.mark.asyncio
async def test_forget_me_emits_after_the_turn_commits(vh, db_session, monkeypatch, heard) -> None:
    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    response = await _forget_me(db_session, monkeypatch, scene)
    assert "what you've said to me" in response.text
    # Not committed yet: nobody has been told.
    await _settle()
    assert heard == []
    await db_session.commit()
    await _settle()
    assert heard == [{"person_id": scene["sarah"]}]
    assert await _waiting() == []
    assert await _scalar("SELECT COUNT(*) FROM people WHERE id = :p", p=scene["sarah"]) == 0


@requires_db
@pytest.mark.asyncio
async def test_forget_me_rolled_back_tells_nobody(vh, db_session, monkeypatch, heard) -> None:
    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    await _forget_me(db_session, monkeypatch, scene)
    await db_session.rollback()
    await _settle()
    assert heard == []
    assert await _waiting() == []
    assert await _scalar("SELECT COUNT(*) FROM people WHERE id = :p", p=scene["sarah"]) == 1


# ─── People -> delete person (the web process) ──────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_people_delete_nudges_the_core_after_its_commit(vh, monkeypatch, heard) -> None:
    from domovoi.tests.auth_testkit import web_client
    from web.backend.api import people as people_api

    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    seen: list = []

    async def core(path, body=None, timeout=30.0, headers=None):
        # The core's side of the hop: by now the web has committed both the
        # delete and its forget row.
        seen.append((path, await _scalar("SELECT COUNT(*) FROM people WHERE id = :p",
                                         p=scene["sarah"]), await _waiting()))
        return 200, {"announced": len(await voice_history.announce_forgets())}

    monkeypatch.setattr(people_api, "post_admin", core)
    async with web_client() as c:
        assert (await c.delete(f"/api/people/{scene['sarah']}")).status_code == 204
        assert (await c.delete("/api/people/999999")).status_code == 404
    await _settle()
    assert seen == [("/v1/admin/voice-history/announce-forgets", 0, [scene["sarah"]])]
    assert heard == [{"person_id": scene["sarah"]}]
    assert await _waiting() == []


@requires_db
@pytest.mark.asyncio
async def test_people_delete_with_the_core_down_is_announced_when_it_is_back(
    vh, monkeypatch, heard
) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client
    from domovoi.workers.voice_history_pruner import VoiceHistoryPruner

    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    # The core is unreachable (the suite points DOMOVOI_URL at a dead port):
    # the delete succeeds, and the forget waits.
    async with web_client() as c:
        assert (await c.delete(f"/api/people/{scene['sarah']}")).status_code == 204
    assert await _said() == ["bob: remind Sarah to buy milk", "someone: is Sarah home"]
    await _settle()
    assert heard == []
    assert await _waiting() == [scene["sarah"]]

    # Back: the core's next voice-history tick (or start) announces it.
    out = await VoiceHistoryPruner().tick()
    await _settle()
    assert out["forgets_announced"] == 1
    assert heard == [{"person_id": scene["sarah"]}]
    assert await _waiting() == []

    # The route the web nudges is admin-gated and drains the same way.
    async with _core() as c:
        install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
        assert (await c.post("/v1/admin/voice-history/announce-forgets")).status_code == 401
        r = await c.post("/v1/admin/voice-history/announce-forgets",
                         headers=bearer("admin-token"))
    assert r.status_code == 200 and r.json() == {"announced": 0}


# ─── Delete all, and the daily trim ─────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_delete_all_emits_none_when_its_job_completes(vh, heard) -> None:
    clean, _ = vh
    await clean()
    await _turn(_now() - DAY, "yesterday")
    await voice_history.request_delete_all()
    # Asked for, not done: nobody told yet.
    await _settle()
    assert heard == [] and await _waiting() == []
    ran = await voice_history.run_pending_jobs()
    await _settle()
    assert [j["state"] for j in ran] == ["done"]
    assert heard == [{"person_id": None}]
    assert await _said() == []
    # A finished job is not announced again.
    await voice_history.run_pending_jobs()
    await _settle()
    assert heard == [{"person_id": None}]


@requires_db
@pytest.mark.asyncio
async def test_a_failed_delete_all_tells_nobody(vh, monkeypatch, heard) -> None:
    clean, _ = vh
    await clean()
    await _turn(_now() - DAY, "yesterday")

    async def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(voice_history, "prune_before", broken)
    await voice_history.request_delete_all()
    ran = await voice_history.run_pending_jobs()
    await _settle()
    assert [j["state"] for j in ran] == ["failed"]
    assert heard == [] and await _waiting() == []


@requires_db
@pytest.mark.asyncio
async def test_the_daily_trim_tells_nobody(vh, monkeypatch, heard) -> None:
    clean, _ = vh
    await clean()
    await _turn(_now() - 200 * DAY, "long ago")
    await _turn(_now() - DAY, "yesterday")
    monkeypatch.setattr(settings, "voice_history_keep", "90")
    job = await voice_history.run_retention(force=True)
    await _settle()
    assert job is not None and job["deleted"]["conversation_log"] == 1
    assert await _said() == ["yesterday"]
    assert heard == [] and await _waiting() == []
