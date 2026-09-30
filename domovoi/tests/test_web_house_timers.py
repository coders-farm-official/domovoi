"""Every timer in the house — the read, the cancel, and the push.

What the dashboard's Home page counts down (design-notes HOME-PLAN.md,
section 3):

* ``GET /api/timers`` — every running timer and reminder, soonest first,
  INCLUDING the ones set with no room (no per-room route can name them),
  each with ``created_at``, plus ``server_now``: the database clock that
  decides when a timer fires, so a phone with a skewed clock still counts
  down right. OPEN, like ``GET /api/satellites/{room}/timers``: household
  state. Except a reminder's words (rule M1, 2026-09-30 — until then only
  a reminder set with NO room was held back): without a household
  credential every reminder, whatever room it was set in, reads
  ``is_reminder`` true with ``message`` and ``label`` null and ``masked``
  true, on both reads. A plain timer's label stays.
* ``DELETE /api/timers/{id}`` — cancel any timer, room or no room. Device
  tier, like the per-room cancel.
* a ``timers_changed`` NOTIFY, commit-coupled (it rides the writer's own
  transaction, so a rollback announces nothing), from every write to the
  ``timers`` table — a voice-set timer, a voice cancel (timer and reminder
  handlers), a web cancel, and a timer going off (only when one did; the
  watcher sweeps every tick) — mapped to the ``timers`` realtime channel.

DB-backed tests carry ``requires_db``; the channel wiring and the route
tiers are checked without a database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.repositories import (
    TIMERS_CHANGED_CHANNEL,
    TimerRepository,
    notify_timers_changed,
    utcnow,
)
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.main import app as web_app
from web.backend.realtime import (
    CORE_REALTIME_CHANNELS,
    NOTIFY_CHANNEL_TO_REALTIME,
    ListenTask,
    StatePollLoop,
    _snapshot_timers,
)


# ─── wiring, no database ─────────────────────────────────────────────────


def test_the_notify_channel_maps_to_the_timers_realtime_channel() -> None:
    assert TIMERS_CHANGED_CHANNEL == "timers_changed"
    assert NOTIFY_CHANNEL_TO_REALTIME["timers_changed"] == "timers"
    assert StatePollLoop._CHANNEL_HELPERS["timers"] is _snapshot_timers
    # A core channel, so no plugin [[realtime]] entry can take it over.
    assert "timers" in CORE_REALTIME_CHANNELS


def _route(method: str, path: str):
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
            return rc
    raise AssertionError(f"{method} {path} is not a web route")


def _gates(rc) -> set:
    out, stack = set(), [getattr(rc, "dependant", None)]
    while stack:
        d = stack.pop()
        for dep in getattr(d, "dependencies", ()) if d is not None else ():
            if dep.call is not None:
                out.add(dep.call)
            stack.append(dep)
    return out


def test_the_house_read_is_open_and_the_cancel_is_device_tier() -> None:
    read = _gates(_route("GET", "/api/timers"))
    assert not read & {admin_auth.require_device, admin_auth.require_device_read,
                       admin_auth.require_admin_read}
    assert admin_auth.require_device in _gates(_route("DELETE", "/api/timers/{timer_id}"))
    # ...the same tiers as the per-room pair beside it.
    assert not _gates(_route("GET", "/api/satellites/{room_id}/timers")) & {
        admin_auth.require_device, admin_auth.require_device_read}
    assert admin_auth.require_device in _gates(
        _route("DELETE", "/api/satellites/{room_id}/timers/{timer_id}"))


# ─── helpers ─────────────────────────────────────────────────────────────


async def _seed() -> dict[str, int]:
    """Three timers: a kitchen pasta timer (10 min, set 5 min ago), a
    reminder set with NO room (2 min), and an office timer (1 h)."""
    now = utcnow()
    ids: dict[str, int] = {}
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        repo = TimerRepository(s)
        ids["pasta"] = await repo.create(
            expires_at=now + timedelta(minutes=10), created_at=now - timedelta(minutes=5),
            label="pasta", message=None, room_id="kitchen",
        )
        ids["mum"] = await repo.create(
            expires_at=now + timedelta(minutes=2), label="call mum",
            message="call mum", room_id=None,
        )
        ids["office"] = await repo.create(
            expires_at=now + timedelta(hours=1), label=None, message=None, room_id="office",
        )
    return ids


def _paired(token: str) -> AsyncClient:
    """The web app as a paired browser: the household token plus the
    header every write under /api/ needs."""
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests", HEADER: token},
    )


async def _count(timer_id: int) -> int:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        return (await conn.execute(
            text("SELECT count(*) FROM timers WHERE id = :id"), {"id": timer_id})).scalar_one()


class _Heard:
    """A plain asyncpg LISTEN on ``timers_changed`` — what the web
    process's ListenTask hears, minus the snapshot pipeline."""

    def __init__(self) -> None:
        self.payloads: list[str] = []
        self._conn = None

    async def __aenter__(self) -> "_Heard":
        import asyncpg

        self._conn = await asyncpg.connect(ListenTask._asyncpg_dsn())
        await self._conn.add_listener(
            TIMERS_CHANGED_CHANNEL, lambda _c, _p, _ch, payload: self.payloads.append(payload)
        )
        return self

    async def __aexit__(self, *exc) -> None:
        await self._conn.close()

    async def settle(self, n: int | None = None, timeout: float = 2.0) -> list[str]:
        """Wait for ``n`` notifications (or, with ``None``, give stray ones
        a moment to arrive) and return what was heard."""
        deadline = asyncio.get_running_loop().time() + timeout
        if n is None:
            await asyncio.sleep(0.3)
        while n is not None and len(self.payloads) < n:
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.05)
        return list(self.payloads)


# ─── the read ────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_house_list_is_every_timer_soonest_first(_db, db_session) -> None:
    async with web_client() as c:
        await claim_admin(c)   # a claimed box: the read must stay open anyway
    ids = await _seed()
    async with web_client() as anon:
        r = await anon.get("/api/timers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["id"] for t in body["timers"]] == [ids["mum"], ids["pasta"], ids["office"]]
    mum, pasta, office = body["timers"]
    # The room-less reminder no per-room route can list: it counts down for
    # anyone, but its words (the label is the message too) need the
    # household token.
    assert mum["room_id"] is None and mum["is_reminder"] is True
    assert mum["message"] is None and mum["label"] is None and mum["masked"] is True
    assert pasta["room_id"] == "kitchen" and pasta["label"] == "pasta" and not pasta["is_reminder"]
    assert pasta["masked"] is False
    assert office["label"] is None and office["room_id"] == "office"
    # created_at + expires_at give the full length: the pasta timer is 15 min.
    start = datetime.fromisoformat(pasta["created_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(pasta["expires_at"].replace("Z", "+00:00"))
    assert abs((end - start) - timedelta(minutes=15)) < timedelta(seconds=1)
    assert all(t["created_at"] for t in body["timers"])
    server_now = datetime.fromisoformat(body["server_now"].replace("Z", "+00:00"))
    assert abs(server_now - datetime.now(timezone.utc)) < timedelta(seconds=30)


@requires_db
@pytest.mark.asyncio
async def test_every_reminders_words_need_a_household_credential(_db, db_session) -> None:
    """Rule M1: a room's reminder is held back like a room-less one."""
    async with web_client() as c:
        await claim_admin(c)
    token = await db_device_token()
    ids = await _seed()
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        ids["bins"] = await TimerRepository(s).create(
            expires_at=utcnow() + timedelta(minutes=30), label="bins out",
            message="bins out", room_id="kitchen",
        )

    def by_id(body: dict) -> dict[int, dict]:
        return {t["id"]: t for t in body["timers"]}

    async with web_client() as anon:
        seen = by_id((await anon.get("/api/timers")).json())
        stale = by_id((await anon.get("/api/timers", headers={HEADER: "not-the-token"})).json())
        room = (await anon.get("/api/satellites/kitchen/timers")).json()
    async with _paired(token) as paired:
        told = by_id((await paired.get("/api/timers")).json())
        room_told = (await paired.get("/api/satellites/kitchen/timers")).json()
    assert seen[ids["mum"]]["message"] is None and seen[ids["mum"]]["label"] is None
    assert stale[ids["mum"]]["message"] is None
    assert told[ids["mum"]]["message"] == "call mum" and told[ids["mum"]]["label"] == "call mum"
    # A room's reminder is held back the same way, on both reads.
    assert seen[ids["bins"]]["message"] is None and seen[ids["bins"]]["label"] is None
    assert seen[ids["bins"]]["masked"] is True and seen[ids["bins"]]["room_id"] == "kitchen"
    assert told[ids["bins"]]["message"] == "bins out" and told[ids["bins"]]["masked"] is False
    bins = next(t for t in room if t["id"] == ids["bins"])
    assert bins["message"] is None and bins["masked"] is True
    assert next(t for t in room_told if t["id"] == ids["bins"])["message"] == "bins out"
    # A plain timer's label is not speech.
    assert seen[ids["pasta"]]["label"] == "pasta" and seen[ids["pasta"]]["masked"] is False


@requires_db
@pytest.mark.asyncio
async def test_a_fresh_install_reads_every_reminder(_db, db_session) -> None:
    """Pre-setup LAN grace: nobody holds a credential yet."""
    ids = await _seed()
    async with web_client() as c:
        rows = {t["id"]: t for t in (await c.get("/api/timers")).json()["timers"]}
    assert rows[ids["mum"]]["message"] == "call mum"


@requires_db
@pytest.mark.asyncio
async def test_an_empty_house_lists_no_timers(_db, db_session) -> None:
    async with web_client() as c:
        r = await c.get("/api/timers")
    assert r.status_code == 200
    assert r.json()["timers"] == [] and r.json()["server_now"]


@requires_db
@pytest.mark.asyncio
async def test_the_per_room_read_carries_created_at_too(db_session) -> None:
    ids = await _seed()
    async with web_client() as c:
        rows = (await c.get("/api/satellites/kitchen/timers")).json()
    assert [t["id"] for t in rows] == [ids["pasta"]]
    assert rows[0]["created_at"] and rows[0]["masked"] is False


@requires_db
@pytest.mark.asyncio
async def test_the_snapshot_is_the_list_without_the_clock(db_session) -> None:
    ids = await _seed()
    snap = await _snapshot_timers()
    assert [t["id"] for t in snap] == [ids["mum"], ids["pasta"], ids["office"]]
    assert set(snap[0]) == {"id", "expires_at", "created_at", "label", "message",
                            "room_id", "is_reminder"}
    # Stable between two reads with nothing changed, or the poll loop would
    # push "timers.changed" every 1.5 s.
    assert await _snapshot_timers() == snap


# ─── the cancel ──────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_cancel_takes_a_household_credential(_db, db_session) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await db_device_token()
    ids = await _seed()
    async with web_client() as anon:
        r = await anon.delete(f"/api/timers/{ids['pasta']}")
        assert r.status_code == 401, r.text
    assert await _count(ids["pasta"]) == 1
    async with _paired(token) as paired:
        assert (await paired.delete(f"/api/timers/{ids['pasta']}")).status_code == 204
        # The room-less reminder — the one only this route can reach.
        assert (await paired.delete(f"/api/timers/{ids['mum']}")).status_code == 204
        again = await paired.delete(f"/api/timers/{ids['pasta']}")
        assert again.status_code == 404 and str(ids["pasta"]) in again.json()["detail"]
    assert await _count(ids["pasta"]) == 0 and await _count(ids["mum"]) == 0
    assert await _count(ids["office"]) == 1


@requires_db
@pytest.mark.asyncio
async def test_a_fresh_install_cancels_without_a_credential(_db, db_session) -> None:
    """Pre-setup LAN grace, as on every device-tier route."""
    ids = await _seed()
    async with web_client() as c:
        assert (await c.delete(f"/api/timers/{ids['office']}")).status_code == 204


# ─── the push ────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_setting_a_timer_notifies_on_commit_only(db_session) -> None:
    from domovoi.db.session import SessionLocal, session_scope

    async with _Heard() as heard:
        async with SessionLocal() as s:
            await TimerRepository(s).create(
                expires_at=utcnow() + timedelta(minutes=1), label="x", message=None,
                room_id="kitchen",
            )
            await s.rollback()
        assert await heard.settle() == [], "a rolled-back timer announced itself"
        async with session_scope() as s:
            await TimerRepository(s).create(
                expires_at=utcnow() + timedelta(minutes=1), label="x", message=None,
                room_id="kitchen",
            )
        assert await heard.settle(1) == ["created"]


@requires_db
@pytest.mark.asyncio
async def test_a_voice_cancel_notifies_only_when_it_cancelled_something(db_session) -> None:
    from domovoi.db.session import session_scope

    await _seed()
    async with _Heard() as heard:
        async with session_scope() as s:
            assert await TimerRepository(s).cancel_by_label("nothing-by-this-name", "kitchen") == 0
        assert await heard.settle() == []
        async with session_scope() as s:
            assert await TimerRepository(s).cancel_by_label("pasta", "kitchen") == 1
        assert await heard.settle(1) == ["cancelled"]


@requires_db
@pytest.mark.asyncio
async def test_a_reminder_cancel_notifies(db_session) -> None:
    from domovoi.db.session import session_scope
    from domovoi.handlers.reminder import ReminderHandler
    from domovoi.models import Context

    async with session_scope() as s:
        await TimerRepository(s).create(
            expires_at=utcnow() + timedelta(minutes=5), label="feed the cat",
            message="feed the cat", room_id="kitchen",
        )
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    async with _Heard() as heard:
        async with session_scope() as s:
            reply = await ReminderHandler()._cancel("the cat", ctx, s)
        assert "cancelled" in reply.text.lower()
        assert await heard.settle(1) == ["cancelled"]
        async with session_scope() as s:
            await ReminderHandler()._cancel("the cat", ctx, s)   # nothing left
        assert await heard.settle() == ["cancelled"]


@requires_db
@pytest.mark.asyncio
async def test_firing_notifies_but_an_empty_sweep_does_not(db_session) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await TimerRepository(s).create(
            expires_at=utcnow() + timedelta(hours=1), label="later", message=None,
            room_id="kitchen",
        )
    async with _Heard() as heard:
        async with session_scope() as s:
            assert await TimerRepository(s).pop_expired() == []
        assert await heard.settle() == [], "the watcher's empty tick announced a change"
        async with session_scope() as s:
            await s.execute(text("UPDATE timers SET expires_at = now() - interval '1 second'"))
        async with session_scope() as s:
            fired = await TimerRepository(s).pop_expired()
        assert [f[1] for f in fired] == ["later"]
        assert await heard.settle(1) == ["fired"]


@requires_db
@pytest.mark.asyncio
async def test_both_web_cancels_notify(_db, db_session) -> None:
    ids = await _seed()
    async with _Heard() as heard:
        async with web_client() as c:
            assert (await c.delete(f"/api/timers/{ids['mum']}")).status_code == 204
            assert (await c.delete(
                f"/api/satellites/kitchen/timers/{ids['pasta']}")).status_code == 204
            # A cancel that found nothing changed nothing.
            assert (await c.delete(f"/api/timers/{ids['mum']}")).status_code == 404
        assert await heard.settle(2) == ["cancelled", "cancelled"]
        assert await heard.settle() == ["cancelled", "cancelled"]


@requires_db
@pytest.mark.asyncio
async def test_the_listen_task_routes_timers_changed_to_the_timers_channel(db_session) -> None:
    from domovoi.db.session import session_scope

    class _Fake:
        _interval_sec = 1.5

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def emit_for_channel(self, channel: str) -> None:
            self.calls.append(channel)

    fake = _Fake()
    task = ListenTask(fake)  # type: ignore[arg-type]
    await task.start()
    try:
        await asyncio.sleep(0.3)
        async with session_scope() as s:
            await notify_timers_changed(s, "created")
        deadline = asyncio.get_running_loop().time() + 2.0
        while not fake.calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()
    assert "timers" in fake.calls, fake.calls
