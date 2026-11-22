"""The server clock, and the client-side fit of it (protocol §7).

The server clock is ``perf_counter_ns`` in microseconds: monotonic, high
resolution on Linux and Windows alike (``time.monotonic`` ticks at about
15.6 ms on Windows before Python 3.13).

``ClockFit`` is the reference implementation of the client's estimator,
used by the reference client and the tests; device players port it.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque


def now_us() -> int:
    return time.perf_counter_ns() // 1000


WINDOW_US = 60_000_000
KEEP_FRACTION = 0.10
MIN_KEEP = 4
RTT_SLACK_US = 100
RATE_SPAN_US = 10_000_000
YOUNG_US = 2_000_000
MAX_RATE = 500e-6
CONVERGED_SAMPLES = 16


class ClockFit:
    """``server ~= local + a + b * (local - tref)`` from four-timestamp pings."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, float, float]] = deque()  # mid, offset, rtt
        self.count = 0
        self.tref = 0.0
        self.a = 0.0
        self.b = 0.0
        self.best_rtt = math.inf

    @property
    def converged(self) -> bool:
        return self.count >= CONVERGED_SAMPLES

    def add(self, t0: float, t1: float, t2: float, t3: float) -> None:
        rtt = (t3 - t0) - (t2 - t1)
        if rtt < 0:
            return  # a clock went backwards or the pong is garbage
        mid = (t0 + t3) / 2
        self._samples.append((mid, ((t1 - t0) + (t2 - t3)) / 2, rtt))
        while self._samples and self._samples[0][0] < mid - WINDOW_US:
            self._samples.popleft()
        self.count += 1
        self._refit(mid)

    def _refit(self, tref: float) -> None:
        ordered = sorted(self._samples, key=lambda s: s[2])
        n = max(MIN_KEEP, math.ceil(len(ordered) * KEEP_FRACTION))
        # Also every sample within RTT_SLACK_US of the best: on a steady
        # network that spreads the kept set over the whole window, so the
        # rate fit has a span to work with.
        limit = max(ordered[min(n, len(ordered)) - 1][2], ordered[0][2] + RTT_SLACK_US)
        keep = [s for s in ordered if s[2] <= limit]
        self.best_rtt = keep[0][2]
        b = 0.0
        xs = [s[0] - tref for s in keep]
        if len(keep) >= MIN_KEEP and max(xs) - min(xs) >= RATE_SPAN_US:
            ys = [s[1] for s in keep]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
                b = max(-MAX_RATE, min(MAX_RATE, b))
        else:
            # No rate yet: an old offset is off by rate x age, so use the
            # newest kept samples only.
            recent = [s for s in keep if s[0] >= tref - YOUNG_US]
            keep = recent or keep
        self.a = statistics.median(s[1] - b * (s[0] - tref) for s in keep)
        self.b = b
        self.tref = tref

    def to_server(self, local_us: float) -> float:
        return local_us + self.a + self.b * (local_us - self.tref)

    def to_local(self, server_us: float) -> float:
        # Inverse of to_server; b is tiny, so one step is exact to well
        # under a nanosecond.
        return (server_us - self.a + self.b * self.tref) / (1 + self.b)
