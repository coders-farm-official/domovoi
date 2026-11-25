"""Retention for support-session transcripts (domovoi/support_sessions.py):
30 days, then 200 MB in all, oldest first; the audit rows go with the 30
days. Stub-suppressed like the other disk workers: the suite drives
``tick()`` directly against a temporary home."""

from __future__ import annotations

from domovoi import support_sessions
from domovoi.workers.base import Worker


class SupportSessionPruner(Worker):
    name = "support_session_pruner"
    enabled_setting = None
    interval_setting = "support_session_pruner_interval_sec"
    stub_suppressed = True

    async def tick(self) -> dict[str, int]:
        return await support_sessions.prune()
