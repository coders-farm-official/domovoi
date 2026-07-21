"""Phone drop-in endpoint — a thin, drop-in-only peer for the intercom bridge.

The Pi↔Pi drop-in bridge in ``streaming.py`` is peer-symmetric: during a
call each ``StreamSession`` only touches its peer's ``ws`` (send_bytes /
send_text), ``room_id``, and the ``dropin_*`` bookkeeping attributes. That
means the "other end" doesn't have to be a full satellite — anything that
provides that attribute surface can join a call.

``PhoneDropinSession`` is exactly that: a duck-typed peer for the Android
app (or any future phone client). It deliberately does NOT register in
``app.state.active_sessions`` — a phone is not a satellite. It must not
evict a room's Pi (`/v1/stream` registration is last-writer-wins), must
not get wake-word/TTS routing, and must not provision an MPD container.
The only registry it touches is ``satellite_full_duplex`` (phones have
hardware AEC via the voice-communication audio path, which is the entire
meaning of that flag), so the Pi client's own full-duplex re-check passes.

Wire protocol (WebSocket ``/v1/dropin/{room_id}?phone_id=...``):

  client→server
    binary                 16 kHz mono int16 PCM mic frames, relayed
                           verbatim to the target room's Pi.
    text  dropin_end       {"type":"dropin_end"} — hang up.

  server→client
    text  dropin_start     {"type":"dropin_start","peer_room":...,
                           "peer_label":...,"audio_sample_rate":16000,
                           "full_duplex":true} — call is live.
    binary                 16 kHz mono int16 PCM: the connected/disconnected
                           chimes and the room's relayed mic audio.
    text  dropin_end       {"type":"dropin_end","reason":...} — call over
                           (either side hung up, silence timeout, failure).
    text  error            {"type":"error","code":...} — refused before
                           start; code is a feasibility reason
                           ("target_offline", "target_no_aec",
                           "target_busy", "initiator_busy", "disabled").

Same LAN trust model as ``/v1/stream`` — no auth. Revisit both together
if satellite audio ever leaves the LAN (docs/BACKLOG.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from domovoi.config import settings
from domovoi.dropin_common import OK

log = logging.getLogger(__name__)


def phone_dropin_feasibility(app: Any, phone_id: str, target_room: str) -> str:
    """Can a phone open a live drop-in on ``target_room`` right now?

    The phone-side variant of ``dropin_common.dropin_feasibility`` — the
    initiator isn't a satellite, so the initiator-side session/AEC checks
    don't apply (the phone asserts AEC by construction; its session is
    being created by this very handshake).
    """
    if not getattr(settings, "dropin_enabled", True):
        return "disabled"
    state = app.state
    if target_room not in state.active_sessions:
        return "target_offline"
    if not state.satellite_full_duplex.get(target_room, False):
        return "target_no_aec"
    if target_room in state.active_dropins:
        return "target_busy"
    if phone_id in state.active_dropins:
        return "initiator_busy"
    return OK


class PhoneDropinSession:
    """One phone call: joins the existing bridge as the duck-typed peer of
    the target room's ``StreamSession``, relays the phone's mic frames to
    the Pi, and lets the shared ``_begin_dropin`` / ``_end_dropin``
    machinery own pairing, chimes, music suppression on the Pi, the
    silence watchdog, and the ``dropin_calls`` audit row."""

    def __init__(self, ws: Any, *, phone_id: str, target_room: str) -> None:
        self.ws = ws
        # The bridge and ``active_dropins`` key peers by room_id; the phone's
        # "room" is its client identity (e.g. "phone-a3f2").
        self.room_id = phone_id
        self.target_room = target_room
        # Peer surface StreamSession's drop-in code touches:
        self.dropin_peer: Any = None
        self.dropin_call_id: int | None = None
        self._dropin_last_audio: float = 0.0
        self._dropin_silence_task: asyncio.Task[None] | None = None

    # StreamSession._suppress_music_for/_restore_music_for call this on the
    # peer when its room has resumable music — a phone never does, but keep
    # the method so the surface is complete.
    async def _safe_send_text(self, payload: dict[str, Any]) -> None:
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception as e:  # noqa: BLE001 — peer may be gone; best-effort
            log.debug("phone dropin %s: send failed: %s", self.room_id, e)

    # The Pi side calls peer._end_dropin from its disconnect/relay-failure
    # paths symmetrically; delegate to whichever real StreamSession we're
    # paired with (its _end_dropin is idempotent under dropin_lock).
    async def _end_dropin(self, *, ended_by: str, status: str = "ended") -> None:
        peer = self.dropin_peer
        if peer is not None:
            await peer._end_dropin(ended_by=ended_by, status=status)

    async def run(self) -> None:
        await self.ws.accept()
        app = self.ws.app

        code = phone_dropin_feasibility(app, self.room_id, self.target_room)
        if code != OK:
            await self._safe_send_text({"type": "error", "code": code})
            await self._close()
            return

        target = app.state.active_sessions[self.target_room]

        # Assert the phone's AEC capability for the duration of the call so
        # _begin_dropin's full_duplex fan-out (and the Pi client's own
        # defense-in-depth re-check) sees both ends as echo-cancelling.
        app.state.satellite_full_duplex[self.room_id] = True
        try:
            # The target's StreamSession drives the shared pairing logic;
            # roles in the frames are symmetric so which side is `self`
            # only affects the initiator flags, corrected just below.
            await target._begin_dropin(self)
            if self.dropin_peer is not target:
                # Lost a race (target got into another call between the
                # feasibility check and the lock).
                await self._safe_send_text({"type": "error", "code": "target_busy"})
                return

            # _begin_dropin marked the Pi as initiator; the phone placed
            # this call — flip the flags so the dashboard rows read true.
            active = app.state.active_dropins
            if self.room_id in active and self.target_room in active:
                active[self.room_id]["initiator"] = True
                active[self.target_room]["initiator"] = False

            await self._pump()
        finally:
            if self.dropin_peer is not None:
                await target._end_dropin(ended_by=self.room_id, status="ended")
            app.state.satellite_full_duplex.pop(self.room_id, None)
            await self._close()

    async def _pump(self) -> None:
        """Receive loop: binary mic frames relay to the Pi; a dropin_end
        text frame (or disconnect) hangs up. Exits when the call ends on
        either side."""
        while True:
            try:
                msg = await self.ws.receive()
            except Exception:
                return  # socket gone; run() tears down
            if msg.get("type") == "websocket.disconnect":
                return
            if self.dropin_peer is None:
                return  # call ended from the Pi/admin side
            data = msg.get("bytes")
            if data:
                await self._relay(data)
                continue
            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except ValueError:
                    continue
                if ctrl.get("type") == "dropin_end":
                    await self._end_dropin(ended_by=self.room_id, status="ended")
                    return

    async def _relay(self, data: bytes) -> None:
        """Mirror of StreamSession._on_audio's relay branch, phone→Pi."""
        peer = self.dropin_peer
        if peer is None:
            return
        gate = float(getattr(settings, "dropin_relay_gate_dbfs", -120.0))
        if gate > -120.0:
            from domovoi.streaming import _pcm_dbfs

            if _pcm_dbfs(data) < gate:
                return
        self._dropin_last_audio = asyncio.get_running_loop().time()
        try:
            await peer.ws.send_bytes(data)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "phone dropin relay %s→%s failed; tearing down: %s",
                self.room_id, peer.room_id, e,
            )
            await self._end_dropin(ended_by="system", status="failed")

    async def _close(self) -> None:
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001 — already closed / never opened
            pass
