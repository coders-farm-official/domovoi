"""Read-only view of whitelisted core settings (design §4.10).

Plugins read core facts through ``sdk.core_config`` instead of
importing the settings singleton — the whitelist is the API surface, so
a core config rename outside it can never break a plugin, and plugins
can't quietly grow a dependency on (or mutate) arbitrary core knobs.
"""

from __future__ import annotations

from typing import Any

from domovoi import lan_address
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
    # The house's Ollama and its Q&A model (SDK 1.9), for a plugin that
    # writes with the same model the household's answers come from.
    # Requests go through sdk.http so the AI switch still applies.
    "ollama_url",
    "ollama_model",
    # The household's AI switch, live (domovoi/ai_features.py): False means
    # nothing may ask a language model, and sdk.http refuses requests that
    # would (SDK 1.7). Read it to skip a summary step up front.
    "ai_features",
    # The household's place (SDK 1.12), the same one core's weather and
    # local news use, so a weather plugin needn't ask for it again. Since
    # SDK 1.13 it is the Home location's name (Settings → Household); the
    # whole place is sdk.household.home_location().
    "news_location",
    # "Keep voice history" (SDK 1.17): "30" | "90" | "365" | "forever",
    # live. A plugin that keeps anything tied to a person keeps it no
    # longer than this, and deletes it on core.voice_history_deleted.
    "voice_history_keep",
)


class CoreConfigView:
    def __getattr__(self, name: str) -> Any:
        if name not in CORE_CONFIG_WHITELIST:
            raise AttributeError(
                f"core setting {name!r} is not plugin-visible; whitelisted: "
                f"{sorted(CORE_CONFIG_WHITELIST)}"
            )
        if name == "mpd_http_base":
            # Always a usable URL prefix: the setting's default is the
            # literal "auto", which only the core knows how to resolve.
            return lan_address.mpd_http_base()
        if name == "news_location":
            from domovoi import home_location

            return home_location.name()
        if name == "ai_features":
            from domovoi import ai_features

            return ai_features.enabled()
        if name == "voice_history_keep":
            # Always one of the four choices, whatever the .env spells.
            from domovoi.config import normalize_voice_history_keep

            return normalize_voice_history_keep(settings.voice_history_keep)
        return getattr(settings, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("core config is read-only for plugins")
