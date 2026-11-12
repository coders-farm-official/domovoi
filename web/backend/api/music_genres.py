"""Music → Genres: the library browsed by genre (owner, 2026-11-12).

* ``GET /api/music/genres`` — one page of the genres the library has, each
  with its track count, searched and sorted like the Artists list (paging.py:
  limit / cursor / offset, ``total``, ``next_cursor``). Every answer also
  says how far the genre read has got (``tags_read`` of ``tags_total``,
  ``reading``) and how many read tracks have no genre (``untagged_count``),
  of which how many are waiting for a MusicBrainz genre
  (``musicbrainz_pending``), so a list that is still filling in can say so.
* ``GET /api/music/genre?key=`` — one genre: its name, track count and
  every track id (artist order) for "play" and "shuffle" in a room. Its
  tracks as rows come from ``GET /api/music/library?genre=<key>`` (paged,
  the Track shape); ``key=`` empty is the tracks with no genre.

A genre is a key of ``library_tracks.genre_keys`` (V050), normalised and
split by ``domovoi/library_genres.py`` ("Hip-Hop" and "Rap" are one,
"Rock; Indie" is two), named for display by its ``genre_label``. A track
with two genres counts under both. Genres come from the files' own tags,
read by the indexer for new files and by the core's tag backfill
(``domovoi/workers/library_tag_backfill.py``) for the tracks that were
already there; until that pass is done the list is partial and
``reading`` is true. A track whose file names no genre but which song
recognition matched gets one from MusicBrainz (V052,
``domovoi/workers/library_year_fetch.py``): until it has been asked it is
in the "no genre" row AND counted in ``musicbrainz_pending``, which is 0
whenever that lookup cannot run (song recognition off, Internet "never"),
so a wait that would never end is never shown as one.

Open reads, like ``GET /api/music/library`` and the Artists list: names of
music only. Worked out from one grouped query per request (one row per
distinct genre), nothing cached.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from domovoi import egress
from domovoi.config import settings as core_settings
from domovoi.library_genres import genre_label
from domovoi.library_genres import TAGS_VERSION
from web.backend.api import music_search, paging
from web.backend.db import session_scope

router = APIRouter(prefix="/api/music", tags=["music"])

#: Most track ids one genre answer carries (play / shuffle send them on).
MAX_TRACK_IDS = 2000
#: Longest genre key accepted (the normaliser keeps 40 characters).
MAX_KEY_CHARS = 100


class GenreRow(BaseModel):
    key: str
    name: str
    track_count: int
    cover_track_id: int | None = None


class GenrePage(BaseModel):
    total: int
    items: list[GenreRow]
    limit: int
    offset: int | None = None
    has_more: bool = False
    next_cursor: str | None = None
    # How far the genre read has got: tracks whose tags have been read for
    # genre and year, of all tracks. ``reading`` while they differ.
    tags_read: int = 0
    tags_total: int = 0
    reading: bool = False
    # Read tracks that have no genre (the "no genre" row).
    untagged_count: int = 0
    # Of those, the ones waiting for a MusicBrainz genre (a recording id,
    # not asked yet); 0 when the lookup can't run.
    musicbrainz_pending: int = 0


class GenreDetail(BaseModel):
    key: str
    name: str
    track_count: int
    track_ids: list[int]
    track_ids_truncated: bool = False


def musicbrainz_lookup_possible() -> bool:
    """Whether the core's MusicBrainz genre lookup can ever run here: song
    recognition on and the Internet answer not "never" (being offline for
    now is a wait, not a never)."""
    return bool(core_settings.library_enricher_enabled) and egress.internet_allowed()


async def _progress(s) -> tuple[int, int, int, int]:
    row = (await s.execute(text(
        "SELECT count(*) FILTER (WHERE tags_version >= :v),"
        " count(*),"
        " count(*) FILTER (WHERE tags_version >= :v AND coalesce(cardinality(genre_keys), 0) = 0),"
        " count(*) FILTER (WHERE tags_version >= :v AND coalesce(cardinality(genre_keys), 0) = 0"
        "                  AND musicbrainz_recording_id IS NOT NULL AND genre_checked_at IS NULL)"
        " FROM library_tracks"
    ), {"v": TAGS_VERSION})).one()
    pending = int(row[3]) if musicbrainz_lookup_possible() else 0
    return int(row[0]), int(row[1]), int(row[2]), pending


def genre_where(key: str, params: dict) -> str:
    """The WHERE clause for "tracks of genre ``key``" ('' = read tracks
    with no genre), binding its parameters into ``params``. Shared with
    ``GET /api/music/library?genre=``."""
    if key == "":
        params["genre_tv"] = TAGS_VERSION
        return "(tags_version >= :genre_tv AND coalesce(cardinality(genre_keys), 0) = 0)"
    params["genre_key"] = [key]
    return "genre_keys @> CAST(:genre_key AS text[])"


@router.get("/genres", response_model=GenrePage)
async def list_genres(
    q: str | None = Query(default=None, max_length=200,
                          description="Search genre names (the library search's rules)."),
    sort: Literal["name", "tracks"] = Query(default="name"),
    limit: int = Query(default=paging.DEFAULT_LIMIT, ge=1, le=paging.MAX_LIMIT,
                       description=paging.LIMIT_DOC),
    offset: int | None = paging.offset_query(),
    cursor: str | None = paging.cursor_query(),
) -> GenrePage:
    """One page of the library's genres (module docstring), with the read's
    progress. With ``q``, best match first, then ``sort``."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT k, count(*) AS n, min(id) AS first_id"
            " FROM library_tracks, unnest(genre_keys) AS k"
            " WHERE genre_keys IS NOT NULL"
            " GROUP BY k"
        ))).all()
        read, total_tracks, untagged, mb_pending = await _progress(s)
    tokens = music_search.search_tokens(q) if q else []
    ranked: list[tuple[tuple, GenreRow]] = []
    for key, n, first_id in rows:
        name = genre_label(str(key))
        r = 0
        if q:
            got = music_search.text_rank(music_search.search_norm(name), tokens) if tokens else None
            if got is None:
                continue
            r = got
        norm = music_search.search_norm(name)
        sort_key: tuple = (-r, -int(n), norm, str(key)) if sort == "tracks" else (-r, norm, str(key))
        ranked.append((sort_key, GenreRow(key=str(key), name=name, track_count=int(n),
                                          cover_track_id=int(first_id))))
    ranked.sort(key=lambda kv: kv[0])
    page, fields = paging.page_view(
        [g for _, g in ranked], [k for k, _ in ranked],
        sort=f"{sort}~q" if q else sort, limit=limit, cursor=cursor, offset=offset,
    )
    return GenrePage(
        items=page, **fields,
        tags_read=read, tags_total=total_tracks, reading=read < total_tracks,
        untagged_count=untagged, musicbrainz_pending=mb_pending,
    )


@router.get("/genre", response_model=GenreDetail)
async def genre_detail(
    key: str = Query(..., max_length=MAX_KEY_CHARS,
                     description="A genre key from GET /api/music/genres; '' = tracks with no genre."),
) -> GenreDetail:
    """One genre: its name, track count and every track id (artist order,
    as ``GET /api/music/library?genre=&sort=artist`` lists them), for play
    / shuffle in a room."""
    params: dict = {"cap": MAX_TRACK_IDS + 1}
    where = genre_where(key, params)
    async with session_scope() as s:
        count = int((await s.execute(
            text(f"SELECT count(*) FROM library_tracks WHERE {where}"), params,
        )).scalar_one())
        if count == 0:
            raise HTTPException(status_code=404, detail="no tracks of that genre in the library")
        ids = [int(r[0]) for r in (await s.execute(text(
            f"SELECT id FROM library_tracks WHERE {where}"
            " ORDER BY LOWER(artist) ASC NULLS LAST, added_at DESC, id DESC LIMIT :cap"
        ), params)).all()]
    return GenreDetail(
        key=key, name=genre_label(key) if key else "no genre", track_count=count,
        track_ids=ids[:MAX_TRACK_IDS], track_ids_truncated=len(ids) > MAX_TRACK_IDS,
    )


__all__ = ["genre_where", "router"]
