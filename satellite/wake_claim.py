"""One turn per wake word, the device's side (rooms, 1.5.0).

Several satellites in one room hear one "hey Domovoi". The core makes it
one turn (domovoi/wake_claims.py; the wire contract is
docs/uml/satellite-protocol.md, "One turn per wake word"):

* the device declares ``wake_claim`` in its hello's ``supports`` and, while
  its room says ``claim: true`` (``ready.room`` and the ``room`` frame), sends
  ``wake_claim`` the moment the wake word fires, holding its chime and its
  music stop;
* the core answers at once with ``wake_grant``: ``owner`` (chime and capture
  as before, ``claim`` on the ``utterance_start``), ``quiet`` (no chime, the
  LED only fades, stream up to ``stream_ms`` of the command, then
  ``quiet_end``) or ``quiet`` with ``stream_ms: 0`` (stand down);
* a quiet stream the core picks hears ``capture_keep`` (go on to the
  device's own end of speech), the others ``end_capture {claim}`` (stop);
* no grant within :data:`FAIL_OPEN_SEC`: act as owner. A ``quiet`` grant
  that comes after that ends the capture (the core drops it anyway).

This module is the bookkeeping between the two threads: the mic thread
opens a claim and waits; the receiver delivers the core's frames. No audio,
no I/O, so it runs the same on a Pi, a PC and in the tests.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

from satellite.enrolment import is_in_room

ABILITY = "wake_claim"
#: No ``wake_grant`` within this: the device owns the turn (fail open).
FAIL_OPEN_SEC = 0.150
#: After a quiet stream or a stand-down, the wake word is not listened for
#: this long (owner answer Q5: the losers are back after about a second).
COOLDOWN_SEC = 1.0
#: A quiet stream never runs longer than this before ``capture_keep``,
#: whatever a grant says (the protocol's ``stream_ms`` is 2000).
MAX_STREAM_MS = 5000
FRAME_MS = 30

OWNER = "owner"
QUIET = "quiet"
STAND_DOWN = "stand_down"

#: ``quiet_end`` reasons.
LIMIT = "limit"
STOPPED = "stopped"


def room_says_claim(room: Any) -> bool:
    """``ready.room`` / the ``room`` frame's payload: whether this device
    claims its wakes. Anything but a literal ``claim: true`` is no."""
    return isinstance(room, dict) and room.get("claim") is True


def room_place(room: Any, before: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """``ready.room`` / the ``room`` frame's payload as this device keeps it:
    ``{"room_id", "name", "shared", "claim"}``, or ``before`` when it names
    no room id (an older core sends no ``room`` at all). ``name`` is the
    server's, or None when it sent none (null, missing or blank: it could
    not read the room's row). Then the name this device last had for the
    SAME room id stays; after a move to another room it is None, and the
    device says "this room". An id is never used as a name."""
    if not isinstance(room, dict):
        return before
    rid = room.get("room_id")
    if not is_in_room(rid):
        return before
    raw = room.get("name")
    name = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if name is None and before is not None and before.get("room_id") == rid:
        name = before.get("name")
    return {"room_id": rid, "name": name, "shared": room.get("shared") is True,
            "claim": room.get("claim") is True}


def claim_frame(claim: int, word: str, score: float | None, floor_dbfs: float | None,
                *, barge_in: bool = False) -> dict[str, Any]:
    """The ``wake_claim`` frame. ``floor_dbfs``: the room's level before the
    wake phrase (``adaptive_gate.pre_wake_floor``), or None."""
    frame: dict[str, Any] = {
        "type": "wake_claim",
        "claim": int(claim),
        "word": word,
        "score": round(float(score), 3) if score is not None else 0.0,
        "floor_dbfs": (
            None if floor_dbfs is None or not math.isfinite(floor_dbfs)
            else round(float(floor_dbfs), 1)
        ),
    }
    if barge_in:
        frame["barge_in"] = True
    return frame


@dataclass(frozen=True)
class Decision:
    """What the device does with its claim's answer."""

    role: str  # OWNER | QUIET | STAND_DOWN
    stream_ms: int = 0
    turn: str | None = None
    failed_open: bool = False

    @property
    def stream_frames(self) -> int:
        return self.stream_ms // FRAME_MS


def decide(grant: dict[str, Any] | None) -> Decision:
    """The answer to a claim, or None (no answer in time: fail open)."""
    if grant is None:
        return Decision(OWNER, failed_open=True)
    turn = grant.get("turn") if isinstance(grant.get("turn"), str) else None
    if grant.get("role") == QUIET:
        raw = grant.get("stream_ms")
        ms = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
        ms = max(0, min(MAX_STREAM_MS, ms))
        return Decision(QUIET, ms, turn) if ms >= FRAME_MS else Decision(STAND_DOWN, 0, turn)
    # "owner", or a role this device doesn't know: owning is the safe side
    # (the core drops a duplicate; a missed command is worse).
    return Decision(OWNER, 0, turn)


def _claim_of(payload: dict[str, Any]) -> int | None:
    c = payload.get("claim")
    return c if isinstance(c, int) and not isinstance(c, bool) else None


class ClaimBook:
    """The claims of one device, shared by the mic thread and the receiver.

    The mic thread: :meth:`open` a claim, :meth:`wait` for its grant (at
    most :data:`FAIL_OPEN_SEC`); as owner :meth:`own` it; as a quiet stream
    :meth:`start_quiet`, read :attr:`kept` / :attr:`stopped` before each
    frame, and :meth:`end_quiet`. The receiver: :meth:`on_grant`,
    :meth:`on_keep` and :meth:`on_end_capture`."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        self._pending: int | None = None
        self._grant: dict[str, Any] | None = None
        self._owned: int | None = None
        self._revoked: int | None = None
        self._quiet: int | None = None
        self._keep = False
        self._stop = False

    # ── the mic thread ─────────────────────────────────────────────────
    def open(self) -> int:
        """A new claim number (increasing; never reused by this device)."""
        with self._cond:
            self._seq += 1
            self._pending = self._seq
            self._grant = None
            return self._seq

    def wait(self, claim: int, timeout: float = FAIL_OPEN_SEC) -> dict[str, Any] | None:
        """The grant for ``claim``, or None when none came in ``timeout``
        (or the session ended: :meth:`abort`)."""
        with self._cond:
            self._cond.wait_for(lambda: self._pending != claim or self._grant is not None, timeout)
            grant = self._grant if self._pending == claim else None
            self._pending = None
            self._grant = None
            return grant

    def own(self, claim: int) -> None:
        """This device owns ``claim``'s turn (granted, or failed open)."""
        with self._cond:
            self._owned = claim
            self._revoked = None

    def revoked(self, claim: int | None) -> bool:
        """A ``quiet`` grant came for ``claim`` after the device failed open."""
        with self._cond:
            return claim is not None and self._revoked == claim

    def start_quiet(self, claim: int) -> None:
        with self._cond:
            self._quiet = claim
            self._keep = False
            self._stop = False

    @property
    def quiet_active(self) -> bool:
        """A quiet stream is running (the mic is streaming to the core)."""
        with self._cond:
            return self._quiet is not None

    @property
    def kept(self) -> bool:
        with self._cond:
            return self._keep

    @property
    def stopped(self) -> bool:
        with self._cond:
            return self._stop

    def end_quiet(self) -> None:
        with self._cond:
            self._quiet = None
            self._keep = False
            self._stop = False

    # ── the receiver ───────────────────────────────────────────────────
    def on_grant(self, payload: dict[str, Any]) -> int | None:
        """A ``wake_grant``. Returns the claim number when it REVOKES a turn
        this device already acts as owner of (a ``quiet`` grant that came
        after the fail-open), else None."""
        claim = _claim_of(payload)
        if claim is None:
            return None
        with self._cond:
            if claim == self._pending:
                self._grant = dict(payload)
                self._cond.notify_all()
                return None
            if claim == self._owned and payload.get("role") == QUIET:
                self._revoked = claim
                return claim
            return None

    def on_keep(self, payload: dict[str, Any]) -> bool:
        """``capture_keep``: the quiet stream ``claim`` was picked."""
        claim = _claim_of(payload)
        with self._cond:
            if claim is None or claim != self._quiet:
                return False
            self._keep = True
            return True

    def on_end_capture(self, payload: dict[str, Any]) -> bool:
        """``end_capture {claim}``: stop the quiet stream ``claim``. False
        when it names no quiet stream of this device (a stale one)."""
        claim = _claim_of(payload)
        with self._cond:
            if claim is None or claim != self._quiet:
                return False
            self._stop = True
            return True

    def abort(self) -> None:
        """The session ended: a waiting claim fails open now, a quiet stream
        stops."""
        with self._cond:
            self._pending = None
            self._grant = None
            if self._quiet is not None:
                self._stop = True
            self._cond.notify_all()
