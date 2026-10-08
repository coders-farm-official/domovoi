"""Numbers-only statistics about the Domovoi server itself.

One read today: where recent voice turns spent their time, stage by stage
(speech-to-text, voice identification, routing, the first reply audio).
The Models page shows it as a single line under speech recognition, so
the effect of a Whisper change — a smaller model, more CPU threads — is a
number on the page rather than a feeling.

It is a proxy: the numbers come from ``intents_log.timings``, which the
web process could read itself, but "what is transcribing right now" is the
core's in-process state (the web process holds its own stale ``settings``
copy), and one answer should come from one place.

Counts and milliseconds only, no transcript, no person, no session — and
still a paired device's read (``require_device_read`` at both hops, since
2026-10-08): a turn count for one room over a short window says whether
somebody just spoke there (docs/SECURITY_PRIVACY.md). The hop forwards the
caller's headers like every web→core hop does
(domovoi/tests/test_web_proxy_credentials.py), so the core sees the same
credential.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request

from domovoi.admin_auth import require_device_read
from domovoi.models import MAX_ROOM_ID_CHARS
from web.backend.domovoi_client import auth_forward_headers, bridge_response, get_admin

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/latency", dependencies=[Depends(require_device_read)])
async def get_latency(
    request: Request,
    since: datetime | None = Query(
        None,
        description=(
            "ISO 8601 start of the window (no offset = UTC). Default: the "
            "last 7 days."
        ),
    ),
    room: str | None = Query(None, max_length=MAX_ROOM_ID_CHARS),
):
    """Proxy → core ``GET /v1/stats/latency``: per-stage p50/p95/max and
    counts over recent voice turns, plus the Whisper settings. ``since``
    is parsed here, so a malformed value is this process's 422 rather than
    something forwarded; 502 when the core doesn't answer."""
    params: dict[str, str] = {}
    if since is not None:
        params["since"] = since.isoformat()
    if room:
        params["room"] = room
    path = "/v1/stats/latency" + (f"?{urlencode(params)}" if params else "")
    return bridge_response(*await get_admin(path, headers=auth_forward_headers(request)))
