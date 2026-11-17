"""Shared fixtures for the Friend Houses tests.

``no_tasks_left_behind``: Friend Houses starts background work (retries,
the shared-list loop, alerts, deliveries). A task that outlived its test
could write to the database after the next test's TRUNCATE and fail that
test at random (Domovoi Core's report, 2026-11-17). Modules that drive the
database flows use this fixture: at teardown it cancels and awaits every
task the test left running, and fails the test that left one, so a leak
shows up where it starts instead of as someone else's red.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
async def no_tasks_left_behind():
    before = set(asyncio.all_tasks())
    yield
    me = asyncio.current_task()
    left = [t for t in asyncio.all_tasks() if t is not me and t not in before and not t.done()]
    for task in left:
        task.cancel()
    await asyncio.gather(*left, return_exceptions=True)
    assert not left, "background tasks outlived the test: " + ", ".join(
        sorted(t.get_name() for t in left))
