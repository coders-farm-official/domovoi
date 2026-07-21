from __future__ import annotations

import asyncio

import pytest

from domovoi import lifecycle


@pytest.fixture(autouse=True)
def _reset_shutdown_event():
    # Snapshot + reset the module-level event around each test so they don't
    # leak state into one another.
    was_set = lifecycle.shutdown_event.is_set()
    lifecycle.shutdown_event.clear()
    yield
    if was_set:
        lifecycle.shutdown_event.set()
    else:
        lifecycle.shutdown_event.clear()


@pytest.mark.asyncio
async def test_wait_or_shutdown_times_out_when_no_signal() -> None:
    fired = await lifecycle.wait_or_shutdown(0.1)
    assert fired is False


@pytest.mark.asyncio
async def test_wait_or_shutdown_returns_true_when_signaled() -> None:
    async def _fire_later() -> None:
        await asyncio.sleep(0.05)
        lifecycle.signal_shutdown()

    task = asyncio.create_task(_fire_later())
    try:
        fired = await lifecycle.wait_or_shutdown(2.0)
    finally:
        await task
    assert fired is True
    assert lifecycle.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_wait_or_shutdown_returns_immediately_if_already_set() -> None:
    lifecycle.signal_shutdown()
    fired = await lifecycle.wait_or_shutdown(2.0)
    assert fired is True


def test_signal_shutdown_idempotent() -> None:
    lifecycle.signal_shutdown()
    lifecycle.signal_shutdown()  # second call: no-op, no exception
    assert lifecycle.shutdown_event.is_set()
