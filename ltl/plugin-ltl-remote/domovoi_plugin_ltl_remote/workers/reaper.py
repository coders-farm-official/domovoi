"""Retention sweep for the access log and stale device registrations.

Two unbounded tables would otherwise grow forever: ``remote_access_log``
gains a row per remote request, and ``remote_devices`` collects
registrations nobody ever approved. Both are bounded here by settings
rather than by hope.
"""

from __future__ import annotations

from typing import Any

from domovoi.sdk import Worker

from .. import store


class RemoteRetentionReaper(Worker):
    """Poll worker. Cheap, idempotent, and safe to miss a tick — nothing
    depends on it having run recently, only on it running eventually."""

    name = "ltl_remote_reaper"
    interval_setting = "reaper_interval_sec"
    # No enabled_setting: retention must keep working after someone turns
    # the link off, or disabling remote access would freeze the access
    # log at whatever size it had reached.
    stub_suppressed = True

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    async def tick(self) -> None:
        settings = self._sdk.config
        result = await store.reap(
            self._sdk,
            access_log_days=int(settings.access_log_retention_days),
            pending_device_hours=int(settings.pending_device_ttl_hours),
        )
        if result["access_log_rows"] or result["pending_devices"]:
            self._sdk.log.info(
                "ltl_remote: reaped %d access log rows, %d stale device registrations",
                result["access_log_rows"], result["pending_devices"],
            )
