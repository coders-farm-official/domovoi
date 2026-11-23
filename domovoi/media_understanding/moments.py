"""The moment in a video a search matched (design MEDIA-UNDERSTANDING §9,
step 4: "each video hit carries the moment (``at_s``) of the best match").

``search_doc`` says WHICH video matches; this says WHEN: the transcript
line or the described keyframe that matches the words best, so a hit can
open the player there. Slice 2's ``GET /api/media/search`` calls
:func:`add_moments` on its page of hits::

    page = await search.search(s, parsed, ...)
    await moments.add_moments(s, page["items"], parsed.words)

Each video hit gains ``at_s`` (seconds, None when only its name or
summary matched) and ``moment``: ``{"at_s", "from": "transcript", "said"}``
or ``{"at_s", "from": "frame", "might_show", "tags"}``. ``said``,
``might_show`` and ``tags`` are untrusted text (a model's words, or what
anyone in the video said): shown as text, never HTML, a caption as
"Might show: ...". Ties go to the earliest moment.

Core-internal; both processes import it (the web's search route).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

#: How much of a matched line a hit carries.
MOMENT_TEXT_MAX = 300

MOMENTS_SQL = """
WITH q AS (SELECT websearch_to_tsquery('english', :q) AS tsq)
SELECT DISTINCT ON (item_id) item_id, at_s, source, words, tags FROM (
    SELECT sg.item_id, sg.start_s AS at_s, 'transcript' AS source, sg.text AS words,
           CAST(NULL AS TEXT[]) AS tags,
           ts_rank_cd(to_tsvector('english', sg.text), q.tsq) AS rank
      FROM media_segments sg, q
     WHERE sg.item_id = ANY(:ids) AND numnode(q.tsq) > 0 AND to_tsvector('english', sg.text) @@ q.tsq
    UNION ALL
    SELECT f.item_id, f.at_s, 'frame', f.caption, f.tags,
           ts_rank_cd(setweight(to_tsvector('english', array_to_string(f.tags, ' ')), 'A')
                      || setweight(to_tsvector('english', f.caption || ' ' || coalesce(f.ocr_text, '')), 'B'),
                      q.tsq)
      FROM media_frames f, q
     WHERE f.item_id = ANY(:ids) AND f.at_s IS NOT NULL AND numnode(q.tsq) > 0
       AND (setweight(to_tsvector('english', array_to_string(f.tags, ' ')), 'A')
            || setweight(to_tsvector('english', f.caption || ' ' || coalesce(f.ocr_text, '')), 'B')) @@ q.tsq
) x
ORDER BY item_id, rank DESC, at_s ASC
"""


async def moments(s: Any, item_ids: list[int], words: str) -> dict[int, dict[str, Any]]:
    """``{item_id: moment}`` for the videos among ``item_ids`` whose
    transcript or keyframes match ``words`` (``websearch_to_tsquery``)."""
    ids = [int(i) for i in item_ids]
    if not ids or not (words or "").strip():
        return {}
    rows = (await s.execute(text(MOMENTS_SQL), {"q": words, "ids": ids})).mappings().all()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        said = (r["words"] or "")[:MOMENT_TEXT_MAX]
        m: dict[str, Any] = {"at_s": round(float(r["at_s"]), 2), "from": r["source"]}
        if r["source"] == "transcript":
            m["said"] = said
        else:
            m["might_show"] = said or None
            m["tags"] = list(r["tags"] or [])
        out[int(r["item_id"])] = m
    return out


async def add_moments(s: Any, hits: list[dict[str, Any]], words: str) -> list[dict[str, Any]]:
    """Give each video hit its ``at_s`` and ``moment`` (in place; also
    returned). Photos are left as they are."""
    videos = [h for h in hits if h.get("kind") == "video"]
    found = await moments(s, [h["id"] for h in videos], words)
    for h in videos:
        m = found.get(int(h["id"]))
        h["at_s"] = m["at_s"] if m else None
        h["moment"] = m
    return hits
