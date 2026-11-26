"""What a room id may look like.

Room ids are used as WebSocket paths, dashboard URLs, MPD container-name
components and config values, so they stay boring.

``ROOM_ID_RE`` is the rule for a room id the core creates (USB adoption's
pre-seed); the apps and satellite enrolment mirror it. :func:`is_room_id`
is what a room id coming in from outside may be: that rule, or a name the
satellite setup portal made from a typed room name
(``satellite/provisioning_protocol.py`` ``slugify_room``: lowercase
letters, digits and dashes). Rooms named either way are in houses today.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request

ROOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")

# A superset of ROOM_ID_RE: the portal's slugs may start with a digit and
# carry dashes. The ONE rule for a room id that comes in from outside: a
# path, a body, a hello's `requested_room`, an enrolment's `in=`. The Pi and
# PC (satellite/enrolment.py IN_ROOM_RE), the dashboard's enrolment request
# (web/backend/schemas.py) and the phone apps (RoomEnrolment.isRoomId,
# RoomEnrolmentParser.isRoomID) mirror it; the Pi's and the dashboard's
# tests pin theirs to this one.
ACCEPTED_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")

# A room paired before the rule above (capitals, spaces) is refused at the
# socket, but the dashboard must still be able to remove it: a DELETE takes
# any printable id without a path separator.
_REMOVABLE_RE = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,64}\Z")


def is_room_id(value: Any) -> bool:
    """True when ``value`` is a room id the house can have."""
    return isinstance(value, str) and ACCEPTED_RE.fullmatch(value) is not None


async def require_room_id_path(request: Request) -> None:
    """Router dependency: answer 422 when the route's ``{room_id}`` path
    parameter is not a room id. A route without one passes, and so does a
    DELETE naming an older room id, so that room can still be removed."""
    room_id = request.path_params.get("room_id")
    if room_id is None or is_room_id(room_id):
        return
    if request.method == "DELETE" and _REMOVABLE_RE.fullmatch(room_id):
        return
    raise HTTPException(status_code=422, detail="not a room id")
