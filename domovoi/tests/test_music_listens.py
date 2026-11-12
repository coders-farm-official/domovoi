"""The listening history (V051, Up next P1, 2026-11-12) and "this is me".

* ``POST /api/music/listens``: device tier, validation, late and duplicate
  batches, whose each row is (listener, claimed device, shared screen);
* the room side: the ListenLogger worker over stub players (finished,
  skipped by a skip stamp, stopped, songs the library doesn't know, the
  room's person) and the voice / web starts that say whose a room is;
* retention: the pruner and "Don't keep";
* clearing: a person's own device, an admin, everyone else refused;
  delete-all at the security tier;
* the claim rules matrix (``PUT /api/devices/{id}/person`` and the
  registration's ``owner_person_id``).

The DB-backed tests carry ``requires_db`` (a lane with V051 applied).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import admin_auth, music_listens
from domovoi.clients import mpd as mpd_mod
from domovoi.config import (
    MUSIC_LISTENS_KEEP_CHOICES,
    music_listens_keep_days,
    normalize_music_listens_keep,
)
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts
from web.backend.api import music_listens as listens_api
from web.backend.main import app as web_app

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = next((REPO_ROOT / "domovoi" / "db" / "migrations").glob("V051__*.sql"))
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


# ─── pure ────────────────────────────────────────────────────────────────


def test_outcome_rule() -> None:
    o = music_listens.outcome_for
    assert o(180, 200, False) == "finished"           # 90 %
    assert o(195, 200, True) == "finished"            # a skip in the outro still finished
    assert o(30, 200, True) == "skipped"
    assert o(30, 200, False) == "stopped"
    assert o(30, None, False) == "stopped"            # no length: never "finished"
    assert o(30, None, True) == "skipped"


def test_keep_setting_never_deletes_more_than_asked() -> None:
    n = normalize_music_listens_keep
    assert MUSIC_LISTENS_KEEP_CHOICES == ("off", "90", "365")
    assert n(None) == n("") == "365"
    assert n("off") == n("Don't keep") == "off"
    assert n("30") == n("90d") == "90"
    assert n("120") == n("400") == n("0") == n("forever") == n("never") == "365"
    assert music_listens_keep_days("off") is None and music_listens_keep_days("90") == 90


def _row(**kw):
    base = dict(track_id=1, person_id=None, room_id=None, device_id="browser-a1",
                player="browser", session_id=None, origin="explicit",
                started_at=NOW - timedelta(minutes=5), played_sec=100,
                duration_sec=200, outcome="stopped")
    base.update(kw)
    return base


def test_clean_listen_drops_what_it_must() -> None:
    c = music_listens.clean_listen
    assert c(_row(), now=NOW)["played_sec"] == 100
    assert c(_row(started_at=NOW - timedelta(days=6, hours=23)), now=NOW) is not None
    assert c(_row(started_at=NOW - timedelta(days=7, minutes=1)), now=NOW) is None
    assert c(_row(started_at=NOW + timedelta(minutes=9)), now=NOW) is not None
    assert c(_row(started_at=NOW + timedelta(minutes=11)), now=NOW) is None
    assert c(_row(device_id=None), now=NOW) is None
    assert c(_row(outcome="loved"), now=NOW) is None
    assert c(_row(player="radio"), now=NOW) is None
    assert c(_row(played_sec=10 ** 9), now=NOW)["played_sec"] == music_listens.MAX_SECONDS
    naive = c(_row(started_at=(NOW - timedelta(hours=1)).replace(tzinfo=None)), now=NOW)
    assert naive["started_at"].tzinfo is not None


def test_skip_stamps_mark_one_song() -> None:
    music_listens.SKIP_STAMPS.clear()
    music_listens.note_skip("kitchen")
    assert music_listens.take_skip("kitchen", since=0.0) is True
    assert music_listens.take_skip("kitchen", since=0.0) is False   # consumed
    music_listens.note_skip("kitchen")
    assert music_listens.take_skip("kitchen", since=10 ** 12) is False  # before the song


def test_migration_shape() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for stmt in body.split(";"):
        assert "--" not in stmt
    assert "CREATE TABLE IF NOT EXISTS music_listens" in body
    assert re.search(r"person_id\s+INT\s+REFERENCES people\(id\) ON DELETE CASCADE", body)
    assert "CHECK (room_id IS NOT NULL OR device_id IS NOT NULL)" in body
    assert "uq_music_listens_once" in body
    assert "CREATE TABLE IF NOT EXISTS music_room_listeners" in body


# ─── tiers, from the route table ─────────────────────────────────────────


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
    assert admin_auth.require_device in _gates("POST", "/api/music/listens")
    assert admin_auth.require_device_read in _gates("GET", "/api/music/listens/summary")
    assert admin_auth.require_device in _gates("DELETE", "/api/music/listens/people/{person_id}")
    every = _gates("POST", "/api/music/listens/delete-all")
    assert admin_auth.require_admin_security in every
    assert admin_auth.require_device not in every
    assert admin_auth.require_device in _gates("PUT", "/api/devices/{device_id}/person")


# ─── against the database ────────────────────────────────────────────────


@pytest.fixture
async def clean():
    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE music_listens, music_room_listeners, queue_device_blocks, "
                "room_queue_items, devices, library_tracks, people RESTART IDENTITY CASCADE"))

    await _truncate()
    music_listens.SKIP_STAMPS.clear()
    yield
    await _truncate()


async def _seed() -> dict:
    async with engine.begin() as conn:
        t1 = (await conn.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, duration_sec) "
            "VALUES ('/home/alex/Music/Nina/Feeling Good.flac', 'Feeling Good', 'Nina', 200) "
            "RETURNING id"))).scalar_one()
        t2 = (await conn.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, duration_sec) "
            "VALUES ('C:\\Users\\alex\\Music\\Otis\\Dock.mp3', 'Dock', 'Otis', 100) RETURNING id"))).scalar_one()
        alex = (await conn.execute(text(
            "INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
        sam = (await conn.execute(text(
            "INSERT INTO people (name) VALUES ('Sam') RETURNING id"))).scalar_one()
    return {"t1": t1, "t2": t2, "alex": alex, "sam": sam}


async def _register(c, device_id: str, **extra) -> dict:
    r = await c.post("/api/devices/register",
                     json={"device_id": device_id, "platform": "browser", **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _listens(where: str = "TRUE") -> list:
    async with engine.begin() as conn:
        return (await conn.execute(text(
            "SELECT track_id, person_id, device_id, room_id, player, outcome, played_sec "
            f"FROM music_listens WHERE {where} ORDER BY id"))).all()


def _listen(track_id: int, device_id: str, **kw) -> dict:
    out = {
        "track_id": track_id, "device_id": device_id,
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "played_sec": 120, "duration_sec": 200, "outcome": "stopped",
    }
    out.update(kw)
    return out


async def _setup_household():
    async with web_client() as c:
        admin = await claim_admin(c)
    token = await db_device_token()
    return admin, token


def _dev(token: str, device_id: str):
    return web_client(headers={"X-Requested-With": "t", HEADER: token, "X-Device-Id": device_id})


@requires_db
@pytest.mark.asyncio
async def test_post_listens_tiers_validation_late_and_twice(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    async with web_client() as anon:
        r = await anon.post("/api/music/listens", json={"listens": [_listen(ids["t1"], "browser-a1")]})
        assert r.status_code == 401
    async with _dev(token, "browser-a1") as c:
        await _register(c, "browser-a1")
        # validation: the whole batch is refused for a malformed row
        bad = await c.post("/api/music/listens", json={"listens": [_listen(ids["t1"], "browser-a1", outcome="loved")]})
        assert bad.status_code == 422
        too_many = await c.post("/api/music/listens",
                                json={"listens": [_listen(ids["t1"], "browser-a1")] * 201})
        assert too_many.status_code == 422
        assert (await c.post("/api/music/listens", json={"listens": []})).status_code == 422
        bad_id = await c.post("/api/music/listens", json={"listens": [_listen(ids["t1"], "a b")]})
        assert bad_id.status_code == 422

        now = datetime.now(timezone.utc)
        batch = [
            _listen(ids["t1"], "browser-a1", outcome="finished", played_sec=199),
            _listen(ids["t2"], "browser-a1", started_at=(now - timedelta(days=6)).isoformat()),  # late, kept
            _listen(ids["t2"], "browser-a1", started_at=(now - timedelta(days=8)).isoformat()),  # too late
            _listen(ids["t2"], "browser-a1", started_at=(now + timedelta(hours=1)).isoformat()),  # ahead
            _listen(9999, "browser-a1"),                                                         # not in the library
        ]
        r = await c.post("/api/music/listens", json={"listens": batch})
        assert r.status_code == 200, r.text
        assert r.json() == {"accepted": 2, "dropped": 3, "recording": True}
        # The same batch again (the answer was lost): nothing new.
        again = await c.post("/api/music/listens", json={"listens": batch})
        assert again.json()["accepted"] == 0
    rows = await _listens()
    assert [(r.track_id, r.outcome, r.player, r.person_id) for r in rows] == [
        (ids["t1"], "finished", "browser", None), (ids["t2"], "stopped", "browser", None)]


@requires_db
@pytest.mark.asyncio
async def test_whose_a_listen_is(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    async with _dev(token, "android-p1") as phone, _dev(token, "browser-tab") as tablet, \
            web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        await phone.post("/api/devices/register",
                         json={"device_id": "android-p1", "platform": "android"})
        await _register(tablet, "browser-tab")
        assert (await phone.put("/api/devices/android-p1/person", json={"person_id": ids["alex"]})).status_code == 200
        await adm.patch("/api/devices/browser-tab/shared-screen", json={"shared_screen": True})
        r = await phone.post("/api/music/listens", json={"listens": [
            _listen(ids["t1"], "android-p1"),                                   # the claimed person
            _listen(ids["t2"], "android-p1", listener_person_id=ids["sam"]),    # listening as Sam
            _listen(ids["t1"], "browser-tab", played_sec=5),                    # shared screen: household
            _listen(ids["t2"], "browser-tab", played_sec=6, listener_person_id=ids["sam"]),  # ignored
            _listen(ids["t2"], "browser-new", played_sec=7, listener_person_id=4242),  # nobody: household
            _listen(ids["t1"], "android-p1", played_sec=8, as_household=True,   # lent out: household
                    started_at=(datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()),
        ]})
        assert r.json()["accepted"] == 6
    rows = await _listens()
    assert [(r.device_id, r.person_id, r.player) for r in rows] == [
        ("android-p1", ids["alex"], "android"), ("android-p1", ids["sam"], "android"),
        ("browser-tab", None, "browser"), ("browser-tab", None, "browser"),   # shared: pick ignored
        ("browser-new", None, "browser"), ("android-p1", None, "android")]


@requires_db
@pytest.mark.asyncio
async def test_dont_keep_records_nothing(_db, clean, monkeypatch) -> None:
    ids = await _seed()
    _, token = await _setup_household()
    monkeypatch.setattr(listens_api, "get_cached_snapshot", lambda: {"music_listens_keep": "off"})
    async with _dev(token, "browser-a1") as c:
        r = await c.post("/api/music/listens", json={"listens": [_listen(ids["t1"], "browser-a1")]})
        assert r.json() == {"accepted": 0, "dropped": 1, "recording": False}
        s = (await c.get("/api/music/listens/summary")).json()
        assert s["keep"] == "off" and s["keep_days"] is None and s["total"] == 0
    assert await _listens() == []


# ─── clearing ────────────────────────────────────────────────────────────


async def _insert(track, person, device="browser-x", room=None, ago_days=0.0):
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO music_listens (track_id, person_id, device_id, room_id, player, origin, "
            "started_at, played_sec, outcome) VALUES (:t, :p, :d, :r, :pl, 'explicit', "
            "now() - make_interval(secs => :ago), 10, 'stopped')"),
            {"t": track, "p": person, "d": device, "r": room, "pl": "room" if room else "browser",
             "ago": ago_days * 86400 + (person or 0) + (1 if room else 0)})


KEY = "X-Domovoi-Claim-Key"


def _k(key: str | None) -> dict:
    return {KEY: key} if key else {}


async def _age_claim(device_id: str, minutes: float = 11) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE devices SET owner_claimed_at = now() - make_interval(mins => :m) "
            "WHERE device_id = :d"), {"m": minutes, "d": device_id})


@requires_db
@pytest.mark.asyncio
async def test_each_person_clears_their_own(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    await _insert(ids["t1"], ids["alex"], device="browser-1")
    await _insert(ids["t1"], ids["sam"], device="browser-2")
    await _insert(ids["t1"], None, room="kitchen")
    async with _dev(token, "browser-alex") as alex, _dev(token, "browser-none") as nobody:
        await _register(alex, "browser-alex")
        await _register(nobody, "browser-none")
        claimed = (await alex.put("/api/devices/browser-alex/person", json={"person_id": ids["alex"]})).json()
        key = claimed["claim_key"]
        assert key and len(key) >= 40
        url = f"/api/music/listens/people/{ids['alex']}"

        # Not anyone's: refused, naming who may.
        r = await nobody.delete(url)
        assert r.status_code == 403
        assert "only Alex's own phone or browser, or an admin" in r.json()["detail"]
        # Alex's device can't clear Sam's.
        assert (await alex.delete(f"/api/music/listens/people/{ids['sam']}", headers=_k(key))).status_code == 403
        # Within 10 minutes of the claim: an admin's, key or not.
        r = await alex.delete(url, headers=_k(key))
        assert r.status_code == 403 and "less than 10 minutes ago" in r.json()["detail"]
        await _age_claim("browser-alex")
        # Without the key, or with a wrong one: refused, saying why.
        r = await alex.delete(url)
        assert r.status_code == 403 and "can't prove" in r.json()["detail"]
        assert (await alex.delete(url, headers=_k("x" * 43))).status_code == 403
        # Copying Alex's device id AND key from another browser still needs both.
        async with _dev(token, "browser-alex") as pretend:
            assert (await pretend.delete(url)).status_code == 403
        assert (await alex.delete("/api/music/listens/people/999", headers=_k(key))).status_code == 404
        ok = await alex.delete(url, headers=_k(key))
        assert ok.status_code == 200 and ok.json() == {"deleted": 1}
    assert [r.person_id for r in await _listens()] == [ids["sam"], None]
    async with web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        assert (await adm.delete(f"/api/music/listens/people/{ids['sam']}")).json() == {"deleted": 1}
    assert [r.person_id for r in await _listens()] == [None]
    async with web_client() as anon:
        assert (await anon.delete(f"/api/music/listens/people/{ids['sam']}")).status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_a_claim_from_before_keys_says_this_is_me_again(_db, clean) -> None:
    """A V035 claim (owner set, no key hash): acting as the person is refused
    with "say this is me again"; saying it again for the same person mints a
    key; switching or releasing first is refused."""
    ids = await _seed()
    _, token = await _setup_household()
    async with _dev(token, "android-old") as phone:
        await _register(phone, "android-old")
        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE devices SET owner_person_id = :p, owner_claimed_at = now() - interval '1 day' "
                "WHERE device_id = 'android-old'"), {"p": ids["alex"]})
        await _insert(ids["t1"], ids["alex"], device="android-old")
        r = await phone.delete(f"/api/music/listens/people/{ids['alex']}")
        assert r.status_code == 403 and "this is me" in r.json()["detail"]
        r = await phone.put("/api/devices/android-old/person", json={"person_id": None})
        assert r.status_code == 403 and "this is me" in r.json()["detail"]
        r = await phone.put("/api/devices/android-old/person", json={"person_id": ids["sam"]})
        assert r.status_code == 403
        again = (await phone.put("/api/devices/android-old/person", json={"person_id": ids["alex"]})).json()
        assert again["claim_key"] and again["owner_person_id"] == ids["alex"]


@requires_db
@pytest.mark.asyncio
async def test_claim_keys(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    alex, sam = ids["alex"], ids["sam"]

    async def stored(device_id):
        async with engine.begin() as conn:
            return (await conn.execute(text(
                "SELECT claim_key_hash, owner_claimed_at FROM devices WHERE device_id = :d"),
                {"d": device_id})).first()

    async with _dev(token, "browser-a") as a, \
            web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        await _register(a, "browser-a")
        k1 = (await a.put("/api/devices/browser-a/person", json={"person_id": alex})).json()["claim_key"]
        h1 = (await stored("browser-a"))[0]
        # Only the hash is kept, and it is the key's SHA-256.
        import hashlib
        assert h1 == hashlib.sha256(k1.encode()).hexdigest() and k1 not in str(await stored("browser-a"))
        # Never shown again: not on a plain registration, not on the roster.
        again = await _register(a, "browser-a")
        assert again["claim_key"] is None and (await stored("browser-a"))[0] == h1
        assert all(d["claim_key"] is None for d in (await adm.get("/api/devices")).json())
        # Re-claiming needs the key and issues a new one; the old one stops working.
        assert (await a.put("/api/devices/browser-a/person", json={"person_id": alex})).status_code == 403
        k2 = (await a.put("/api/devices/browser-a/person", json={"person_id": alex},
                          headers=_k(k1))).json()["claim_key"]
        assert k2 and k2 != k1
        assert (await a.put("/api/devices/browser-a/person", json={"person_id": sam},
                            headers=_k(k1))).status_code == 403
        # Switching with the key works and mints; releasing wipes the hash.
        k3 = (await a.put("/api/devices/browser-a/person", json={"person_id": sam},
                          headers=_k(k2))).json()["claim_key"]
        assert k3
        r = await a.put("/api/devices/browser-a/person", json={"person_id": None}, headers=_k(k3))
        assert r.status_code == 200 and r.json()["claim_key"] is None
        assert tuple(await stored("browser-a")) == (None, None)
        # An admin setting a device's person wipes any key.
        k4 = (await a.put("/api/devices/browser-a/person", json={"person_id": alex})).json()["claim_key"]
        assert k4
        r = await adm.put("/api/devices/browser-a/person", json={"person_id": sam})
        assert r.status_code == 200 and r.json()["claim_key"] is None
        assert (await stored("browser-a"))[0] is None
        # ...and so does marking it a shared screen.
        await adm.put("/api/devices/browser-a/person", json={"person_id": None})
        k5 = (await a.put("/api/devices/browser-a/person", json={"person_id": alex})).json()["claim_key"]
        assert k5 and (await stored("browser-a"))[0]
        await adm.patch("/api/devices/browser-a/shared-screen", json={"shared_screen": True})
        assert tuple(await stored("browser-a")) == (None, None)


@requires_db
@pytest.mark.asyncio
async def test_a_claim_never_lands_on_a_shared_screen(_db, clean) -> None:
    """The race: decided while the device wasn't shared, written after an
    admin marked it. The write's `AND NOT shared_screen` refuses it."""
    from web.backend.api import devices as devices_api
    from web.backend.db import session_scope

    ids = await _seed()
    _, token = await _setup_household()
    async with _dev(token, "browser-a") as a:
        await _register(a, "browser-a")
    async with session_scope() as s:
        decision = await devices_api.claim_decision(
            s, "browser-a", ids["alex"], admin=False, caller="browser-a", key=None)
        assert decision.refusal is None and decision.mint
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE devices SET shared_screen = TRUE WHERE device_id = 'browser-a'"))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        async with session_scope() as s:
            await devices_api.apply_claim(s, "browser-a", ids["alex"], decision)
    assert e.value.status_code == 409
    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT owner_person_id, claim_key_hash FROM devices WHERE device_id = 'browser-a'"))).first()
    assert tuple(row) == (None, None)


@requires_db
@pytest.mark.asyncio
async def test_delete_all_is_the_admins_and_closed_before_setup(_db, clean) -> None:
    ids = await _seed()
    await _insert(ids["t1"], ids["alex"])
    await _insert(ids["t1"], None, room="kitchen")
    async with web_client() as anon:
        assert (await anon.post("/api/music/listens/delete-all")).status_code == 501
    admin, token = await _setup_household()
    async with _dev(token, "browser-a1") as dev:
        assert (await dev.post("/api/music/listens/delete-all")).status_code in (401, 403)
    async with web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        r = await adm.post("/api/music/listens/delete-all")
        assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert await _listens() == []


@requires_db
@pytest.mark.asyncio
async def test_forgetting_a_person_deletes_their_listens(_db, clean) -> None:
    ids = await _seed()
    await _insert(ids["t1"], ids["alex"])
    await _insert(ids["t1"], ids["sam"])
    admin, _ = await _setup_household()
    async with web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        assert (await adm.delete(f"/api/people/{ids['alex']}")).status_code == 204
    assert [r.person_id for r in await _listens()] == [ids["sam"]]


@requires_db
@pytest.mark.asyncio
async def test_summary_counts_per_person(_db, clean) -> None:
    ids = await _seed()
    await _insert(ids["t1"], ids["alex"])
    await _insert(ids["t2"], ids["alex"], device="browser-y")
    await _insert(ids["t1"], None, room="kitchen")
    _, token = await _setup_household()
    async with web_client() as anon:
        assert (await anon.get("/api/music/listens/summary")).status_code == 401
    async with _dev(token, "browser-a1") as c:
        s = (await c.get("/api/music/listens/summary")).json()
    assert s["keep"] == "365" and s["keep_label"] == "1 year" and s["total"] == 3
    assert s["household"] == 1
    assert {p["name"]: p["listens"] for p in s["people"]} == {"Alex": 2, "Sam": 0}
    assert "Feeling Good" not in str(s)


# ─── retention ───────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_pruner_keeps_what_the_setting_keeps(clean, monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.workers.music_listens_pruner import MusicListensPruner

    ids = await _seed()
    await _insert(ids["t1"], ids["alex"], ago_days=400)
    await _insert(ids["t1"], ids["sam"], ago_days=100)
    await _insert(ids["t1"], None, room="kitchen", ago_days=1)
    monkeypatch.setattr(settings, "music_listens_keep", "365")
    assert await MusicListensPruner().tick() == 1
    monkeypatch.setattr(settings, "music_listens_keep", "90")
    assert await MusicListensPruner().tick() == 1
    assert [r.room_id for r in await _listens()] == ["kitchen"]
    monkeypatch.setattr(settings, "music_listens_keep", "off")
    assert await MusicListensPruner().tick() == 1
    assert await _listens() == []


# ─── the claim rules ─────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_claim_rules_matrix(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    async with _dev(token, "browser-a") as a, _dev(token, "browser-b") as b, \
            web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        await _register(a, "browser-a")
        await _register(b, "browser-b")
        alex, sam = ids["alex"], ids["sam"]

        # A person nobody has claimed: the device itself may (no key needed).
        r = await a.put("/api/devices/browser-a/person", json={"person_id": alex})
        assert r.status_code == 200 and r.json()["owner_person_name"] == "Alex"
        ka = r.json()["claim_key"]
        # The one it already is: fine again, with its key.
        r = await a.put("/api/devices/browser-a/person", json={"person_id": alex}, headers=_k(ka))
        assert r.status_code == 200
        ka = r.json()["claim_key"]
        # A person another device has claimed: an admin's.
        r = await b.put("/api/devices/browser-b/person", json={"person_id": alex})
        assert r.status_code == 403 and "already claimed" in r.json()["detail"]
        assert (await adm.put("/api/devices/browser-b/person", json={"person_id": alex})).status_code == 200
        # Another device's person, claiming or releasing: an admin's.
        assert (await a.put("/api/devices/browser-b/person", json={"person_id": sam})).status_code == 403
        assert (await a.put("/api/devices/browser-b/person", json={"person_id": None})).status_code == 403
        # b was set by an admin, so it holds no key: it says "this is me"
        # again before it can let go.
        assert (await b.put("/api/devices/browser-b/person", json={"person_id": None})).status_code == 403
        kb = (await b.put("/api/devices/browser-b/person", json={"person_id": alex})).json()["claim_key"]
        r = await b.put("/api/devices/browser-b/person", json={"person_id": None}, headers=_k(kb))
        assert r.json()["owner_person_id"] is None
        # Its own, with its key: switching to someone unclaimed.
        r = await a.put("/api/devices/browser-a/person", json={"person_id": sam}, headers=_k(ka))
        assert r.json()["owner_person_id"] == sam
        # Unknown device / person.
        assert (await adm.put("/api/devices/browser-zz/person", json={"person_id": sam})).status_code == 404
        assert (await a.put("/api/devices/browser-a/person", json={"person_id": 999})).status_code == 404
        # A shared screen is nobody's: refused even to an admin, and marking one drops its person.
        await adm.patch("/api/devices/browser-a/shared-screen", json={"shared_screen": True})
        roster = {d["device_id"]: d for d in (await adm.get("/api/devices")).json()}
        assert roster["browser-a"]["owner_person_id"] is None
        assert (await a.put("/api/devices/browser-a/person", json={"person_id": sam})).status_code == 409
        assert (await adm.put("/api/devices/browser-a/person", json={"person_id": sam})).status_code == 409
        # People show how many devices each has.
        people = {p["name"]: p for p in (await b.get("/api/people")).json()}
        assert people["Alex"]["device_count"] == 0 and people["Sam"]["device_count"] == 0
    async with web_client() as anon:
        assert (await anon.put("/api/devices/browser-b/person", json={"person_id": ids["sam"]})).status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_registration_owner_follows_the_same_rules(_db, clean) -> None:
    ids = await _seed()
    _, token = await _setup_household()
    async with _dev(token, "android-1") as p1, _dev(token, "android-2") as p2:
        first = await _register(p1, "android-1", owner_person_id=ids["alex"])
        assert first["owner_person_id"] == ids["alex"] and first["owner_person_name"] == "Alex"
        key = first["claim_key"]
        assert key   # a registration that claims hands out a key, once
        # A second phone saying Alex is refused quietly: it registers, unclaimed.
        second = await _register(p2, "android-2", owner_person_id=ids["alex"])
        assert second["owner_person_id"] is None and second["claim_key"] is None
        # A plain registration (same person, or none sent) touches nothing.
        same = await _register(p1, "android-1", owner_person_id=ids["alex"])
        assert same["owner_person_id"] == ids["alex"] and same["claim_key"] is None
        assert (await _register(p1, "android-1"))["owner_person_id"] == ids["alex"]   # not sent: kept
        # null without the key: refused quietly (an app sending null on every
        # start can't undo a claim); with the key: released.
        assert (await _register(p1, "android-1", owner_person_id=None))["owner_person_id"] == ids["alex"]
        r = await p1.post("/api/devices/register", headers=_k(key),
                          json={"device_id": "android-1", "platform": "browser", "owner_person_id": None})
        assert r.json()["owner_person_id"] is None


# ─── the rooms ───────────────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def room_player(monkeypatch):
    stub = mpd_mod.MPDStubClient()
    monkeypatch.setattr(mpd_mod, "_clients", {"kitchen": stub})
    return stub


def _play(stub, file: str, songid: int, elapsed: float = 0.0, duration: float | None = None) -> None:
    stub._song = {"file": file, "id": songid, "_elapsed": elapsed}
    if duration is not None:
        stub._song["duration"] = duration
    stub._state = "play"


@requires_db
@pytest.mark.asyncio
async def test_room_listens_finished_skipped_stopped(clean, room_player) -> None:
    from domovoi.workers.listen_logger import ListenLogger

    ids = await _seed()
    async with engine.begin() as conn:
        await music_listens.set_room_listener(conn, "kitchen", ids["alex"])
    clock = _Clock()
    worker = ListenLogger(clock=clock)

    # A song the player moves on from by itself near its end: finished.
    _play(room_player, "Nina/Feeling Good.flac", 1, elapsed=2.0)
    assert await worker.tick() == 0
    for _ in range(38):                       # 38 looks, 5 s apart: 192 s
        clock.t += 5
        room_player._song["_elapsed"] += 5
        await worker.tick()
    clock.t += 5
    _play(room_player, "Otis/Dock.mp3", 2, elapsed=1.0)   # the next song
    assert await worker.tick() == 1

    # Skipped 20 s in.
    for _ in range(4):
        clock.t += 5
        room_player._song["_elapsed"] += 5
        await worker.tick()
    music_listens.note_skip("kitchen")
    clock.t += 1
    _play(room_player, "Nina/Feeling Good.flac", 3, elapsed=0.5)
    assert await worker.tick() == 1

    # Paused, then stopped 30 s in: stopped, and the pause adds nothing.
    for _ in range(6):
        clock.t += 5
        room_player._song["_elapsed"] += 5
        await worker.tick()
    room_player._state = "pause"
    clock.t += 300
    await worker.tick()
    room_player._state = "stop"
    clock.t += 5
    assert await worker.tick() == 1

    # A stream, then a file the library doesn't have: watched, never written.
    _play(room_player, "https://radio.example/stream", 4)
    await worker.tick()
    _play(room_player, "Unknown/Song.mp3", 5)
    clock.t += 30
    await worker.tick()
    room_player._state = "stop"
    clock.t += 5
    assert await worker.tick() == 0

    rows = await _listens()
    assert [(r.track_id, r.outcome, r.player, r.room_id, r.person_id) for r in rows] == [
        (ids["t1"], "finished", "room", "kitchen", ids["alex"]),
        (ids["t2"], "skipped", "room", "kitchen", ids["alex"]),
        (ids["t1"], "stopped", "room", "kitchen", ids["alex"]),
    ]
    assert rows[0].played_sec >= 180 and 18 <= rows[1].played_sec <= 23 and 29 <= rows[2].played_sec <= 32


@requires_db
@pytest.mark.asyncio
async def test_room_song_that_ran_out_between_looks_finished(clean, room_player, monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.workers.listen_logger import ListenLogger

    ids = await _seed()
    clock = _Clock()
    worker = ListenLogger(clock=clock)
    _play(room_player, "Otis/Dock.mp3", 7, elapsed=94.0)    # 100 s song, 94 s in
    await worker.tick()
    clock.t += 5
    room_player._state = "stop"                            # the queue ended
    assert await worker.tick() == 1
    (row,) = await _listens()
    assert row.outcome == "finished" and row.person_id is None   # nobody said whose: household
    # Under "Don't keep" a room writes nothing.
    monkeypatch.setattr(settings, "music_listens_keep", "off")
    _play(room_player, "Otis/Dock.mp3", 8, elapsed=50.0)
    await worker.tick()
    room_player._state = "stop"
    clock.t += 5
    assert await worker.tick() == 0
    assert len(await _listens()) == 1


@requires_db
@pytest.mark.asyncio
async def test_a_skip_from_the_handler_stamps_the_room(clean, room_player) -> None:
    from domovoi.handlers.music import MusicHandler
    from domovoi.models import Context

    room_player._queue = room_player._assign_ids([{"file": "a.mp3"}, {"file": "b.mp3"}])
    room_player._song = room_player._queue[0]
    room_player._state = "play"
    ctx = Context(room_id="kitchen")
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await MusicHandler()._smart_skip(ctx, s)
    assert "kitchen" in music_listens.SKIP_STAMPS


@requires_db
@pytest.mark.asyncio
async def test_who_a_room_is_for(clean) -> None:
    ids = await _seed()
    await music_listens.note_voice_start("kitchen", ids["sam"])
    async with engine.begin() as conn:
        assert await music_listens.room_listener(conn, "kitchen") == ids["sam"]
    await music_listens.note_voice_start("kitchen", None)     # an unrecognised voice
    async with engine.begin() as conn:
        assert await music_listens.room_listener(conn, "kitchen") is None
        await music_listens.set_room_listener(conn, "kitchen", 4242)   # nobody: household
        assert await music_listens.room_listener(conn, "kitchen") is None


@requires_db
@pytest.mark.asyncio
async def test_a_device_replacing_a_rooms_queue_makes_it_theirs(_db, clean, monkeypatch) -> None:
    from web.backend.api import music as music_api

    ids = await _seed()
    _, token = await _setup_household()

    async def _ok(path, payload=None, **kw):
        return 200, {"text": "ok"}

    async def _refused(path, payload=None, **kw):
        return 404, {"detail": "no"}

    async with _dev(token, "browser-alex") as c:
        await _register(c, "browser-alex")
        await c.put("/api/devices/browser-alex/person", json={"person_id": ids["alex"]})
        monkeypatch.setattr(music_api, "post_admin", _refused)
        await c.post("/api/music/play-track", json={"room_id": "office", "track_id": ids["t1"]})
        monkeypatch.setattr(music_api, "post_admin", _ok)
        r = await c.post("/api/music/play-tracks", json={"room_id": "kitchen", "track_ids": [ids["t1"]]})
        assert r.status_code == 200, r.text
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT room_id, person_id, device_id FROM music_room_listeners"))).all()
    assert [tuple(r) for r in rows] == [("kitchen", ids["alex"], "browser-alex")]


@requires_db
@pytest.mark.asyncio
async def test_two_devices_claiming_one_person_at_once_get_one_yes(_db, clean) -> None:
    import asyncio

    ids = await _seed()
    _, token = await _setup_household()
    async with _dev(token, "browser-a") as a, _dev(token, "browser-b") as b:
        await _register(a, "browser-a")
        await _register(b, "browser-b")
        ra, rb = await asyncio.gather(
            a.put("/api/devices/browser-a/person", json={"person_id": ids["alex"]}),
            b.put("/api/devices/browser-b/person", json={"person_id": ids["alex"]}),
        )
    assert sorted([ra.status_code, rb.status_code]) == [200, 403]


# ─── review fixes, 2026-11-12 ────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_server_works_out_the_outcome_and_ignores_a_shared_screens_pick(_db, clean) -> None:
    ids = await _seed()
    admin, token = await _setup_household()
    async with _dev(token, "browser-tab") as tab, \
            web_client(headers={"X-Requested-With": "t", **bearer(admin)}) as adm:
        await _register(tab, "browser-tab")
        await adm.patch("/api/devices/browser-tab/shared-screen", json={"shared_screen": True})
        now = datetime.now(timezone.utc)
        r = await tab.post("/api/music/listens", json={"listens": [
            # says finished at 10 of 200 s: stopped
            _listen(ids["t1"], "browser-tab", played_sec=10, outcome="finished",
                    listener_person_id=ids["sam"], started_at=(now - timedelta(minutes=30)).isoformat()),
            # says stopped at 195 of 200: finished
            _listen(ids["t2"], "browser-tab", played_sec=195, outcome="stopped",
                    started_at=(now - timedelta(minutes=20)).isoformat()),
            # a skip is the player's word, kept
            _listen(ids["t1"], "browser-tab", played_sec=20, outcome="skipped",
                    started_at=(now - timedelta(minutes=5)).isoformat()),
        ]})
        assert r.json()["accepted"] == 3
    rows = await _listens()
    assert [(r.outcome, r.person_id) for r in rows] == [
        ("stopped", None), ("finished", None), ("skipped", None)]


@requires_db
@pytest.mark.asyncio
async def test_volume_caps(_db, clean, monkeypatch) -> None:
    ids = await _seed()
    _, token = await _setup_household()
    now = datetime.now(timezone.utc)
    async with _dev(token, "browser-a1") as c:
        # More seconds played than have passed since the first song began.
        r = await c.post("/api/music/listens", json={"listens": [
            _listen(ids["t1"], "browser-a1", played_sec=500, started_at=(now - timedelta(minutes=5)).isoformat()),
            _listen(ids["t2"], "browser-a1", played_sec=500, started_at=(now - timedelta(minutes=4)).isoformat()),
        ]})
        assert r.status_code == 422 and "more seconds played" in r.json()["detail"]
        # The daily cap: past it, the rest is dropped.
        monkeypatch.setattr(listens_api, "DEVICE_DAILY_CAP", 2)
        r = await c.post("/api/music/listens", json={"listens": [
            _listen(ids["t1"], "browser-a1", played_sec=10, started_at=(now - timedelta(minutes=50 - i)).isoformat())
            for i in range(4)
        ]})
        assert r.json() == {"accepted": 2, "dropped": 2, "recording": True}
        r = await c.post("/api/music/listens", json={"listens": [
            _listen(ids["t2"], "browser-a1", played_sec=10, started_at=(now - timedelta(minutes=9)).isoformat())]})
        assert r.json()["accepted"] == 0
    assert len(await _listens()) == 2


@requires_db
@pytest.mark.asyncio
async def test_a_rooms_listener_expires_with_its_music(clean, room_player) -> None:
    from domovoi.workers import listen_logger as ll

    ids = await _seed()
    wall = [datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)]
    clock = _Clock()
    worker = ll.ListenLogger(clock=clock, now=lambda: wall[0])

    def step(sec: float) -> None:
        clock.t += sec
        wall[0] = wall[0] + timedelta(seconds=sec)

    async def set_listener(person, at):
        async with engine.begin() as conn:
            await music_listens.set_room_listener(conn, "kitchen", person)
            await conn.execute(text("UPDATE music_room_listeners SET set_at = :t"), {"t": at})

    # Sam started it; it plays, then stops.
    await set_listener(ids["sam"], wall[0])
    step(1)
    _play(room_player, "Nina/Feeling Good.flac", 1, elapsed=0.0)
    await worker.tick()
    step(30)
    room_player._song["_elapsed"] = 30.0
    await worker.tick()
    room_player._state = "stop"
    step(5)
    await worker.tick()
    # Music starts again with nobody saying whose: Sam's record is from
    # before the stop, so the new song is the household's.
    step(60)
    _play(room_player, "Otis/Dock.mp3", 2, elapsed=0.0)
    await worker.tick()
    step(40)
    room_player._song["_elapsed"] = 40.0
    await worker.tick()
    room_player._state = "stop"
    step(5)
    await worker.tick()
    rows = await _listens()
    assert [(r.track_id, r.person_id) for r in rows] == [(ids["t1"], ids["sam"]), (ids["t2"], None)]
    # Stopped 30 minutes: the record is forgotten.
    step(31 * 60)
    await worker.tick()
    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT COUNT(*) FROM music_room_listeners"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_dont_keep_prunes_again_a_minute_later(clean, monkeypatch) -> None:
    import asyncio

    from domovoi.config import settings

    ids = await _seed()
    monkeypatch.setattr(settings, "music_listens_keep", "off")
    monkeypatch.setattr(music_listens, "SECOND_PRUNE_AFTER_SEC", 0.3)
    await _insert(ids["t1"], ids["alex"])
    music_listens.schedule_prune_now()
    await asyncio.sleep(0.1)
    assert await _listens() == []
    # A row a player landed after the first prune...
    await _insert(ids["t2"], ids["sam"])
    await asyncio.sleep(0.6)
    # ...goes with the second.
    assert await _listens() == []
    for t in list(music_listens._PRUNE_TASKS):
        await t
