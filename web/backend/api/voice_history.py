"""Voice history API — People → Voice history (owner, 2026-10-17).

* ``GET /api/voice-history`` — how long voice history is kept ("Keep voice
  history": 30 days, 90 days, 1 year, forever), how many turns are kept
  and since when, the last daily cleanup, and the latest "Delete all
  voice history" with its progress. Counts and dates only, never what was
  said: the device READ tier, like the rest of the People page's reads,
  so a paired phone can see how long the house keeps its voice. Read from
  the database this process shares; the setting's live value from the
  core's snapshot (this process's own copy goes stale on a save).
* ``POST /api/voice-history/delete-all`` — admin, security tier (Bearer
  only, 501 until first-run setup; review 2026-10-17). Forwarded to the core, which
  writes the job and runs it in the background (domovoi/voice_history.py);
  the answer is the job.

The setting itself is written like every other setting: ``PATCH
/api/config/editable`` with ``voice_history_keep`` (admin security tier).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from domovoi import voice_history
from domovoi.admin_auth import require_admin_security, require_device_read
from domovoi.config import VOICE_HISTORY_KEEP_CHOICES
from domovoi.config import settings as core_settings
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_cached_snapshot,
    post_admin,
)

router = APIRouter(prefix="/api/voice-history", tags=["voice-history"])


def live_keep() -> str:
    """The live "Keep voice history": the core's snapshot, else this
    process's copy (the core hasn't answered yet)."""
    live = (get_cached_snapshot() or {}).get("voice_history_keep")
    if live in VOICE_HISTORY_KEEP_CHOICES:
        return live
    return core_settings.voice_history_keep


@router.get("", dependencies=[Depends(require_device_read)])
async def get_voice_history() -> dict[str, Any]:
    """``{keep, keep_label, keep_days, choices, turns, oldest_at,
    delete_all, last_cleanup}``: "Keep voice history" (``30`` / ``90`` /
    ``365`` / ``forever``; ``keep_days`` null for forever), the
    conversation turns kept and the oldest one's time, and the latest
    "Delete all voice history" and daily cleanup as jobs (``state``:
    queued / running / failed / done, ``cutoff``, ``deleted`` counts per
    store). No text. A paired device or the admin."""
    async with session_scope() as s:
        return await voice_history.status(s, live_keep())


@router.post("/delete-all", dependencies=[Depends(require_admin_security)])
async def delete_all_voice_history(request: Request):
    """Delete all voice history: every turn said until now, everywhere,
    with Domovoi's answers, the routing log, the rooms' short-term
    conversations and the satellites' logs kept on the server. Admin,
    security tier: 501 before setup.
    Forwarded to the core (``POST /v1/admin/voice-history/delete-all``),
    which answers at once with ``{job}`` and runs it in the background;
    pressing again while one runs answers that one. 502 when the core is
    unreachable."""
    status, payload = await post_admin(
        "/v1/admin/voice-history/delete-all",
        {},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)
