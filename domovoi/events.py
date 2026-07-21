"""In-process event bus (design §4.9, locked 21).

Fire-and-forget with per-subscriber exception isolation: ``emit()``
schedules each subscriber as its own asyncio task inside a try/except,
so a slow or broken subscriber never blocks the emitter or its peers.

**NO delivery guarantee, NO ordering guarantee across events, NO
replay.** Anything needing durability goes through a queue table
(``media_acquisitions``), not the bus.

Consequence for bus-driven cleanup (normative, §4.9/§6.1): because
delivery is best-effort, any state kept consistent *only* by a bus
subscription is allowed to go stale across a crash. Cross-schema
soft-ref cleanup and correlation MUST pair the subscription (the fast
path) with a **periodic reconciliation sweep** (the correct path) — a
cheap tick that deletes rows whose soft ref no longer resolves and
re-correlates missed completions by ``origin_ref``. The bus is
latency, the sweep is truth; bounded staleness = one sweep interval.
The acquisition-side sweep helper lives in
:func:`domovoi.acquisitions.AcquisitionService.completed_for_origin`.

Event names: the ``core.*`` catalog below is versioned API — payload
shapes are stable per design §12 (removing an event or a payload field
is a breaking change). Plugin events are ``plugin.<slug>.<event>``,
emitted via the SDK which force-prefixes the slug; their payloads are
the plugin's own contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

CATALOG_VERSION = 1

# The v1 core event catalog (design §4.9). Emitting an unknown `core.*`
# name raises — a typo'd event that nobody could ever subscribe to is a
# bug, not a payload. Plugin-namespaced names are an open namespace.
CORE_EVENTS: frozenset[str] = frozenset({
    "core.turn_completed",
    "core.media_play_recorded",
    "core.library_track_added",
    "core.library_track_deleted",
    "core.entity_deleted",
    "core.playlist_deleted",
    "core.acquisition_enqueued",
    "core.acquisition_completed",
    "core.acquisition_failed",
    "core.now_playing_stamped",
    "core.now_playing_cleared",
    "core.connectivity_changed",
    "core.session_connected",
    "core.session_disconnected",
    "core.plugin_installed",
    "core.plugin_enabled",
    "core.plugin_disabled",
    "core.plugin_uninstalled",
    "core.plugin_upgraded",
})

_PLUGIN_EVENT_PREFIX = "plugin."


@dataclass(frozen=True)
class Event:
    """What a subscriber callback receives."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[Event], Awaitable[None]]


@dataclass
class Subscription:
    """Handle returned by :meth:`EventBus.subscribe`; pass back to
    :meth:`EventBus.unsubscribe` (or just let plugin teardown call
    :meth:`EventBus.unsubscribe_owner`)."""

    event: str
    callback: EventCallback
    owner: str


class EventBus:
    """See module docstring. ``owner`` on subscribe is the teardown key
    — the plugin runtime subscribes with the plugin slug so disable /
    uninstall is a single :meth:`unsubscribe_owner` call; core-owned
    subscriptions use the default ``"core"``."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscription]] = {}

    # ── Subscription management ────────────────────────────────────────
    def subscribe(
        self, event: str, cb: EventCallback, *, owner: str = "core"
    ) -> Subscription:
        self._validate_name(event)
        sub = Subscription(event=event, callback=cb, owner=owner)
        self._subscribers.setdefault(event, []).append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subscribers.get(sub.event)
        if subs is not None and sub in subs:
            subs.remove(sub)

    def unsubscribe_owner(self, owner: str) -> int:
        """Drop every subscription registered by ``owner`` (plugin
        disable/uninstall teardown). Returns how many were removed."""
        removed = 0
        for subs in self._subscribers.values():
            keep = [s for s in subs if s.owner != owner]
            removed += len(subs) - len(keep)
            subs[:] = keep
        return removed

    def subscriber_count(self, event: str) -> int:
        return len(self._subscribers.get(event, []))

    # ── Emission ───────────────────────────────────────────────────────
    def emit(self, event: str, payload: dict[str, Any] | None = None) -> int:
        """Schedule delivery to every subscriber; returns how many were
        scheduled. Never raises on subscriber failure (per-subscriber
        isolation) and never blocks the emitter — subscribers run as
        their own tasks on the current event loop. With no running loop
        (sync unit-test contexts) delivery is skipped with a debug log:
        fire-and-forget means the emitter must not care.
        """
        self._validate_name(event)
        subs = list(self._subscribers.get(event, ()))
        if not subs:
            return 0
        evt = Event(name=event, payload=dict(payload or {}))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("event %s emitted with no running loop; dropped", event)
            return 0
        for sub in subs:
            loop.create_task(
                self._deliver(sub, evt), name=f"event-{event}-{sub.owner}"
            )
        return len(subs)

    @staticmethod
    async def _deliver(sub: Subscription, evt: Event) -> None:
        try:
            await sub.callback(evt)
        except Exception as e:  # noqa: BLE001 — isolation is the contract
            log.warning(
                "event subscriber (owner=%s) failed on %s: %s",
                sub.owner, evt.name, e,
            )

    @staticmethod
    def _validate_name(event: str) -> None:
        if event.startswith("core."):
            if event not in CORE_EVENTS:
                raise ValueError(
                    f"unknown core event {event!r} — catalog v{CATALOG_VERSION}: "
                    f"{sorted(CORE_EVENTS)}"
                )
            return
        if not event.startswith(_PLUGIN_EVENT_PREFIX):
            raise ValueError(
                f"event name {event!r} must be 'core.<event>' (catalog) or "
                f"'plugin.<slug>.<event>'"
            )


# The process-wide singleton (single-process invariant — dossier §7 inv. 14).
EVENTS = EventBus()
