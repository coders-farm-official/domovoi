"""Up next: the endless smart queue's engine (UP-NEXT-QUEUE-PLAN §2).

Pure Python, importable by both processes (the web runs it for browser
and app sessions, the core for rooms):

* :mod:`.model` — the values: tracks, picks, reason codes, settings;
* :mod:`.pick` — the picker: pure functions, no database, clock or
  randomness of its own;
* :mod:`.data` — the SQL that builds the picker's inputs, behind two
  small interfaces (the library; listening history);
* :mod:`.session` — a session's state and the events that change it;
* :mod:`.engine` — one refill: read, pick, advance the session.

No API, no UI and no tables of its own yet: sessions and picks are kept
by phase P3, rooms refilled by phase P4.
"""

from .engine import next_batch
from .model import DEFAULT_CONFIG, Batch, EngineConfig, Pick, Track, TrackStats
from .session import SeedRef, UpNextSession, device_listener, room_listener

__all__ = [
    "DEFAULT_CONFIG", "Batch", "EngineConfig", "Pick", "SeedRef", "Track", "TrackStats",
    "UpNextSession", "device_listener", "next_batch", "room_listener",
]
