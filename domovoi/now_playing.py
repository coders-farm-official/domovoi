"""Now-playing source registry (design §4.7, locked 9).

A single generic registry (no per-provider ``app.state`` dicts): any feature that starts external playback in a
room stamps ``(source slug, opaque data)`` here, and the core streaming
layer / sweeper / web now-playing card / favorites clear and read stamps
**generically** — core never knows a provider's vocabulary.

* Sources are EXPLICITLY registered (``register_source``) inside a
  plugin's ``register()`` — stamping with an unregistered source raises,
  and a manifest-declared ``now-playing-source:<x>`` whose
  ``register_source`` never ran fails the install-time contract check.
* One stamp per room (replace semantics).
* Storage is an in-memory dict on the core process (single-process
  invariant, dossier §7 inv. 14), mirrored into snapshots for
  web/Android — never with ``elapsed_sec`` (dossier §7 inv. 8).
* ``register_matcher`` hangs a per-source attribution hook for the web
  "favorite now playing" chain (§4.7/§10.2): the favorites endpoint
  walks matchers in slug order and the first hit attributes the play.

Sweeping (split per §4.7): the core
half lives in :mod:`domovoi.workers.playback_state_sweeper` — it clears
stamps whose room's MPD no longer plays the stamped stream. Provider-
specific staleness (e.g. expiring stream tokens) stays in the owning
plugin, which calls ``clear(room, source=<its own>)`` from its own tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from domovoi.events import EVENTS

log = logging.getLogger(__name__)

# A matcher receives the room's MPD probe results and the stamp (if this
# room carries one) and returns an attribution dict (e.g. {"kind": <source>,
# "ref": ...}) or None to pass to the next matcher in the chain.
NowPlayingMatcher = Callable[..., Any]


@dataclass
class NowPlayingStamp:
    room_id: str
    source: str            # registered source slug: "radio", "library", ...
    data: dict[str, Any]   # opaque to core; by convention carries
                           # "stream_url" (the sweeper's freshness key)
                           # and "title"
    stamped_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


class NowPlayingRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, str] = {}                 # slug → owner
        self._matchers: dict[str, tuple[str, NowPlayingMatcher]] = {}
        self._stamps: dict[str, NowPlayingStamp] = {}      # room_id → stamp

    # ── Source registration ────────────────────────────────────────────
    def register_source(self, source_slug: str, *, owner: str = "core") -> None:
        existing = self._sources.get(source_slug)
        if existing is not None and existing != owner:
            raise ValueError(
                f"now-playing source {source_slug!r} already registered by "
                f"{existing!r}"
            )
        self._sources[source_slug] = owner

    def register_matcher(
        self, source_slug: str, fn: NowPlayingMatcher, *, owner: str = "core"
    ) -> None:
        """Favorites-attribution hook (§4.7). ``source_slug`` must be a
        registered source; one matcher per source."""
        if source_slug not in self._sources:
            raise ValueError(
                f"register_matcher: source {source_slug!r} is not registered"
            )
        self._matchers[source_slug] = (owner, fn)

    def matchers(self) -> list[tuple[str, NowPlayingMatcher]]:
        """The attribution chain: ``(source_slug, fn)`` in ascending
        source-slug order — deterministic per design §4.10."""
        return [(slug, self._matchers[slug][1]) for slug in sorted(self._matchers)]

    def sources(self) -> frozenset[str]:
        return frozenset(self._sources)

    def unregister_owner(self, owner: str) -> None:
        """Plugin disable/uninstall teardown: drop the owner's sources +
        matchers and clear any live stamps carrying those sources."""
        gone = [s for s, o in self._sources.items() if o == owner]
        for slug in gone:
            del self._sources[slug]
            self._matchers.pop(slug, None)
            for room_id in [
                r for r, st in self._stamps.items() if st.source == slug
            ]:
                self.clear(room_id, source=slug)

    # ── Stamps ─────────────────────────────────────────────────────────
    def stamp(self, room_id: str, source: str, data: dict[str, Any]) -> NowPlayingStamp:
        if source not in self._sources:
            raise ValueError(
                f"now-playing source {source!r} is not registered — call "
                f"register_source() inside register() (design §4.7). "
                f"Registered: {sorted(self._sources)}"
            )
        st = NowPlayingStamp(room_id=room_id, source=source, data=dict(data))
        self._stamps[room_id] = st          # one stamp per room (replace)
        EVENTS.emit("core.now_playing_stamped", {"room_id": room_id, "source": source})
        return st

    def get(self, room_id: str) -> NowPlayingStamp | None:
        return self._stamps.get(room_id)

    def clear(self, room_id: str, source: str | None = None) -> bool:
        """Drop the room's stamp. ``source`` filters: only clear when the
        current stamp carries that source (so a provider's own pruning
        can't evict a successor's stamp). Returns True when cleared."""
        st = self._stamps.get(room_id)
        if st is None:
            return False
        if source is not None and st.source != source:
            return False
        del self._stamps[room_id]
        EVENTS.emit(
            "core.now_playing_cleared", {"room_id": room_id, "source": st.source}
        )
        return True

    def rooms(self) -> list[str]:
        return list(self._stamps.keys())

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """JSON-safe mirror for /v1/admin/snapshot → web/Android. Never
        includes ``elapsed_sec`` (dossier §7 inv. 8 — the GET-flood
        regression)."""
        return {
            room: {
                "source": st.source,
                "data": dict(st.data),
                "stamped_at": st.stamped_at.isoformat(),
            }
            for room, st in self._stamps.items()
        }


# The process-wide singleton (single-process invariant — dossier §7 inv. 14).
NOW_PLAYING = NowPlayingRegistry()

# Core-owned source seeds. Core features stamp these; plugins register
# their own inside register() (radio → "radio", providers → their slug).
NOW_PLAYING.register_source("library")
NOW_PLAYING.register_source("playlist")
NOW_PLAYING.register_source("spoken_audio")
