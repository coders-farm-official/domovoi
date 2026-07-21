"""Tests for the web backend's realtime LISTEN/NOTIFY pipeline.

Two layers covered:

* :py:meth:`StatePollLoop.emit_for_channel` — diff-then-broadcast for a
  single channel. Used by both the poll tick and the LISTEN-driven
  wake-up, so getting the diff math right matters in two places.
* :py:class:`ListenTask` — connects to Postgres, registers listeners,
  and fans NOTIFY events through ``emit_for_channel``. Verified by
  firing a NOTIFY from a sibling connection and asserting the call
  reached a fake poll loop within a sub-second window.

Lives under ``domovoi/tests`` because that's where conftest's
DB-safety scaffolding (test-DB-only, TRUNCATE between tests) lives;
the web backend has no test directory of its own.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend.realtime import ListenTask, StateBroadcaster, StatePollLoop


# ─── emit_for_channel — diff & broadcast ──────────────────────────────────


@pytest.mark.asyncio
async def test_emit_for_channel_broadcasts_on_change() -> None:
    """First call captures a baseline; second call with a different
    value broadcasts; third call with the same value is a no-op."""
    broadcaster = StateBroadcaster()
    broadcaster.broadcast = AsyncMock()  # type: ignore[method-assign]
    loop = StatePollLoop(broadcaster, interval_sec=0.1)

    # Stub helper that the test can drive.
    next_value: dict[str, Any] = {"value": [{"id": 1}]}

    async def fake_helper() -> Any:
        return next_value["value"]

    # Patch the class-level dict for just this channel; restore on
    # teardown so other tests don't see the patch.
    original_helpers = StatePollLoop._CHANNEL_HELPERS
    StatePollLoop._CHANNEL_HELPERS = {
        **original_helpers,
        "test.channel": fake_helper,
    }
    try:
        # First call: baseline. _previous starts empty so any value
        # is "different" and should broadcast.
        await loop.emit_for_channel("test.channel")
        assert broadcaster.broadcast.call_count == 1
        args, kwargs = broadcaster.broadcast.call_args
        assert args[0] == "test.channel.changed"
        assert args[1] == {"data": [{"id": 1}]}

        # Same value again — must NOT broadcast.
        await loop.emit_for_channel("test.channel")
        assert broadcaster.broadcast.call_count == 1

        # Different value — must broadcast.
        next_value["value"] = [{"id": 2}]
        await loop.emit_for_channel("test.channel")
        assert broadcaster.broadcast.call_count == 2
        args, _ = broadcaster.broadcast.call_args
        assert args[1] == {"data": [{"id": 2}]}
    finally:
        StatePollLoop._CHANNEL_HELPERS = original_helpers


@pytest.mark.asyncio
async def test_emit_for_channel_swallows_helper_exception() -> None:
    """A helper that raises must not propagate out of emit_for_channel
    — the LISTEN task and the poll tick both call it, and either
    failing because the DB blipped would cascade poorly."""
    broadcaster = StateBroadcaster()
    broadcaster.broadcast = AsyncMock()  # type: ignore[method-assign]
    loop = StatePollLoop(broadcaster, interval_sec=0.1)

    async def boom() -> Any:
        raise RuntimeError("simulated snapshot failure")

    original_helpers = StatePollLoop._CHANNEL_HELPERS
    StatePollLoop._CHANNEL_HELPERS = {**original_helpers, "boom.channel": boom}
    try:
        # Must not raise.
        await loop.emit_for_channel("boom.channel")
        # Must not have broadcast anything.
        assert broadcaster.broadcast.call_count == 0
    finally:
        StatePollLoop._CHANNEL_HELPERS = original_helpers


@pytest.mark.asyncio
async def test_emit_for_channel_unknown_channel_is_noop() -> None:
    """A channel name with no helper registered is silently ignored —
    safer than raising, since helper registration is module-level
    init that could legitimately race against the LISTEN task at
    startup."""
    broadcaster = StateBroadcaster()
    broadcaster.broadcast = AsyncMock()  # type: ignore[method-assign]
    loop = StatePollLoop(broadcaster, interval_sec=0.1)
    await loop.emit_for_channel("never.registered")
    assert broadcaster.broadcast.call_count == 0


# ─── ListenTask — real Postgres NOTIFY → emit_for_channel ────────────────


class _FakePollLoop:
    """Minimal stand-in for StatePollLoop. Records every channel name
    passed to emit_for_channel, lets the test assert ordering and
    timing without dragging in a real broadcaster."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self._interval_sec = 1.5

    async def emit_for_channel(self, channel: str) -> None:
        self.calls.append((channel, time.monotonic()))


@requires_db
@pytest.mark.asyncio
async def test_listen_task_routes_acquisitions_notify_to_poll_loop() -> None:
    """End-to-end: start a ListenTask against the test DB, fire a
    NOTIFY from a sibling sqlalchemy connection, and verify the
    fake poll loop sees an emit_for_channel('acquisitions') call
    within a generous 2 s window."""
    from domovoi.db.session import engine

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]

    await task.start()
    try:
        # Give the LISTEN connection a moment to come up. Without this
        # the NOTIFY can fire before the listener is registered and
        # be silently dropped (Postgres NOTIFY isn't queued for late
        # subscribers).
        await asyncio.sleep(0.3)

        # Fire the NOTIFY from a sibling connection. Real mutation
        # sites do this in the same transaction as their UPDATE.
        async with engine.begin() as conn:
            await conn.execute(text("SELECT pg_notify('acquisitions_changed', 'test')"))

        # Wait up to 2 s for the callback to land. Sub-second is the
        # design target; 2 s is the safety margin for slow CI.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()

    assert any(channel == "acquisitions" for channel, _ in fake.calls), (
        f"expected acquisitions emit, got {fake.calls}"
    )


@requires_db
@pytest.mark.asyncio
async def test_listen_task_picks_up_plugin_channels_via_refresh() -> None:
    """Design §5.3: plugin ``[[realtime]]`` entries land in the channel
    map at registry-resync time, and ``refresh_channels()`` re-establishes
    the LISTEN set live — a NOTIFY on the plugin channel then routes to
    its realtime channel without a web-process restart."""
    from domovoi.db.session import engine

    from web.backend.realtime import set_plugin_notify_channels

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]

    await task.start()
    try:
        await asyncio.sleep(0.3)
        # Register a plugin channel AFTER connect, then refresh — the
        # running listen session must reconnect with the new set.
        set_plugin_notify_channels(
            {"plugin_demo_things_changed": "demo.things"}
        )
        task.refresh_channels()
        await asyncio.sleep(0.5)   # reconnect window

        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_notify('plugin_demo_things_changed', 'x')")
            )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()
        set_plugin_notify_channels({})

    assert any(channel == "demo.things" for channel, _ in fake.calls), (
        f"expected demo.things emit, got {fake.calls}"
    )


@requires_db
@pytest.mark.asyncio
async def test_listen_task_routes_calendar_notify_to_poll_loop() -> None:
    """Same shape as the downloads test, but exercising the calendar
    channel. Two channels means a real subscription map (not a
    hardcoded single channel) needed for either to work, so this
    catches `NOTIFY_CHANNEL_TO_REALTIME` mis-wiring."""
    from domovoi.db.session import engine

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]

    await task.start()
    try:
        await asyncio.sleep(0.3)
        async with engine.begin() as conn:
            await conn.execute(text("SELECT pg_notify('calendar_changed', 'test')"))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()

    assert any(channel == "calendar.events" for channel, _ in fake.calls), (
        f"expected calendar.events emit, got {fake.calls}"
    )


@requires_db
@pytest.mark.asyncio
async def test_listen_task_routes_library_notify_to_poll_loop() -> None:
    """``library_changed`` (fired by ``index_music_dir`` after a sweep
    inserts rows) maps to the ``library.indexer`` realtime channel so the
    Library + Stats views refetch as soon as new tracks land — what makes
    an upload surface without a timed guess or a manual rescan."""
    from domovoi.db.session import engine

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]

    await task.start()
    try:
        await asyncio.sleep(0.3)
        async with engine.begin() as conn:
            await conn.execute(text("SELECT pg_notify('library_changed', 'inserted=1')"))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()

    assert any(channel == "library.indexer" for channel, _ in fake.calls), (
        f"expected library.indexer emit, got {fake.calls}"
    )


# ─── Wire-level — mutation sites really fire NOTIFY ───────────────────────


@requires_db
@pytest.mark.asyncio
async def test_acquisition_enqueue_triggers_notify_end_to_end() -> None:
    """Enqueueing a media acquisition (the path every producer uses —
    voice handlers, web add-by-*, plugins) must produce a NOTIFY that
    wakes the LISTEN task. This covers the full mutation-to-frontend
    path rather than just the LISTEN side in isolation."""
    from domovoi.acquisitions import ACQUISITIONS
    from domovoi.db.session import session_scope

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]
    await task.start()
    try:
        await asyncio.sleep(0.3)
        # The service fires pg_notify('acquisitions_changed', ...) in the
        # same transaction as the INSERT (commit-coupled NOTIFY).
        async with session_scope() as s:
            await ACQUISITIONS.enqueue(
                s, kind="url", text="https://example.test/v=notify",
                requested_by="web", skip_library_dedup=True,
            )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()

    assert fake.calls, "expected at least one emit_for_channel call from the insert"
    # First call should be the generic acquisitions channel (what
    # acquisitions_changed maps to).
    assert fake.calls[0][0] == "acquisitions"


@requires_db
@pytest.mark.asyncio
async def test_index_music_dir_insert_triggers_library_notify(tmp_path, monkeypatch) -> None:
    """Dropping a fresh audio file into MUSIC_DIR and running the indexer
    must produce a ``library_changed`` NOTIFY that wakes the LISTEN task —
    the full upload/rescan-to-frontend path, not just the LISTEN side in
    isolation. (A junk-byte ``.mp3`` is fine: the indexer falls back to
    the filename when mutagen can't read tags, so the row still inserts.)"""
    from domovoi.config import settings
    from domovoi.db.session import engine
    from domovoi.workers.library_indexer import index_music_dir

    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    audio = tmp_path / "realtime_notify_probe.mp3"
    audio.write_bytes(b"not really audio; indexer falls back to the filename stem")

    fake = _FakePollLoop()
    task = ListenTask(fake)  # type: ignore[arg-type]
    await task.start()
    try:
        await asyncio.sleep(0.3)
        counts = await index_music_dir()
        assert counts["inserted"] >= 1
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fake.calls:
            await asyncio.sleep(0.05)
    finally:
        await task.stop()
        # Drop the row the indexer inserted so it doesn't leak into other
        # tests' "library is empty" assumptions.
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM library_tracks WHERE file_path = :fp"),
                {"fp": str(audio)},
            )

    assert any(channel == "library.indexer" for channel, _ in fake.calls), (
        f"expected library.indexer emit from index_music_dir, got {fake.calls}"
    )
