"""Search the house's photos and videos by what is in them and when
(1.5.0, design MEDIA-UNDERSTANDING §9; slice 2: words and dates).

* ``GET /api/media/search`` (household, ``require_device_read``):
  ``q`` (what someone typed: words, a kind like "photos", a time like
  "last summer" or "2023"), optional ``kind`` (``image`` | ``video``),
  ``from`` / ``to`` (``YYYY-MM-DD``, the household's days, ``to``
  included), ``limit`` (1-100, default 40) and ``offset``.

  Answers ``{"query": {"words", "kind", "when"}, "items": [...], "more"}``.
  Each item: ``id``, ``library_id``, ``path``, ``name``, ``kind``,
  ``analysed``, ``taken_at``, ``date_from`` (``photo``: its EXIF date;
  ``file``: the file's time), ``might_show`` (the caption: what the
  picture MIGHT show, a model's words), ``tags``, ``has_text``, ``text``
  (the text found in it, up to 500 characters) and ``score``.

  ``might_show``, ``tags`` and ``text`` are untrusted text a model or OCR
  made from a picture: show them as text, never as HTML, and a caption as
  "Might show: ...". The household tier because what is in the family's
  photos (the words on a letter, a caption naming a scene) is household
  content, like its history; the thumbnails stay where they are
  (``/api/images/thumb``).

The parsing and the query are in :mod:`domovoi.media_understanding.search`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domovoi.admin_auth import require_device_read
from domovoi.media_understanding import moments, search
from web.backend.db import session_scope

router = APIRouter(tags=["media-understanding"])

#: A search is at most this long (what someone types or says).
Q_MAX = 300


def _day(raw: str | None, name: str) -> date | None:
    if raw is None or raw == "":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} is a date, YYYY-MM-DD") from None


@router.get("/api/media/search", dependencies=[Depends(require_device_read)])
async def media_search(
    q: str = Query("", max_length=Q_MAX),
    kind: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    limit: int = Query(40, ge=1, le=search.LIMIT_MAX),
    offset: int = Query(0, ge=0, le=100_000),
) -> dict[str, Any]:
    from domovoi import household_time

    if kind not in (None, "", "image", "video"):
        raise HTTPException(status_code=422, detail="kind is image or video")
    start_d, end_d = _day(date_from, "from"), _day(date_to, "to")
    if start_d and end_d and end_d < start_d:
        raise HTTPException(status_code=422, detail="to is before from")
    start = household_time.to_utc(datetime(start_d.year, start_d.month, start_d.day)) if start_d else None
    end = None
    if end_d:
        nxt = end_d + timedelta(days=1)
        end = household_time.to_utc(datetime(nxt.year, nxt.month, nxt.day))
    parsed = search.parse_query(q)
    async with session_scope() as s:
        page = await search.search(s, parsed, kind=kind or None, start=start, end=end, limit=limit, offset=offset)
        # A video hit opens at the moment that matched (transcript line or keyframe).
        await moments.add_moments(s, page["items"], parsed.words)
    return {"query": {"words": parsed.words, "kind": kind or parsed.kind, "when": parsed.when}, **page}
