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
API_VERSION = "1.1.0"

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
from domovoi.models import Context, Intent, Response  # noqa: E402,F401
from domovoi.sdk.assets import AssetAPI, CannedSound  # noqa: E402,F401
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
from domovoi.plugin_http import open_endpoint  # noqa: E402,F401

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
    "MediaCandidate",
    "NowPlayingView",
    "PlaybackAPI",
    "PluginDB",
    "PluginSDK",
    "RealtimeAPI",
    "Response",
    "SessionAPI",
    "StreamingSearchProvider",
    "build_sdk",
    "get_logger",
    "open_endpoint",
]
