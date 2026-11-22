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
# carry dashes.
_ACCEPTED_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")


def is_room_id(value: Any) -> bool:
    """True when ``value`` is a room id the house can have."""
    return isinstance(value, str) and _ACCEPTED_RE.fullmatch(value) is not None


async def require_room_id_path(request: Request) -> None:
    """Router dependency: answer 422 when the route's ``{room_id}`` path
    parameter is not a room id. A route without one passes."""
    room_id = request.path_params.get("room_id")
    if room_id is not None and not is_room_id(room_id):
        raise HTTPException(status_code=422, detail="not a room id")
