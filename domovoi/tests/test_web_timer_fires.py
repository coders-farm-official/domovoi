"""Timers and reminders reach the whole house — the web process's half
(owner decision 2026-09-30).

What the web serves, and to whom:

* **Rule M1** — a reminder's words are household speech. Every OPEN timer
  read (``GET /api/timers``, ``GET /api/satellites/{room}/timers``, the new
  ``GET /api/timers/fires`` and the ``fires`` inside ``/api/timers``)
  answers a caller with no household credential with every reminder's
  ``message`` and ``label`` null and ``masked`` true — whatever room it was
  set in (until now only reminders set with NO room were held back). The
  household token, an admin Bearer, the dashboard cookie and the pre-setup
  grace read the words. Countdown times, rooms, kinds and delivery
  outcomes stay open, and a plain timer's label ("pasta") is not masked.
* **Fire history** (V017) — ``GET /api/timers/fires`` (open, M1):
  ``since_id`` catches up oldest first, ``room_id`` is the room it was
  SET in, ``timer_id`` finds one timer's fire, ``limit`` 1–200. 503 — not
  an empty list — when V017 is missing, so a client can tell "unknown"
  from "none". ``GET /api/timers`` carries the last 10 minutes as
  ``fires`` (``null`` without V017). Each fire has a ``summary`` the web
  computes ("heard in garage, kitchen · still announcing").
* **"Only reminders for this device"** — ``GET`` (open) and ``PUT``
  (device tier + the CSRF header) ``/api/satellites/{room}/timer-
  announcements``; every roster row carries ``timers_own_only``.
* **Realtime** — ``NOTIFY timer_fires_changed`` → the core channel
  ``timer_fires`` → ``{"type": "timer_fires.changed", "data": [Fire]}``,
  the last hour, unmasked (the socket is device tier).

Two halves:

* DB-FREE (always runs): the routes through the real web app with the
  auth primitives faked (``auth_testkit.install_fake_db``) and the V017
  seam (:mod:`web.backend.timer_fires`) replaced by fakes; the seam's own
  SQL shape against a recording fake session; the summary rules.
* DB-BACKED (``requires_db``): the seam against a real database. The V017
  tables are created from the migration file itself, statement by
  statement (``timer_fires_testkit.apply_v017``; the V016 precedent in
  test_command_captures_api runs its one statement whole), and truncated
  around each test — they are NOT in conftest's TABLES_TO_TRUNCATE, so a
  lane without V017 still runs everything else. Skipped when the file is
  not on this branch (V017 is the core's).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend import realtime, timer_fires
from web.backend.api import captures as captures_api
from web.backend.api import satellites as sat_api

ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"
BROWSER = {"X-Requested-With": "XMLHttpRequest"}
T0 = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
V017 = MIGRATIONS / "V017__timer_fires.sql"


# ─── fakes ───────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar


def _timer_row(tid, *, label=None, message=None, room_id="kitchen", minutes=10):
    """(id, expires_at, label, message, room_id, created_at) — _TIMER_COLUMNS."""
    return (tid, T0 + timedelta(minutes=minutes), label, message, room_id, T0 - timedelta(minutes=1))


# A garage reminder, a reminder set with no room, a kitchen pasta timer.
TIMER_ROWS = [
    _timer_row(1, label="call mom", message="call mom", room_id="garage", minutes=5),
    _timer_row(2, label="bins out", message="bins out", room_id=None, minutes=6),
    _timer_row(3, label="pasta", room_id="kitchen", minutes=7),
]


class _RouteSession:
    """What the timer routes run on: the clock and the ``timers`` rows."""

    def __init__(self, rows: list) -> None:
        self.rows = rows

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql == "SELECT now()":
            return _Result(scalar=T0)
        if "FROM timers" in sql:
            rows = self.rows
            if "WHERE room_id = :room_id" in sql:
                rows = [r for r in rows if r[4] == params["room_id"]]
            return _Result(rows=rows)
        raise AssertionError(f"unexpected SQL: {sql}")


def _delivery(room, outcome, *, origin=False, detail=None):
    return {"room_id": room, "is_origin": origin, "outcome": outcome, "detail": detail,
            "finished_at": T0 + timedelta(seconds=3) if outcome not in ("pending", "sending") else None}


def _fire(fid, *, message=None, label=None, room_id="garage", deliveries=None, acked_by=None,
          fired=None):
    fired = fired or T0 - timedelta(seconds=30)
    return timer_fires.fire_from_row(
        {"id": fid, "timer_id": 100 + fid, "label": label, "message": message, "room_id": room_id,
         "created_at": fired - timedelta(minutes=10), "due_at": fired, "fired_at": fired,
         "settled_at": None, "acked_at": None, "acked_by": acked_by},
        deliveries if deliveries is not None else [_delivery(room_id, "spoken", origin=True)],
    )


FIRES = [
    _fire(12, message="call mom", label="call mom",
          deliveries=[_delivery("garage", "spoken", origin=True), _delivery("kitchen", "pending",
                                                                           detail="capturing")]),
    _fire(11, label="pasta", room_id="kitchen",
          deliveries=[_delivery("kitchen", "spoken", origin=True)]),
]


@pytest.fixture
def claimed(monkeypatch):
    """An install with an admin (so no pre-setup grace), one admin session,
    one household token."""
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)


@pytest.fixture
def seam(monkeypatch):
    """The routes' database: a fake session with TIMER_ROWS, and the V017
    seam faked. Returns the recorder the fakes write to."""
    rec: dict[str, Any] = {"recent": [], "set": [], "known": {"garage", "kitchen"},
                           "fires": [dict(f) for f in FIRES], "flags": {}, "classified": 0}
    session = _RouteSession(TIMER_ROWS)

    @asynccontextmanager
    async def fake_scope():
        yield session

    async def recent_fires(s, **kw):
        rec["recent"].append(kw)
        return None if rec["fires"] is None else [dict(f) for f in rec["fires"]]

    async def own_only_rooms(s):
        return dict(rec["flags"])

    async def set_own_only(s, room_id, on):
        rec["set"].append((room_id, on))
        if rec.get("missing"):
            raise timer_fires.LedgerMissing("missing")
        if not on:
            rec["flags"].pop(room_id, None)
            return None
        return rec["flags"].setdefault(room_id, T0)

    async def room_known(s, room_id):
        return room_id in rec["known"]

    real_check = sat_api.check_device_request

    async def counted_check(request, *a, **kw):
        rec["classified"] += 1
        return await real_check(request, *a, **kw)

    monkeypatch.setattr(sat_api, "session_scope", fake_scope)
    monkeypatch.setattr(timer_fires, "recent_fires", recent_fires)
    monkeypatch.setattr(timer_fires, "own_only_rooms", own_only_rooms)
    monkeypatch.setattr(timer_fires, "set_own_only", set_own_only)
    monkeypatch.setattr(captures_api, "_room_known", room_known)
    monkeypatch.setattr(sat_api, "check_device_request", counted_check)
    return rec


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    """The web app with no lifespan (no poll loop, no LISTEN task)."""
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers=headers or {}, **kw)


def _anon() -> AsyncClient:
    return _client()


def _token() -> AsyncClient:
    return _client({HEADER: DEVICE_TOKEN})


# ─── W1: tiers ───────────────────────────────────────────────────────────


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


_GATES = {admin_auth.require_device, admin_auth.require_device_read, admin_auth.require_admin_read,
          admin_auth.require_admin_mutation, admin_auth.require_admin_security}


def test_the_reads_are_open_and_the_flag_write_is_device_tier() -> None:
    for path in ("/api/timers", "/api/timers/fires", "/api/satellites/{room_id}/timers",
                 "/api/satellites/{room_id}/timer-announcements"):
        assert not _gates(_route("GET", path)) & _GATES, path
    put = _gates(_route("PUT", "/api/satellites/{room_id}/timer-announcements"))
    assert admin_auth.require_device in put
    assert not put & (_GATES - {admin_auth.require_device})


@pytest.mark.asyncio
async def test_the_new_reads_answer_with_no_credential(claimed, seam) -> None:
    async with _anon() as c:
        for path in ("/api/timers/fires", "/api/satellites/garage/timer-announcements",
                     "/api/timers", "/api/satellites/garage/timers"):
            r = await c.get(path)
            assert r.status_code == 200, (path, r.text)


@pytest.mark.asyncio
async def test_the_flag_write_takes_a_household_credential(claimed, seam) -> None:
    body = {"own_only": True}
    path = "/api/satellites/garage/timer-announcements"
    async with _client(BROWSER) as c:
        assert (await c.put(path, json=body)).status_code == 401
    async with _client(BROWSER, cookies={COOKIE: ADMIN_TOKEN}) as c:
        assert (await c.put(path, json=body)).status_code == 403     # the cookie alone
    assert seam["set"] == []                                          # refused before any write
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        assert (await c.put(path, json=body)).status_code == 200
    async with _client({**BROWSER, **bearer(ADMIN_TOKEN)}) as c:
        assert (await c.put(path, json={"own_only": False})).status_code == 200
    assert seam["set"] == [("garage", True), ("garage", False)]
    # The CSRF backstop answers before routing, credential or not.
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.put(path, json=body)
    assert r.status_code == 403 and "X-Requested-With" in r.text
    assert len(seam["set"]) == 2


@pytest.mark.asyncio
async def test_a_fresh_install_writes_the_flag_without_a_credential(monkeypatch, seam) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _client(BROWSER) as c:
        r = await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})
    assert r.status_code == 200


# ─── W2: rule M1 ─────────────────────────────────────────────────────────


def _by_id(rows: list[dict]) -> dict[int, dict]:
    return {r["id"]: r for r in rows}


@pytest.mark.asyncio
async def test_no_credential_reads_every_reminder_masked(claimed, seam) -> None:
    async with _anon() as c:
        body = (await c.get("/api/timers")).json()
    timers = _by_id(body["timers"])
    for tid in (1, 2):   # a room's reminder AND one set with no room
        assert timers[tid]["message"] is None and timers[tid]["label"] is None, tid
        assert timers[tid]["masked"] is True and timers[tid]["is_reminder"] is True
    # Still counts down, still says where.
    assert timers[1]["room_id"] == "garage" and timers[1]["expires_at"]
    assert timers[3]["label"] == "pasta" and timers[3]["masked"] is False
    fires = _by_id(body["fires"])
    assert fires[12]["message"] is None and fires[12]["label"] is None and fires[12]["masked"] is True
    # Where it was heard is not speech: it stays.
    assert fires[12]["summary"] == "heard in garage · still announcing"
    assert fires[12]["heard_in"] == ["garage"]
    assert [d["outcome"] for d in fires[12]["deliveries"]] == ["spoken", "pending"]
    assert fires[11]["label"] == "pasta" and fires[11]["masked"] is False


@pytest.mark.parametrize("who", ["token", "bearer", "cookie", "pre-setup"])
@pytest.mark.asyncio
async def test_a_household_credential_reads_the_words(monkeypatch, seam, who) -> None:
    install_fake_db(monkeypatch, admin=who != "pre-setup", sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    kw: dict[str, Any] = {}
    if who == "token":
        kw["headers"] = {HEADER: DEVICE_TOKEN}
    elif who == "bearer":
        kw["headers"] = bearer(ADMIN_TOKEN)
    elif who == "cookie":
        kw["cookies"] = {COOKIE: ADMIN_TOKEN}
    async with _client(**kw) as c:
        body = (await c.get("/api/timers")).json()
        room = (await c.get("/api/satellites/garage/timers")).json()
        hist = (await c.get("/api/timers/fires")).json()
    timers = _by_id(body["timers"])
    assert timers[1]["message"] == "call mom" and timers[2]["label"] == "bins out"
    assert not any(t["masked"] for t in body["timers"])
    assert _by_id(body["fires"])[12]["message"] == "call mom"
    assert room[0]["message"] == "call mom" and room[0]["masked"] is False
    assert _by_id(hist["fires"])[12]["message"] == "call mom"


@pytest.mark.asyncio
async def test_a_stale_token_reads_masked(claimed, seam) -> None:
    async with _client({HEADER: "not-the-token"}) as c:
        body = (await c.get("/api/timers")).json()
    assert _by_id(body["timers"])[1]["masked"] is True


@pytest.mark.asyncio
async def test_the_per_room_read_and_the_history_are_masked_the_same_way(claimed, seam) -> None:
    async with _anon() as c:
        room = (await c.get("/api/satellites/garage/timers")).json()
        kitchen = (await c.get("/api/satellites/kitchen/timers")).json()
        hist = (await c.get("/api/timers/fires")).json()
    (reminder,) = room
    assert reminder["message"] is None and reminder["label"] is None and reminder["masked"] is True
    assert kitchen[0]["label"] == "pasta" and kitchen[0]["masked"] is False
    fires = _by_id(hist["fires"])
    assert fires[12]["masked"] is True and fires[12]["message"] is None
    assert fires[11]["label"] == "pasta"


@pytest.mark.asyncio
async def test_a_read_with_no_reminder_classifies_nobody(claimed, seam) -> None:
    seam["fires"] = [dict(FIRES[1])]
    async with _anon() as c:
        await c.get("/api/satellites/kitchen/timers")
        await c.get("/api/timers/fires?room_id=kitchen")
    assert seam["classified"] == 0
    async with _anon() as c:
        await c.get("/api/timers")          # the garage reminder is in this one
    assert seam["classified"] == 1


# ─── W3: the fire history route ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_history_passes_its_filters_through(claimed, seam) -> None:
    async with _anon() as c:
        r = await c.get("/api/timers/fires?since_id=5&room_id=garage&timer_id=17&limit=200")
        assert r.status_code == 200
        body = r.json()
        await c.get("/api/timers/fires")
    assert seam["recent"][0] == {"since_id": 5, "origin_room_id": "garage", "timer_id": 17,
                                 "limit": 200}
    assert seam["recent"][1] == {"since_id": None, "origin_room_id": None, "timer_id": None,
                                 "limit": 50}
    assert body["server_now"].startswith("2026-09-30T15:00:00")
    assert [f["id"] for f in body["fires"]] == [12, 11]
    f = body["fires"][0]
    assert set(f) == {"id", "timer_id", "kind", "is_reminder", "label", "message", "masked",
                      "room_id", "created_at", "due_at", "fired_at", "settled_at", "acked_at",
                      "acked_by", "heard_in", "summary", "deliveries"}
    assert set(f["deliveries"][0]) == {"room_id", "is_origin", "outcome", "detail", "finished_at"}


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "since_id=-1", "room_id=" + "r" * 121,
                                   "timer_id=x"])
@pytest.mark.asyncio
async def test_the_history_refuses_bad_bounds(claimed, seam, query) -> None:
    async with _anon() as c:
        r = await c.get(f"/api/timers/fires?{query}")
    assert r.status_code == 422, r.text
    assert seam["recent"] == []


@pytest.mark.asyncio
async def test_no_fire_history_is_503_there_and_null_in_the_timer_list(claimed, seam) -> None:
    seam["fires"] = None
    async with _anon() as c:
        r = await c.get("/api/timers/fires")
        timers = (await c.get("/api/timers")).json()
    assert r.status_code == 503
    assert r.json() == {"detail": "timer fire history needs database migration V017 — run Flyway"}
    assert timers["fires"] is None
    assert [t["id"] for t in timers["timers"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_the_timer_list_reads_ten_minutes_of_fires(claimed, seam) -> None:
    async with _anon() as c:
        await c.get("/api/timers")
    assert seam["recent"] == [{"window_sec": 600, "limit": 20}]


# ─── the summary line ────────────────────────────────────────────────────


@pytest.mark.parametrize(("deliveries", "acked_by", "summary"), [
    ([_delivery("garage", "spoken", origin=True)], None, "heard in garage"),
    ([_delivery("garage", "spoken", origin=True), _delivery("office", "interrupted"),
      _delivery("kitchen", "sending")], None, "heard in garage, office · still announcing"),
    ([_delivery("garage", "pending", origin=True), _delivery("kitchen", "sending")], None,
     "announcing…"),
    ([], None, "no satellite was online"),
    ([_delivery("garage", "offline", origin=True), _delivery("kitchen", "busy_timeout")], None,
     "not heard in any room (garage offline)"),
    ([_delivery("garage", "failed", origin=True), _delivery("kitchen", "offline")], None,
     "not heard in any room"),
    ([_delivery("kitchen", "spoken"), _delivery("office", "cancelled", detail="acknowledged:kitchen")],
     "kitchen", "heard in kitchen · stopped in kitchen"),
    ([_delivery("garage", "offline", origin=True)], "kitchen",
     "not heard in any room (garage offline) · stopped in kitchen"),
])
def test_the_summary_says_where_it_was_heard(deliveries, acked_by, summary) -> None:
    f = _fire(1, deliveries=deliveries, acked_by=acked_by)
    assert f["summary"] == summary
    assert timer_fires.fire_summary(f) == summary
    assert "…" in timer_fires.fire_summary(_fire(2, deliveries=[_delivery("x", "pending")]))


def test_a_fire_lists_its_origin_first_then_the_rooms_a_to_z() -> None:
    f = _fire(1, room_id="kitchen", deliveries=[
        _delivery("office", "spoken"), _delivery("bedroom", "interrupted"),
        _delivery("kitchen", "spoken", origin=True), _delivery("attic", "offline")])
    assert [d["room_id"] for d in f["deliveries"]] == ["kitchen", "attic", "bedroom", "office"]
    assert f["heard_in"] == ["kitchen", "bedroom", "office"]


def test_the_kind_follows_the_message_even_an_empty_one() -> None:
    assert _fire(1, message="")["is_reminder"] is True
    assert _fire(1, message="")["kind"] == "reminder"
    assert _fire(1, label="pasta")["kind"] == "timer"


def test_masking_holds_back_a_reminders_words_only() -> None:
    reminder = _fire(1, message="call mom", label="call mom")
    masked = timer_fires.mask_fire(reminder)
    assert masked["message"] is None and masked["label"] is None and masked["masked"] is True
    assert reminder["message"] == "call mom"                        # a copy
    assert masked["summary"] == reminder["summary"] and masked["deliveries"] == reminder["deliveries"]
    timer = _fire(2, label="pasta")
    assert timer_fires.mask_fire(timer) == timer


# ─── the seam's SQL, against a recording session ─────────────────────────


class _SqlSession:
    def __init__(self, *, ready=True, fires=(), deliveries=(), flag=None) -> None:
        self.ready, self.fires, self.deliveries, self.flag = ready, list(fires), list(deliveries), flag
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params))
        if "to_regclass" in sql:
            return _Result(scalar=self.ready)
        if "FROM timer_fire_deliveries" in sql:
            return _Result(rows=self.deliveries)
        if "FROM timer_fires" in sql:
            return _Result(rows=self.fires)
        if sql.startswith("SELECT enabled_at FROM timer_own_only_rooms"):
            return _Result(scalar=self.flag)
        if "FROM timer_own_only_rooms" in sql:
            return _Result(rows=[("garage", T0)])
        return _Result()

    def sql(self) -> list[str]:
        return [c[0] for c in self.calls]


_FIRE_ROW = (7, 17, "timer", "pasta", None, "kitchen", T0 - timedelta(minutes=10), T0, T0,
             None, None, None)


@pytest.mark.asyncio
async def test_the_history_query_catches_up_oldest_first() -> None:
    s = _SqlSession(fires=[_FIRE_ROW], deliveries=[
        (7, "office", False, "pending", "capturing", None),
        (7, "kitchen", True, "spoken", None, T0)])
    out = await timer_fires.recent_fires(s, since_id=5, origin_room_id="kitchen", timer_id=17, limit=9)
    probe, query, deliveries = s.calls
    assert "to_regclass('public.timer_fires')" in probe[0]
    assert "id > :since_id" in query[0] and "origin_room_id = :origin_room_id" in query[0]
    assert "timer_id = :timer_id" in query[0] and "ORDER BY id ASC" in query[0]
    assert query[1] == {"since_id": 5, "origin_room_id": "kitchen", "timer_id": 17, "limit": 9}
    assert "fire_id = ANY(:ids)" in deliveries[0] and deliveries[1] == {"ids": [7]}
    (f,) = out
    assert f["room_id"] == "kitchen" and f["label"] == "pasta" and f["kind"] == "timer"
    assert [d["room_id"] for d in f["deliveries"]] == ["kitchen", "office"]
    assert f["summary"] == "heard in kitchen · still announcing"


@pytest.mark.asyncio
async def test_the_history_query_is_newest_first_within_a_window() -> None:
    s = _SqlSession(fires=[])
    assert await timer_fires.recent_fires(s, window_sec=600, limit=20) == []
    query = s.calls[1]
    assert "fired_at >= now() - make_interval(secs => :window_sec)" in query[0]
    assert "ORDER BY fired_at DESC, id DESC" in query[0]
    assert query[1] == {"window_sec": 600.0, "limit": 20}
    assert len(s.calls) == 2                                       # no deliveries read for nothing


@pytest.mark.asyncio
async def test_the_seam_never_reads_the_spoken_words() -> None:
    s = _SqlSession(fires=[_FIRE_ROW], deliveries=[(7, "kitchen", True, "spoken", None, T0)])
    await timer_fires.recent_fires(s)
    for sql in s.sql():
        assert "base_text" not in sql and "spoken_text" not in sql


@pytest.mark.asyncio
async def test_without_v017_the_seam_says_so_and_touches_nothing() -> None:
    s = _SqlSession(ready=False)
    assert await timer_fires.recent_fires(s) is None
    assert await timer_fires.own_only_rooms(s) == {}
    with pytest.raises(timer_fires.LedgerMissing):
        await timer_fires.set_own_only(s, "garage", True)
    assert all("to_regclass" in sql for sql in s.sql())


@pytest.mark.asyncio
async def test_the_flag_is_an_idempotent_insert_or_a_delete() -> None:
    s = _SqlSession(flag=T0)
    assert await timer_fires.set_own_only(s, "garage", True) == T0
    assert any("INSERT INTO timer_own_only_rooms (room_id) VALUES (:r) ON CONFLICT (room_id) DO NOTHING"
               in sql for sql in s.sql())
    s = _SqlSession()
    assert await timer_fires.set_own_only(s, "garage", False) is None
    assert s.sql()[1] == "DELETE FROM timer_own_only_rooms WHERE room_id = :r"
    assert len(s.calls) == 2
    assert await timer_fires.own_only_rooms(_SqlSession()) == {"garage": T0}


# ─── W4: the flag's routes and the roster ────────────────────────────────


@pytest.mark.asyncio
async def test_the_flag_reads_off_by_default_and_on_with_its_since(claimed, seam) -> None:
    async with _anon() as c:
        off = (await c.get("/api/satellites/garage/timer-announcements")).json()
    assert off == {"room_id": "garage", "own_only": False, "since": None}
    async with _token() as c:
        c.headers.update(BROWSER)
        on = (await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})).json()
        again = (await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})).json()
        read = (await c.get("/api/satellites/garage/timer-announcements")).json()
        cleared = (await c.put("/api/satellites/garage/timer-announcements", json={"own_only": False})).json()
    assert on["own_only"] is True and on["since"].startswith("2026-09-30T15:00:00")
    assert again == on == read
    assert cleared == {"room_id": "garage", "own_only": False, "since": None}


@pytest.mark.asyncio
async def test_an_unknown_room_is_404_both_ways(claimed, seam) -> None:
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        assert (await c.get("/api/satellites/attic/timer-announcements")).status_code == 404
        r = await c.put("/api/satellites/attic/timer-announcements", json={"own_only": True})
    assert r.status_code == 404 and "attic" in r.json()["detail"]
    assert seam["set"] == []


@pytest.mark.parametrize("body", [{}, {"own_only": "yes"}, {"own_only": 1}, {"own_only": None},
                                  {"own_only": True, "room_id": "kitchen"}])
@pytest.mark.asyncio
async def test_a_bad_body_is_422(claimed, seam, body) -> None:
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        r = await c.put("/api/satellites/garage/timer-announcements", json=body)
    assert r.status_code == 422, (body, r.text)
    assert seam["set"] == []


@pytest.mark.asyncio
async def test_a_bad_room_id_is_422(claimed, seam) -> None:
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        r = await c.put(f"/api/satellites/{'r' * 121}/timer-announcements", json={"own_only": True})
        g = await c.get(f"/api/satellites/{'r' * 121}/timer-announcements")
    assert r.status_code == 422 and g.status_code == 422


@pytest.mark.asyncio
async def test_without_v017_the_write_is_503(claimed, seam) -> None:
    seam["missing"] = True
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        r = await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})
    assert r.status_code == 503
    assert "V017" in r.json()["detail"]


@pytest.mark.asyncio
async def test_the_write_is_logged_without_anything_said(claimed, seam, caplog) -> None:
    caplog.set_level("INFO", logger=sat_api.log.name)
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})
    assert "timer announcements: room garage own_only=True" in caplog.text


@pytest.fixture
def roster(monkeypatch, seam):
    async def rooms():
        return [{"room_id": r, "control_port": None, "http_port": None, "last_connected_at": None}
                for r in ("garage", "kitchen")]

    async def empty(*a, **kw):
        return {}

    monkeypatch.setattr(sat_api, "_list_rooms", rooms)
    monkeypatch.setattr(sat_api, "_list_pairings", empty)
    monkeypatch.setattr(sat_api, "_list_satellite_meta", empty)
    monkeypatch.setattr(sat_api.command_captures, "opted_in_rooms_or_empty", empty)
    monkeypatch.setattr(sat_api, "get_cached_snapshot", lambda: None)
    seam["flags"] = {"garage": T0}
    return seam


@pytest.mark.asyncio
async def test_every_roster_row_carries_the_flag(claimed, roster) -> None:
    async with _anon() as c:
        rows = {r["room_id"]: r for r in (await c.get("/api/satellites")).json()}
        one = (await c.get("/api/satellites/kitchen")).json()
    assert rows["garage"]["timers_own_only"] is True
    assert rows["kitchen"]["timers_own_only"] is False
    assert one["timers_own_only"] is False


@pytest.mark.asyncio
async def test_the_roster_survives_an_unreadable_flag(monkeypatch) -> None:
    @asynccontextmanager
    async def broken():
        raise RuntimeError("database away")
        yield  # pragma: no cover

    monkeypatch.setattr(sat_api, "session_scope", broken)
    assert await sat_api._own_only_rooms_or_empty() == {}
    room = {"room_id": "kitchen", "control_port": None, "http_port": None, "last_connected_at": None}
    assert (await sat_api._satellite_for(room, {}, {}, {})).timers_own_only is False


# ─── W5: realtime ────────────────────────────────────────────────────────


def test_the_notify_channel_maps_to_the_timer_fires_realtime_channel() -> None:
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME["timer_fires_changed"] == "timer_fires"
    assert realtime.StatePollLoop._CHANNEL_HELPERS["timer_fires"] is realtime._snapshot_timer_fires
    # A core channel, so no plugin [[realtime]] entry can take it over.
    assert "timer_fires" in realtime.CORE_REALTIME_CHANNELS
    # The existing channel is untouched.
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME["timers_changed"] == "timers"


@pytest.fixture
def pushed(monkeypatch):
    rec: dict[str, Any] = {"calls": [], "fires": [dict(f) for f in FIRES]}

    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def recent_fires(s, **kw):
        rec["calls"].append(kw)
        return None if rec["fires"] is None else [dict(f) for f in rec["fires"]]

    monkeypatch.setattr(realtime, "session_scope", fake_scope)
    monkeypatch.setattr(timer_fires, "recent_fires", recent_fires)
    return rec


@pytest.mark.asyncio
async def test_the_snapshot_is_the_last_hour_unmasked_as_json(pushed) -> None:
    snap = await realtime._snapshot_timer_fires()
    assert pushed["calls"] == [{"window_sec": 3600, "limit": 20}]
    assert [f["id"] for f in snap] == [12, 11]
    assert snap[0]["message"] == "call mom" and snap[0]["masked"] is False     # device tier
    assert snap[0]["summary"] == "heard in garage · still announcing"
    assert snap[0]["fired_at"] == FIRES[0]["fired_at"].isoformat()
    assert snap[0]["deliveries"][0]["finished_at"] == FIRES[0]["deliveries"][0]["finished_at"].isoformat()
    json.dumps(snap)                                                          # plain JSON
    # Stable between two reads with nothing changed, or the poll would push every tick.
    assert await realtime._snapshot_timer_fires() == snap
    pushed["fires"] = None
    assert await realtime._snapshot_timer_fires() == []


class _WS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_a_change_is_pushed_under_data_once(pushed) -> None:
    broadcaster = realtime.StateBroadcaster()
    ws = _WS()
    await broadcaster.connect(ws)
    loop = realtime.StatePollLoop(broadcaster)
    await loop.emit_for_channel("timer_fires")
    await loop.emit_for_channel("timer_fires")          # nothing changed: nothing sent
    (msg,) = [json.loads(m) for m in ws.sent]
    assert set(msg) == {"type", "data"}
    assert msg["type"] == "timer_fires.changed"
    assert [f["id"] for f in msg["data"]] == [12, 11]
    pushed["fires"][0]["summary"] = "heard in garage, kitchen"
    await loop.emit_for_channel("timer_fires")
    assert json.loads(ws.sent[-1])["data"][0]["summary"] == "heard in garage, kitchen"


# ─── DB-backed: the seam against a real database ─────────────────────────


async def _v017() -> None:
    if not V017.exists():
        pytest.skip("V017__timer_fires.sql is the core's; it arrives with that branch")
    # V017 holds several statements and a prepared statement takes one: the
    # core's testkit runs them one by one (IF NOT EXISTS), then empties the
    # three tables.
    from domovoi.tests.timer_fires_testkit import apply_v017

    await apply_v017()


async def _clear() -> None:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE timer_fire_deliveries, timer_fires, timer_own_only_rooms RESTART IDENTITY"))
        await conn.execute(text("DELETE FROM satellites WHERE room_id IN ('kitchen', 'garage')"))


@pytest.fixture
async def ledger(db_session):
    await _v017()
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM satellites WHERE room_id IN ('kitchen', 'garage')"))
        await conn.execute(text(
            "INSERT INTO satellites (room_id, adopted_via) VALUES ('kitchen', 'manual'), ('garage', 'manual')"))
    yield
    await _clear()


async def _insert_fire(*, message=None, label=None, origin="garage", ago_sec=30,
                       deliveries=(), acked_by=None) -> int:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        fid = (await conn.execute(text(
            """
            INSERT INTO timer_fires (timer_id, kind, label, message, origin_room_id, created_at,
                                     due_at, fired_at, base_text, acked_by)
            VALUES (:tid, :kind, :label, :message, :origin, now() - interval '10 minutes',
                    now() - make_interval(secs => :ago), now() - make_interval(secs => :ago),
                    'SECRET SPOKEN WORDS', :acked_by)
            RETURNING id
            """),
            {"tid": 17, "kind": "reminder" if message is not None else "timer", "label": label,
             "message": message, "origin": origin, "ago": float(ago_sec), "acked_by": acked_by},
        )).scalar_one()
        for room, is_origin, outcome in deliveries:
            await conn.execute(text(
                "INSERT INTO timer_fire_deliveries (fire_id, room_id, is_origin, outcome, detail,"
                " spoken_text, finished_at) VALUES (:f, :r, :o, :out, NULL, 'SECRET SPOKEN WORDS',"
                " CASE WHEN :out IN ('pending', 'sending') THEN NULL ELSE now() END)"),
                {"f": fid, "r": room, "o": is_origin, "out": outcome})
    return int(fid)


@requires_db
@pytest.mark.asyncio
async def test_db_the_history_reads_back_as_fires(ledger) -> None:
    from domovoi.db.session import session_scope

    old = await _insert_fire(label="eggs", ago_sec=20 * 60,
                             deliveries=[("garage", True, "spoken")])
    new = await _insert_fire(message="call mom", label="call mom", ago_sec=30,
                             deliveries=[("kitchen", False, "pending"), ("garage", True, "spoken")])
    async with session_scope() as s:
        newest = await timer_fires.recent_fires(s)
        since = await timer_fires.recent_fires(s, since_id=0)
        recent = await timer_fires.recent_fires(s, window_sec=600)
        garage = await timer_fires.recent_fires(s, origin_room_id="garage", limit=1)
        none_there = await timer_fires.recent_fires(s, origin_room_id="attic")
    assert [f["id"] for f in newest] == [new, old]
    assert [f["id"] for f in since] == [old, new]
    assert [f["id"] for f in recent] == [new]
    assert [f["id"] for f in garage] == [new] and none_there == []
    f = newest[0]
    assert f["is_reminder"] and f["message"] == "call mom" and f["room_id"] == "garage"
    assert [d["room_id"] for d in f["deliveries"]] == ["garage", "kitchen"]
    assert f["summary"] == "heard in garage · still announcing"
    assert "SECRET SPOKEN WORDS" not in json.dumps(newest, default=str)


@requires_db
@pytest.mark.asyncio
async def test_db_the_flag_turns_on_once_and_off(ledger) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        first = await timer_fires.set_own_only(s, "garage", True)
    async with session_scope() as s:
        again = await timer_fires.set_own_only(s, "garage", True)
        rooms = await timer_fires.own_only_rooms(s)
    assert first is not None and again == first and rooms == {"garage": first}
    async with session_scope() as s:
        assert await timer_fires.set_own_only(s, "garage", False) is None
        assert await timer_fires.own_only_rooms(s) == {}


@requires_db
@pytest.mark.asyncio
async def test_db_the_routes_end_to_end(_db, ledger) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await db_device_token()
    await _insert_fire(message="call mom", label="call mom", deliveries=[("garage", True, "spoken")])
    paired = {HEADER: token, "X-Requested-With": "domovoi-tests"}
    async with web_client() as anon:
        masked = (await anon.get("/api/timers")).json()["fires"]
        refused = await anon.put("/api/satellites/garage/timer-announcements", json={"own_only": True},
                                 headers={"X-Requested-With": "domovoi-tests"})
    async with web_client(headers=paired) as c:
        told = (await c.get("/api/timers/fires")).json()["fires"]
        on = await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})
        roster = {r["room_id"]: r for r in (await c.get("/api/satellites")).json()}
    assert masked[0]["message"] is None and masked[0]["masked"] is True
    assert refused.status_code == 401
    assert told[0]["message"] == "call mom"
    assert on.status_code == 200 and on.json()["own_only"] is True
    assert roster["garage"]["timers_own_only"] is True and roster["kitchen"]["timers_own_only"] is False
    snap = await realtime._snapshot_timer_fires()
    assert snap[0]["message"] == "call mom"
