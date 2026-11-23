"""Photo and video results on a room's screen (1.5.0, media understanding
slice 6; design MEDIA-UNDERSTANDING §9 "Voice").

"Show me photos of the beach" said in a room answers by voice
(domovoi/handlers/media_search.py). When that room has a screen, the
results are shown there too, for :data:`SHOW_SEC`:

* a **room display** (a phone or tablet in room mode, a session with a
  :class:`domovoi.room_display.RoomDisplayFeed`: ``RoomDevices.screens``)
  gets them in its ``room_display`` frame's ``media`` part
  (:func:`frame_media`, documented in domovoi/room_display.py);
* a **video satellite's kiosk** (a live satellite of the room whose hello
  said ``sat_type: "video"``; web/static/display.html) reads them from the
  web backend, ``GET /api/satellites/{room}/media``, which takes them from
  the admin snapshot (:func:`snapshot`); the ``satellites.media`` push says
  when to read again.

A room with neither gets nothing: nothing is kept for it.

**Who may put what on a screen.** The search already ran with slice 6's
gate (``media_search.people_allowed``: a recognized voice or a phone, never
a guest), so a guest who names a person got ``people_refused`` and no
results. :func:`show` checks again: results filtered by a person are
shown only when the caller says the turn may see people.

**Thumbnails.** A room phone holds only its room key, which no HTTP route
takes. Each item it is sent carries ``/v1/room-media/{token}``: a random
token (:func:`mint`) that names one item, kept in this process only, good
for :data:`TOKEN_TTL_SEC`; :func:`serve` answers it with the dashboard's
own thumbnail (a photo) or poster frame (a video). The kiosk holds the
household token and uses the dashboard's routes directly.

**Clearing.** Results go after :data:`SHOW_SEC`, when the next results
replace them, and (on a room display) with the next frame sent for any
other reason (music starts, a timer goes off: domovoi/room_display.py).

Captions are untrusted text from a picture model: every screen draws them
as text, never markup, as "Might show: …".
"""

from __future__ import annotations

import itertools
import logging
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

#: How long results stay on a screen.
SHOW_SEC = 90.0
#: How long a thumbnail link works (a little longer than the results show).
TOKEN_TTL_SEC = 600.0
#: Items a screen gets (the Photos page has the rest).
MAX_ITEMS = 6
#: Thumbnail links remembered at most (oldest dropped first).
MAX_TOKENS = 256
#: Longest caption sent to a screen.
CAPTION_MAX = 160
#: The longest spoken line kept as the results' title.
SAID_MAX = 300

THUMB_PATH = "/v1/room-media/"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}\Z")

Clock = Callable[[], float]


@dataclass
class _Token:
    room_id: str
    library_id: str
    path: str
    kind: str
    until: float


@dataclass
class Shown:
    """Results on one room's screen."""

    id: int
    room_id: str
    said: str
    count: int
    more: bool
    person: bool
    until: float
    items: list[dict[str, Any]] = field(default_factory=list)


_SHOWN: dict[str, Shown] = {}
_TOKENS: "OrderedDict[str, _Token]" = OrderedDict()
_SEQ = itertools.count(1)


def reset() -> None:
    """Forget everything (tests)."""
    _SHOWN.clear()
    _TOKENS.clear()


# ─── which rooms have a screen ───────────────────────────────────────────


def room_displays(app: Any, room_id: str) -> list[Any]:
    """The room's live sessions with a room display feed."""
    from domovoi import room_devices

    try:
        return room_devices.registry(app).screens(room_id)
    except Exception:  # noqa: BLE001 — no registry: no screen
        return []


def kiosk_in(app: Any, room_id: str) -> bool:
    """Whether a live satellite of the room is a video satellite (a kiosk
    screen showing web/static/display.html)."""
    from domovoi import room_devices

    types = getattr(getattr(app, "state", None), "satellite_sat_type", None) or {}
    try:
        live = room_devices.registry(app).live_in(room_id)
    except Exception:  # noqa: BLE001
        return False
    return any(types.get(getattr(s, "satellite_id", None)) == "video" for s in live)


def has_screen(app: Any, room_id: str) -> bool:
    return bool(room_displays(app, room_id)) or kiosk_in(app, room_id)


# ─── showing ─────────────────────────────────────────────────────────────


def _date(card: dict[str, Any]) -> str | None:
    v = card.get("taken_at") or card.get("date_from")
    return str(v)[:10] if v else None


def _caption(v: Any) -> str | None:
    if not v:
        return None
    s = " ".join(str(v).split())
    return s[:CAPTION_MAX] or None


def mint(room_id: str, library_id: str, path: str, kind: str, *, clock: Clock = time.monotonic) -> str:
    """A thumbnail token for one item, for :data:`TOKEN_TTL_SEC`."""
    now = clock()
    for tok, t in list(_TOKENS.items()):
        if t.until <= now:
            del _TOKENS[tok]
    while len(_TOKENS) >= MAX_TOKENS:
        _TOKENS.popitem(last=False)
    tok = secrets.token_urlsafe(24)
    _TOKENS[tok] = _Token(room_id, library_id, path, kind, now + TOKEN_TTL_SEC)
    return tok


def resolve(token: str, *, clock: Clock = time.monotonic) -> _Token | None:
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None
    t = _TOKENS.get(token)
    if t is None:
        return None
    if t.until <= clock():
        _TOKENS.pop(token, None)
        return None
    return t


def show(app: Any, room_id: str | None, answer: Any, said: str, *, people_ok: bool,
         clock: Clock = time.monotonic) -> Shown | None:
    """Put ``answer``'s results (an :class:`ask.Answer`) on ``room_id``'s
    screens. None, and nothing kept, when there is nothing to show, the
    room has no screen, or the results are filtered by a person and the
    turn may not see people (a guest)."""
    if not room_id or answer is None or getattr(answer, "state", None) != "found":
        return None
    cards = list(getattr(answer, "cards", None) or [])[:MAX_ITEMS]
    if not cards:
        return None
    if getattr(answer, "person", None) and not people_ok:
        return None
    displays = room_displays(app, room_id)
    if not displays and not kiosk_in(app, room_id):
        return None
    items = []
    for c in cards:
        kind = "video" if c.get("kind") == "video" else "image"
        at_s = c.get("at_s")
        items.append({
            "kind": kind,
            "token": mint(room_id, str(c["library_id"]), str(c["path"]), kind, clock=clock),
            "web_thumb": c.get("thumb"),
            "library_id": c.get("library_id"),
            "path": c.get("path"),
            "caption": _caption(c.get("might_show")),
            "date": _date(c),
            "at_s": float(at_s) if at_s is not None and kind == "video" else None,
        })
    shown = Shown(id=next(_SEQ), room_id=room_id, said=" ".join(str(said or "").split())[:SAID_MAX],
                  count=int(getattr(answer, "count", len(cards)) or len(cards)),
                  more=bool(getattr(answer, "more", False)), person=bool(getattr(answer, "person", None)),
                  until=clock() + SHOW_SEC, items=items)
    _SHOWN[room_id] = shown
    for sess in displays:
        feed = getattr(sess, "room_display", None)
        if feed is not None:
            feed.poke()
    return shown


def clear(room_id: str) -> None:
    _SHOWN.pop(room_id, None)


def current(room_id: str, *, clock: Clock = time.monotonic) -> Shown | None:
    shown = _SHOWN.get(room_id)
    if shown is None:
        return None
    if shown.until <= clock():
        _SHOWN.pop(room_id, None)
        return None
    return shown


def _left_ms(shown: Shown, clock: Clock) -> int:
    return max(0, int(round((shown.until - clock()) * 1000)))


def frame_media(room_id: str, *, clock: Clock = time.monotonic) -> dict[str, Any] | None:
    """The ``room_display`` frame's ``media`` part (domovoi/room_display.py)."""
    shown = current(room_id, clock=clock)
    if shown is None:
        return None
    return {
        "id": shown.id,
        "said": shown.said,
        "count": shown.count,
        "more": shown.more,
        "expires_in_ms": _left_ms(shown, clock),
        "items": [{"kind": i["kind"], "thumb": THUMB_PATH + i["token"], "caption": i["caption"],
                   "date": i["date"], "at_s": i["at_s"]} for i in shown.items],
    }


def web_media(room_id: str, *, clock: Clock = time.monotonic) -> dict[str, Any] | None:
    """The same results for the kiosk page, with the dashboard's thumbnail
    routes (the kiosk holds the household token)."""
    shown = current(room_id, clock=clock)
    if shown is None:
        return None
    return {
        "id": shown.id,
        "said": shown.said,
        "count": shown.count,
        "more": shown.more,
        "expires_in_ms": _left_ms(shown, clock),
        "items": [{"kind": i["kind"], "thumb": i["web_thumb"], "caption": i["caption"],
                   "date": i["date"], "at_s": i["at_s"]} for i in shown.items],
    }


def snapshot(*, clock: Clock = time.monotonic) -> dict[str, Any]:
    """``{room: web_media}`` for every room showing results now (the admin
    snapshot's ``room_media``)."""
    out: dict[str, Any] = {}
    for room in list(_SHOWN):
        m = web_media(room, clock=clock)
        if m is not None:
            out[room] = m
    return out


# ─── the thumbnail route ─────────────────────────────────────────────────


async def serve(token: str) -> Any:
    """``GET /v1/room-media/{token}``: the item's thumbnail (a photo) or
    poster frame (a video), the dashboard's own bytes. 404 for a token that
    is unknown, expired or malformed."""
    from fastapi import HTTPException

    t = resolve(token)
    if t is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        if t.kind == "video":
            from web.backend.api.videos import poster

            resp = await poster(library_id=t.library_id, path=t.path)
        else:
            from web.backend.api.images import thumb

            resp = await thumb(library_id=t.library_id, path=t.path, size="m")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — a core without web/, an unreadable file
        log.debug("room media: no thumbnail for a token: %s", type(e).__name__)
        raise HTTPException(status_code=404, detail="not found") from None
    try:
        resp.headers["Cache-Control"] = f"private, max-age={int(TOKEN_TTL_SEC)}"
    except Exception:  # noqa: BLE001
        pass
    return resp
