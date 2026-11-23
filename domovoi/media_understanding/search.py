"""Searching what the house worked out about its photos and videos
(design MEDIA-UNDERSTANDING §9; slice 2: words and dates).

**The index.** ``media_items.search_doc`` is a tsvector kept by the writer
(:func:`refresh_doc`, called by every analyser that saves a result), with
weights by how much a word says about the file:

* **A** the tags ("dog", "beach");
* **B** the captions and the summary;
* **C** the text found in the picture and what is said (transcripts);
* **D** the file and folder names.

Every part is read with the ``english`` configuration, so "beaches" finds
"beach". File and folder names are also matched by trigram
(``idx_media_items_names``), so a file nobody has looked at yet is found
by its name, and a part of a name ("birthd") still matches.

**A query** (:func:`parse_query`) is plain code, never a model:

* the kind: "photo(s)", "picture(s)", "pics" → images; "video(s)",
  "clip(s)" → videos;
* a time: a year ("2023"), a month ("July", "July 2023"), a season
  ("summer", "last summer", "winter 2022"), a holiday ("Christmas",
  "Christmas 2021", "Halloween", "New Year's Eve"), or "today",
  "yesterday", "this/last week/month/year". A month, season or holiday
  without a year means every year's;
* the rest are the words, searched with ``websearch_to_tsquery`` (all of
  them must match, quotes for a phrase, ``-word`` to leave one out).

**Ranking**: text score (``ts_rank_cd`` over the weights, plus the name
trigram similarity), then the date taken, newest first. Without words,
newest first.

The date of a file is ``taken_at`` (EXIF, else the file's time, set by the
photo date analyser) or, before it is analysed, the file's modified time.
Dates are the household's wall clock (:mod:`domovoi.household_time`).

Everything a search returns that a model or Tesseract made (captions,
tags, text) is untrusted text: callers show it as text, never as HTML,
and a caption is shown as "might show" (a small model sometimes sees
things that aren't there).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

#: Each part of the document is cut to this many characters (a tsvector
#: holds at most 1 MB).
DOC_PART_MAX = 100_000
#: Results per page, at most.
LIMIT_MAX = 100
#: The oldest year a month, season or holiday without a year reaches back
#: to (each year is one range in the filter).
EARLIEST_YEAR = 1970
#: How much the name trigram similarity weighs beside the text score.
NAME_WEIGHT = 0.3

_SPLIT = re.compile(r"[\\/_\-.+,;()\[\]{}\s]+")


def name_words(rel_path: str) -> str:
    """A file's folder and file names as words: ``Trips/Beach_2023/IMG-001.jpg``
    → ``Trips Beach 2023 IMG 001``."""
    stem = rel_path.rsplit(".", 1)[0] if "." in rel_path.rsplit("/", 1)[-1] else rel_path
    return " ".join(p for p in _SPLIT.split(stem) if p)


REFRESH_SQL = """
UPDATE media_items m SET search_doc =
    setweight(to_tsvector('english', left(coalesce(f.tags, ''), :mx)), 'A')
 || setweight(to_tsvector('english', left(coalesce(f.captions, '') || ' ' || coalesce(sm.summary, ''), :mx)), 'B')
 || setweight(to_tsvector('english', left(coalesce(f.ocr, '') || ' ' || coalesce(sg.words, ''), :mx)), 'C')
 || setweight(to_tsvector('english', :names), 'D'),
    updated_at = now()
FROM (SELECT string_agg(array_to_string(tags, ' '), ' ') AS tags,
             string_agg(caption, ' ') AS captions,
             string_agg(ocr_text, ' ') AS ocr
        FROM media_frames WHERE item_id = :i) f,
     (SELECT string_agg(text, ' ' ORDER BY idx) AS words FROM media_segments WHERE item_id = :i) sg,
     (SELECT max(summary) AS summary FROM media_summaries WHERE item_id = :i) sm
WHERE m.id = :i
"""


async def refresh_doc(s: Any, item_id: int) -> None:
    """Rebuild one item's ``search_doc`` from its results and its names."""
    rel = (await s.execute(text("SELECT rel_path FROM media_items WHERE id = :i"), {"i": item_id})).scalar()
    if rel is None:
        return
    await s.execute(text(REFRESH_SQL), {"i": item_id, "mx": DOC_PART_MAX, "names": name_words(rel)})


# ─── reading a query ──────────────────────────────────────────────────────

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9
MONTHS.pop("may", None)
MONTHS["may"] = 5

#: (first month, months long); winter starts in December.
SEASONS = {"spring": (3, 3), "summer": (6, 3), "fall": (9, 3), "autumn": (9, 3), "winter": (12, 3)}

#: name -> ((month, day) first, (month, day) last) of a holiday, every year.
HOLIDAYS: dict[tuple[str, ...], tuple[tuple[int, int], tuple[int, int]]] = {
    ("christmas",): ((12, 24), (12, 26)),
    ("xmas",): ((12, 24), (12, 26)),
    ("christmas", "eve"): ((12, 24), (12, 24)),
    ("christmas", "day"): ((12, 25), (12, 25)),
    ("halloween",): ((10, 31), (10, 31)),
    ("new", "year's", "eve"): ((12, 31), (12, 31)),
    ("new", "years", "eve"): ((12, 31), (12, 31)),
    ("new", "year's", "day"): ((1, 1), (1, 1)),
    ("new", "years", "day"): ((1, 1), (1, 1)),
    ("valentine's", "day"): ((2, 14), (2, 14)),
    ("valentines", "day"): ((2, 14), (2, 14)),
}

KIND_WORDS = {
    "photo": "image", "photos": "image", "picture": "image", "pictures": "image", "pics": "image",
    "pic": "image", "image": "image", "images": "image", "snap": "image", "snaps": "image",
    "video": "video", "videos": "video", "clip": "video", "clips": "video", "movie": "video",
    "movies": "video", "footage": "video",
}
#: Asking words that say nothing about the file ("show me photos of ...").
FILLER = {"show", "me", "find", "search", "for", "of", "the", "a", "an", "my", "our", "some", "all",
          "any", "where", "we", "were", "was", "with", "taken", "from", "that", "those", "these"}
#: Words that may stand before a time and go with it.
TIME_PREPS = {"in", "from", "at", "during", "on", "of", "around"}

_WORD = re.compile(r"\"[^\"]*\"|\S+")


@dataclass
class Query:
    """A search, read: the words left, the kind, and the date ranges
    (naive household wall clock, end exclusive; any one may match)."""

    words: str = ""
    kind: str | None = None
    ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    when: str | None = None   # the time words as said, for the answer ("last summer")


def _day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _month_range(year: int, month: int, months: int = 1) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    m = month - 1 + months
    return start, datetime(year + m // 12, m % 12 + 1, 1)


def _every_year(make, today: date) -> list[tuple[datetime, datetime]]:
    return [make(y) for y in range(today.year, EARLIEST_YEAR - 1, -1)]


def _season(name: str, year: int) -> tuple[datetime, datetime]:
    first, months = SEASONS[name]
    return _month_range(year, first, months)


def _season_of(today: date, name: str) -> int:
    """The year of the season ``name`` that is under way or last ended."""
    first, months = SEASONS[name]
    for year in (today.year, today.year - 1, today.year - 2):
        start, _end = _month_range(year, first, months)
        if start.date() <= today:
            return year
    return today.year - 1


def _holiday(span: tuple[tuple[int, int], tuple[int, int]], year: int) -> tuple[datetime, datetime]:
    (m1, d1), (m2, d2) = span
    return datetime(year, m1, d1), datetime(year, m2, d2) + timedelta(days=1)


def _year(tok: str) -> int | None:
    if re.fullmatch(r"(19|20)\d\d", tok):
        return int(tok)
    if re.fullmatch(r"'\d\d", tok):
        return 2000 + int(tok[1:])
    return None


def parse_query(q: str, *, today: date | None = None) -> Query:
    """Kind, time and words out of what someone typed or said."""
    if today is None:
        from domovoi import household_time

        today = household_time.now().date()
    raw = _WORD.findall((q or "").replace("’", "'").strip())
    toks = [t.lower().strip(",.!?;:") if not t.startswith('"') else t for t in raw]
    out = Query()
    used = [False] * len(toks)
    said: list[str] = []

    def take(i: int, n: int) -> None:
        for j in range(i, i + n):
            used[j] = True
        # A preposition just before the time goes with it ("in July").
        k = i - 1
        while k >= 0 and not used[k] and toks[k] in TIME_PREPS:
            used[k] = True
            k -= 1
        said.append(" ".join(raw[i:i + n]))

    i = 0
    while i < len(toks):
        if used[i]:
            i += 1
            continue
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        prev = toks[i - 1] if i > 0 and not used[i - 1] else ""
        rel = prev if prev in ("last", "this") else ""
        # Holidays (longest first).
        hit = None
        for words, span in sorted(HOLIDAYS.items(), key=lambda kv: -len(kv[0])):
            if tuple(toks[i:i + len(words)]) == words:
                hit = (words, span)
                break
        if hit is not None:
            words, span = hit
            n = len(words)
            year = _year(toks[i + n]) if i + n < len(toks) else None
            if year is not None:
                out.ranges.append(_holiday(span, year))
                n += 1
            elif rel:
                y = today.year
                if rel == "last" and (span[1][0], span[1][1]) >= (today.month, today.day):
                    y -= 1      # this year's hasn't ended yet: the last one was a year ago
                out.ranges.append(_holiday(span, y))
                i -= 1          # "last"/"this" goes with it
                n += 1
            else:
                out.ranges.extend(_every_year(lambda y, sp=span: _holiday(sp, y), today))
            take(i, n)
            i += n
            continue
        if t in SEASONS:
            year = _year(nxt)
            if year is not None:
                out.ranges.append(_season(t, year))
                take(i, 2)
                i += 2
            elif rel:
                y = _season_of(today, t)
                if rel == "last" and _season(t, y)[1].date() > today:
                    y -= 1      # this one is still under way: last one is the one before
                out.ranges.append(_season(t, y))
                take(i - 1, 2)
                i += 1
            else:
                out.ranges.extend(_every_year(lambda y, s=t: _season(s, y), today))
                take(i, 1)
                i += 1
            continue
        if t in MONTHS and not (t == "may" and not (_year(nxt) or prev in TIME_PREPS or rel)):
            m = MONTHS[t]
            year = _year(nxt)
            if year is not None:
                out.ranges.append(_month_range(year, m))
                take(i, 2)
                i += 2
            elif rel:
                y = today.year if m <= today.month else today.year - 1
                if rel == "last" and m == today.month:
                    y -= 1
                out.ranges.append(_month_range(y, m))
                take(i - 1, 2)
                i += 1
            else:
                out.ranges.extend(_every_year(lambda y, mm=m: _month_range(y, mm), today))
                take(i, 1)
                i += 1
            continue
        if t in ("today", "yesterday"):
            d = today if t == "today" else today - timedelta(days=1)
            out.ranges.append((_day(d), _day(d + timedelta(days=1))))
            take(i, 1)
            i += 1
            continue
        if t in ("last", "this") and nxt in ("week", "month", "year"):
            if nxt == "week":
                monday = today - timedelta(days=today.weekday())
                start = monday - timedelta(days=7) if t == "last" else monday
                out.ranges.append((_day(start), _day(start + timedelta(days=7))))
            elif nxt == "month":
                y, m = (today.year, today.month)
                if t == "last":
                    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
                out.ranges.append(_month_range(y, m))
            else:
                y = today.year - (1 if t == "last" else 0)
                out.ranges.append((datetime(y, 1, 1), datetime(y + 1, 1, 1)))
            take(i, 2)
            i += 2
            continue
        year = _year(t)
        if year is not None and EARLIEST_YEAR <= year <= today.year + 1:
            out.ranges.append((datetime(year, 1, 1), datetime(year + 1, 1, 1)))
            take(i, 1)
            i += 1
            continue
        i += 1

    words: list[str] = []
    for j, t in enumerate(toks):
        if used[j]:
            continue
        if t in KIND_WORDS:
            out.kind = out.kind or KIND_WORDS[t]
            continue
        if t in FILLER or t in ("last", "this"):
            continue
        words.append(raw[j] if raw[j].startswith('"') else t)
    out.words = " ".join(words)
    out.when = " ".join(said) or None
    return out


# ─── searching ────────────────────────────────────────────────────────────

WHEN_SQL = "coalesce(m.taken_at, to_timestamp(m.mtime))"


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _name_terms(words: str) -> list[str]:
    """The plain words to match against names (no quotes, no ``-word``),
    at least 3 characters long (trigrams)."""
    out = []
    for w in re.findall(r"[^\s\"]+", words):
        if w.startswith("-") or w.lower() == "or":
            continue
        w = w.strip("'")
        if len(w) >= 3:
            out.append(w)
    return out


async def search(s: Any, query: Query, *, kind: str | None = None, start: datetime | None = None,
                 end: datetime | None = None, limit: int = 40, offset: int = 0,
                 only_ids: list[int] | None = None) -> dict[str, Any]:
    """One page of hits for ``query`` (filters: ``kind``, ``start``/``end``
    as UTC instants on top of the query's own time, and ``only_ids``: only
    these items, the person filter of :mod:`.ask`)."""
    from domovoi import household_time

    limit = max(1, min(int(limit), LIMIT_MAX))
    offset = max(0, int(offset))
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}
    where: list[str] = []
    kind = kind or query.kind
    if kind in ("image", "video"):
        where.append("m.kind = :kind")
        params["kind"] = kind
    if only_ids is not None:
        where.append("m.id = ANY(CAST(:only_ids AS bigint[]))")
        params["only_ids"] = [int(i) for i in only_ids]
    if start is not None:
        where.append(f"{WHEN_SQL} >= :start")
        params["start"] = start
    if end is not None:
        where.append(f"{WHEN_SQL} < :end")
        params["end"] = end
    if query.ranges:
        params["r_from"] = [household_time.to_utc(a) for a, _b in query.ranges]
        params["r_to"] = [household_time.to_utc(b) for _a, b in query.ranges]
        where.append(f"EXISTS (SELECT 1 FROM unnest(CAST(:r_from AS timestamptz[]), CAST(:r_to AS timestamptz[])) "
                     f"AS r(a, b) WHERE {WHEN_SQL} >= r.a AND {WHEN_SQL} < r.b)")
    score = "0"
    words = query.words.strip()
    if words:
        params["q"] = words
        names = _name_terms(words)
        name_cond = "false"
        if names:
            conds = []
            for n, w in enumerate(names):
                params[f"n{n}"] = _like(w)
                conds.append(f"m.rel_path ILIKE :n{n}")
            name_cond = "(" + " AND ".join(conds) + ")"
            params["names"] = " ".join(names)
            name_score = f"{NAME_WEIGHT} * similarity(m.rel_path, :names)"
        else:
            name_score = "0"
        where.append(f"((numnode(q.tsq) > 0 AND m.search_doc @@ q.tsq) OR {name_cond})")
        score = (f"(CASE WHEN numnode(q.tsq) > 0 THEN coalesce(ts_rank_cd(m.search_doc, q.tsq), 0) ELSE 0 END "
                 f"+ {name_score})")
    sql = (
        "SELECT m.id, m.library_id, m.rel_path, m.kind, m.state, m.taken_at, m.mtime, "
        f"{WHEN_SQL} AS taken, {score} AS score, "
        "f.caption, f.tags, f.has_text, f.ocr_text "
        "FROM media_items m "
        "CROSS JOIN (SELECT websearch_to_tsquery('english', coalesce(CAST(:q AS text), '')) AS tsq) q "
        "LEFT JOIN media_frames f ON f.item_id = m.id AND f.idx = 0 AND f.at_s IS NULL "
        + ("WHERE " + " AND ".join(f"({w})" for w in where) + " " if where else "")
        + "ORDER BY score DESC, taken DESC NULLS LAST, m.id DESC LIMIT :limit OFFSET :offset"
    )
    params.setdefault("q", "")
    rows = (await s.execute(text(sql), params)).mappings().all()
    more = len(rows) > limit
    return {"items": [hit(r) for r in rows[:limit]], "more": more}


#: How much of the text found in a picture a hit carries.
HIT_TEXT_MAX = 500


def hit(r: Any) -> dict[str, Any]:
    """One result. ``might_show`` is the caption (what the picture MIGHT
    show: a model's words); it, ``tags`` and ``text`` are untrusted text."""
    taken = r["taken"]
    ocr = r["ocr_text"] or None
    return {
        "id": int(r["id"]),
        "library_id": r["library_id"],
        "path": r["rel_path"],
        "name": r["rel_path"].rsplit("/", 1)[-1],
        "kind": r["kind"],
        "analysed": r["state"] == "done",
        "taken_at": taken.isoformat() if taken is not None else None,
        "date_from": "photo" if r["taken_at"] is not None else "file",
        "might_show": (r["caption"] or None),
        "tags": list(r["tags"] or []),
        "has_text": r["has_text"],
        "text": ocr[:HIT_TEXT_MAX] if ocr else None,
        "score": round(float(r["score"] or 0), 4),
    }
