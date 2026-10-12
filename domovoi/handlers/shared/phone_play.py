"""Media asked for from a phone plays on the phone (the chat mic).

A turn from the app's chat mic (``Context.from_phone``, domovoi/phone_voice.py)
comes from a device with a player of its own, and no room's: "play X" there
means play X on the phone (owner decisions 2026-10-08). The media handlers
keep their whole decision — what to play, what to say — and, for a phone
turn, hand the result to the app as a device action
(``Response.device_actions``) instead of preparing a room's MPD:

* ``{"type": "play", "items": [...]}`` — the queue to play, each item
  ``{"kind": "track" | "episode" | "book" | "station", "id", "title",
  "artist", "album"?, "duration_sec"?}``; the app makes the same queue
  entries its own screens make (``PlayItem.fromTrack`` …);
* ``{"type": "pause" | "resume" | "stop" | "next" | "previous"}`` — the
  phone player's transport;
* ``{"type": "volume", "change": "up" | "down" | "set", "percent"?}`` —
  the phone's media volume.

What the phone is playing comes with the turn (``Context.device_player``),
so "what's playing" answers about the phone.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.models import Context, Response

#: The most items one play hands the phone (an artist's whole catalogue
#: is shuffled and cut to this).
PHONE_QUEUE_CAP = 200

_TRANSPORT_TEXT = {
    "pause": "Paused.",
    "resume": "Resuming.",
    "stop": "Stopped.",
    "next": "Next track.",
    "previous": "Previous track.",
}


def track_item(row: Any) -> dict[str, Any]:
    """A library_tracks row (``TrackRow`` or a mapping) as a play item."""
    get = row.get if isinstance(row, dict) else lambda k, d=None: getattr(row, k, d)
    title = get("title") or PurePath(str(get("file_path") or "")).stem or "a song"
    item: dict[str, Any] = {"kind": "track", "id": int(get("id")), "title": title}
    if get("artist"):
        item["artist"] = get("artist")
    if get("album"):
        item["album"] = get("album")
    return item


def play_on_phone(
    handler_name: str,
    ctx: Context,
    text_: str,
    items: Iterable[dict[str, Any]],
    *,
    data: dict[str, Any] | None = None,
) -> Response:
    """The reply to a play from a phone: ``text_`` said, ``items`` played
    on the phone once it has been heard."""
    queue = list(items)[:PHONE_QUEUE_CAP]
    return Response(
        text=text_,
        session_id=ctx.session_id,
        matched_handler=handler_name,
        data=dict(data or {}, played_on="phone"),
        device_actions=[{"type": "play", "items": queue}],
    )


def phone_transport(handler_name: str, ctx: Context, action: str) -> Response:
    """Pause / resume / stop / next / previous, on the phone's player."""
    if action in ("pause", "next", "previous") and not ctx.device_player:
        return Response(
            text="Nothing is playing on this phone.",
            session_id=ctx.session_id,
            matched_handler=handler_name,
            failure="not_playing",
        )
    return Response(
        text=_TRANSPORT_TEXT.get(action, f"Done: {action}."),
        session_id=ctx.session_id,
        matched_handler=handler_name,
        device_actions=[{"type": action}],
    )


def phone_volume_set(handler_name: str, ctx: Context, level: int) -> Response:
    """ "volume 6": a 1-10 knob, as on a satellite, but on the phone's own
    scale — tenths of its media volume."""
    level = max(1, min(10, int(level)))
    return Response(
        text=f"Volume set to {level}.",
        session_id=ctx.session_id,
        matched_handler=handler_name,
        device_actions=[{"type": "volume", "change": "set", "percent": level * 10}],
    )


def phone_volume_bump(handler_name: str, ctx: Context, delta: int) -> Response:
    """ "turn it up / down": one step of the phone's own volume."""
    up = delta > 0
    return Response(
        text="Turning it up." if up else "Turning it down.",
        session_id=ctx.session_id,
        matched_handler=handler_name,
        device_actions=[{"type": "volume", "change": "up" if up else "down"}],
    )


def phone_now_playing(handler_name: str, ctx: Context) -> Response:
    """What the phone said it is playing when it sent the turn."""
    player = ctx.device_player or {}
    title = player.get("title")
    if not title:
        return Response(
            text="Nothing is playing on this phone.",
            session_id=ctx.session_id,
            matched_handler=handler_name,
        )
    artist = player.get("artist")
    what = f"{title} by {artist}" if artist else title
    paused = player.get("playing") is False
    return Response(
        text=f"This is {what}." if not paused else f"{what} is paused on this phone.",
        session_id=ctx.session_id,
        matched_handler=handler_name,
        data={"song": {"title": title, "artist": artist}},
    )


async def playlist_queue(
    session: AsyncSession, playlist_id: int, mode: str, first_track_id: int,
) -> list[dict[str, Any]]:
    """A whole playlist as the phone's queue, starting with the track the
    playlist pick chose (``playlist_pick.pick_next_track``): in order from
    there, wrapping round (a room loops at the end the same way), or
    shuffled after it. ``playlist_id`` 0 is the virtual Favorites list."""
    import random

    if playlist_id == 0:
        sql = (
            "SELECT id, title, artist, album, file_path FROM library_tracks "
            "WHERE favorited ORDER BY id"
        )
        params: dict[str, Any] = {}
    else:
        sql = (
            "SELECT lt.id, lt.title, lt.artist, lt.album, lt.file_path FROM playlist_tracks pt "
            "JOIN library_tracks lt ON lt.id = pt.track_id "
            "WHERE pt.playlist_id = :pid ORDER BY pt.position, pt.track_id"
        )
        params = {"pid": playlist_id}
    rows = [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]
    at = next((i for i, r in enumerate(rows) if int(r["id"]) == int(first_track_id)), 0)
    if mode == "shuffle":
        rest = rows[:at] + rows[at + 1:]
        random.shuffle(rest)
        rows = ([rows[at]] if rows else []) + rest
    else:
        rows = rows[at:] + rows[:at]
    return rows[:PHONE_QUEUE_CAP]


async def search_library(
    session: AsyncSession, query: dict[str, str], *, limit: int = 1,
) -> list[dict[str, Any]]:
    """What a room's MPD tag and file-name search would have found, read
    from ``library_tracks`` instead (a phone has no MPD): every word of the
    title / artist / any query in the track's title, artist, album or file
    path, case-insensitive. Shortest title first, so "play harbor" prefers
    "Harbor" to "Harbor Lights (Live)"."""
    words = [
        w for k in ("title", "artist", "any") for w in str(query.get(k) or "").lower().split()
        if w
    ][:8]
    if not words:
        return []
    clauses = []
    params: dict[str, Any] = {"lim": limit}
    for i, w in enumerate(words):
        params[f"w{i}"] = f"%{w}%"
        clauses.append(
            f"(lower(coalesce(title, '')) LIKE :w{i} OR lower(coalesce(artist, '')) LIKE :w{i} "
            f"OR lower(coalesce(album, '')) LIKE :w{i} OR lower(file_path) LIKE :w{i})"
        )
    rows = (
        await session.execute(
            text(
                "SELECT id, title, artist, album, file_path FROM library_tracks "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY length(coalesce(title, file_path)), id LIMIT :lim"
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]
