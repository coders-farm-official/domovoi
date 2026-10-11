"""Holds: "this container is being worked on, don't page anyone about it".

The parts of the core that create, recreate and remove containers on
purpose say so here, and the guard leaves those containers alone until the
work is done and a short settle time has passed:

* ``room:<room_id>`` — the music player provisioner while it starts,
  warms or retires a room's MPD container;
* ``plugin:<slug>`` — the plugin installer while it installs, upgrades,
  enables, disables or uninstalls that plugin (the plugin's own watchdog
  recreates its containers in the minutes after);
* ``core:searxng`` — the search helper's start or stop.

In memory on purpose: a restart ends every hold, and the guard's boot
grace covers the restart itself. Every hold ends on its own — a step that
never returns is held for :data:`RUNNING_HOLD_SEC` at most — so nothing
here can keep a container that is wrong looking right for long.
"""

from __future__ import annotations

import contextlib
import time
from typing import Iterator

# key -> (monotonic time the hold ends, reason)
_TIMED: dict[str, tuple[float, str]] = {}
# key -> [steps running, monotonic start of the oldest, reason]
_RUNNING: dict[str, list] = {}

# The longest a running step holds its key.
RUNNING_HOLD_SEC = 30 * 60.0


def hold(key: str, seconds: float, reason: str) -> None:
    """Hold ``key`` for ``seconds`` from now (extends, never shortens)."""
    until = time.monotonic() + max(0.0, float(seconds))
    current = _TIMED.get(key)
    if current is None or current[0] < until:
        _TIMED[key] = (until, reason)


def held(key: str) -> str | None:
    """The reason ``key`` is held right now, or None."""
    now = time.monotonic()
    running = _RUNNING.get(key)
    if running is not None and running[0] > 0 and now - running[1] < RUNNING_HOLD_SEC:
        return str(running[2])
    current = _TIMED.get(key)
    if current is None:
        return None
    if current[0] <= now:
        _TIMED.pop(key, None)
        return None
    return current[1]


def active() -> dict[str, str]:
    """Every hold in force: ``{key: reason}`` (the status endpoint)."""
    keys = set(_TIMED) | set(_RUNNING)
    return {k: r for k in sorted(keys) if (r := held(k)) is not None}


def clear() -> None:
    """Forget every hold (tests)."""
    _TIMED.clear()
    _RUNNING.clear()


@contextlib.contextmanager
def holding(key: str, reason: str, *, settle_sec: float) -> Iterator[None]:
    """Hold ``key`` while the block runs, then for ``settle_sec`` more.
    Overlapping blocks for one key hold it until the last one ends."""
    running = _RUNNING.get(key)
    if running is None or running[0] <= 0:
        _RUNNING[key] = [1, time.monotonic(), reason]
    else:
        running[0] += 1
    try:
        yield
    finally:
        running = _RUNNING.get(key)
        if running is not None:
            running[0] -= 1
            if running[0] <= 0:
                _RUNNING.pop(key, None)
        hold(key, settle_sec, reason)
