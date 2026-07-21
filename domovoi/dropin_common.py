"""Shared, dependency-light helpers for two-way drop-in (Feature 4).

This module reads ONLY ``app.state`` dicts — it imports nothing from
``streaming``, ``router``, or ``handlers``. That keeps it free of the
``streaming → router → handlers/__init__ → dropin`` import cycle, so the
handler (``handlers/dropin.py``), the admin endpoints (``main.py``), and the
streaming layer can all share the same feasibility logic.
"""

from __future__ import annotations

from typing import Any

# Feasibility result codes. "ok" means a drop-in can be opened right now;
# every other value is a refusal reason the caller maps to spoken text
# (DropInHandler) or an HTTP status (the admin endpoint).
OK = "ok"


def pretty_room(room_id: str) -> str:
    """Spoken form of a room_id — ``living_room`` → ``living room``."""
    return room_id.replace("_", " ")


def dropin_feasibility(app: Any, initiator_room: str, target_room: str) -> str:
    """Can ``initiator_room`` open a live drop-in on ``target_room`` *now*?

    Reads the live registries the streaming layer maintains on ``app.state``:
      * ``active_sessions``       — which rooms have a connected Pi.
      * ``satellite_full_duplex`` — which rooms have on-chip AEC (XVF3800).
      * ``active_dropins``        — which rooms are already mid-call.

    Returns ``OK`` or one of: ``same_room``, ``initiator_offline``,
    ``target_offline``, ``initiator_no_aec``, ``target_no_aec``,
    ``initiator_busy``, ``target_busy``. The target being an *unknown* room
    (vs. a known-but-disconnected one) is resolved upstream by the handler's
    room matcher, so it isn't a code here.
    """
    if initiator_room == target_room:
        return "same_room"

    state = app.state
    sessions = state.active_sessions
    full_duplex = state.satellite_full_duplex
    active = state.active_dropins

    if initiator_room not in sessions:
        return "initiator_offline"
    if target_room not in sessions:
        return "target_offline"
    # Both ends must capture-while-playing without howling. No AEC → refuse
    # (no half-duplex fallback — drop-in is XVF3800-only by decision).
    if not full_duplex.get(initiator_room, False):
        return "initiator_no_aec"
    if not full_duplex.get(target_room, False):
        return "target_no_aec"
    if initiator_room in active:
        return "initiator_busy"
    if target_room in active:
        return "target_busy"
    return OK
