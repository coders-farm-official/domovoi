"""Retention for ``guests_seen`` (V023): the guests an admin can ban.

The core writes one row per (self-asserted device id, source address) each
time it lets a guest act (``domovoi/guest_bans.py``): the id, the address,
the user agent, the room and the last action. That is personal data about
people who never paired anything, so it does not wait for the next guest
to be pruned: this worker deletes rows not seen for
``guest_bans.GUESTS_SEEN_RETENTION_DAYS`` (30) on a timer (review finding
7). The per-address cap lives with the write.

Pure DB, so it runs under ``USE_STUBS`` too. Runs once shortly after
start, then every ``guests_seen_pruner_interval_sec`` (default one hour).
"""

from __future__ import annotations

from domovoi import guest_bans
from domovoi.workers.base import Worker


class GuestsSeenPruner(Worker):
    name = "guests_seen_pruner"
    enabled_setting = None
    interval_setting = "guests_seen_pruner_interval_sec"
    stub_suppressed = False

    async def tick(self) -> int:
        return await guest_bans.prune_guests_seen()
