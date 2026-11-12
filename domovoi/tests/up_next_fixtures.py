"""Shared fixtures for the Up next tests: a synthetic library and in-memory
stand-ins for the engine's two data interfaces (domovoi/up_next/data.py).

The library: 300 tracks by 40 artists over six decades (each artist sits
in one decade, ±4 years), eight genres; every 11th track has no year and
every 13th no genre, every 17th is a household favourite.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

from domovoi.up_next.data import Scope
from domovoi.up_next.model import Track, TrackStats
from domovoi.up_next.session import SeedRef

NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)
ERAS = (1965, 1975, 1985, 1995, 2005, 2015)
GENRES = ("rock", "soul", "jazz", "hip hop", "electronic", "folk", "pop", "r&b")


def artist_of(i: int, artists: int = 40) -> str:
    return f"artist {(i - 1) % artists:02d}"


def synthetic_library(n: int = 300, artists: int = 40, *, seed: int = 7,
                      years: bool = True, genres: bool = True) -> list[Track]:
    rng = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        a = (i - 1) % artists
        year = ERAS[a % len(ERAS)] + rng.randint(-4, 4)
        keys = (GENRES[a % len(GENRES)],) + ((GENRES[(a + 3) % len(GENRES)],) if a % 3 == 0 else ())
        out.append(Track(
            id=i,
            artist_norm=artist_of(i, artists),
            year=year if years and i % 11 else None,
            genre_keys=keys if genres and i % 13 else (),
            favorite=i % 17 == 0,
        ))
    return out


class MemoryLibrary:
    """:class:`~domovoi.up_next.data.LibrarySource` over a list of tracks:
    everything is a candidate, every track a wildcard."""

    def __init__(self, tracks: Sequence[Track], *, playlists: Mapping[int, tuple[str, list[int]]] | None = None,
                 similar: Mapping[int, str] | None = None) -> None:
        self.by_id = {t.id: t for t in tracks}
        self.playlists = dict(playlists or {})
        self._similar = dict(similar or {})
        self.calls: list[str] = []

    async def tracks(self, ids: Iterable[int]) -> dict[int, Track]:
        self.calls.append("tracks")
        return {i: self.by_id[i] for i in ids if i in self.by_id}

    async def near(self, seeds, *, salt, era_years):
        self.calls.append("near")
        return list(self.by_id.values())

    async def wildcards(self, *, salt, rng):
        self.calls.append("wildcards")
        return list(self.by_id.values())

    async def playlist_together(self, seed_ids, *, salt):
        self.calls.append("playlist_together")
        hits: dict[int, dict[int, str]] = {}
        for _pid, (name, ids) in sorted(self.playlists.items()):
            for s in seed_ids:
                if s in ids:
                    for o in ids:
                        if o != s and o in self.by_id:
                            hits.setdefault(s, {}).setdefault(o, f"from {name.lower()}")
        tids = {t for m in hits.values() for t in m}
        return hits, [self.by_id[t] for t in sorted(tids)]

    async def list_members(self, seed: SeedRef) -> list[Track]:
        self.calls.append("list_members")
        if seed.kind == "ids":
            return [self.by_id[i] for i in seed.track_ids if i in self.by_id]
        if seed.kind == "playlist":
            _name, ids = self.playlists[int(seed.ref)]
            return [self.by_id[i] for i in ids if i in self.by_id]
        if seed.kind == "artist":
            return [t for t in self.by_id.values() if t.artist_norm == seed.ref]
        if seed.kind == "genre":
            return [t for t in self.by_id.values() if seed.ref in t.genre_keys]
        return []

    async def similar(self, seeds):
        return dict(self._similar)


class MemoryListens:
    def __init__(self, *, stats: Mapping[int, TrackStats] | None = None,
                 heard: Mapping[int, datetime] | None = None,
                 together: Mapping[int, Mapping[int, str]] | None = None) -> None:
        self._stats = dict(stats or {})
        self._heard = dict(heard or {})
        self._together = {k: dict(v) for k, v in (together or {}).items()}
        self.scopes: list[Scope] = []

    async def stats(self, scope, track_ids, *, now):
        self.scopes.append(scope)
        ids = set(track_ids)
        return {k: v for k, v in self._stats.items() if k in ids}

    async def last_heard(self, scope, *, now, hours):
        self.scopes.append(scope)
        return {k: v for k, v in self._heard.items() if v >= now - timedelta(hours=hours)}

    async def together(self, scope, seed_ids, *, now):
        self.scopes.append(scope)
        return {s: dict(self._together[s]) for s in seed_ids if s in self._together}
