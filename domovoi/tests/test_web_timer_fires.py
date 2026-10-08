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
  grace read the words. Countdown times, rooms and kinds stay open, and a
  plain timer's label ("pasta") is not masked.
* **Rule F1** — the fire ledger is 7 days of when every timer went off,
  each room's outcome and reason code, and which room said "stop the
  timer" and when. The same household credentials read it whole (and the
  ``/ws/state`` socket, which admits exactly them, pushes it). Anyone else
  — no credential, a stale token, a throttled source — reads the last 10
  minutes only (``window_sec: 600``), each fire cut to what Home's done
  line and the alert card draw: ids, kind, origin room, times,
  ``heard_in`` and ``summary`` (without `` · stopped in …``), a plain
  timer's label; ``deliveries`` ``[]``, ``acked_at`` / ``acked_by`` /
  ``settled_at`` null.
* **Fire history** (V018) — ``GET /api/timers/fires`` (open, M1 + F1):
  ``since_id`` catches up oldest first, ``room_id`` is the room it was
  SET in, ``timer_id`` finds one timer's fire, ``limit`` 1–200. 503 — not
  an empty list — when V018 is missing, so a client can tell "unknown"
  from "none". ``GET /api/timers`` carries the last 10 minutes as
  ``fires`` (``null`` without V018). Each fire has a ``summary`` the web
  computes ("heard in garage, kitchen · still announcing").
* **"Only reminders for this device"** — ``GET`` (open) and ``PUT``
  (device tier + the CSRF header) ``/api/satellites/{room}/timer-
  announcements``; every roster row carries ``timers_own_only``.
* **Realtime** — ``NOTIFY timer_fires_changed`` → the core channel
  ``timer_fires`` → ``{"type": "timer_fires.changed", "data": [Fire]}``,
  the last hour, unmasked (the socket is device tier).

Two halves:

* DB-FREE (always runs): the routes through the real web app with the
  auth primitives faked (``auth_testkit.install_fake_db``) and the V018
  seam (:mod:`web.backend.timer_fires`) replaced by fakes; the seam's own
  SQL shape against a recording fake session; the summary rules.
* DB-BACKED (``requires_db``): the seam against a real database. The V018
  tables are created from the migration file itself, statement by
  statement (``timer_fires_testkit.apply_v018``; the V016 precedent in
  test_command_captures_api runs its one statement whole), and truncated
  around each test — they are NOT in conftest's TABLES_TO_TRUNCATE, so a
  lane without V018 still runs everything else. Skipped when the file is
  not on this branch (V018 is the core's).
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
V018 = MIGRATIONS / "V018__timer_fires.sql"


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
    """The routes' database: a fake session with TIMER_ROWS, and the V018
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
    # Where it was heard is not speech: Home's line stays. Each room's own
    # row (outcome, reason code, finish time) is held back (rule F1).
    assert fires[12]["summary"] == "heard in garage · still announcing"
    assert fires[12]["heard_in"] == ["garage"]
    assert fires[12]["deliveries"] == []
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
async def test_a_read_that_answers_everyone_alike_classifies_nobody(
    monkeypatch, claimed, seam
) -> None:
    """A wrong token costs backoff, so an open timer read classifies the
    caller only when its answer depends on who asks."""
    # Plain timers only, nothing went off: nobody.
    session = _RouteSession([_timer_row(3, label="pasta", room_id="kitchen")])

    @asynccontextmanager
    async def plain_scope():
        yield session

    monkeypatch.setattr(sat_api, "session_scope", plain_scope)
    seam["fires"] = []
    async with _anon() as c:
        await c.get("/api/satellites/kitchen/timers")
        await c.get("/api/timers")
    assert seam["classified"] == 0
    # A fire in it: cut down for this caller or not (rule F1).
    seam["fires"] = [dict(FIRES[1])]
    async with _anon() as c:
        await c.get("/api/timers")
    assert seam["classified"] == 1
    # A reminder in it: masked for this caller or not (rule M1).
    session.rows = TIMER_ROWS
    seam["fires"] = []
    async with _anon() as c:
        await c.get("/api/satellites/garage/timers")
    assert seam["classified"] == 2


@pytest.mark.asyncio
async def test_the_history_always_asks_who_is_reading(claimed, seam) -> None:
    """How far back GET /api/timers/fires reaches depends on the caller
    (rule F1), even when the answer is empty — so it always classifies, and
    a presented token always goes through the backoff."""
    seam["fires"] = []
    async with _anon() as c:
        assert (await c.get("/api/timers/fires")).json()["window_sec"] == 600
        await c.get("/api/timers/fires?room_id=kitchen")
    assert seam["classified"] == 2


# ─── F1: who reads the whole fire ledger ─────────────────────────────────

# A pasta timer that went off in the kitchen: heard there and in the
# office (where somebody started talking over it), still waiting in the
# garage (a live call), and stopped from the office. Everything rule F1
# holds back from an open read is set on it.
_WHOLE = timer_fires.fire_from_row(
    {"id": 21, "timer_id": 121, "label": "pasta", "message": None, "room_id": "kitchen",
     "created_at": T0 - timedelta(minutes=12), "due_at": T0 - timedelta(minutes=2),
     "fired_at": T0 - timedelta(minutes=2), "settled_at": T0 - timedelta(seconds=10),
     "acked_at": T0 - timedelta(seconds=50), "acked_by": "office"},
    [_delivery("kitchen", "spoken", origin=True), _delivery("office", "interrupted"),
     _delivery("garage", "pending", detail="in_call"),
     _delivery("attic", "busy_timeout", detail="recording")],
)
# A garage reminder nobody has heard yet.
_ON_ITS_WAY = timer_fires.fire_from_row(
    {"id": 22, "timer_id": 122, "label": "call mom", "message": "call mom", "room_id": "garage",
     "created_at": T0 - timedelta(minutes=5), "due_at": T0 - timedelta(seconds=20),
     "fired_at": T0 - timedelta(seconds=20), "settled_at": None, "acked_at": None,
     "acked_by": None},
    [_delivery("garage", "pending", origin=True, detail="capturing")],
)

# What an open read carries of each field, for the two fires above. `same`
# = exactly the ledger's value; anything else is the value it must read.
_SAME = object()
_OPEN_FIELDS: dict[str, tuple[Any, Any]] = {
    # field: (pasta timer, garage reminder)
    "id": (_SAME, _SAME),
    "timer_id": (_SAME, _SAME),
    "kind": (_SAME, _SAME),
    "is_reminder": (_SAME, _SAME),
    "room_id": (_SAME, _SAME),
    "created_at": (_SAME, _SAME),
    "due_at": (_SAME, _SAME),
    "fired_at": (_SAME, _SAME),
    "heard_in": (_SAME, _SAME),
    # A plain timer's label was served openly while it ran; a reminder's
    # label holds its words (rule M1).
    "label": (_SAME, None),
    "message": (_SAME, None),
    "masked": (_SAME, True),
    # Home's line stays, less who stopped it.
    "summary": ("heard in kitchen, office · still announcing", _SAME),
    "deliveries": ([], []),
    "acked_at": (None, None),
    "acked_by": (None, None),
    "settled_at": (None, None),
}
_WHOLE_JSON = {
    "summary": "heard in kitchen, office · still announcing · stopped in office",
    "acked_by": "office",
    "delivery_rows": [("kitchen", "spoken", None), ("attic", "busy_timeout", "recording"),
                      ("garage", "pending", "in_call"), ("office", "interrupted", None)],
}

# Every DeviceCheckResult, and whether it reads the whole ledger.
_TIERS = {
    "token": True, "bearer": True, "cookie": True, "pre-setup": True,
    "anon": False, "stale-token": False, "throttled": False,
}


def _tier_client(monkeypatch, who: str) -> AsyncClient:
    """A client presenting ``who``'s credential, on an install set up for it."""
    install_fake_db(monkeypatch, admin=who != "pre-setup", sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    if who == "token":
        return _client({HEADER: DEVICE_TOKEN})
    if who == "bearer":
        return _client(bearer(ADMIN_TOKEN))
    if who == "cookie":
        return _client(cookies={COOKIE: ADMIN_TOKEN})
    if who == "stale-token":
        return _client({HEADER: "not-the-token"})
    if who == "throttled":
        # Ten charged failures: a wait of minutes, however slow this run is.
        for _ in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS + 10):
            admin_auth.DEVICE_TOKEN_BACKOFF.record_failure("127.0.0.1")
        # The right token, from a source that guessed too often: not looked at.
        return _client({HEADER: DEVICE_TOKEN})
    return _client()


async def _both_reads(monkeypatch, seam, who: str) -> tuple[dict, dict]:
    seam["fires"] = [dict(_WHOLE), dict(_ON_ITS_WAY)]
    async with _tier_client(monkeypatch, who) as c:
        hist = await c.get("/api/timers/fires")
        listed = await c.get("/api/timers")
    assert hist.status_code == 200 and listed.status_code == 200
    return hist.json(), listed.json()


@pytest.mark.parametrize("who", sorted(_TIERS))
@pytest.mark.asyncio
async def test_the_history_reaches_back_by_tier(monkeypatch, seam, who) -> None:
    """The whole kept history to a household credential; the last 10
    minutes to anyone else — asked of the database, and said in the
    answer (``window_sec``) so a client never reads an empty window as an
    empty history."""
    hist, _ = await _both_reads(monkeypatch, seam, who)
    window = None if _TIERS[who] else 600
    assert seam["recent"][0]["window_sec"] == window
    assert hist["window_sec"] == window
    # GET /api/timers reads its own 10 minutes whoever asks.
    assert seam["recent"][1] == {"window_sec": 600, "limit": 20}


@pytest.mark.parametrize("who", sorted(_TIERS))
@pytest.mark.asyncio
async def test_each_tier_reads_the_whole_fire_or_the_cut_down_one(monkeypatch, seam, who) -> None:
    hist, listed = await _both_reads(monkeypatch, seam, who)
    for fires in (hist["fires"], listed["fires"]):
        pasta, reminder = _by_id(fires)[21], _by_id(fires)[22]
        if _TIERS[who]:
            assert pasta["summary"] == _WHOLE_JSON["summary"]
            assert pasta["acked_by"] == "office" and pasta["acked_at"] and pasta["settled_at"]
            assert [(d["room_id"], d["outcome"], d["detail"]) for d in pasta["deliveries"]] == (
                _WHOLE_JSON["delivery_rows"])
            assert reminder["message"] == "call mom" and reminder["masked"] is False
            assert reminder["deliveries"][0]["detail"] == "capturing"
        else:
            assert pasta["deliveries"] == [] and reminder["deliveries"] == []
            assert pasta["acked_by"] is None and pasta["acked_at"] is None
            assert pasta["settled_at"] is None
            assert "stopped" not in pasta["summary"] and "office" in pasta["heard_in"]
            assert reminder["message"] is None and reminder["masked"] is True


@pytest.mark.parametrize("field", sorted(_OPEN_FIELDS))
@pytest.mark.asyncio
async def test_every_field_an_open_read_carries(monkeypatch, seam, field) -> None:
    """Field by field, what a caller with no household credential reads of a
    fire — on the history and in GET /api/timers alike — against what the
    household token reads."""
    whole, whole_list = await _both_reads(monkeypatch, seam, "token")
    seam["recent"].clear()
    cut, cut_list = await _both_reads(monkeypatch, seam, "anon")
    assert _by_id(cut["fires"]) == _by_id(cut_list["fires"])          # one rule, both reads
    assert _by_id(whole["fires"]) == _by_id(whole_list["fires"])
    for fid, expected in zip((21, 22), _OPEN_FIELDS[field]):
        got = _by_id(cut["fires"])[fid][field]
        want = _by_id(whole["fires"])[fid][field] if expected is _SAME else expected
        assert got == want, (fid, field, got, want)
    # And nothing beyond the Fire shape rides along.
    assert set(_by_id(cut["fires"])[21]) == set(_OPEN_FIELDS)


@pytest.mark.asyncio
async def test_the_open_read_still_says_where_home_s_line_needs(claimed, seam) -> None:
    """The summary an unpaired tablet shows is Home's line in every case but
    the acknowledgement: still announcing, heard nowhere with the origin
    room offline (the open roster already says which rooms are offline),
    announced nowhere."""
    seam["fires"] = [
        _fire(31, deliveries=[_delivery("garage", "pending", origin=True, detail="responding")]),
        _fire(32, deliveries=[_delivery("garage", "offline", origin=True)], acked_by="kitchen"),
        _fire(33, room_id=None, deliveries=[]),
    ]
    async with _anon() as c:
        fires = _by_id((await c.get("/api/timers/fires")).json()["fires"])
    assert fires[31]["summary"] == "announcing…" and fires[31]["deliveries"] == []
    assert fires[32]["summary"] == "not heard in any room (garage offline)"
    assert fires[33]["summary"] == "not announced in any room"


@pytest.mark.asyncio
async def test_a_guessed_token_on_the_history_pays_the_backoff(claimed, seam) -> None:
    """The answer tells a good token from a bad one (how far back it
    reaches), so guessing through it costs what guessing anywhere does:
    after the free attempts, even the right token is not looked at."""
    seam["fires"] = []                   # an empty answer is charged all the same
    async with _client({HEADER: "guess"}) as c:
        for _ in range(admin_auth.DEVICE_TOKEN_FREE_ATTEMPTS + 1):
            assert (await c.get("/api/timers/fires")).json()["window_sec"] == 600
    # Every guess was charged: this source now waits before it may guess again.
    assert admin_auth.DEVICE_TOKEN_BACKOFF.retry_after("127.0.0.1") > 0
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()
    async with _token() as c:
        assert (await c.get("/api/timers/fires")).json()["window_sec"] is None


def test_open_fire_cuts_a_copy_and_leaves_the_ledger_whole() -> None:
    fire = dict(_WHOLE)
    cut = timer_fires.open_fire(fire)
    assert fire == _WHOLE                                          # a copy
    assert fire["deliveries"] and fire["acked_by"] == "office"
    assert cut["deliveries"] == [] and cut["acked_by"] is None
    assert cut["summary"] == "heard in kitchen, office · still announcing"
    assert timer_fires.open_fire(cut) == cut                       # idempotent
    reminder = timer_fires.open_fire(dict(_ON_ITS_WAY))
    assert reminder["message"] is None and reminder["label"] is None and reminder["masked"] is True
    assert reminder["summary"] == "announcing…"


def test_the_open_window_is_the_timer_list_s() -> None:
    """One slice of the ledger is open: the 10 minutes GET /api/timers
    carries for Home, which is also as far back as the dashboard's alert
    catch-up alerts (components.jsx TIMER_FIRE_CATCHUP_MS)."""
    assert timer_fires.OPEN_WINDOW_SEC == 600
    assert sat_api._TIMERS_FIRES_WINDOW_SEC == timer_fires.OPEN_WINDOW_SEC
    comps = (Path(__file__).resolve().parents[2] / "web" / "static" / "components.jsx").read_text(
        encoding="utf-8")
    assert "const TIMER_FIRE_CATCHUP_MS = 10 * 60 * 1000;" in comps


def test_the_whole_ledger_is_the_reminder_words_tier() -> None:
    """Rule F1 and rule M1 are one tier: whoever reads a reminder's words
    reads the whole ledger, and nobody else does."""
    assert sat_api._READS_FIRE_LEDGER == sat_api._READS_REMINDER_TEXT
    assert set(sat_api._READS_FIRE_LEDGER) == {"ok", "admin", "pre-setup", "cookie-only"}


def test_the_docs_say_who_reads_the_fire_history() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    security = (docs / "SECURITY_PRIVACY.md").read_text(encoding="utf-8")
    who = next(line for line in security.splitlines()
                if line.startswith("| **Who reads the fire history** (rule F1)"))
    for phrase in ("7 days", "`/ws/state`", "`window_sec`", "backoff"):
        assert phrase in who, phrase
    shows = next(line for line in security.splitlines()
                 if line.startswith("| **What the open read still shows**"))
    for phrase in ("only the last 10 minutes", "`deliveries: []`", "`acked_by`", "`in_call`",
                   "Held back from it"):
        assert phrase in shows, phrase
    assert "pages back through the whole 7-day history with no credential" not in security
    api = (docs / "API_REFERENCE.md").read_text(encoding="utf-8")
    row = next(line for line in api.splitlines() if line.startswith("| `GET /api/timers/fires`"))
    assert "Open, M1, F1" in row and "`window_sec: 600`" in row and "`window_sec: null`" in row
    assert "**Rule F1 — the fire history.**" in api


# ─── W3: the fire history route ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_history_passes_its_filters_through(claimed, seam) -> None:
    async with _token() as c:
        r = await c.get("/api/timers/fires?since_id=5&room_id=garage&timer_id=17&limit=200")
        assert r.status_code == 200
        body = r.json()
        await c.get("/api/timers/fires")
    async with _anon() as c:
        await c.get("/api/timers/fires?since_id=5&room_id=garage&timer_id=17&limit=200")
    assert seam["recent"][0] == {"since_id": 5, "origin_room_id": "garage", "timer_id": 17,
                                 "limit": 200, "window_sec": None}
    assert seam["recent"][1] == {"since_id": None, "origin_room_id": None, "timer_id": None,
                                 "limit": 50, "window_sec": None}
    # No credential: the same filters, held to the last 10 minutes (F1).
    assert seam["recent"][2] == {"since_id": 5, "origin_room_id": "garage", "timer_id": 17,
                                 "limit": 200, "window_sec": 600}
    assert body["server_now"].startswith("2026-09-30T15:00:00")
    assert set(body) == {"server_now", "fires", "window_sec"}
    assert body["window_sec"] is None
    assert [f["id"] for f in body["fires"]] == [12, 11]
    f = body["fires"][0]
    assert set(f) == {"id", "timer_id", "kind", "is_reminder", "label", "message", "masked",
                      "room_id", "created_at", "due_at", "fired_at", "settled_at", "acked_at",
                      "acked_by", "heard_in", "summary", "deliveries"}
    assert set(f["deliveries"][0]) == {"room_id", "is_origin", "outcome", "detail", "finished_at"}


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "since_id=-1", "room_id=" + "r" * 121,
                                   "timer_id=x", "timer_id=-1",
                                   # Past the columns' own range (timer_id INTEGER,
                                   # id BIGINT): a 422, not a 500 out of the driver
                                   # that any LAN caller could provoke.
                                   "timer_id=3000000000", "timer_id=2147483648",
                                   "since_id=9223372036854775808",
                                   "since_id=99999999999999999999"])
@pytest.mark.asyncio
async def test_the_history_refuses_bad_bounds(claimed, seam, query) -> None:
    async with _anon() as c:
        r = await c.get(f"/api/timers/fires?{query}")
    assert r.status_code == 422, r.text
    assert seam["recent"] == []


@pytest.mark.asyncio
async def test_the_history_takes_the_largest_ids_the_columns_hold(claimed, seam) -> None:
    async with _anon() as c:
        a = await c.get(f"/api/timers/fires?timer_id={2**31 - 1}")
        b = await c.get(f"/api/timers/fires?since_id={2**63 - 1}")
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text


@pytest.mark.asyncio
async def test_no_fire_history_is_503_there_and_null_in_the_timer_list(claimed, seam) -> None:
    seam["fires"] = None
    async with _anon() as c:
        r = await c.get("/api/timers/fires")
        timers = (await c.get("/api/timers")).json()
    assert r.status_code == 503
    assert r.json() == {"detail": "timer fire history needs database migration V018 — run Flyway"}
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
    # No room was going to announce it: none online, or every online room
    # announces only its own (a reminder set with no room).
    ([], None, "not announced in any room"),
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
async def test_without_v018_the_seam_says_so_and_touches_nothing() -> None:
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
async def test_without_v018_the_write_is_503(claimed, seam) -> None:
    seam["missing"] = True
    async with _client({**BROWSER, HEADER: DEVICE_TOKEN}) as c:
        r = await c.put("/api/satellites/garage/timer-announcements", json={"own_only": True})
    assert r.status_code == 503
    assert "V018" in r.json()["detail"]


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
    # The roster is a household read since WEB-15 (2026-10-08).
    async with _token() as c:
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


async def _v018() -> None:
    if not V018.exists():
        pytest.skip("V018__timer_fires.sql is the core's; it arrives with that branch")
    # V018 holds several statements and a prepared statement takes one: the
    # core's testkit runs them one by one (IF NOT EXISTS), then empties the
    # three tables.
    from domovoi.tests.timer_fires_testkit import apply_v018

    await apply_v018()


async def _clear() -> None:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE timer_fire_deliveries, timer_fires, timer_own_only_rooms RESTART IDENTITY"))
        await conn.execute(text("DELETE FROM satellites WHERE room_id IN ('kitchen', 'garage')"))


@pytest.fixture
async def ledger(db_session):
    await _v018()
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
        for room, is_origin, outcome, *detail in deliveries:
            await conn.execute(text(
                "INSERT INTO timer_fire_deliveries (fire_id, room_id, is_origin, outcome, detail,"
                " spoken_text, finished_at) VALUES (:f, :r, :o, :out, :detail, 'SECRET SPOKEN WORDS',"
                " CASE WHEN :out IN ('pending', 'sending') THEN NULL ELSE now() END)"),
                {"f": fid, "r": room, "o": is_origin, "out": outcome,
                 "detail": detail[0] if detail else None})
        if acked_by is not None:
            await conn.execute(text(
                "UPDATE timer_fires SET acked_at = now(), settled_at = now() WHERE id = :f"),
                {"f": fid})
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


@requires_db
@pytest.mark.asyncio
async def test_db_an_open_read_reaches_back_ten_minutes(_db, ledger) -> None:
    """Rule F1 against a real database, through the real routes: with no
    credential, the history is the last 10 minutes only (however it is
    asked: newest first, since an id, by room, by timer), each fire cut
    down; the household token reads all of it, whole."""
    async with web_client() as c:
        await claim_admin(c)
    token = await db_device_token()
    old = await _insert_fire(label="eggs", origin="kitchen", ago_sec=2 * 3600,
                             deliveries=[("kitchen", True, "spoken"),
                                         ("garage", False, "interrupted")],
                             acked_by="garage")
    new = await _insert_fire(label="pasta", origin="kitchen", ago_sec=60,
                             deliveries=[("kitchen", True, "spoken"),
                                         ("garage", False, "pending", "in_call")],
                             acked_by="kitchen")
    old_timer = "/api/timers/fires?timer_id=17&room_id=kitchen&since_id=0&limit=200"
    async with web_client() as anon:
        newest = (await anon.get("/api/timers/fires")).json()
        since = (await anon.get("/api/timers/fires?since_id=0")).json()
        by_room = (await anon.get("/api/timers/fires?room_id=kitchen&limit=200")).json()
        by_timer = (await anon.get(old_timer)).json()
        listed = (await anon.get("/api/timers")).json()["fires"]
    for body in (newest, since, by_room, by_timer):
        assert body["window_sec"] == 600
        assert [f["id"] for f in body["fires"]] == [new]
    for f in (newest["fires"][0], listed[0]):
        assert f["id"] == new and f["label"] == "pasta"
        assert f["deliveries"] == [] and f["acked_by"] is None and f["acked_at"] is None
        assert f["settled_at"] is None
        assert f["heard_in"] == ["kitchen"]
        assert f["summary"] == "heard in kitchen · still announcing"
    async with web_client(headers={HEADER: token}) as c:
        whole = (await c.get("/api/timers/fires")).json()
        whole_since = (await c.get("/api/timers/fires?since_id=0")).json()
    assert whole["window_sec"] is None
    assert [f["id"] for f in whole["fires"]] == [new, old]
    assert [f["id"] for f in whole_since["fires"]] == [old, new]
    top = whole["fires"][0]
    assert top["acked_by"] == "kitchen" and top["acked_at"] and top["settled_at"]
    assert [(d["room_id"], d["outcome"], d["detail"]) for d in top["deliveries"]] == [
        ("kitchen", "spoken", None), ("garage", "pending", "in_call")]
    assert top["summary"] == "heard in kitchen · still announcing · stopped in kitchen"
    assert "SECRET SPOKEN WORDS" not in json.dumps([newest, whole, listed])
