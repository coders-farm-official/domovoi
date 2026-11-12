"""Up next for browsers and phones (V053, phase P3): ``/api/music/up-next``.

* the migration's shape and the routes' tiers (from the route table);
* starting a session: list / library / continue modes, the first batch, the
  reasons a person reads, a new start ending the device's previous one;
* ``next`` is idempotent: the same ``have_through_seq`` gets the same batch;
* feedback: remove, less like this (the artist's waiting picks come back
  as ``removed``), an early skip; the Autoplay switch (off: library mode
  draws nothing, a list stops at its end; on again: the list goes round);
* whose mix it is: listening as, the claimed device, a shared screen,
  household on purpose, an unknown person refused;
* a device acts on its own sessions only; no credential is 401;
* the listening history feeds the session (drift seeds, two early skips
  of one artist exclude it), also under "Don't keep".

The DB-backed tests carry ``requires_db`` (a lane with V053 applied).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    claim_admin,
    db_device_token,
    web_client,
    bearer,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.api import music_listens as listens_api
from web.backend.api import up_next as up_next_api
from web.backend.main import app as web_app

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = next((REPO_ROOT / "domovoi" / "db" / "migrations").glob("V053__*.sql"))
BASE = "/api/music/up-next"


# ─── pure ────────────────────────────────────────────────────────────────


def test_migration_shape() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for stmt in body.split(";"):
        assert "--" not in stmt
    assert "CREATE TABLE IF NOT EXISTS up_next_sessions" in body
    assert "CREATE TABLE IF NOT EXISTS up_next_picks" in body
    assert re.search(r"uq_up_next_sessions_active\s+ON up_next_sessions \(target\) WHERE ended_at IS NULL", body)
    assert re.search(r"person_id\s+INT\s+REFERENCES people\(id\) ON DELETE SET NULL", body)
    assert "REFERENCES up_next_sessions(id) ON DELETE CASCADE" in body
    assert "PRIMARY KEY (session_id, seq)" in body


def _gates(method: str, path: str) -> set:
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or ()):
            out, stack = set(), [rc.dependant]
            while stack:
                for dep in stack.pop().dependencies:
                    if dep.call is not None:
                        out.add(dep.call)
                    stack.append(dep)
            return out
    raise AssertionError(f"{method} {path} is not a web route")


def test_route_tiers() -> None:
    assert admin_auth.require_device in _gates("POST", f"{BASE}/sessions")
    assert admin_auth.require_device_read in _gates("GET", f"{BASE}/sessions/{{session_id}}")
    for path in (f"{BASE}/sessions/{{session_id}}/next", f"{BASE}/sessions/{{session_id}}/feedback"):
        assert admin_auth.require_device in _gates("POST", path)
    assert admin_auth.require_device in _gates("PATCH", f"{BASE}/sessions/{{session_id}}")


def test_list_reasons_say_what_the_pick_shares() -> None:
    from domovoi.up_next.model import Track

    seed = Track(1, "nina", 1966, ("soul", "jazz"))
    r = up_next_api._list_reason
    assert r(seed, Track(2, "nina", 1990), "from road trip") == "same artist"
    assert r(seed, Track(3, "otis", 1968), "from road trip") == "60s"
    assert r(seed, Track(4, "otis", 1970), "x") == "from 1966-ish"
    assert r(seed, Track(5, "ella", 1990, ("jazz",)), "x") == "jazz"
    assert r(seed, Track(6, "ella", 1990, ("rock",)), "from road trip") == "from road trip"
    assert r(None, Track(6, "ella"), "from this list") == "from this list"


# ─── against the database ────────────────────────────────────────────────


@pytest.fixture
async def clean():
    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE up_next_picks, up_next_sessions, music_listens, music_room_listeners, "
                "playlist_tracks, playlists, devices, library_tracks, people RESTART IDENTITY CASCADE"))

    await _truncate()
    yield
    await _truncate()


ARTISTS = ("Nina", "Otis", "Ella", "Miles", "Aretha", "Sam")


async def _seed(n: int = 60) -> dict:
    """``n`` tracks by six artists over three decades, two genres; a playlist
    "Road Trip" of the first 20; two people."""
    async with engine.begin() as conn:
        ids = []
        for i in range(n):
            a = ARTISTS[i % len(ARTISTS)]
            year = (1965, 1985, 1995)[i % 3] + (i % 4)
            genre = ("soul", "jazz")[i % 2]
            ids.append((await conn.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec, year, genre, genre_keys) "
                "VALUES (:f, :t, :a, :al, 200, :y, :g, ARRAY[:g]) RETURNING id"),
                {"f": f"/home/alex/Music/{a}/{i:03d}.flac", "t": f"Song {i}", "a": a,
                 "al": f"{a} Album", "y": year, "g": genre})).scalar_one())
        pl = (await conn.execute(text(
            "INSERT INTO playlists (name) VALUES ('Road Trip') RETURNING id"))).scalar_one()
        for pos, tid in enumerate(ids[:20]):
            await conn.execute(text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, :o)"),
                {"p": pl, "t": tid, "o": pos})
        alex = (await conn.execute(text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
        sam = (await conn.execute(text("INSERT INTO people (name) VALUES ('Sam') RETURNING id"))).scalar_one()
    return {"ids": ids, "playlist": pl, "alex": alex, "sam": sam}


async def _household():
    async with web_client() as c:
        admin = await claim_admin(c)
    return admin, await db_device_token()


def _dev(token: str, device_id: str):
    return web_client(headers={"X-Requested-With": "t", HEADER: token, "X-Device-Id": device_id})


def _start(device_id: str, **start) -> dict:
    extra = {k: start.pop(k) for k in ("listener_person_id", "as_household", "autoplay") if k in start}
    return {"target": {"kind": "device", "device_id": device_id}, "start": start, **extra}


async def _register(c, device_id: str) -> None:
    r = await c.post("/api/devices/register", json={"device_id": device_id, "platform": "browser"})
    assert r.status_code == 200, r.text


@requires_db
@pytest.mark.asyncio
async def test_a_song_clicked_in_a_playlist_starts_a_list_session(_db, clean) -> None:
    d = await _seed()
    _, token = await _household()
    first = d["ids"][0]
    async with _dev(token, "browser-a1") as c:
        r = await c.post(f"{BASE}/sessions", json=_start(
            "browser-a1", track_id=first, context={"kind": "playlist", "ref": str(d["playlist"])}))
        assert r.status_code == 200, r.text
        s = r.json()
    assert s["mode"] == "list" and s["autoplay"] is True and s["person"] is None
    assert s["target"] == {"kind": "device", "device_id": "browser-a1"}
    assert s["seed"]["title"] == "Song 0" and s["seed"]["list"] == {"kind": "playlist", "name": "Road Trip"}
    assert s["seed"]["label"] == "from road trip"
    picks = s["picks"]
    # The rest of the playlist, each once, then (autoplay on) round again,
    # starting a new cycle: the batch stops before any song would repeat.
    assert sorted(p["track_id"] for p in picks[:19]) == sorted(d["ids"][1:20])
    assert {p["track_id"] for p in picks[19:]} <= set(d["ids"][:20])
    assert [p["seq"] for p in picks] == list(range(1, len(picks) + 1))
    assert s["have_through_seq"] == len(picks) and s["batch_no"] == 1
    labels = {p["reason_label"] for p in picks}
    assert labels <= {"same artist", "60s", "from 1965-ish", "soul", "jazz", "from road trip"} | {
        lab for lab in labels if re.fullmatch(r"from \d{4}-ish|\d0s", lab)}
    assert "same artist" in labels
    assert all(p["reason_code"] == "from_list" and p["title"] and p["artist"] for p in picks)
    assert all("file_path" not in p for p in picks)


@requires_db
@pytest.mark.asyncio
async def test_library_and_continue_modes_and_a_new_start_ends_the_old(_db, clean) -> None:
    d = await _seed()
    _, token = await _household()
    async with _dev(token, "browser-a1") as c:
        lib = (await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][3]))).json()
        assert lib["mode"] == "library" and lib["seed"]["list"] is None
        assert 0 < len(lib["picks"]) <= 30
        assert d["ids"][3] not in {p["track_id"] for p in lib["picks"]}
        reasons = {p["reason_code"] for p in lib["picks"]}
        assert reasons <= {"same_artist", "same_era", "same_genre", "played_together", "wildcard"}
        cont = (await c.post(f"{BASE}/sessions", json=_start(
            "browser-a1", context={"kind": "playlist", "ref": str(d["playlist"])}, whole_list=True))).json()
        assert cont["mode"] == "continue" and cont["picks"] == [] and cont["have_through_seq"] == 0
        # The library session is over: its row ended, its next says so.
        old = await c.post(f"{BASE}/sessions/{lib['id']}/next", json={"have_through_seq": 30})
        assert old.json()["ended"] is True and old.json()["picks"] == []
        # Continue mode draws when asked (the list is nearly over).
        more = (await c.post(f"{BASE}/sessions/{cont['id']}/next", json={"have_through_seq": 0})).json()
        assert len(more["picks"]) > 0 and more["batch_no"] == 0
        # artist / album / genre / ids lists are read too.
        for ctx in ({"kind": "artist", "ref": "Nina"}, {"kind": "album", "ref": "Otis|Otis Album"},
                    {"kind": "genre", "ref": "jazz", "name": "Jazz"},
                    {"kind": "ids", "track_ids": d["ids"][10:16], "name": "search results"}):
            r = await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][1 if ctx["kind"] != "ids" else 10], context=ctx))
            assert r.status_code == 200, (ctx, r.text)
            assert r.json()["mode"] == "list" and r.json()["picks"], ctx
        ids_s = r.json()
        assert sorted(p["track_id"] for p in ids_s["picks"][:5]) == sorted(d["ids"][11:16])
        assert ids_s["seed"]["list"]["name"] == "search results"
    async with engine.begin() as conn:
        active = (await conn.execute(text(
            "SELECT count(*) FROM up_next_sessions WHERE target = 'device:browser-a1' AND ended_at IS NULL"))).scalar()
    assert active == 1


@requires_db
@pytest.mark.asyncio
async def test_start_validation(_db, clean) -> None:
    d = await _seed(12)
    _, token = await _household()
    async with _dev(token, "browser-a1") as c:
        post = lambda body: c.post(f"{BASE}/sessions", json=body)  # noqa: E731
        assert (await post(_start("browser-a1"))).status_code == 422                      # no song
        assert (await post(_start("browser-a1", whole_list=True))).status_code == 422     # no list
        assert (await post(_start("browser-a1", track_id=99999))).status_code == 404
        assert (await post(_start("browser-a1", track_id=d["ids"][0],
                                  context={"kind": "playlist", "ref": "777"}))).status_code == 404
        assert (await post(_start("browser-a1", track_id=d["ids"][0],
                                  context={"kind": "album", "ref": "no bar"}))).status_code == 422
        assert (await post(_start("browser-a1", track_id=d["ids"][0],
                                  context={"kind": "ids", "track_ids": list(range(1, 502))}))).status_code == 422
        room = {"target": {"kind": "room", "room_id": "kitchen"}, "start": {"track_id": d["ids"][0]}}
        assert (await post(room)).status_code == 422                                      # rooms: phase P4
        assert (await post(_start("browser-a1", track_id=d["ids"][0], listener_person_id=4242))).status_code == 422


@requires_db
@pytest.mark.asyncio
async def test_next_is_idempotent(_db, clean) -> None:
    d = await _seed()
    _, token = await _household()
    async with _dev(token, "browser-a1") as c:
        s = (await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))).json()
        have = s["have_through_seq"]
        a = (await c.post(f"{BASE}/sessions/{s['id']}/next", json={"have_through_seq": have})).json()
        b = (await c.post(f"{BASE}/sessions/{s['id']}/next", json={"have_through_seq": have})).json()
        assert a == b and a["picks"] and a["batch_no"] == 1
        assert a["picks"][0]["seq"] == have + 1
        # Asking from an earlier seq hands back the first batch still on record.
        again = (await c.post(f"{BASE}/sessions/{s['id']}/next", json={"have_through_seq": 0})).json()
        assert [p["seq"] for p in again["picks"]] == [p["seq"] for p in s["picks"]]
        c2 = (await c.post(f"{BASE}/sessions/{s['id']}/next",
                           json={"have_through_seq": a["picks"][-1]["seq"]})).json()
        assert c2["batch_no"] == 2 and c2["picks"][0]["seq"] == a["picks"][-1]["seq"] + 1
        assert (await c.post(f"{BASE}/sessions/{uuid.uuid4()}/next", json={"have_through_seq": 0})).status_code == 404
        assert (await c.post(f"{BASE}/sessions/not-a-uuid/next", json={"have_through_seq": 0})).status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_feedback_remove_less_like_this_and_skip(_db, clean) -> None:
    d = await _seed()
    _, token = await _household()
    async with _dev(token, "browser-a1") as c:
        s = (await c.post(f"{BASE}/sessions", json=_start(
            "browser-a1", track_id=d["ids"][0], context={"kind": "ids", "track_ids": d["ids"][:40]}))).json()
        sid, picks = s["id"], s["picks"]
        fb = lambda body: c.post(f"{BASE}/sessions/{sid}/feedback", json=body)  # noqa: E731
        first = picks[0]
        r = await fb({"seq": first["seq"], "kind": "remove"})
        assert r.json() == {"removed": [first["seq"]]}
        assert (await fb({"seq": first["seq"], "kind": "remove"})).json() == {"removed": []}
        # Less like this: every waiting pick by that artist goes, and none comes back.
        victim = picks[1]
        same = sorted(p["seq"] for p in picks[1:] if p["artist"] == victim["artist"])
        r = (await fb({"seq": victim["seq"], "kind": "less_like_this"})).json()
        assert r["removed"] == same and len(same) >= 2
        nxt = (await c.post(f"{BASE}/sessions/{sid}/next", json={"have_through_seq": s["have_through_seq"]})).json()
        assert victim["artist"] not in {p["artist"] for p in nxt["picks"]}
        assert set(same) | {first["seq"]} <= set(nxt["removed"])
        got = (await c.get(f"{BASE}/sessions/{sid}")).json()
        assert victim["artist"] not in {p["artist"] for p in got["picks"]}
        # Two early skips of one artist exclude it.
        other = next(p for p in nxt["picks"] if p["artist"] != victim["artist"])
        art = other["artist"]
        r1 = (await fb({"track_id": other["track_id"], "kind": "skip", "played_sec": 5})).json()
        assert r1 == {"removed": []}
        second = next(p for p in nxt["picks"] if p["artist"] == art and p["seq"] != other["seq"])
        r2 = (await fb({"seq": second["seq"], "kind": "skip", "played_sec": 5})).json()
        assert r2["removed"] and all(
            p["artist"] == art for p in nxt["picks"] + got["picks"] if p["seq"] in r2["removed"])
        assert (await fb({"kind": "skip"})).status_code == 422
        assert (await fb({"seq": 9999, "kind": "remove"})).status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_autoplay_switch(_db, clean) -> None:
    d = await _seed()
    _, token = await _household()
    async with _dev(token, "browser-a1") as c:
        lib = (await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))).json()
        off = (await c.patch(f"{BASE}/sessions/{lib['id']}", json={"autoplay": False})).json()
        assert off["autoplay"] is False
        n = (await c.post(f"{BASE}/sessions/{lib['id']}/next", json={"have_through_seq": lib["have_through_seq"]})).json()
        assert n["picks"] == [] and n["ended"] is True
        # Started with autoplay off: a library song draws nothing.
        quiet = (await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0], autoplay=False))).json()
        assert quiet["picks"] == [] and quiet["autoplay"] is False
        # A list with autoplay off plays the rest of the list, then stops.
        small = d["ids"][:6]
        lst = (await c.post(f"{BASE}/sessions", json=_start(
            "browser-a1", track_id=small[0], context={"kind": "ids", "track_ids": small}, autoplay=False))).json()
        assert sorted(p["track_id"] for p in lst["picks"]) == sorted(small[1:])
        end = (await c.post(f"{BASE}/sessions/{lst['id']}/next", json={"have_through_seq": 5})).json()
        assert end["picks"] == [] and end["ended"] is True
        on = (await c.patch(f"{BASE}/sessions/{lst['id']}", json={"autoplay": True})).json()
        assert on["autoplay"] is True and on["ended"] is False
        again = (await c.post(f"{BASE}/sessions/{lst['id']}/next", json={"have_through_seq": 5})).json()
        assert sorted(p["track_id"] for p in again["picks"]) == sorted(small)   # round again


@requires_db
@pytest.mark.asyncio
async def test_whose_mix_it_is(_db, clean) -> None:
    d = await _seed(20)
    admin, token = await _household()
    t = d["ids"][0]
    async with _dev(token, "browser-me") as me, _dev(token, "browser-tv") as tv, \
            web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        await _register(me, "browser-me")
        await _register(tv, "browser-tv")
        assert (await me.put("/api/devices/browser-me/person", json={"person_id": d["alex"]})).status_code == 200
        assert (await adm.patch("/api/devices/browser-tv/shared-screen", json={"shared_screen": True})).status_code == 200
        start = lambda c, dev, **kw: c.post(f"{BASE}/sessions", json=_start(dev, track_id=t, **kw))  # noqa: E731
        assert (await start(me, "browser-me")).json()["person"] == {"id": d["alex"], "name": "Alex"}
        assert (await start(me, "browser-me", listener_person_id=d["sam"])).json()["person"]["name"] == "Sam"
        assert (await start(me, "browser-me", as_household=True)).json()["person"] is None
        assert (await start(tv, "browser-tv")).json()["person"] is None             # shared: household
        picked = (await start(tv, "browser-tv", listener_person_id=d["sam"])).json()
        assert picked["person"]["name"] == "Sam"                                    # unless someone picks
        # The "for" selector: household, then a person, then an unknown one.
        sid = picked["id"]
        assert (await tv.patch(f"{BASE}/sessions/{sid}", json={"listener_person_id": None})).json()["person"] is None
        assert (await tv.patch(f"{BASE}/sessions/{sid}", json={"listener_person_id": d["alex"]})).json()["person"]["id"] == d["alex"]
        assert (await tv.patch(f"{BASE}/sessions/{sid}", json={"autoplay": True})).json()["person"]["id"] == d["alex"]
        assert (await tv.patch(f"{BASE}/sessions/{sid}", json={"listener_person_id": 4242})).status_code == 422


@requires_db
@pytest.mark.asyncio
async def test_a_device_acts_on_its_own_sessions_only(_db, clean) -> None:
    d = await _seed(20)
    _, token = await _household()
    async with web_client() as anon:
        r = await anon.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))
        assert r.status_code == 401
    async with _dev(token, "browser-a1") as mine, _dev(token, "browser-b2") as other:
        s = (await mine.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))).json()
        # Starting one for another device, or touching this one from there: 403.
        assert (await other.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))).status_code == 403
        assert (await other.get(f"{BASE}/sessions/{s['id']}")).status_code == 403
        assert (await other.post(f"{BASE}/sessions/{s['id']}/next", json={"have_through_seq": 0})).status_code == 403
        assert (await other.post(f"{BASE}/sessions/{s['id']}/feedback",
                                 json={"seq": 1, "kind": "less_like_this"})).status_code == 403
        assert (await other.patch(f"{BASE}/sessions/{s['id']}", json={"autoplay": False})).status_code == 403
        assert (await mine.get(f"{BASE}/sessions/{s['id']}")).status_code == 200


def _listen(track_id: int, device_id: str, session_id: str, **kw) -> dict:
    out = {"track_id": track_id, "device_id": device_id, "session_id": session_id, "origin": "auto",
           "started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
           "played_sec": 200, "duration_sec": 200, "outcome": "finished"}
    out.update(kw)
    return out


async def _state(sid: str) -> dict:
    async with engine.begin() as conn:
        return (await conn.execute(text("SELECT state FROM up_next_sessions WHERE id = :s"), {"s": sid})).scalar()


@pytest.mark.parametrize("keep", ["365", "off"])
@requires_db
@pytest.mark.asyncio
async def test_listens_feed_the_session(_db, clean, monkeypatch, keep) -> None:
    d = await _seed()
    _, token = await _household()
    monkeypatch.setattr(listens_api, "get_cached_snapshot", lambda: {"music_listens_keep": keep})
    async with _dev(token, "browser-a1") as c, _dev(token, "browser-b2") as other:
        s = (await c.post(f"{BASE}/sessions", json=_start("browser-a1", track_id=d["ids"][0]))).json()
        sid, picks = s["id"], s["picks"]
        heard = picks[0]
        r = await c.post("/api/music/listens", json={"listens": [_listen(heard["track_id"], "browser-a1", sid)]})
        assert r.status_code == 200
        st = await _state(sid)
        assert st["recent_listened"] == [heard["track_id"]]               # a drift seed now
        statuses = {p["seq"]: p["status"] for p in (await c.get(f"{BASE}/sessions/{sid}")).json()["picks"]}
        assert heard["seq"] not in statuses                                  # no longer waiting
        # Another device's listens naming this session change nothing.
        await other.post("/api/music/listens", json={"listens": [
            _listen(picks[1]["track_id"], "browser-b2", sid, played_sec=5, outcome="skipped")]})
        assert (await _state(sid))["early_skips"] == {}
        # Two early skips of one artist: out for the session.
        art = picks[1]["artist"]
        two = [p for p in picks if p["artist"] == art][:2]
        assert len(two) == 2
        now = datetime.now(timezone.utc)
        await c.post("/api/music/listens", json={"listens": [
            _listen(p["track_id"], "browser-a1", sid, played_sec=5, outcome="skipped",
                    started_at=(now - timedelta(minutes=5 - i)).isoformat()) for i, p in enumerate(two)]})
        st = await _state(sid)
        norm = art.lower()
        assert norm in st["excluded_artists"]
        nxt = (await c.post(f"{BASE}/sessions/{sid}/next", json={"have_through_seq": s["have_through_seq"]})).json()
        assert art not in {p["artist"] for p in nxt["picks"]}
        waiting_by_art = {p["seq"] for p in picks if p["artist"] == art} - {p["seq"] for p in two}
        assert waiting_by_art <= set(nxt["removed"])
