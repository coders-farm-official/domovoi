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
# 1.9.0: added speech.render(text, room_id=None): text to a WAV in the
#        room's voice (no room: the server's voice), nothing played; and
#        core_config.ollama_url / ollama_model (the house Q&A model).
# 1.10.0: added spoken_names (the core's spoken-name folding and matching:
#         fold, spoken_forms, phonetic_key, sounds_like, ...), choices
#         (parse_choice_reply, the reader of a reply to "did you mean X?"),
#         Handler.choice_kinds / handle_choice_reply for plugin handlers,
#         playback.ensure_room(room_id) (is this a room of the house; wake its
#         music player), and [[web.player_sources]] room_play_template (the
#         browser player hands a plugin's item to a room).
# 1.11.0: plugin websockets: a @device_endpoint @router.websocket route on
#         the core router is served (the per-slug gate's websocket form:
#         enabled, LAN Host and Origin, household token or admin Bearer at
#         the upgrade, rechecked while open; per-plugin and per-device
#         socket caps, message size and rate limits; open sockets closed
#         on disable or reload). Any other plugin websocket is still
#         refused at load, and the web process serves none.
# 1.12.0: core_config.news_location (the household's place); a plugin that
#         registers the "weather" capability takes weather questions over
#         from core's built-in lookup.
# 1.13.0: sdk.household.home_location() (the Home location: name, lat, lon,
#         country, or None) and sdk.places.suggest(text, limit=8) (the
#         bundled town list); core_config.news_location is now the Home
#         location's name.
# 1.13.1: net_safety.open_raw_stream / iter_raw (and _sync twins):
#         open_stream that asks for identity and refuses an encoded
#         answer, read raw with no total cap (a live stream).
# 1.14.0: "needs AI": Handler.needs_ai / FastPath.needs_ai and the
#         manifest's [[handlers]] needs_ai and [ai] table (does, routes):
#         off with the household's AI switch, answered by core. (Released
#         with Domovoi 1.4.1; next's 1.14-1.18 moved up one at the merge.)
# 1.15.0: [[web.home_cards]]: a plugin card on the dashboard's Home page
#         (window.DomovoiPlugins.<slug>.homeCards.<Component>).
# 1.16.0: playback.player_route(room_id): how a room's music player reaches
#         the core and the address it calls from (the radio relay).
# 1.17.0: playback.relay_url(room_id, remote_url, kind="file"|"radio") and
#         playback.relay_target(url): a room's player plays a remote URL
#         through the core's checked relay (GET /v1/player-relay/<token>).
# 1.18.0: the event core.voice_history_deleted {person_id} (None = all of
#         it), after a person's voice history or all of it is deleted, and
#         core_config.voice_history_keep: the hook any plugin keeping
#         person-linked data must honour (security review M8).
# 1.19.0: sdk.friend_houses (apps with friends, [permissions] friend_houses):
#         friends(), send_mail(), on_mail(handler, schema),
#         on_friends_changed(); FriendText, friend_sentence, friend_reply and
#         per-plugin friend ids; send_image() and FriendImage (a friend's
#         picture as the core's own PNG); every reply of a friend_houses
#         plugin's handlers filtered for speech, a stand-in in the history.
# 1.20.0: sdk.speech.interim
API_VERSION = "1.20.0"

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
from domovoi.sdk.friend_houses import (  # noqa: E402,F401
    FriendHouse,
    FriendHousesAPI,
    FriendImage,
    FriendText,
    friend_reply,
    friend_sentence,
)
from domovoi.sdk.household import HouseholdAPI, PlacesAPI  # noqa: E402,F401
from domovoi.sdk.http import HttpFactory  # noqa: E402,F401
from domovoi.sdk.library import LibraryAPI, LibraryTrack  # noqa: E402,F401
from domovoi.sdk.observability import get_logger  # noqa: E402,F401
from domovoi.sdk.playback import PlaybackAPI  # noqa: E402,F401
from domovoi.player_relay import RelayUnavailable  # noqa: E402,F401
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

# Bound on first use (PEP 562 module __getattr__), so ``from domovoi.sdk
# import spoken_names`` works without the SDK importing the name matcher or
# the choice reader (and the router behind it) itself.
_LAZY_MODULES = {
    "spoken_names": "domovoi.handlers.shared.spoken_names",
    "choices": "domovoi.sdk.choices",
}


def __getattr__(name: str):
    target = _LAZY_MODULES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target)
    globals()[name] = module
    return module


__all__ = [
    "API_VERSION",
    "choices",
    "spoken_names",
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
    "FriendHouse",
    "FriendHousesAPI",
    "FriendImage",
    "FriendText",
    "friend_reply",
    "friend_sentence",
    "Handler",
    "HandlerDisplay",
    "HouseholdAPI",
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
    "PlacesAPI",
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
