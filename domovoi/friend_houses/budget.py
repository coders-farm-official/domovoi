"""Plugin mail budgets: how much one friend, and all of them, may send.

Friend Houses ("serverless messaging and friend system between households")
carries plugin mail ("apps with friends", 1.5.0) on a lane of its own,
separate from people's messages: people's 60 an hour per friend
(``listener.MESSAGES_PER_HOUR``) is spent only on text and list changes,
and plugin mail spends only the buckets here (review D7).

=============================  ===========  ==========
bucket (per direction)         sustained    burst
=============================  ===========  ==========
per (friend, plugin)           2,400 / h    120
per friend, all plugins        3,600 / h    180
per friend, bytes              16 MiB / h   1 MiB
per house, all friends         7,200 / h    360
per house, bytes               64 MiB / h   4 MiB
=============================  ===========  ==========

Each is a token bucket: it refills at the sustained rate and holds at most
the burst, so no hour lets through more than the sustained rate plus one
burst. A mail is let through only when every bucket it touches has room,
and then it spends from all of them at once, so a refused mail spends
nothing.

The receiving listener holds these in memory (a restart grants a fresh
hour; accepted, review D14), and the sender's core holds the same ones, so
the sender refuses itself before the friend has to (persisted there, so a
core restart refills nothing: :meth:`Bucket.state`).

When a **house** bucket refused, the wait reported is a fixed
:data:`HOUSE_RETRY_SEC`, never the exact refill: a friend can't read from
it how busy this house is with others (L7).

No Domovoi imports beyond the standard library: the listener's container
carries this module.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

PAIR_PER_HOUR, PAIR_BURST = 2_400, 120
FRIEND_PER_HOUR, FRIEND_BURST = 3_600, 180
HOUSE_PER_HOUR, HOUSE_BURST = 7_200, 360
FRIEND_BYTES_PER_HOUR, FRIEND_BYTES_BURST = 16 * 1024 * 1024, 1024 * 1024   # L7
BYTES_PER_HOUR, BYTES_BURST = 64 * 1024 * 1024, 4 * 1024 * 1024
HOUSE_RETRY_SEC = 60


class Bucket:
    """A token bucket: refills at ``per_hour`` an hour, holds at most
    ``burst`` (and starts full)."""

    def __init__(self, per_hour: float, burst: float) -> None:
        self._cap = float(burst)
        self._rate = per_hour / 3600.0
        self._tokens = float(burst)
        self._at: float | None = None

    def _refill(self, now: float) -> None:
        if self._at is not None and now > self._at:
            self._tokens = min(self._cap, self._tokens + (now - self._at) * self._rate)
        if self._at is None or now > self._at:
            self._at = now

    def wait(self, cost: float, now: float) -> float:
        """Seconds until ``cost`` fits (0 when it fits now)."""
        self._refill(now)
        if cost > self._cap:
            return math.inf
        return 0.0 if self._tokens >= cost else (cost - self._tokens) / self._rate

    def take(self, cost: float, now: float) -> None:
        self._refill(now)
        self._tokens -= cost

    def state(self) -> dict[str, float | None]:
        """The level and its time, to keep (wall-clock buckets only)."""
        return {"t": self._tokens, "at": self._at}

    @classmethod
    def restore(cls, per_hour: float, burst: float, state: object) -> "Bucket":
        b = cls(per_hour, burst)
        if isinstance(state, dict):
            try:
                t, at = float(state.get("t")), state.get("at")
                b._tokens = max(-float(burst), min(float(burst), t))
                b._at = float(at) if at is not None else None
            except (TypeError, ValueError):
                pass
        return b


class PluginBudgets:
    """The five buckets of one direction, for every friend."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pair: dict[tuple[bytes, str], Bucket] = {}
        self._friend: dict[bytes, Bucket] = {}
        self._friend_bytes: dict[bytes, Bucket] = {}
        self._house = Bucket(HOUSE_PER_HOUR, HOUSE_BURST)
        self._bytes = Bucket(BYTES_PER_HOUR, BYTES_BURST)

    def spend(self, house: bytes, slug: str, size: int) -> int:
        """Spend one mail of ``size`` bytes from ``house`` for ``slug``.
        Returns 0 when it was let through, else the whole seconds to wait
        (at least 1), and then nothing was spent. A refusal by a house
        bucket answers :data:`HOUSE_RETRY_SEC` (L7)."""
        now = self._clock()
        pair = self._pair.get((house, slug)) or Bucket(PAIR_PER_HOUR, PAIR_BURST)
        friend = self._friend.get(house) or Bucket(FRIEND_PER_HOUR, FRIEND_BURST)
        fbytes = self._friend_bytes.get(house) or Bucket(FRIEND_BYTES_PER_HOUR, FRIEND_BYTES_BURST)
        wait = spend_all([(pair, 1), (friend, 1), (fbytes, size)],
                         [(self._house, 1), (self._bytes, size)], now)
        if wait:
            return wait
        # Only listed apps of current friends ever get here (the listener
        # checks the list first), so these maps stay as small as the list.
        self._pair[(house, slug)] = pair
        self._friend[house] = friend
        self._friend_bytes[house] = fbytes
        return 0


def spend_all(friend_side: list[tuple[Bucket, float]], house_side: list[tuple[Bucket, float]],
              now: float) -> int:
    """Spend from every bucket or none: 0 when spent, else whole seconds to
    wait (at least 1). A house bucket's refusal is :data:`HOUSE_RETRY_SEC`."""
    wait = max(b.wait(c, now) for b, c in friend_side)
    house_wait = max(b.wait(c, now) for b, c in house_side)
    if wait > 0 or house_wait > 0:
        own = math.ceil(min(wait, 24 * 3600)) if wait > 0 else 0
        if house_wait > 0:
            return max(HOUSE_RETRY_SEC, own)
        return own or 1
    for b, c in (*friend_side, *house_side):
        b.take(c, now)
    return 0
