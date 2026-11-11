"""Music → Artists: the library browsed by performer (owner, 2026-11-11).

* ``GET /api/music/artists`` — one page of performers with their track
  and album counts; searched like the library (music_search: every word,
  punctuation- and accent-blind, best match first), sorted by name or by
  how many tracks they have. Same paging contract as every files-like list
  (paging.py: limit / cursor / offset, ``total``, ``next_cursor``).
* ``GET /api/music/artist?name=`` — one performer: their albums (track
  count, a track for the cover) and every track id in album order, for
  "play" and "shuffle". Their tracks as rows come from
  ``GET /api/music/library?artist=<name>`` (paged, the Track shape).

A performer is a name as the library credits it, split the way the
spoken-name resolver splits a credit (spoken_names.split_credit):
"feat." / "ft." / "x" / "vs" always separate performers; a list joined by
"," / ";" / "&" ("Jeezy, T.I.") is split only when one of its names is
credited on its own somewhere else in the library — so T.I.'s songs with
Jeezy are under T.I. too, while "Earth, Wind & Fire" stays one band.
Spellings that read the same ("T.I.", "TI") are one performer, shown as
the spelling with the most tracks. Names of music are open reads, like
``GET /api/music/library``.

The list is worked out from one grouped query per request (distinct
credit × album rows: a few thousand on a big library), nothing cached.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from web.backend.api import music_search, paging
from web.backend.db import session_scope

router = APIRouter(prefix="/api/music", tags=["music"])

# "feat." and its kin always join separate performers; "," ";" " & " may
# be one band's name. The same separators as spoken_names.split_credit.
_STRONG_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|x|vs\.?)\s+", re.I)
_WEAK_RE = re.compile(r"\s*(?:,|;|\s&\s)\s*")

#: Most track ids one artist answer carries (play / shuffle send them on).
MAX_TRACK_IDS = 2000


def _key(name: str) -> str:
    """One performer however the name is spaced or dotted: "T.I.", "TI",
    "T I" are all ``ti``."""
    return music_search.search_norm(name).replace(" ", "")


def performers_of(credit: str, known: set[str]) -> list[str]:
    """The performers a credit names (see the module docstring). ``known``:
    the keys of names credited on their own somewhere."""
    out: dict[str, None] = {}
    for part in _STRONG_RE.split(credit or ""):
        part = part.strip()
        if not part:
            continue
        frags = [f.strip() for f in _WEAK_RE.split(part) if f.strip()]
        if len(frags) > 1 and any(_key(f) in known for f in frags):
            for f in frags:
                out.setdefault(f, None)
        else:
            out.setdefault(part, None)
    return list(out)


@dataclass
class _Artist:
    key: str
    spellings: Counter = field(default_factory=Counter)
    credits: set[str] = field(default_factory=set)
    albums: dict[str, int] = field(default_factory=dict)
    tracks: int = 0
    cover_track_id: int | None = None

    @property
    def name(self) -> str:
        return self.spellings.most_common(1)[0][0]


async def _load() -> dict[str, _Artist]:
    """Every performer in the library, by key."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT artist, coalesce(album, '') AS album, count(*) AS n, min(id) AS first_id"
            " FROM library_tracks WHERE coalesce(btrim(artist), '') <> ''"
            " GROUP BY artist, coalesce(album, '')"
        ))).all()
    known: set[str] = set()
    for credit, _album, _n, _first in rows:
        for part in _STRONG_RE.split(credit):
            part = part.strip()
            if part and len([f for f in _WEAK_RE.split(part) if f.strip()]) == 1:
                known.add(_key(part))
    artists: dict[str, _Artist] = {}
    split_cache: dict[str, list[str]] = {}
    for credit, album, n, first_id in rows:
        names = split_cache.get(credit)
        if names is None:
            names = split_cache[credit] = performers_of(credit, known)
        for name in names:
            key = _key(name) or name.strip().lower()
            a = artists.get(key)
            if a is None:
                a = artists[key] = _Artist(key=key)
            a.spellings[name] += int(n)
            a.credits.add(credit)
            a.tracks += int(n)
            a.albums[album] = a.albums.get(album, 0) + int(n)
            if a.cover_track_id is None or (album and first_id < a.cover_track_id):
                a.cover_track_id = int(first_id)
    return artists


async def credits_for(name: str) -> set[str]:
    """The raw ``library_tracks.artist`` values that credit performer
    ``name`` (any spelling that reads the same); empty when none do."""
    artists = await _load()
    a = artists.get(_key(name) or name.strip().lower())
    return set(a.credits) if a else set()


class ArtistRow(BaseModel):
    name: str
    track_count: int
    album_count: int
    cover_track_id: int | None = None


class ArtistPage(BaseModel):
    total: int
    items: list[ArtistRow]
    limit: int
    offset: int | None = None
    has_more: bool = False
    next_cursor: str | None = None


class ArtistAlbum(BaseModel):
    name: str | None
    track_count: int
    cover_track_id: int | None = None


class ArtistDetail(BaseModel):
    name: str
    track_count: int
    albums: list[ArtistAlbum]
    track_ids: list[int]
    track_ids_truncated: bool = False


def _sort_name(name: str) -> str:
    k = music_search.search_norm(name)
    return k[4:] if k.startswith("the ") and len(k) > 4 else k


def _row(a: _Artist) -> ArtistRow:
    return ArtistRow(
        name=a.name, track_count=a.tracks,
        album_count=sum(1 for al in a.albums if al), cover_track_id=a.cover_track_id,
    )


@router.get("/artists", response_model=ArtistPage)
async def list_artists(
    q: str | None = Query(default=None, max_length=200,
                          description="Search artist names (the library search's rules)."),
    sort: Literal["name", "tracks"] = Query(default="name"),
    limit: int = Query(default=paging.DEFAULT_LIMIT, ge=1, le=paging.MAX_LIMIT,
                       description=paging.LIMIT_DOC),
    offset: int | None = paging.offset_query(),
    cursor: str | None = paging.cursor_query(),
) -> ArtistPage:
    """One page of the library's performers (module docstring). With ``q``,
    best match first, then ``sort``."""
    artists = list((await _load()).values())
    tokens = music_search.search_tokens(q) if q else []
    ranked: list[tuple[tuple, _Artist]] = []
    for a in artists:
        r = 0
        if q:
            got = music_search.text_rank(music_search.search_norm(a.name), tokens) if tokens else None
            if got is None:
                continue
            r = got
        name_key = _sort_name(a.name)
        if sort == "tracks":
            key: tuple = (-r, -a.tracks, name_key, a.key)
        else:
            key = (-r, name_key, a.key)
        ranked.append((key, a))
    ranked.sort(key=lambda kv: kv[0])
    cursor_sort = f"{sort}~q" if q else sort
    rows, fields = paging.page_view(
        [a for _, a in ranked], [k for k, _ in ranked],
        sort=cursor_sort, limit=limit, cursor=cursor, offset=offset,
    )
    return ArtistPage(items=[_row(a) for a in rows], **fields)


@router.get("/artist", response_model=ArtistDetail)
async def artist_detail(
    name: str = Query(..., min_length=1, max_length=500),
    album: str | None = Query(
        default=None, max_length=500,
        description="Only this album's track ids ('' = their tracks with no album).",
    ),
) -> ArtistDetail:
    """One performer: albums A-Z (no album last) and every track id in
    album order (the files' order within an album), for play / shuffle;
    with ``album``, that album's ids only."""
    artists = await _load()
    a = artists.get(_key(name) or name.strip().lower())
    if a is None:
        raise HTTPException(status_code=404, detail="no artist by that name in the library")
    params: dict = {"credits": list(a.credits), "cap": MAX_TRACK_IDS + 1}
    only = ""
    if album is not None:
        only = " AND coalesce(album, '') = :album"
        params["album"] = album
    async with session_scope() as s:
        covers = {
            str(al): int(first) for al, first in (await s.execute(text(
                "SELECT coalesce(album, ''), min(id) FROM library_tracks"
                " WHERE artist = ANY(:credits) GROUP BY coalesce(album, '')"
            ), params)).all()
        }
        ids = [int(r[0]) for r in (await s.execute(text(
            "SELECT id FROM library_tracks WHERE artist = ANY(:credits)" + only +
            " ORDER BY LOWER(album) ASC NULLS LAST, file_path, id LIMIT :cap"
        ), params)).all()]
    albums = sorted(a.albums, key=lambda al: (al == "", al.lower()))
    return ArtistDetail(
        name=a.name, track_count=a.tracks,
        albums=[
            ArtistAlbum(name=al or None, track_count=a.albums[al], cover_track_id=covers.get(al))
            for al in albums
        ],
        track_ids=ids[:MAX_TRACK_IDS],
        track_ids_truncated=len(ids) > MAX_TRACK_IDS,
    )


__all__ = ["credits_for", "performers_of", "router"]
