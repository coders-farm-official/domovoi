"""The freshness weight and the Music mix, measured (domovoi/up_next).

Plain shuffles keep landing on the same rotation. The freshness weight
(:func:`domovoi.up_next.pick.freshness`) nudges toward songs not heard in a
while. This module simulates a month of evenings on a seeded 5,000-song
library with a skewed history — 200 songs played over and over in the
last days, 1,500 heard once or twice months ago, the rest never — through
the real engine (:func:`~domovoi.up_next.engine.next_batch`), recording
each evening's picks as listens, and compares:

* freshness on vs off: more distinct songs and artists over the month
  with it on, and the most-played songs still come up sometimes;
* the Music mix's Discovery end vs its Favorites end: Discovery covers
  more of the library.

The library source mimics the SQL one's limits (60 per artist, 80 per
era, 60 per genre near the seeds, 200 wildcards), so candidates are drawn
the way they are in a house. Pure: no database, no network.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from domovoi.up_next import mix as M
from domovoi.up_next.engine import next_batch
from domovoi.up_next.model import DEFAULT_CONFIG, Track, TrackStats
from domovoi.up_next.pick import freshness
from domovoi.up_next.session import SeedRef, UpNextSession

START = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
GENRES = ("rock", "soul", "jazz", "hip hop", "electronic", "folk", "pop", "r&b", "metal", "country")


def _lib(n: int = 5000, artists: int = 250, seed: int = 11) -> list[Track]:
    rng = random.Random(seed)
    era = {a: rng.choice((1965, 1975, 1985, 1995, 2005, 2015)) for a in range(artists)}
    out = []
    for i in range(1, n + 1):
        a = (i - 1) % artists
        out.append(Track(id=i, artist_norm=f"artist {a:03d}", year=era[a] + rng.randint(-4, 4),
                         genre_keys=(GENRES[a % len(GENRES)],), favorite=(i % 97 == 0)))
    return out


def _h(salt: str, i: int) -> int:
    return int.from_bytes(hashlib.blake2b(f"{salt}:{i}".encode(), digest_size=4).digest(), "big")


class SampledLibrary:
    """The SQL library's limits, in memory."""

    def __init__(self, tracks):
        self.by_id = {t.id: t for t in tracks}
        self.by_artist = defaultdict(list)
        self.by_genre = defaultdict(list)
        for t in tracks:
            self.by_artist[t.artist_norm].append(t)
            for g in t.genre_keys:
                self.by_genre[g].append(t)
        self.all = list(tracks)

    async def tracks(self, ids):
        return {i: self.by_id[i] for i in ids if i in self.by_id}

    async def near(self, seeds, *, salt, era_years):
        out = {}
        for s in seeds:
            for lst, lim in ((self.by_artist.get(s.artist_norm, []), 60),
                             ([t for t in self.all if s.year and t.year and abs(t.year - s.year) <= era_years], 80),
                             (self.by_genre.get(s.primary_genre, []), 60)):
                for t in sorted(lst, key=lambda t: _h(salt, t.id))[:lim]:
                    out[t.id] = t
        return list(out.values())

    async def wildcards(self, *, salt, rng):
        return rng.sample(self.all, 200)

    async def playlist_together(self, seed_ids, *, salt):
        return {}, []

    async def list_members(self, seed):
        return []

    async def similar(self, seeds, *, salt=""):
        return {}

    async def popularity(self, track_ids):
        return {}


class History:
    """music_listens in memory: plays per track (time stamps)."""

    def __init__(self):
        self.plays: dict[int, list[datetime]] = defaultdict(list)

    def add(self, tid, at):
        self.plays[tid].append(at)

    async def stats(self, scope, track_ids, *, now):
        out = {}
        for tid in track_ids:
            ts = [t for t in self.plays.get(tid, ()) if t <= now and t >= now - timedelta(days=365)]
            if not ts:
                continue
            recent = [t for t in ts if t >= now - timedelta(days=90)]
            out[tid] = TrackStats(finished=len(recent), plays=len(recent), skipped=0,
                                  last_days=(now - max(ts)).total_seconds() / 86400)
        return out

    async def last_heard(self, scope, *, now, hours):
        cut = now - timedelta(hours=hours)
        return {tid: max(ts) for tid, ts in self.plays.items() if ts and max(ts) >= cut and max(ts) <= now}

    async def together(self, scope, seed_ids, *, now):
        return {}


def _skewed_history(rng: random.Random) -> tuple[History, list[int]]:
    h = History()
    rotation = rng.sample(range(1, 5001), 200)
    for tid in rotation:  # the songs that always come back
        for _ in range(rng.randint(6, 15)):
            h.add(tid, START - timedelta(days=rng.uniform(0.2, 6)))
    for tid in rng.sample(range(1, 5001), 1500):  # heard once or twice, long ago
        for _ in range(rng.randint(1, 2)):
            h.add(tid, START - timedelta(days=rng.uniform(40, 300)))
    return h, rotation


async def _month(config, *, days: int = 30, batches: int = 2, seed: int = 5):
    rng = random.Random(seed)
    lib = SampledLibrary(_lib())
    hist, rotation = _skewed_history(rng)
    picked: list[int] = []
    for day in range(days):
        now = START + timedelta(days=day)
        # Evenings start from a song people love (the rotation) most nights.
        first = rng.choice(rotation) if rng.random() < 0.7 else rng.randint(1, 5000)
        hist.add(first, now)
        sess = UpNextSession.start(target="room:kitchen", mode="library", seed=SeedRef(first),
                                   rng_seed=rng.getrandbits(32))
        t = now
        for _ in range(batches):
            b = await next_batch(sess, library=lib, listens=hist, now=t, config=config)
            for p in b.picks:
                t += timedelta(minutes=4)
                hist.add(p.track_id, t)
                picked.append(p.track_id)
                sess.recent_listened = [p.track_id, *sess.recent_listened][:5]
    artists = {lib.by_id[i].artist_norm for i in picked}
    rot = set(rotation)
    return {"picks": len(picked), "songs": len(set(picked)), "repeats": len(picked) - len(set(picked)),
            "artists": len(artists), "rotation_picks": sum(1 for i in picked if i in rot)}


def test_freshness_shape():
    assert freshness(None) == pytest.approx(1.3)
    assert freshness(TrackStats(last_days=0)) == pytest.approx(0.7)
    assert freshness(TrackStats(last_days=7)) < freshness(TrackStats(last_days=30)) < freshness(None)
    assert freshness(TrackStats(plays=30, last_days=0)) == pytest.approx(0.6)  # the floor


@pytest.mark.slow
async def test_freshness_spreads_a_month_over_more_of_the_library(capsys):
    off = await _month(DEFAULT_CONFIG.__class__(freshness=False))
    on = await _month(DEFAULT_CONFIG)
    with capsys.disabled():
        print(f"\nfreshness off: {off}\nfreshness on:  {on}")
    assert on["picks"] == off["picks"] == 30 * 60
    assert on["songs"] >= off["songs"] * 1.03
    assert on["repeats"] <= off["repeats"] * 0.85        # measured: 280 -> 217
    assert on["artists"] >= off["artists"]
    assert on["rotation_picks"] > 0                      # the favourites still come up
    assert on["rotation_picks"] < off["rotation_picks"]  # just not every night


@pytest.mark.slow
async def test_discovery_covers_more_than_favorites(capsys):
    disc = await _month(M.Mix(discovery_favorites=1).config())
    fav = await _month(M.Mix(discovery_favorites=5).config())
    with capsys.disabled():
        print(f"\ndiscovery: {disc}\nfavorites: {fav}")
    assert disc["songs"] > fav["songs"]
    assert disc["rotation_picks"] < fav["rotation_picks"]
    assert disc["rotation_picks"] > 0
