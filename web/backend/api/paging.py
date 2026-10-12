"""One paging contract for every files-like list: Music, Videos, Files,
Audiobooks, Podcasts.

Owner decision 2026-10-11: no list on these pages ever loads everything at
once. Clients load as they scroll, one page at a time, and every sort,
filter and search runs HERE so it covers the whole list rather than the
rows a client happens to hold.

The contract, the same on every endpoint:

* ``limit`` — page size, default :data:`DEFAULT_LIMIT`, hard max
  :data:`MAX_LIMIT` (a bigger value is a 422, as Music's always was).
* ``cursor`` — opaque, from the previous page's ``next_cursor``. This is
  how both clients page. It is a KEYSET position (the last row's sort key),
  not a count, so a row added or removed while somebody scrolls neither
  repeats a row on the next page nor skips one: the next page starts
  strictly after the last row the client saw, wherever that row now is.
* ``offset`` — also accepted (Music always took it, and the installed
  Android app still sends it). Counting rows is only stable while nothing
  changes, so a client that can follow a cursor should.
* The answer carries ``total`` (rows matching the active filters),
  ``limit``, ``has_more``, ``next_cursor`` (null on the last page) and,
  for an offset request, ``offset``.

A cursor names the sort it was cut for; presenting it with another sort
is a 400 rather than a silently wrong page. Clients reset to page one
whenever a filter, search or sort changes.

Two engines sit behind the one contract: :func:`keyset_where` /
:func:`order_sql` for the Postgres-backed lists (library tracks,
audiobooks, podcasts), and :func:`page_view` for the filesystem lists the
server holds in memory (``listing_cache``).
"""

from __future__ import annotations

import base64
import bisect
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from fastapi import HTTPException, Query

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# A cursor is a handful of sort-key values; anything this long is not one.
_MAX_CURSOR_CHARS = 2048

# Shared Query() declarations, so every endpoint documents the same thing.
LIMIT_DOC = (
    f"Page size (default {DEFAULT_LIMIT}, max {MAX_LIMIT}). Sending any of "
    "limit / cursor / offset asks for the paged answer."
)
CURSOR_DOC = (
    "Opaque position from the previous page's next_cursor. Keyset-based: "
    "rows added or removed between pages are neither repeated nor skipped."
)
OFFSET_DOC = "Row offset (stable only while the list doesn't change; prefer cursor)."


def limit_query(default: Optional[int] = None) -> Any:
    return Query(default, ge=1, le=MAX_LIMIT, description=LIMIT_DOC)


def cursor_query() -> Any:
    return Query(None, max_length=_MAX_CURSOR_CHARS, description=CURSOR_DOC)


def offset_query() -> Any:
    return Query(None, ge=0, description=OFFSET_DOC)


def wants_page(limit: Optional[int], cursor: Optional[str], offset: Optional[int]) -> bool:
    """Whether a request on a list that used to answer in full asked for a
    page. A caller that sends none of the three is an old client (the
    installed Android app) and keeps the full answer it was built for."""
    return limit is not None or cursor is not None or offset is not None


def _bad_cursor(why: str = "invalid cursor") -> HTTPException:
    return HTTPException(status_code=400, detail=why)


def encode_cursor(sort: str, key: Sequence[Any]) -> str:
    raw = json.dumps({"s": sort, "k": [_jsonable(v) for v in key]},
                     separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, sort: str, width: Optional[int]) -> list[Any]:
    """The key values a cursor carries, or 400. ``width`` is how many key
    values this sort has (None: not checked); a cursor cut for another sort
    is refused."""
    if not cursor or len(cursor) > _MAX_CURSOR_CHARS:
        raise _bad_cursor()
    try:
        pad = "=" * (-len(cursor) % 4)
        doc = json.loads(base64.urlsafe_b64decode(cursor + pad).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _bad_cursor()
    if not isinstance(doc, dict) or not isinstance(doc.get("k"), list):
        raise _bad_cursor()
    if doc.get("s") != sort:
        raise _bad_cursor("cursor belongs to a different sort — start again from page one")
    if width is not None and len(doc["k"]) != width:
        raise _bad_cursor()
    return doc["k"]


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def page_fields(
    *, total: int, limit: int, has_more: bool, next_cursor: Optional[str],
    offset: Optional[int] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "total": total, "limit": limit, "has_more": has_more, "next_cursor": next_cursor,
    }
    if offset is not None:
        out["offset"] = offset
    return out


# ─── Postgres keyset ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SortKey:
    """One ORDER BY term. ``nulls_last`` marks a nullable expression whose
    NULLs sort after every value whatever the direction (the way every list
    here has always shown a track with no title: at the end). ``kind`` is
    how a cursor's JSON value is turned back into a bind parameter:
    text | int | float | ts | bool."""
    expr: str
    desc: bool = False
    nulls_last: bool = False
    kind: str = "text"


def order_sql(keys: Sequence[SortKey]) -> str:
    parts: list[str] = []
    for k in keys:
        if k.nulls_last:
            parts.append(f"({k.expr}) IS NULL")
        parts.append(f"{k.expr} {'DESC' if k.desc else 'ASC'}")
    return ", ".join(parts)


def key_columns(keys: Sequence[SortKey]) -> str:
    """``expr AS _pk0, …`` — selected beside the row so the last row's key
    can be put in ``next_cursor`` exactly as Postgres compared it."""
    return ", ".join(f"{k.expr} AS _pk{i}" for i, k in enumerate(keys))


def _typed(kind: str, v: Any) -> Any:
    if v is None:
        return None
    try:
        if kind == "text" and isinstance(v, str):
            return v
        if kind == "int" and isinstance(v, int) and not isinstance(v, bool):
            return v
        if kind == "float" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if kind == "bool" and isinstance(v, bool):
            return v
        if kind == "ts" and isinstance(v, str):
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                raise ValueError("naive timestamp")
            return dt
    except ValueError:
        pass
    raise _bad_cursor()


def keyset_where(
    keys: Sequence[SortKey], cursor: str, sort: str, params: dict[str, Any],
) -> str:
    """SQL predicate for "strictly after the row this cursor names", in the
    order :func:`order_sql` produces. Adds its bind parameters to
    ``params``. The usual expansion
    ``(a > :a) OR (a = :a AND b > :b) OR …``, with a nullable key split in
    two — its ``IS NULL`` flag first, then the value compared with
    ``IS NOT DISTINCT FROM`` — so the NULL group pages like any other."""
    raw = decode_cursor(cursor, sort, len(keys))
    comps: list[tuple[str, str, str, str]] = []   # (lhs, strict op, param, eq op)
    for i, (k, v) in enumerate(zip(keys, raw)):
        val = _typed(k.kind, v)
        if val is None and not k.nulls_last:
            raise _bad_cursor()
        if k.nulls_last:
            pn = f"_pc{i}n"
            params[pn] = val is None
            comps.append((f"(({k.expr}) IS NULL)", ">", pn, "="))
        pv = f"_pc{i}"
        params[pv] = val
        comps.append((k.expr, "<" if k.desc else ">", pv,
                      "IS NOT DISTINCT FROM" if k.nulls_last else "="))
    ors: list[str] = []
    for j in range(len(comps)):
        terms = [f"{lhs} {eq} :{pn}" for (lhs, _op, pn, eq) in comps[:j]]
        lhs, op, pn, _eq = comps[j]
        terms.append(f"{lhs} {op} :{pn}")
        ors.append("(" + " AND ".join(terms) + ")")
    return "(" + " OR ".join(ors) + ")"


def db_page(
    rows: Sequence[Any], keys: Sequence[SortKey], *, limit: int, sort: str,
    key_offset: int,
) -> tuple[list[Any], bool, Optional[str]]:
    """Split a ``LIMIT limit+1`` result into (page, has_more, next_cursor).
    ``key_offset`` is the column index where :func:`key_columns` starts."""
    has_more = len(rows) > limit
    page = list(rows[:limit])
    nxt = None
    if has_more and page:
        last = page[-1]
        nxt = encode_cursor(sort, [last[key_offset + i] for i in range(len(keys))])
    return page, has_more, nxt


# ─── In-memory views (filesystem listings) ───────────────────────────────────
def page_view(
    rows: Sequence[Any],
    keys: Sequence[tuple],
    *,
    sort: str,
    limit: int,
    cursor: Optional[str],
    offset: Optional[int],
    keep: Optional[Callable[[int], bool]] = None,
) -> tuple[list[Any], dict[str, Any]]:
    """One page of an already-sorted list. ``keys[i]`` is the sort key of
    ``rows[i]`` (ascending tuples — a descending field is stored negated).
    ``keep(i)``, when given, is the filter; ``total`` counts what it keeps.
    Returns (page rows, paging fields)."""
    start: Optional[int] = None
    if cursor is not None:
        raw = decode_cursor(cursor, sort, len(keys[0]) if keys else None)
        try:
            start = bisect.bisect_right(keys, tuple(raw))
        except TypeError:
            raise _bad_cursor()

    if keep is None:
        total = len(rows)
        first = start if start is not None else (offset or 0)
        idx = range(first, min(first + limit, total))
        has_more = first + limit < total
        page = [rows[i] for i in idx]
        last = idx[-1] if len(idx) else None
    else:
        matches = [i for i in range(len(rows)) if keep(i)]
        total = len(matches)
        pos = bisect.bisect_left(matches, start) if start is not None else (offset or 0)
        chosen = matches[pos:pos + limit]
        has_more = pos + limit < total
        page = [rows[i] for i in chosen]
        last = chosen[-1] if chosen else None

    nxt = encode_cursor(sort, keys[last]) if (has_more and last is not None) else None
    return page, page_fields(
        total=total, limit=limit, has_more=has_more, next_cursor=nxt,
        offset=None if cursor is not None else (offset or 0),
    )



def db_page_dicts(
    rows: Sequence[Any], keys: Sequence[SortKey], *, limit: int, sort: str,
) -> tuple[list[dict[str, Any]], bool, Optional[str]]:
    """:func:`db_page` for ``.mappings()`` rows: the ``_pkN`` key columns
    are read for ``next_cursor`` and then dropped from every row."""
    has_more = len(rows) > limit
    page = [dict(r) for r in rows[:limit]]
    nxt = None
    if has_more and page:
        nxt = encode_cursor(sort, [page[-1][f"_pk{i}"] for i in range(len(keys))])
    for d in page:
        for i in range(len(keys)):
            d.pop(f"_pk{i}", None)
    return page, has_more, nxt
