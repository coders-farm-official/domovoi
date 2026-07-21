"""Core playback-state sweeper — the core half of the state-sweeper
split (design §4.7, locked 9).

Reconciles three pieces of per-room playback state against what each
room's MPD is *actually* doing, catching the one cleanup case the
dispatch paths can't: a track (or the last track of a queue) ending on
its own. MPD silently goes to ``state=stop`` with no event pushed back
to the core, so:

* **Now-playing stamps** (:data:`domovoi.now_playing.NOW_PLAYING`) go
  stale — the dashboard would keep showing an external source pill.
  Eviction rule per stamp: MPD state not play/pause, OR
  ``currentsong.file`` no longer equals the stamp's canonical
  "what's playing" field (``data["stream_url"]`` by convention).
* **``app.state.current_playlist``** entries go stale the same way
  (freshness key ``last_file_path``) — a stale entry would trap
  MusicHandler's smart-skip inside a playlist that stopped playing.
* **The Pi keeps rendering silence**: MPD's httpd output is
  ``always_on``, so on a natural end the Pi's mpg123 never exits and
  its music LED never clears. ``_sweep_pi_music`` watches every room
  the core has told to play (``resumable_music``) and, once its MPD
  leaves play/pause, sends a one-shot ``music_stop`` frame.

Provider-specific staleness (e.g. expiring ~6 h stream tokens) is NOT
this worker's job — the owning plugin runs its own tick and calls
``NOW_PLAYING.clear(room, source=<its own>)`` (design §4.7 split).

Skipped under ``USE_STUBS=true`` — the suite doesn't run real MPD
daemons; the tick itself is unit-tested with patched clients.
"""

from __future__ import annotations

import logging
from typing import Any

from domovoi.clients.mpd import get_mpd_client_for
from domovoi.now_playing import NOW_PLAYING
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class PlaybackStateSweeper(Worker):
    # Declarative registration (design §4.5). Suppressed under stubs —
    # the suite doesn't run real MPD daemons; the tick is unit-tested
    # with patched clients (dossier §7 inv. 5).
    name = "playback_state_sweeper"
    enabled_setting = None
    interval_setting = "playback_sweeper_interval_sec"
    stub_suppressed = True

    def __init__(self, app: Any) -> None:
        self.app = app
        # Rooms whose MPD we've actually observed playing/paused this run.
        # Guards `_sweep_pi_music` against stopping a room in the brief
        # window between arming playback and MPD catching up.
        self._music_seen_playing: set[str] = set()

    async def tick(self) -> int:
        """Sweep once. Returns the total number of entries pruned across
        the now-playing stamps + ``current_playlist``. Also runs
        `_sweep_pi_music` (a satellite push, not folded into the count)."""
        pruned = 0
        pruned += await self._sweep_stamps()
        pruned += await self._sweep_playlist_dict()
        await self._sweep_pi_music()
        return pruned

    async def _sweep_stamps(self) -> int:
        pruned = 0
        for room_id in NOW_PLAYING.rooms():
            stamp = NOW_PLAYING.get(room_id)
            if stamp is None:
                continue
            try:
                client = get_mpd_client_for(room_id)
                state = await client.state()
                song = await client.current_song() or {}
            except Exception as e:
                log.debug(
                    "sweeper: MPD probe for room=%s (now-playing) failed: %s",
                    room_id, e,
                )
                continue
            stamped_file = stamp.data.get("stream_url")
            if state not in ("play", "pause") or song.get("file") != stamped_file:
                # Either condition means the stamp no longer reflects
                # reality; drop it so the UI affordances fall back cleanly.
                if NOW_PLAYING.clear(room_id, source=stamp.source):
                    pruned += 1
        if pruned:
            log.debug("sweeper: pruned %d stale now-playing stamp(s)", pruned)
        return pruned

    async def _sweep_playlist_dict(self) -> int:
        current: dict[str, Any] | None = getattr(
            self.app.state, "current_playlist", None
        )
        if not current:
            return 0
        pruned = 0
        # Iterate a snapshot of the keys — handlers can mutate the dict
        # concurrently (a new play in another room mid-sweep).
        for room_id in list(current.keys()):
            entry = current.get(room_id)
            if not isinstance(entry, dict):
                current.pop(room_id, None)
                pruned += 1
                continue
            try:
                client = get_mpd_client_for(room_id)
                state = await client.state()
                song = await client.current_song() or {}
            except Exception as e:
                log.debug(
                    "sweeper: MPD probe for room=%s (current_playlist) failed: %s",
                    room_id, e,
                )
                continue
            if state not in ("play", "pause"):
                current.pop(room_id, None)
                pruned += 1
                continue
            if song.get("file") != entry.get("last_file_path"):
                current.pop(room_id, None)
                pruned += 1
        if pruned:
            log.debug("sweeper: pruned %d stale current_playlist entr%s",
                      pruned, "y" if pruned == 1 else "ies")
        return pruned

    async def _sweep_pi_music(self) -> int:
        """Push a one-shot ``music_stop`` to a room's satellite once its
        MPD has run dry, so the Pi kills mpg123 and clears its music LED.

        ``resumable_music`` is set on every ``music_start`` (library,
        external stream, and playlist alike) and popped on an explicit
        stop, so it is exactly the set of rooms whose Pi is currently
        rendering music. For each, one MPD ``status`` round-trip: while
        it reports play/pause the room is left alone (and remembered as
        having played); once it leaves play/pause the playback has ended
        on its own, so we drop the resume intent + dashboard state and
        send the Pi a ``music_stop``.

        The ``_music_seen_playing`` guard means a room is only stopped
        after we've observed it actually playing — without it, the window
        between ``music_start`` (which sets ``resumable_music``) and MPD
        actually starting could read as "already stopped" and kill the
        music the instant it begins. Returns the number of rooms stopped."""
        resumable: dict[str, str] = getattr(
            self.app.state, "resumable_music", None
        ) or {}
        # Forget rooms no longer armed (an explicit stop popped them via the
        # streaming layer) so the seen-set can't leak across songs.
        self._music_seen_playing &= set(resumable.keys())
        if not resumable:
            return 0
        sessions: dict[str, Any] = getattr(
            self.app.state, "active_sessions", None
        ) or {}
        stopped = 0
        for room_id in list(resumable.keys()):
            try:
                state = await get_mpd_client_for(room_id).state()
            except Exception as e:
                log.debug("pi-music sweep: MPD probe for room=%s failed: %s", room_id, e)
                continue
            if state in ("play", "pause"):
                self._music_seen_playing.add(room_id)
                continue
            if room_id not in self._music_seen_playing:
                # Armed but never yet observed playing — MPD is probably
                # still catching up to the music_start. Don't stop early.
                continue
            # Confirmed natural end. Clear resume intent + dashboard state
            # BEFORE the send so a racing turn can't re-arm behind us.
            self._music_seen_playing.discard(room_id)
            resumable.pop(room_id, None)
            NOW_PLAYING.clear(room_id)
            playlist_dict = getattr(self.app.state, "current_playlist", None)
            if isinstance(playlist_dict, dict):
                playlist_dict.pop(room_id, None)
            target = sessions.get(room_id)
            if target is None:
                # No live WS (Pi dropped). Nothing to notify; the entry is
                # already cleared so we won't retry endlessly.
                continue
            try:
                await target._safe_send_text({"type": "music_stop"})
                stopped += 1
                log.info(
                    "room=%s music ended (mpd state=%s); sent music_stop to satellite",
                    room_id, state,
                )
            except Exception as e:
                log.debug("pi-music sweep: music_stop to room=%s failed: %s", room_id, e)
        return stopped
