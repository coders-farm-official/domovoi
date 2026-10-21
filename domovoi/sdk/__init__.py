"""``domovoi.sdk`` — the plugin SDK facade (design §4.10, §12).

One import for plugin authors::

    from domovoi.sdk import (
        PluginSDK, Handler, HandlerDisplay, FastPath, Response,
    )

``PluginSDK`` instances are injected by the plugin loader — plugins
never construct one. ``API_VERSION`` is the semver of this
plugin-facing surface; manifests declare a compat range against it.
"""

from __future__ import annotations

# Semver over the plugin-facing surface (design §12): PluginSDK and every
# API class it exposes, the Handler ABC, capability Protocols, the event
# catalog payloads, the manifest schema, and the band ranges.
# 1.1.0: added sdk.speech (out-of-turn room announcements for plugins).
# 1.2.0: added net_safety (the shared outbound-URL check every fetcher
#        of a caller-chosen URL goes through).
# 1.3.0: added device_endpoint (a plugin route on the household device
#        tier — any paired device — instead of the admin default).
# 1.4.0: added egress (the internet-access policy gate),
#        connectivity.policy / .reason / .internet_allowed; sdk.http refuses
#        non-local requests when the answer is never.
# 1.5.0: added containers: the manifest's [[containers]] table and
#        ctx.add_container(id, resolve) -> ContainerExpectation, so the
#        core's container guard watches the Docker containers a plugin runs.
# 1.6.0: added playback.dispatch(response, room_id): a play or stop from a
#        plugin's HTTP route or worker (no voice turn) reaches the room's
#        satellite as music_start / music_stop.
# 1.7.0: added core_config.ai_features (the household's AI switch, live);
#        sdk.http refuses a request that runs or fetches a model on this
#        box's Ollama while it is off (ai_features.AITurnedOff).
# 1.8.0: added lookup sources (domovoi.lookup: LookupSource with search /
#        fetch and a class of local, api or web; ctx.add_lookup_source;
#        the core orders them by class) and the manifest's optional
#        [plugin] help file, indexed by Domovoi's help desk.
API_VERSION = "1.8.0"

from domovoi.capabilities import (  # noqa: E402,F401
    CAPABILITIES,
    MediaCandidate,
    StreamingSearchProvider,
)
from domovoi.handlers.base import (  # noqa: E402,F401
    FastPath,
    Handler,
    HandlerDisplay,
)
from domovoi import net_safety  # noqa: E402,F401
from domovoi import egress  # noqa: E402,F401
from domovoi.lookup import (  # noqa: E402,F401
    LOOKUP_SOURCE,
    LookupDocument,
    LookupHit,
    LookupSource,
)
from domovoi.models import Context, Intent, Response  # noqa: E402,F401
from domovoi.sdk.assets import AssetAPI, CannedSound  # noqa: E402,F401
from domovoi.sdk.containers import (  # noqa: E402,F401
    ContainerExpectation,
    ContainerResolver,
)
from domovoi.sdk.coreconfig import CoreConfigView  # noqa: E402,F401
from domovoi.sdk.facade import (  # noqa: E402,F401
    AcquisitionView,
    ConnectivityView,
    EventsView,
    NowPlayingView,
    PluginDB,
    PluginSDK,
    build_sdk,
)
from domovoi.sdk.http import HttpFactory  # noqa: E402,F401
from domovoi.sdk.library import LibraryAPI, LibraryTrack  # noqa: E402,F401
from domovoi.sdk.observability import get_logger  # noqa: E402,F401
from domovoi.sdk.playback import PlaybackAPI  # noqa: E402,F401
from domovoi.sdk.realtime import RealtimeAPI  # noqa: E402,F401
from domovoi.sdk.sessions import SessionAPI  # noqa: E402,F401
from domovoi.plugin_http import device_endpoint, open_endpoint  # noqa: E402,F401

# Imported LAST: plugins_runtime modules read domovoi.sdk.API_VERSION at
# import time, which is defined above — keep these below it so the
# partial-init import chain stays acyclic.
from domovoi.plugins_runtime.config_bridge import FieldSpec  # noqa: E402,F401
from domovoi.plugins_runtime.workers import (  # noqa: E402,F401
    LongRunWorker,
    Worker,
)

__all__ = [
    "API_VERSION",
    "FieldSpec",
    "LongRunWorker",
    "Worker",
    "AcquisitionView",
    "AssetAPI",
    "CAPABILITIES",
    "CannedSound",
    "ConnectivityView",
    "ContainerExpectation",
    "ContainerResolver",
    "Context",
    "CoreConfigView",
    "EventsView",
    "FastPath",
    "Handler",
    "HandlerDisplay",
    "HttpFactory",
    "Intent",
    "LibraryAPI",
    "LibraryTrack",
    "LOOKUP_SOURCE",
    "LookupDocument",
    "LookupHit",
    "LookupSource",
    "MediaCandidate",
    "NowPlayingView",
    "net_safety",
    "egress",
    "PlaybackAPI",
    "PluginDB",
    "PluginSDK",
    "RealtimeAPI",
    "Response",
    "SessionAPI",
    "StreamingSearchProvider",
    "build_sdk",
    "get_logger",
    "device_endpoint",
    "open_endpoint",
]
