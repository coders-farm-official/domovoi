"""Queue history — ``/api/music/up-next/history`` (1.4.0, 2026-11-16).

Looking back at what Up next picked, and why. Read-only: nothing here
writes, ends or deletes a session or a pick. The data is the engine's own:

* ``up_next_sessions`` / ``up_next_picks`` (V053, ``why`` V056) — one row
  per session and per pick. An ended session and its picks are deleted
  :data:`domovoi.up_next.store.ENDED_KEEP_DAYS` days after it ended (the
  prune in :func:`domovoi.up_next.store.create`); this module never
  changes that, it only says when (``kept_until``).
* ``music_listens`` (V051) — how long each pick really played and how it
  ended, joined by session and song: a pick that played (or was skipped)
  is paired with the session's ``auto`` listens of the same song, in order
  (the first such pick with the first such listen). Under "Keep listening
  history: Don't keep" there are no listens, and those fields are null.

Admin only (``require_admin_read``, the tier of the other admin reads:
Bearer, or the dashboard cookie, so the download link works from a tab):
the history names whose mix it was and what each room and phone played.

* ``GET /history`` — the sessions, newest first, paged like every list
  (``limit`` / ``cursor`` / ``offset``; ``room`` narrows it to one room).
* ``GET /history/{session_id}`` — one session, a summary of the whole
  session (picks, played vs skipped, distinct artists, the shortest gap
  between two picks of one artist, the reason mix, decades) and one page
  of its picks in order (``cursor`` / ``offset`` over ``seq``).
* ``GET /history/{session_id}/download?format=csv|json`` — every pick of
  the session as a file (JSON also carries the session and the summary).
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text

from domovoi.admin_auth import require_admin_read
from domovoi.up_next import store
from domovoi.up_next.why import why_text
from web.backend.api import paging
from web.backend.db import session_scope

router = APIRouter(prefix="/api/music/up-next/history", tags=["music"],
                   dependencies=[Depends(require_admin_read)])

UNKNOWN_SESSION_DETAIL = "no such up next session (it may have been deleted)"

#: A pick's reason_code in plain words (the stored reason_label is the chip
#: the queue showed: "same artist", "90s", "from road trip").
REASON_WORDS: dict[str, str] = {
    "same_artist": "same artist",
    "same_era": "same era",
    "same_genre": "same genre",
    "played_together": "played together",
    "similar_artist": "similar artist",
    "wildcard": "wildcard",
    "from_list": "from the list",
}

STATUS_WORDS: dict[str, str] = {
    "played": "played",
    "skipped": "skipped",
    "removed": "removed",
    "queued": "not reached",
}

MODE_WORDS: dict[str, str] = {
    "list": "a list, then similar songs",
    "continue": "a whole list, then similar songs",
    "library": "similar songs from the library",
}

_SESSIONS_SORT = "started"
_SESSION_KEYS = (paging.SortKey("s.created_at", desc=True, kind="ts"),
                 paging.SortKey("CAST(s.id AS text)", desc=True))
_PICKS_SORT = "seq"
_PICK_KEYS = (paging.SortKey("p.seq", kind="int"),)


def reason_words(code: str | None) -> str:
    return REASON_WORDS.get(code or "", (code or "").replace("_", " "))


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _target(target: str, device_name: str | None) -> dict[str, Any]:
    kind, _, ident = (target or "").partition(":")
    if kind == "room":
        return {"kind": "room", "room_id": ident, "name": ident}
    return {"kind": "device", "device_id": ident, "name": device_name or ident}


def _session_json(r: dict[str, Any]) -> dict[str, Any]:
    seed = _json(r["seed"]) or {}
    ended = r["ended_at"]
    played, skipped = int(r["played"] or 0), int(r["skipped"] or 0)
    return {
        "id": str(r["id"]),
        "target": _target(r["target"], r.get("device_name")),
        "mode": r["mode"],
        "mode_words": MODE_WORDS.get(r["mode"], r["mode"]),
        "person": ({"id": r["person_id"], "name": r["person_name"]} if r["person_id"] else None),
        "started_at": _iso(r["created_at"]),
        "last_active_at": _iso(r["updated_at"]),
        "ended_at": _iso(ended),
        # When the prune deletes it: ENDED_KEEP_DAYS after it ended. Still
        # running: null (kept while it runs).
        "kept_until": _iso(ended + timedelta(days=store.ENDED_KEEP_DAYS)) if ended else None,
        "seed": {
            "track_id": seed.get("track_id"),
            "title": r["seed_title"],
            "artist": r["seed_artist"],
            "list": ({"kind": seed.get("kind"), "name": seed.get("name")} if seed.get("kind") else None),
        },
        "picks": int(r["picks"] or 0),
        "played": played,
        "skipped": skipped,
        "removed": int(r["removed"] or 0),
    }


_SESSIONS_SQL = """
SELECT s.id, s.target, s.mode, s.person_id, pe.name AS person_name, s.seed,
       s.created_at, s.updated_at, s.ended_at, d.name AS device_name,
       st.title AS seed_title, st.artist AS seed_artist,
       c.picks, c.played, c.skipped, c.removed, {keys}
FROM up_next_sessions s
LEFT JOIN people pe ON pe.id = s.person_id
LEFT JOIN devices d ON s.target = 'device:' || d.device_id
LEFT JOIN library_tracks st ON st.id = CAST(s.seed ->> 'track_id' AS int)
LEFT JOIN LATERAL (
    SELECT count(*) AS picks,
           count(*) FILTER (WHERE p.status = 'played') AS played,
           count(*) FILTER (WHERE p.status = 'skipped') AS skipped,
           count(*) FILTER (WHERE p.status = 'removed') AS removed
    FROM up_next_picks p WHERE p.session_id = s.id
) c ON TRUE
WHERE {where}
ORDER BY {order}
LIMIT :lim {offset}
"""


async def _session_row(s: Any, sid: str) -> dict[str, Any] | None:
    sql = _SESSIONS_SQL.format(keys=paging.key_columns(_SESSION_KEYS), where="s.id = CAST(:sid AS uuid)",
                               order=paging.order_sql(_SESSION_KEYS), offset="")
    r = (await s.execute(text(sql), {"sid": sid, "lim": 1})).mappings().first()
    return dict(r) if r is not None else None


# Each pick, with the listen that was it. Picks that played or were skipped
# are numbered per song in seq order, the session's auto listens of each
# song in time order, and the n-th of one is the n-th of the other. A pick
# never reached (queued) or taken back (removed) has no listen.
_PICKS_SQL = """
WITH p AS (
    SELECT p.*,
           CASE WHEN p.status IN ('played', 'skipped') THEN
               row_number() OVER (PARTITION BY p.track_id, p.status IN ('played', 'skipped')
                                  ORDER BY p.seq)
           END AS k
    FROM up_next_picks p WHERE p.session_id = CAST(:sid AS uuid)
), l AS (
    SELECT m.track_id, m.played_sec, m.duration_sec AS listen_duration_sec, m.outcome, m.started_at,
           row_number() OVER (PARTITION BY m.track_id ORDER BY m.started_at, m.id) AS k
    FROM music_listens m
    WHERE m.session_id = CAST(:sid AS uuid) AND m.origin = 'auto'
)
SELECT p.seq, p.batch_no, p.track_id, p.reason_code, p.reason_label, p.status, p.why,
       p.seed_track_id, t.title, t.artist, t.artist_norm, t.album, t.year, t.duration_sec,
       st.title AS seed_title, st.artist AS seed_artist,
       l.played_sec, l.listen_duration_sec, l.outcome, l.started_at AS listened_at, {keys}
FROM p
JOIN library_tracks t ON t.id = p.track_id
LEFT JOIN library_tracks st ON st.id = p.seed_track_id
LEFT JOIN l ON l.track_id = p.track_id AND l.k = p.k
WHERE {where}
ORDER BY {order}
{limit}
"""


async def _picks(s: Any, sid: str, *, where: str = "TRUE", params: dict[str, Any] | None = None,
                 limit: str = "") -> list[dict[str, Any]]:
    sql = _PICKS_SQL.format(keys=paging.key_columns(_PICK_KEYS), where=where,
                            order=paging.order_sql(_PICK_KEYS), limit=limit)
    rows = (await s.execute(text(sql), {"sid": sid, **(params or {})})).mappings().all()
    return [dict(r) for r in rows]


def _pick_json(r: dict[str, Any]) -> dict[str, Any]:
    listen = None
    if r.get("outcome") is not None:
        listen = {
            "played_sec": r["played_sec"],
            "duration_sec": r["listen_duration_sec"],
            "outcome": r["outcome"],
            "started_at": _iso(r["listened_at"]),
        }
    return {
        "seq": r["seq"],
        "batch_no": r["batch_no"],
        "track_id": r["track_id"],
        "title": r["title"] or "",
        "artist": r["artist"] or "",
        "album": r["album"] or "",
        "year": r["year"],
        "duration_sec": r["duration_sec"],
        "reason_code": r["reason_code"],
        "reason": reason_words(r["reason_code"]),
        "reason_label": r["reason_label"],
        "why": why_text(why) if isinstance(why := _json(r.get("why")), list) else None,
        "seed": ({"track_id": r["seed_track_id"], "title": r["seed_title"], "artist": r["seed_artist"]}
                 if r["seed_track_id"] is not None else None),
        "status": r["status"],
        "status_words": STATUS_WORDS.get(r["status"], r["status"]),
        "listen": listen,
    }


def summarize(picks: list[dict[str, Any]]) -> dict[str, Any]:
    """The summary of a whole session from its picks (``_picks`` rows, in
    seq order): counts, artists, the closest repeat of an artist, the
    reason mix and the decades."""
    statuses = {k: 0 for k in STATUS_WORDS}
    reasons: dict[str, int] = {}
    decades: dict[str, int] = {}
    artists: dict[str, str] = {}
    last_at: dict[str, int] = {}
    gap: dict[str, Any] | None = None
    for r in picks:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        reasons[r["reason_code"]] = reasons.get(r["reason_code"], 0) + 1
        year = r.get("year")
        decade = f"{(int(year) // 10) * 10}s" if year else "unknown"
        decades[decade] = decades.get(decade, 0) + 1
        key = (r.get("artist_norm") or r.get("artist") or "").strip().lower()
        if not key:
            continue
        artists.setdefault(key, r.get("artist") or key)
        seq = int(r["seq"])
        if key in last_at:
            between = seq - last_at[key]
            if gap is None or between < gap["songs"]:
                gap = {"songs": between, "artist": artists[key], "from_seq": last_at[key], "to_seq": seq}
        last_at[key] = seq
    played_secs = [r["played_sec"] for r in picks if r.get("played_sec") is not None]
    return {
        "picks": len(picks),
        "played": statuses["played"],
        "skipped": statuses["skipped"],
        "removed": statuses["removed"],
        "not_reached": statuses["queued"],
        "listened_sec": int(sum(played_secs)) if played_secs else None,
        "distinct_artists": len(artists),
        # Seq distance between the two closest picks of one artist (1:
        # back to back); null when no artist came twice.
        "closest_repeat": gap,
        "reasons": [{"code": c, "words": reason_words(c), "count": n}
                    for c, n in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))],
        "decades": [{"decade": d, "count": n}
                    for d, n in sorted(decades.items(), key=lambda x: (x[0] == "unknown", x[0]))],
    }


def _sid(session_id: str) -> str:
    try:
        return str(uuid.UUID(str(session_id)))
    except ValueError:
        raise HTTPException(status_code=404, detail=UNKNOWN_SESSION_DETAIL) from None


# ─── Routes ───────────────────────────────────────────────────────────────


@router.get("")
async def list_sessions(
    room: Optional[str] = Query(None, max_length=64, description="Only this room's sessions."),
    limit: Optional[int] = paging.limit_query(),
    cursor: Optional[str] = paging.cursor_query(),
    offset: Optional[int] = paging.offset_query(),
) -> dict[str, Any]:
    """Every Up next session still kept, newest first: where it played,
    when, whose mix, the song it started from and how many picks it made.
    Paged (default 50, max 200)."""
    lim = limit or paging.DEFAULT_LIMIT
    where, params = "TRUE", {"lim": lim + 1}
    if room:
        where += " AND s.target = :target"
        params["target"] = f"room:{room}"
    count_where = where
    if cursor is not None:
        where += " AND " + paging.keyset_where(_SESSION_KEYS, cursor, _SESSIONS_SORT, params)
    off = ""
    if cursor is None and offset:
        off = "OFFSET :off"
        params["off"] = int(offset)
    sql = _SESSIONS_SQL.format(keys=paging.key_columns(_SESSION_KEYS), where=where,
                               order=paging.order_sql(_SESSION_KEYS), offset=off)
    async with session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()
        total = int((await s.execute(
            text(f"SELECT count(*) FROM up_next_sessions s WHERE {count_where}"),
            {k: v for k, v in params.items() if k == "target"})).scalar_one())
    page, has_more, nxt = paging.db_page_dicts(rows, _SESSION_KEYS, limit=lim, sort=_SESSIONS_SORT)
    return {
        "sessions": [_session_json(r) for r in page],
        "kept_days": store.ENDED_KEEP_DAYS,
        **paging.page_fields(total=total, limit=lim, has_more=has_more, next_cursor=nxt,
                             offset=None if cursor is not None else (offset or 0)),
    }


@router.get("/{session_id}")
async def session_detail(
    session_id: str,
    limit: Optional[int] = paging.limit_query(),
    cursor: Optional[str] = paging.cursor_query(),
    offset: Optional[int] = paging.offset_query(),
) -> dict[str, Any]:
    """One session, the summary of all its picks, and one page of its picks
    in the order they were made (``seq``), each with its reason, the song
    it followed and, when it played, how long and how it ended."""
    sid = _sid(session_id)
    lim = limit or paging.DEFAULT_LIMIT
    async with session_scope() as s:
        row = await _session_row(s, sid)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_SESSION_DETAIL)
        everything = await _picks(s, sid)
    if cursor is not None:
        after = paging.decode_cursor(cursor, _PICKS_SORT, len(_PICK_KEYS))
        if not isinstance(after[0], int) or isinstance(after[0], bool):
            raise HTTPException(status_code=400, detail="invalid cursor")
        rest = [r for r in everything if r["seq"] > after[0]]
    else:
        rest = everything[int(offset or 0):]
    page = rest[:lim]
    has_more = len(rest) > lim
    nxt = paging.encode_cursor(_PICKS_SORT, [page[-1]["seq"]]) if has_more and page else None
    return {
        "session": _session_json(row),
        "summary": summarize(everything),
        "picks": [_pick_json(r) for r in page],
        **paging.page_fields(total=len(everything), limit=lim, has_more=has_more, next_cursor=nxt,
                             offset=None if cursor is not None else (offset or 0)),
    }


CSV_COLUMNS = (
    "seq", "title", "artist", "album", "year", "reason_code", "reason", "reason_label", "why",
    "seed_title", "seed_artist", "status", "played_sec", "duration_sec", "outcome", "listened_at",
)

# A cell a spreadsheet would run as a formula (a song tag can say anything).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(v: Any) -> Any:
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return "'" + v
    return "" if v is None else v


def _csv(picks: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for p in picks:
        listen = p["listen"] or {}
        seed = p["seed"] or {}
        w.writerow([_cell(v) for v in (
            p["seq"], p["title"], p["artist"], p["album"], p["year"], p["reason_code"], p["reason"],
            p["reason_label"], p["why"], seed.get("title"), seed.get("artist"), p["status"],
            listen.get("played_sec"), listen.get("duration_sec") or p["duration_sec"],
            listen.get("outcome"), listen.get("started_at"),
        )])
    return buf.getvalue()


def _filename(sess: dict[str, Any], ext: str) -> str:
    where = re.sub(r"[^A-Za-z0-9._-]+", "-", sess["target"]["name"] or "player").strip("-")[:40] or "player"
    day = (sess["started_at"] or "")[:10]
    return f"up-next-{where}-{day}.{ext}" if day else f"up-next-{where}.{ext}"


@router.get("/{session_id}/download")
async def download_session(
    session_id: str,
    format: Literal["csv", "json"] = Query("csv", description="csv or json"),
) -> Response:
    """Every pick of one session as a file: CSV (one row per pick) or JSON
    (``{session, summary, picks}``, the same shapes as the reads above)."""
    sid = _sid(session_id)
    async with session_scope() as s:
        row = await _session_row(s, sid)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_SESSION_DETAIL)
        everything = await _picks(s, sid)
    sess = _session_json(row)
    picks = [_pick_json(r) for r in everything]
    if format == "json":
        body = json.dumps({"session": sess, "summary": summarize(everything), "picks": picks},
                          ensure_ascii=False, indent=2)
        media = "application/json"
    else:
        body = _csv(picks)
        media = "text/csv; charset=utf-8"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{_filename(sess, format)}"',
        "Cache-Control": "no-store",
    })
