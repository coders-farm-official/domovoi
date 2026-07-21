"""Handler registry — assembled by priority band, not by list position.

Dispatch order (design §4.2): ascending ``priority_band``; ties break
core-before-plugin, then plugin slug, then handler name — see
``base.registry_sort_key``. There is no hand-ordered list; each handler carries its own
``# band rationale:`` note next to ``priority_band``.

Condensed band map — core handlers occupy exactly the design §4.2 table:

    100 dismiss          brush-offs win before anything acts
    110 voice_profile    "i'm Sarah" before any greedy capture
    120 wifi             "fix the wifi" wins over any future "fix"
    130 voice            device-control cluster near top
    140 reminder         before timer ("remind" collision)
    150 calculator       digit-anchored, well before media
    160 timer
    170 clock
    180 repeat           clusters with double_check
    190 double_check     owns "verify"/"are you sure"
    200 dropin           immediately before intercom
    210 intercom         before voice_notes ("tell the kitchen X")
    220 chat_mode
    230 voice_notes
    240 memory
    250 homelab
    260 news             before all greedy media
    270 spoken_audio     anchored media before playlist/music
    280 (radio plugin)   before music so "play 97.5 fm" isn't poached
    290 playlist         before music's ^play catch-all
    300 music            greedy ^play catch-all
    310 library          "find X in my library" before greedier "find X"
    900 (media-provider plugin)  greedy ^find catch-all — LAST band

Named ranges for plugins: 100-199 brush-off/identity (anchored only),
200-269 device control & comms, 270-349 anchored media, 350-899 general
plugin space (the default home), 900-999 greedy catch-alls (required for
any unanchored ``(.+)$`` fast path).
"""

from __future__ import annotations

from domovoi.handlers.base import (
    FastPath,
    Handler,
    HandlerDisplay,
    normalize_fast_paths,
    registry_sort_key,
)
from domovoi.handlers.calculator import CalculatorHandler
from domovoi.handlers.chat_mode import ChatModeHandler
from domovoi.handlers.clock import ClockHandler
from domovoi.handlers.dismiss import DismissHandler
from domovoi.handlers.double_check import DoubleCheckHandler
from domovoi.handlers.dropin import DropInHandler
from domovoi.handlers.homelab import HomelabHandler
from domovoi.handlers.intercom import IntercomHandler
from domovoi.handlers.library import LibraryHandler
from domovoi.handlers.memory import MemoryHandler
from domovoi.handlers.music import MusicHandler
from domovoi.handlers.news import NewsHandler
from domovoi.handlers.playlist import PlaylistHandler
from domovoi.handlers.reminder import ReminderHandler
from domovoi.handlers.repeat import RepeatHandler
from domovoi.handlers.spoken_audio import SpokenAudioHandler
from domovoi.handlers.timer import TimerHandler
from domovoi.handlers.voice import VoiceHandler
from domovoi.handlers.voice_notes import VoiceNotesHandler
from domovoi.handlers.voice_profile import VoiceProfileHandler
from domovoi.handlers.wifi import WifiHandler

# One instance per core handler. Order here is IRRELEVANT — the registry
# sorts on (priority_band, core-first, plugin slug, name).
_CORE_HANDLERS: list[Handler] = [
    CalculatorHandler(),
    ChatModeHandler(),
    ClockHandler(),
    DismissHandler(),
    DoubleCheckHandler(),
    DropInHandler(),
    HomelabHandler(),
    IntercomHandler(),
    LibraryHandler(),
    MemoryHandler(),
    MusicHandler(),
    NewsHandler(),
    PlaylistHandler(),
    ReminderHandler(),
    RepeatHandler(),
    SpokenAudioHandler(),
    TimerHandler(),
    VoiceHandler(),
    VoiceNotesHandler(),
    VoiceProfileHandler(),
    WifiHandler(),
]

# The live registry. The list OBJECT is stable (router holds a reference);
# mutations go through register_handler / unregister_handler, which keep
# it band-sorted and the name index in sync. The plugin loader (C3) uses
# the same two calls for hot enable/disable.
HANDLERS: list[Handler] = []
HANDLER_BY_NAME: dict[str, Handler] = {}


def register_handler(handler: Handler) -> None:
    """Insert a handler into the band-sorted registry (idempotent on name:
    re-registering a name replaces the old instance)."""
    normalize_fast_paths(handler)
    existing = HANDLER_BY_NAME.get(handler.name)
    if existing is not None:
        HANDLERS.remove(existing)
    HANDLERS.append(handler)
    HANDLERS.sort(key=registry_sort_key)
    HANDLER_BY_NAME[handler.name] = handler


def unregister_handler(handler: Handler | str) -> None:
    name = handler if isinstance(handler, str) else handler.name
    existing = HANDLER_BY_NAME.pop(name, None)
    if existing is not None:
        HANDLERS.remove(existing)


for _h in _CORE_HANDLERS:
    register_handler(_h)
del _h

__all__ = [
    "CalculatorHandler",
    "ChatModeHandler",
    "ClockHandler",
    "DismissHandler",
    "DoubleCheckHandler",
    "DropInHandler",
    "FastPath",
    "Handler",
    "HandlerDisplay",
    "HANDLERS",
    "HANDLER_BY_NAME",
    "HomelabHandler",
    "IntercomHandler",
    "LibraryHandler",
    "MemoryHandler",
    "MusicHandler",
    "NewsHandler",
    "PlaylistHandler",
    "register_handler",
    "ReminderHandler",
    "RepeatHandler",
    "SpokenAudioHandler",
    "TimerHandler",
    "unregister_handler",
    "VoiceHandler",
    "VoiceNotesHandler",
    "VoiceProfileHandler",
    "WifiHandler",
]
