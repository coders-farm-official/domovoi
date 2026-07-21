"""Event bus (design §4.9): fire-and-forget, per-subscriber isolation,
catalog validation, owner-keyed teardown."""

from __future__ import annotations

import asyncio

import pytest

from domovoi.events import CORE_EVENTS, Event, EventBus


@pytest.mark.asyncio
async def test_emit_delivers_to_subscribers() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def cb(evt: Event) -> None:
        seen.append(evt)

    bus.subscribe("core.acquisition_enqueued", cb)
    scheduled = bus.emit("core.acquisition_enqueued", {"id": 7})
    assert scheduled == 1
    await asyncio.sleep(0.05)
    assert len(seen) == 1
    assert seen[0].name == "core.acquisition_enqueued"
    assert seen[0].payload == {"id": 7}


@pytest.mark.asyncio
async def test_broken_subscriber_never_blocks_peers_or_emitter() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def broken(evt: Event) -> None:
        raise RuntimeError("subscriber bug")

    async def slow_then_fine(evt: Event) -> None:
        await asyncio.sleep(0.02)
        seen.append("fine")

    bus.subscribe("core.connectivity_changed", broken)
    bus.subscribe("core.connectivity_changed", slow_then_fine)
    # Emitter must not raise and must not await either subscriber.
    assert bus.emit("core.connectivity_changed", {"online": True}) == 2
    await asyncio.sleep(0.1)
    assert seen == ["fine"]


@pytest.mark.asyncio
async def test_unknown_core_event_rejected() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="unknown core event"):
        bus.emit("core.totally_made_up", {})
    with pytest.raises(ValueError):
        bus.subscribe("core.also_made_up", _noop)
    # Non-namespaced names are rejected too.
    with pytest.raises(ValueError):
        bus.emit("random_event", {})


async def _noop(evt: Event) -> None:  # pragma: no cover — helper
    pass


@pytest.mark.asyncio
async def test_plugin_namespace_is_open() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def cb(evt: Event) -> None:
        seen.append(evt)

    bus.subscribe("plugin.radio.station_tuned", cb, owner="radio")
    bus.emit("plugin.radio.station_tuned", {"station_id": 3})
    await asyncio.sleep(0.05)
    assert seen and seen[0].payload["station_id"] == 3


@pytest.mark.asyncio
async def test_unsubscribe_owner_teardown() -> None:
    bus = EventBus()
    bus.subscribe("core.turn_completed", _noop, owner="radio")
    bus.subscribe("core.turn_completed", _noop, owner="radio")
    bus.subscribe("core.turn_completed", _noop, owner="core")
    assert bus.subscriber_count("core.turn_completed") == 3
    assert bus.unsubscribe_owner("radio") == 2
    assert bus.subscriber_count("core.turn_completed") == 1


@pytest.mark.asyncio
async def test_unsubscribe_single_subscription() -> None:
    bus = EventBus()
    sub = bus.subscribe("core.turn_completed", _noop)
    assert bus.subscriber_count("core.turn_completed") == 1
    bus.unsubscribe(sub)
    assert bus.subscriber_count("core.turn_completed") == 0
    # Idempotent.
    bus.unsubscribe(sub)


def test_emit_without_loop_is_dropped_not_raised() -> None:
    """Fire-and-forget: a sync caller with no running loop must not
    crash — delivery is simply skipped."""
    bus = EventBus()

    async def cb(evt: Event) -> None:  # pragma: no cover — never runs
        raise AssertionError

    bus.subscribe("core.turn_completed", cb)
    assert bus.emit("core.turn_completed", {}) == 0


def test_catalog_covers_design_v1() -> None:
    """The §4.9 catalog names all exist (payloads are stable API)."""
    for name in (
        "core.turn_completed", "core.media_play_recorded",
        "core.library_track_added", "core.library_track_deleted",
        "core.entity_deleted", "core.playlist_deleted",
        "core.acquisition_enqueued", "core.acquisition_completed",
        "core.acquisition_failed", "core.now_playing_stamped",
        "core.now_playing_cleared", "core.connectivity_changed",
        "core.session_connected", "core.session_disconnected",
    ):
        assert name in CORE_EVENTS
