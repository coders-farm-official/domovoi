"""The Music mix (domovoi/up_next/mix.py, web/backend/api/music_mix.py)
and "why this song?" (domovoi/up_next/why.py).

* the sliders become the engine's numbers, the middle step is today's;
* bad stored values fall back; the list length is bounded;
* a person's override wins for their sessions, the household's otherwise;
* "why" is said from the signals the picker really used;
* the API: tiers, round trip, a person override, the autoplay default a
  new session takes.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.up_next import mix as M
from domovoi.up_next.model import (
    DEFAULT_CONFIG,
    DEFAULT_SHARES,
    SAME_ARTIST,
    WILDCARD,
    Pick,
    Track,
    TrackStats,
)
from domovoi.up_next.pick import library_batch, why_signals
from domovoi.up_next.why import factor_text, why_text

NOW = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)


# ─── Sliders → engine ─────────────────────────────────────────────────────


def test_the_middle_step_is_todays_engine():
    c = M.Mix().config()
    assert (c.fresh_min, c.fresh_max, c.affinity_step) == (
        DEFAULT_CONFIG.fresh_min, DEFAULT_CONFIG.fresh_max, DEFAULT_CONFIG.affinity_step)
    assert c.shares == pytest.approx(DEFAULT_SHARES)
    assert c.list_weighted_share == DEFAULT_CONFIG.list_weighted_share
    assert c.list_repeat_min == DEFAULT_CONFIG.list_repeat_min


def test_discovery_widens_freshness_and_favorites_leans_on_taste():
    d, f = M.Mix(discovery_favorites=1).config(), M.Mix(discovery_favorites=5).config()
    assert d.fresh_max - d.fresh_min > M.Mix().config().fresh_max - M.Mix().config().fresh_min
    assert f.fresh_max - f.fresh_min < M.Mix().config().fresh_max - M.Mix().config().fresh_min
    assert f.affinity_step > DEFAULT_CONFIG.affinity_step > d.affinity_step
    steps = [M.Mix(discovery_favorites=i).config() for i in M.STEPS]
    assert [c.fresh_max for c in steps] == sorted((c.fresh_max for c in steps), reverse=True)


def test_wander_takes_more_wildcards_and_shares_still_add_up():
    prev = -1.0
    for i in M.STEPS:
        c = M.Mix(close_wander=i).config()
        assert sum(c.shares.values()) == pytest.approx(1.0)
        assert c.shares[WILDCARD] > prev
        prev = c.shares[WILDCARD]
    assert M.Mix(close_wander=1).config().list_weighted_share > M.Mix(close_wander=5).config().list_weighted_share


def test_stored_values_are_checked():
    m = M.from_json({"discovery_favorites": 9, "close_wander": "2", "autoplay": "yes", "list_repeat_min": 5})
    assert (m.discovery_favorites, m.close_wander, m.autoplay, m.list_repeat_min) == (3, 2, True, 20)
    assert M.from_json('{"list_repeat_min": 9999}').list_repeat_min == 500
    assert M.from_json("not json") == M.Mix()
    assert M.person_override({"close_wander": 5, "other": 1}) == {"close_wander": 5}
    assert M.person_override(None) is None


def test_a_person_override_replaces_only_the_sliders():
    base = M.Mix(discovery_favorites=2, close_wander=2, autoplay=False, list_repeat_min=80)
    out = M.apply_override(base, {"discovery_favorites": 5})
    assert out == M.Mix(discovery_favorites=5, close_wander=2, autoplay=False, list_repeat_min=80)
    assert M.apply_override(base, None) == base


def test_wander_changes_what_a_batch_holds():
    lib = [Track(i, f"a{i % 5}", 1990 + i % 3, ("soul",)) for i in range(1, 400)]
    seeds = [(lib[0], 1.0)]

    def wild(step):
        picks = library_batch(n=30, rng=random.Random(4), now=NOW, seeds=seeds, candidates=lib,
                              config=M.Mix(close_wander=step).config())
        return sum(p.reason_code == WILDCARD for p in picks)

    assert wild(1) < wild(3) < wild(5)


# ─── Why this song ────────────────────────────────────────────────────────


def test_why_comes_from_the_real_signals():
    t = Track(5, "nina", 1966, ("soul",))
    p = Pick(5, SAME_ARTIST, "same artist", seed_track_id=1)
    why = why_signals(p, t, stats={5: TrackStats(8, 8, 0, 3.0)}, popularity=None,
                      config=DEFAULT_CONFIG, seed_roles={1: "last"})
    assert why == (("same_artist", "last"), ("plays_often", "8"))
    assert why_text(why) == "same artist as the last song · you play this often"

    old = why_signals(p, t, stats={5: TrackStats(0, 0, 0, 120.0), 9: TrackStats(1, 1, 0, 1.0)},
                      popularity=None, config=DEFAULT_CONFIG, seed_roles={1: "start"})
    assert why_text(old) == "same artist as the song you started with · not played in 4 months"
    # No listening history at all: freshness is the same for every song, so
    # it is never given as a reason.
    assert why_signals(p, t, stats={}, popularity=None, config=DEFAULT_CONFIG) == (("same_artist", "recent"),)
    pop = why_signals(p, t, stats={}, popularity={5: 1.0}, config=DEFAULT_CONFIG)
    assert why_text(pop) == "same artist as a song you just heard · one of the artist's best-known songs"


def test_why_words():
    assert why_text([["same_era", "90s|last"]]) == "from the 90s, like the last song"
    assert why_text([["same_era", "from 1997-ish|start"]]) == "from around 1997, like the song you started with"
    assert why_text([["same_genre", "soul|recent"], ["favorite", ""]]) == "soul, like a song you just heard · a favourite"
    assert why_text([["played_together", "from road trip|last"]]) == "on road trip with the last song"
    assert why_text([["similar_artist", "similar to radiohead"]]) == "by an artist like radiohead"
    assert why_text([["wildcard", "wildcard"], ["not_played", ""]]) == "a wildcard from your library · not played in a long time"
    assert why_text(None) is None and why_text([["bogus", "x"]]) is None
    assert factor_text([["same_artist", "last"], ["plays_often", "5"]]) == "you play this often"
    assert factor_text([["same_artist", "last"]]) is None


def test_every_library_pick_carries_its_why():
    lib = [Track(i, f"a{i % 7}", 1980 + i % 20, ("soul",) if i % 2 else ("jazz",)) for i in range(1, 300)]
    stats = {i: TrackStats(i % 5, i % 5, 0, float(i % 40)) for i in range(1, 300, 3)}
    picks = library_batch(n=30, rng=random.Random(1), now=NOW, seeds=[(lib[0], 1.0)], candidates=lib,
                          stats=stats, seed_roles={lib[0].id: "last"})
    assert picks and all(p.why and p.why[0][0] == p.reason_code for p in picks)
    assert all(why_text(p.why) for p in picks)


# ─── The API ──────────────────────────────────────────────────────────────

from domovoi.tests.auth_testkit import (  # noqa: E402
    HEADER,
    _db,  # noqa: F401 — fixture
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db  # noqa: E402
from domovoi.tests.test_up_next_api import _gates  # noqa: E402


def test_route_tiers():
    assert admin_auth.require_device_read in _gates("GET", "/api/music/mix")
    assert admin_auth.require_device in _gates("PUT", "/api/music/mix")
    assert admin_auth.require_device_read in _gates("GET", "/api/people/{person_id}/music-mix")
    assert admin_auth.require_device in _gates("PUT", "/api/people/{person_id}/music-mix")


@pytest.fixture
async def clean():
    from domovoi.db.session import engine

    async def _t():
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM household_settings WHERE key = 'music_mix'"))
            await conn.execute(text(
                "TRUNCATE up_next_picks, up_next_sessions, devices, library_tracks, people RESTART IDENTITY CASCADE"))
    await _t()
    yield
    await _t()


@requires_db
async def test_mix_round_trip_person_override_and_autoplay_default(_db, clean):
    from domovoi.db.session import engine

    async with web_client() as c:
        await claim_admin(c)
    token = await db_device_token()
    async with engine.begin() as conn:
        alex = (await conn.execute(text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
        tid = (await conn.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES ('/home/alex/Music/a.flac', 'A', 'Nina') "
            "RETURNING id"))).scalar_one()
    dev = web_client(headers={"X-Requested-With": "t", HEADER: token, "X-Device-Id": "browser-1"})
    async with dev as d, web_client() as anon:
        r = await d.get("/api/music/mix")
        assert r.status_code == 200
        body = r.json()
        assert (body["discovery_favorites"], body["close_wander"], body["autoplay"]) == (3, 3, True)
        assert body["internet"]["similar_artists"] is None  # no core snapshot in tests

        assert (await anon.put("/api/music/mix", json={"autoplay": False})).status_code in (401, 403)
        r = await d.put("/api/music/mix", json={"discovery_favorites": 1, "autoplay": False})
        assert r.status_code == 200 and r.json()["discovery_favorites"] == 1 and r.json()["close_wander"] == 3
        assert (await d.put("/api/music/mix", json={"close_wander": 6})).status_code == 422
        assert (await d.put("/api/music/mix", json={"list_repeat_min": 5})).status_code == 422

        r = await d.get(f"/api/people/{alex}/music-mix")
        assert r.json() == {"person_id": alex, "override": None,
                            "uses": {"discovery_favorites": 1, "close_wander": 3}}
        r = await d.put(f"/api/people/{alex}/music-mix",
                        json={"override": {"discovery_favorites": 5, "close_wander": 4}})
        assert r.json()["uses"] == {"discovery_favorites": 5, "close_wander": 4}
        assert (await d.get("/api/music/mix")).json()["people_with_own_mix"] == [alex]
        r = await d.put(f"/api/people/{alex}/music-mix", json={"override": None})
        assert r.json()["override"] is None
        assert (await d.get("/api/people/999/music-mix")).status_code == 404

        # A session started without saying takes the household's autoplay.
        r = await d.post("/api/music/up-next/sessions", json={
            "target": {"kind": "device", "device_id": "browser-1"}, "start": {"track_id": tid}})
        assert r.status_code in (200, 201), r.text
        assert r.json()["autoplay"] is False

    async with engine.begin() as conn:
        from domovoi.up_next import mix as mix_mod

        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(bind=conn) as s:
            await mix_mod.save_person(s, alex, {"close_wander": 5})
            used = await mix_mod.resolve(s, alex)
            house = await mix_mod.resolve(s, None)
    assert (used.discovery_favorites, used.close_wander) == (1, 5)
    assert (house.discovery_favorites, house.close_wander) == (1, 3)
