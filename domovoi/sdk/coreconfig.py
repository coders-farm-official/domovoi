"""Read-only view of whitelisted core settings (design §4.10).

Plugins read core facts through ``sdk.core_config`` instead of
importing the settings singleton — the whitelist is the API surface, so
a core config rename outside it can never break a plugin, and plugins
can't quietly grow a dependency on (or mutate) arbitrary core knobs.
"""

from __future__ import annotations

from typing import Any

from domovoi.config import settings

# The plugin-visible core settings. Extending this list is a MINOR
# version bump of the SDK surface (design §12); removing an entry is
# breaking.
CORE_CONFIG_WHITELIST: tuple[str, ...] = (
    "bot_name",
    "music_dir",
    "podcasts_dir",
    "audiobooks_dir",
    "mpd_host",
    "mpd_port_base_control",
    "mpd_port_base_http",
    "mpd_http_base",
    "use_stubs",
    "log_level",
)


class CoreConfigView:
    def __getattr__(self, name: str) -> Any:
        if name not in CORE_CONFIG_WHITELIST:
            raise AttributeError(
                f"core setting {name!r} is not plugin-visible; whitelisted: "
                f"{sorted(CORE_CONFIG_WHITELIST)}"
            )
        return getattr(settings, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("core config is read-only for plugins")
