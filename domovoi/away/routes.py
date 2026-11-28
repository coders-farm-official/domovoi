"""The core's two away chat routes, for the web process (1.5.0).

The web's ``/api/away`` routes (``web/backend/api/away.py``) own the
enrolments and the setting (rows in the shared database); the core owns
Tor and the listener. These let the web say "the phones changed" and ask
"is the away onion up for this phone?". Device tier: the web calls them
with the household token (``service_auth_headers``); neither reveals a
key, and reconciling twice is harmless.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from domovoi.admin_auth import require_device

router = APIRouter(prefix="/v1/away", tags=["away-chat"])


_KICK: dict[str, Any] = {"task": None}


def kick() -> None:
    """Reconcile soon, unless a reconcile asked for here hasn't finished
    yet: the away watch (every 30 s while the onion runs) picks up anything
    that one read too early. The web's word and a phone's ``away.leave``
    (:mod:`domovoi.away.bridge`) both come here."""
    import asyncio

    from domovoi.friend_houses import service

    task = _KICK["task"]
    if task is None or task.done():
        _KICK["task"] = asyncio.get_running_loop().create_task(service.reconcile(),
                                                                name="away-chat-reconcile")
        service._PENDING.add(_KICK["task"])
        _KICK["task"].add_done_callback(service._PENDING.discard)


@router.post("/reconcile", dependencies=[Depends(require_device)])
async def away_reconcile() -> dict[str, Any]:
    """Apply the enrolled phones and "Chat from away" now: the listener's
    phone list at once, Tor's keys in the next batched reload (the first
    phone: a restart). Asked again while one asked here hasn't finished,
    nothing more is queued (:func:`kick`)."""
    kick()
    return {"ok": True}


@router.get("/state", dependencies=[Depends(require_device)])
async def away_state(device: str | None = Query(None, max_length=64)) -> dict[str, Any]:
    """``ready`` once Tor runs the away onion (and has ``device``'s key) and
    has had time to publish it, ``publishing`` while the onion's descriptor
    carrying ``device``'s key is probably still on its way to the Tor
    network, ``starting`` while it is coming up, null when it doesn't run
    (:func:`domovoi.friend_houses.service.away_status`)."""
    from domovoi.friend_houses import service

    return {"state": service.away_status(device)}
