"""Shared, dependency-light helpers for two-way drop-in (Feature 4).

This module reads ONLY ``app.state`` dicts — it imports nothing from
``streaming``, ``router``, or ``handlers``. That keeps it free of the
``streaming → router → handlers/__init__ → dropin`` import cycle, so the
handler (``handlers/dropin.py``), the admin endpoints (``main.py``), and the
streaming layer can all share the same feasibility logic.
"""

from __future__ import annotations

import re
from typing import Any

# Feasibility result codes. "ok" means a drop-in can be opened right now;
# every other value is a refusal reason the caller maps to spoken text
# (DropInHandler) or an HTTP status (the admin endpoint).
OK = "ok"

#: What ends a drop-in when it is said in a room that is in one
#: (:func:`end_call_kind`). Outside a call none of this is special: a bare
#: "stop" stays the music's (handlers/dropin.py's own ``_END_RE`` never
#: takes it). Three kinds, because they are not equally sure:
#:
#: * ``END_STOP`` — a bare "stop" ("stop it", "stop stop"): ends the call
#:   only when said by the satellite that is in it, after a timer or alarm
#:   ringing there has been silenced first;
#: * ``END_EXPLICIT`` — "hang up", "end / stop / leave the drop-in" or the
#:   call: always about the call, from wherever it is said for that room,
#:   and ahead of any parked question;
#: * ``END_WEAK`` — "cancel", "that's enough", "end it", "disconnect": end
#:   the call only when nothing else is waiting for an answer there (a
#:   parked question takes "cancel" for itself).
END_STOP, END_EXPLICIT, END_WEAK = "stop", "explicit", "weak"
_END_STOP_RE = re.compile(r"^(?:stop|stop it|stop that|stop now)(?: stop)*(?: please)?$")
_END_EXPLICIT_RE = re.compile(
    r"^(?:hang up(?: now)?(?: the (?:call|phone|drop[- ]?in))?"
    r"|end (?:the )?(?:call|drop[- ]?in)(?: now)?"
    r"|stop (?:the )?(?:call|drop[- ]?in)"
    r"|leave (?:the )?(?:call|drop[- ]?in)"
    r"|cancel (?:the )?(?:call|drop[- ]?in))"
    r"(?: please)?$"
)
_END_WEAK_RE = re.compile(r"^(?:end it|disconnect|that's enough|cancel)(?: please)?$")


def end_call_kind(transcript: str) -> str | None:
    """Which way ``transcript`` (already lowercased by the router's
    ``normalize_transcript``) asks to end a live drop-in — ``END_STOP``,
    ``END_EXPLICIT`` or ``END_WEAK`` (see above) — or None. Punctuation
    and a leading "okay" / "please" don't count: "Stop.", "okay, hang up
    please"."""
    t = re.sub(r"[.!?,;:]+", " ", (transcript or "").lower())
    t = " ".join(t.split())
    for lead in ("okay ", "ok ", "please ", "hey ", "um ", "uh "):
        if t.startswith(lead):
            t = t[len(lead):]
    if _END_EXPLICIT_RE.match(t):
        return END_EXPLICIT
    if _END_STOP_RE.match(t):
        return END_STOP
    if _END_WEAK_RE.match(t):
        return END_WEAK
    return None


def end_call_phrase(transcript: str) -> bool:
    """Whether ``transcript`` is any of :func:`end_call_kind`'s phrases."""
    return end_call_kind(transcript) is not None


def room_in_call(app: Any, room_id: str | None) -> bool:
    """Whether ``room_id`` is in a live drop-in right now
    (``app.state.active_dropins``, kept by the streaming layer)."""
    if app is None or not room_id:
        return False
    active = getattr(getattr(app, "state", None), "active_dropins", None) or {}
    return room_id in active


def starts_live_audio(response: Any) -> bool:
    """Whether this reply hands the room's speaker or microphone to
    something else the moment it has been said: a drop-in being opened,
    accepted or ended (``dropin_action``), or an announcement going out to
    other rooms (``announce_to_rooms``). Such a turn never asks anything
    back ("By the way, was that you, X?") and never opens a follow-up
    listen: the follow-up capture and the call (or the announcement) would
    fight over the same microphone and speaker."""
    if response is None:
        return False
    return bool(getattr(response, "dropin_action", None)) or bool(
        getattr(response, "announce_to_rooms", None)
    )


def pretty_room(room_id: str) -> str:
    """Spoken form of a room_id — ``living_room`` → ``living room``,
    ``dining-room`` → ``dining room``."""
    return room_id.replace("_", " ").replace("-", " ")


def room_may_reach_other_rooms(app: Any, room_id: str | None) -> bool:
    """May this room's live satellite reach ANOTHER room — start a drop-in
    there, or announce into it (CORE-11)?

    False only for a socket the core accepted with no pairing token
    (``StreamSession.token_authenticated`` is False): lenient pairing's
    case 5, or the lenient fallback when the pairing check could not run.
    Such a room is any LAN device that named itself, so it may use its own
    room and nothing else, the rule timer delivery already applies to
    house-wide fires. A room with no live session (nothing is speaking
    from it) and a stand-in without the attribute are not this check's
    business: the feasibility checks refuse what has to be refused there.
    """
    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    sess = sessions.get(room_id or "")
    return getattr(sess, "token_authenticated", True) is not False


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
