"""Check GitHub-installed plugins for a newer release, once a day.

Every ``plugin_update_check_tick_sec`` (1 h) the worker hands the registry
to :data:`domovoi.plugins_runtime.update_check.CHECKER`, which asks
``api.github.com`` about each plugin whose last answer is older than a day.
Under ``INTERNET_ACCESS=never`` nothing is requested (each plugin reads
``internet_off``); while the connectivity probe reads offline the tick is
skipped. Nothing is ever installed from here: updating is an admin's
choice on the Plugins page, through the normal install path.
"""

from __future__ import annotations

from domovoi import connectivity, egress
from domovoi.workers.base import Worker


class PluginUpdateChecker(Worker):
    name = "plugin_update_check"
    interval_setting = "plugin_update_check_tick_sec"
    stub_suppressed = True
    # Gated in tick(): under "never" it still marks the plugins, so the
    # page can say why they aren't checked.
    requires_online = False

    async def tick(self) -> dict[str, int]:
        from domovoi.plugins_runtime import registry as reg
        from domovoi.plugins_runtime.update_check import CHECKER

        if not egress.internet_turned_off():
            probe = connectivity.current_probe()
            if probe is not None and not getattr(probe, "online", True):
                return {"checked": 0, "skipped": 1}
        results = await CHECKER.run(await reg.list_plugins())
        return {"checked": len(results), "skipped": 0}
