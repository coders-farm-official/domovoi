"""Queue history (1.4.0): ``/api/music/up-next/history``.

* the routes' tier (from the route table): admin read, GET only;
* the summary's arithmetic (pure): counts, distinct artists, the closest
  repeat of one artist, the reason mix, decades;
* against the database: admin required (no credential and the household
  device token are refused, before and after a valid admin read), the
  session list newest first and paged, one session's picks in seq order
  with the listening history joined (a repeated song pairs in order, a
  removed or unreached pick has no listen, a non-``auto`` listen is never
  paired), the picks paged by cursor and offset, the CSV and JSON
  downloads' shape, an unknown session 404, and nothing written.

The DB-backed tests carry ``requires_db`` (a lane with V053/V056 applied).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin_with_cookie,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.api import up_next_history as history_api
from web.backend.main import app as web_app

BASE = "/api/music/up-next/history"
T0 = datetime(2026, 11, 15, 21, 0, tzinfo=timezone.utc)


# ─── pure ────────────────────────────────────────────────────────────────


def _gates(path: str) -> tuple[set, set]:
    methods, calls = set(), set()
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) != path:
            continue
        methods |= set(getattr(rc, "methods", None) or ())
        stack = [rc.dependant]
        while stack:
            for dep in stack.pop().dependencies:
                if dep.call is not None:
                    calls.add(dep.call)
                stack.append(dep)
    assert methods, f"{path} is not a web route"
    return methods, calls


@pytest.mark.parametrize("path", [BASE, f"{BASE}/{{session_id}}", f"{BASE}/{{session_id}}/download"])
def test_routes_are_admin_reads_only(path) -> None:
    methods, calls = _gates(path)
    assert methods <= {"GET", "HEAD"}, f"{path} must stay read-only"
    assert admin_auth.require_admin_read in calls
    assert admin_auth.require_device_read not in calls and admin_auth.require_device not in calls


def _row(seq, artist, year, code, status, **kw):
    return {"seq": seq, "artist": artist, "artist_norm": artist.lower(), "year": year,
            "reason_code": code, "status": status, "played_sec": kw.get("played_sec")}


def test_summary() -> None:
    rows = [
        _row(1, "Nina", 1965, "same_artist", "played", played_sec=200),
        _row(2, "Otis", 1968, "same_era", "skipped", played_sec=12),
        _row(3, "Ella", 1995, "same_genre", "played", played_sec=180),
        _row(4, "Otis", None, "wildcard", "removed"),
        _row(5, "Nina", 1966, "same_artist", "queued"),
    ]
    s = history_api.summarize(rows)
    assert (s["picks"], s["played"], s["skipped"], s["removed"], s["not_reached"]) == (5, 2, 1, 1, 1)
    assert s["listened_sec"] == 392
    assert s["distinct_artists"] == 3
    assert s["closest_repeat"] == {"songs": 2, "artist": "Otis", "from_seq": 2, "to_seq": 4}
    assert s["reasons"][0] == {"code": "same_artist", "words": "same artist", "count": 2}
    assert {r["code"] for r in s["reasons"]} == {"same_artist", "same_era", "same_genre", "wildcard"}
    assert s["decades"] == [{"decade": "1960s", "count": 3}, {"decade": "1990s", "count": 1},
                            {"decade": "unknown", "count": 1}]
    empty = history_api.summarize([])
    assert empty["picks"] == 0 and empty["closest_repeat"] is None and empty["listened_sec"] is None


def test_csv_cells_never_run_as_formulas() -> None:
    assert history_api._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert history_api._cell("-ish") == "'-ish"
    assert history_api._cell("Song") == "Song" and history_api._cell(None) == ""


# ─── against the database ────────────────────────────────────────────────


@pytest.fixture
async def clean():
    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE up_next_picks, up_next_sessions, music_listens, devices, "
                "library_tracks, people RESTART IDENTITY CASCADE"))

    await _truncate()
    yield
    await _truncate()


async def _seed() -> dict:
    """A room's long session (8 picks, a repeated song, a removed and an
    unreached pick, listens incl. one explicit) and two older ones."""
    async with engine.begin() as conn:
        async def track(title, artist, year):
            return (await conn.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, year) "
                "VALUES (:f, :t, :a, 'Album', 200, :y) RETURNING id"),
                {"f": f"/home/alex/Music/{artist}/{title}.flac", "t": title, "a": artist, "y": year})).scalar_one()

        seed = await track("Start", "Nina", 1965)
        a = await track("A", "Otis", 1967)
        b = await track("B", "Ella", 1995)
        c = await track("=Sneaky", "Miles", None)
        d = await track("D", "Nina", 1972)
        sarah = (await conn.execute(text("INSERT INTO people (name) VALUES ('Sarah') RETURNING id"))).scalar_one()

        async def session(target, created, ended=None, person=None):
            sid = str(uuid.uuid4())
            await conn.execute(text(
                "INSERT INTO up_next_sessions (id, target, person_id, mode, seed, rng_seed, created_at, "
                "updated_at, ended_at) VALUES (:id, :t, :p, 'library', CAST(:seed AS jsonb), 1, :c, :c, :e)"),
                {"id": sid, "t": target, "p": person, "c": created, "e": ended,
                 "seed": f'{{"track_id": {seed}, "kind": null, "name": null}}'})
            return sid

        old = await session("device:browser-a1", T0 - timedelta(days=3), T0 - timedelta(days=2))
        mid = await session("room:Kitchen", T0 - timedelta(days=1), T0 - timedelta(hours=20))
        night = await session("room:Living Room", T0, None, person=sarah)
        picks = [  # seq, track, code, label, status
            (1, a, "same_era", "60s", "played"),
            (2, b, "same_genre", "soul", "skipped"),
            (3, a, "same_era", "60s", "played"),        # the same song again
            (4, c, "wildcard", "wildcard", "removed"),
            (5, d, "same_artist", "same artist", "played"),
            (6, b, "same_genre", "soul", "played"),
            (7, d, "same_artist", "same artist", "queued"),
            (8, c, "wildcard", "wildcard", "played"),     # its listen was not 'auto'
        ]
        for seq, tid, code, label, status in picks:
            await conn.execute(text(
                "INSERT INTO up_next_picks (session_id, seq, batch_no, track_id, reason_code, reason_label, "
                "seed_track_id, status, why) VALUES (:s, :q, 1, :t, :c, :l, :seed, :st, "
                "CAST(:why AS jsonb))"),
                {"s": night, "q": seq, "t": tid, "c": code, "l": label, "seed": seed, "st": status,
                 "why": '[["same_artist", "last"]]' if code == "same_artist" else None})
        listens = [  # track, minutes after T0, played, outcome, origin
            (a, 4, 199, "finished", "auto"),
            (b, 8, 15, "skipped", "auto"),
            (a, 9, 180, "finished", "auto"),
            (d, 12, 200, "finished", "auto"),
            (b, 16, 190, "finished", "auto"),
            (c, 20, 100, "stopped", "explicit"),
            (seed, 0, 200, "finished", "explicit"),
        ]
        for tid, mins, played, outcome, origin in listens:
            await conn.execute(text(
                "INSERT INTO music_listens (track_id, room_id, player, session_id, origin, started_at, "
                "played_sec, duration_sec, outcome) VALUES (:t, 'Living Room', 'room', :s, :o, :at, :p, 200, :oc)"),
                {"t": tid, "s": night, "o": origin, "at": T0 + timedelta(minutes=mins), "p": played,
                 "oc": outcome})
    return {"old": old, "mid": mid, "night": night, "tracks": {"a": a, "b": b, "c": c, "d": d}}


async def _snapshot() -> tuple:
    async with engine.begin() as conn:
        p = (await conn.execute(text(
            "SELECT session_id, seq, status FROM up_next_picks ORDER BY 1, 2"))).all()
        s = (await conn.execute(text(
            "SELECT id, version, ended_at FROM up_next_sessions ORDER BY 1"))).all()
        n = (await conn.execute(text("SELECT count(*) FROM music_listens"))).scalar_one()
    return p, s, n


@requires_db
@pytest.mark.asyncio
async def test_admin_required(_db, clean) -> None:
    d = await _seed()
    async with web_client() as c:
        token, cookie = await claim_admin_with_cookie(c)
    household = await db_device_token()
    paths = [BASE, f"{BASE}/{d['night']}", f"{BASE}/{d['night']}/download?format=json"]
    async with web_client(headers={}) as c:
        for p in paths:
            assert (await c.get(p)).status_code == 401, p
            # The household device token is not an admin.
            r = await c.get(p, headers={HEADER: household})
            assert r.status_code == 401, (p, r.text)
            r = await c.get(p, headers=bearer(household))
            assert r.status_code == 401, (p, r.text)
            assert (await c.get(p, headers=bearer(token))).status_code == 200, p
    # The dashboard cookie reads it too: a download link works from a tab.
    async with web_client(headers={}, cookies={admin_auth.COOKIE_NAME: cookie}) as c:
        assert (await c.get(f"{BASE}/{d['night']}/download")).status_code == 200


@requires_db
@pytest.mark.asyncio
async def test_sessions_newest_first_and_paged(_db, clean) -> None:
    d = await _seed()
    async with web_client() as c:
        token, _ = await claim_admin_with_cookie(c)
    before = await _snapshot()
    async with web_client(headers=bearer(token)) as c:
        r = await c.get(BASE)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [s["id"] for s in body["sessions"]] == [d["night"], d["mid"], d["old"]]
        assert body["total"] == 3 and body["has_more"] is False and body["kept_days"] == 30
        night = body["sessions"][0]
        assert night["target"] == {"kind": "room", "room_id": "Living Room", "name": "Living Room"}
        assert night["person"]["name"] == "Sarah"
        assert night["seed"]["title"] == "Start" and night["seed"]["artist"] == "Nina"
        assert (night["picks"], night["played"], night["skipped"], night["removed"]) == (8, 5, 1, 1)
        assert night["ended_at"] is None and night["kept_until"] is None
        old = body["sessions"][2]
        assert old["target"]["kind"] == "device" and old["picks"] == 0
        assert old["kept_until"].startswith("2026-12-13")

        # Paged by cursor: no row twice, none skipped.
        r1 = (await c.get(BASE, params={"limit": 2})).json()
        assert len(r1["sessions"]) == 2 and r1["has_more"] and r1["next_cursor"] and r1["total"] == 3
        r2 = (await c.get(BASE, params={"limit": 2, "cursor": r1["next_cursor"]})).json()
        assert [s["id"] for s in r1["sessions"] + r2["sessions"]] == [d["night"], d["mid"], d["old"]]
        assert r2["has_more"] is False and r2["next_cursor"] is None
        # And by offset.
        r3 = (await c.get(BASE, params={"limit": 1, "offset": 1})).json()
        assert [s["id"] for s in r3["sessions"]] == [d["mid"]] and r3["offset"] == 1
        # One room only.
        r4 = (await c.get(BASE, params={"room": "Kitchen"})).json()
        assert [s["id"] for s in r4["sessions"]] == [d["mid"]] and r4["total"] == 1
        assert (await c.get(BASE, params={"limit": 999})).status_code == 422
        assert (await c.get(BASE, params={"cursor": "nonsense"})).status_code == 400
    assert await _snapshot() == before


@requires_db
@pytest.mark.asyncio
async def test_one_session_in_order_with_its_listens(_db, clean) -> None:
    d = await _seed()
    async with web_client() as c:
        token, _ = await claim_admin_with_cookie(c)
    before = await _snapshot()
    async with web_client(headers=bearer(token)) as c:
        r = await c.get(f"{BASE}/{d['night']}")
        assert r.status_code == 200, r.text
        body = r.json()
    picks = body["picks"]
    assert [p["seq"] for p in picks] == list(range(1, 9))
    assert body["total"] == 8 and body["has_more"] is False
    p1 = picks[0]
    assert (p1["title"], p1["artist"], p1["year"]) == ("A", "Otis", 1967)
    assert (p1["reason_code"], p1["reason"], p1["reason_label"]) == ("same_era", "same era", "60s")
    assert p1["seed"]["title"] == "Start" and p1["status"] == "played"
    # The listens pair in order: the first "A" with the first listen of A.
    assert p1["listen"]["played_sec"] == 199 and p1["listen"]["outcome"] == "finished"
    assert p1["listen"]["started_at"].startswith("2026-11-15T21:04")
    assert picks[2]["listen"]["played_sec"] == 180
    assert picks[1]["listen"]["outcome"] == "skipped" and picks[5]["listen"]["played_sec"] == 190
    # Removed and unreached picks have no listen; an explicit listen pairs with nothing.
    assert picks[3]["status"] == "removed" and picks[3]["listen"] is None
    assert picks[6]["status_words"] == "not reached" and picks[6]["listen"] is None
    assert picks[7]["status"] == "played" and picks[7]["listen"] is None
    assert picks[4]["why"] == "same artist as the last song"
    s = body["summary"]
    assert (s["picks"], s["played"], s["skipped"], s["removed"], s["not_reached"]) == (8, 5, 1, 1, 1)
    assert s["distinct_artists"] == 4
    assert s["closest_repeat"]["songs"] == 2
    assert {x["code"]: x["count"] for x in s["reasons"]} == {
        "same_era": 2, "same_genre": 2, "wildcard": 2, "same_artist": 2}
    assert {x["decade"]: x["count"] for x in s["decades"]} == {
        "1960s": 2, "1990s": 2, "1970s": 2, "unknown": 2}
    assert body["session"]["id"] == d["night"]
    assert await _snapshot() == before


@requires_db
@pytest.mark.asyncio
async def test_picks_paged(_db, clean) -> None:
    d = await _seed()
    async with web_client() as c:
        token, _ = await claim_admin_with_cookie(c)
    async with web_client(headers=bearer(token)) as c:
        seen, cursor = [], None
        while True:
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            r = (await c.get(f"{BASE}/{d['night']}", params=params)).json()
            seen += [p["seq"] for p in r["picks"]]
            # The summary covers the whole session on every page.
            assert r["summary"]["picks"] == 8 and r["total"] == 8
            if not r["has_more"]:
                break
            cursor = r["next_cursor"]
        assert seen == list(range(1, 9))
        r = (await c.get(f"{BASE}/{d['night']}", params={"limit": 2, "offset": 6})).json()
        assert [p["seq"] for p in r["picks"]] == [7, 8] and r["has_more"] is False
        # A later page still carries its listen (pairing is over the whole session).
        r = (await c.get(f"{BASE}/{d['night']}", params={"limit": 1, "offset": 5})).json()
        assert r["picks"][0]["listen"]["played_sec"] == 190
        sessions_cursor = (await c.get(BASE, params={"limit": 1})).json()["next_cursor"]
        assert (await c.get(f"{BASE}/{d['night']}", params={"cursor": sessions_cursor})).status_code == 400
        assert (await c.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404
        assert (await c.get(f"{BASE}/not-a-uuid")).status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_downloads(_db, clean) -> None:
    d = await _seed()
    async with web_client() as c:
        token, _ = await claim_admin_with_cookie(c)
    before = await _snapshot()
    async with web_client(headers=bearer(token)) as c:
        r = await c.get(f"{BASE}/{d['night']}/download", params={"format": "csv"})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert r.headers["content-disposition"] == 'attachment; filename="up-next-Living-Room-2026-11-15.csv"'
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert list(rows[0]) == list(history_api.CSV_COLUMNS)
        assert [int(x["seq"]) for x in rows] == list(range(1, 9))
        assert rows[0]["title"] == "A" and rows[0]["played_sec"] == "199" and rows[0]["outcome"] == "finished"
        assert rows[0]["reason"] == "same era" and rows[0]["seed_title"] == "Start"
        assert rows[3]["status"] == "removed" and rows[3]["played_sec"] == ""
        assert rows[3]["title"] == "'=Sneaky"     # never a formula in a spreadsheet

        r = await c.get(f"{BASE}/{d['night']}/download", params={"format": "json"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        assert r.headers["content-disposition"].endswith('.json"')
        doc = r.json()
        assert set(doc) == {"session", "summary", "picks"}
        assert doc["session"]["id"] == d["night"] and len(doc["picks"]) == 8
        assert doc["picks"][0]["listen"]["played_sec"] == 199
        assert doc["picks"][3]["title"] == "=Sneaky"
        assert doc["summary"]["picks"] == 8

        assert (await c.get(f"{BASE}/{d['night']}/download", params={"format": "xml"})).status_code == 422
        assert (await c.get(f"{BASE}/{uuid.uuid4()}/download")).status_code == 404
    assert await _snapshot() == before
