"""Retention for the opt-in command recordings (domovoi/command_captures.py).

Each tick deletes every recording older than
``settings.command_capture_retention_days`` (14, and never more), removes
anything still on disk for a room that is no longer opted in (the opt-out
deletes its own files; this catches a write that raced it, or a row
removed by hand), and enforces ``settings.command_capture_max_mb`` across
all rooms, oldest first. The sweep of opted-out rooms is skipped for a
tick whose opt-in list can't be read, so a database that is away never
reads as "nobody is opted in".

Pure file work plus one small read, but stub-suppressed like the other
workers that touch the disk: the suite drives ``tick()`` directly against
a temporary directory. Runs once shortly after start, then every
``command_capture_pruner_interval_sec`` (default one hour), so a recording
outlives its 14 days by at most that long.
"""

from __future__ import annotations

from domovoi import command_captures
from domovoi.workers.base import Worker


class CommandCapturePruner(Worker):
    name = "command_capture_pruner"
    enabled_setting = None
    interval_setting = "command_capture_pruner_interval_sec"
    stub_suppressed = True

    async def tick(self) -> dict[str, int]:
        return await command_captures.prune()
