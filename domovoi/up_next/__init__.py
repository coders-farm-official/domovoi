"""Up next: the endless smart queue's engine (UP-NEXT-QUEUE-PLAN §2).

Pure Python, importable by both processes (the web runs it for browser
and app sessions, the core for rooms):

* :mod:`.model` — the values: tracks, picks, reason codes, settings;
* :mod:`.pick` — the picker: pure functions, no database, clock or
  randomness of its own;
* :mod:`.data` — the SQL that builds the picker's inputs, behind two
  small interfaces (the library; listening history);
* :mod:`.session` — a session's state and the events that change it;
* :mod:`.engine` — one refill: read, pick, advance the session;
* :mod:`.store` — sessions and picks in the database (V053, phase P3);
* :mod:`.service` — what both processes share around a stored session
  (seeds from a request, drawing a batch, the answer shapes);
* :mod:`.rooms` — a room's session: started by the room's play paths and
  refilled in the room's own queue by the core's ``up_next_extender``
  worker (phase P4).
"""

from .engine import next_batch
from .model import DEFAULT_CONFIG, Batch, EngineConfig, Pick, Track, TrackStats
from .session import SeedRef, UpNextSession, device_listener, room_listener

__all__ = [
    "DEFAULT_CONFIG", "Batch", "EngineConfig", "Pick", "SeedRef", "Track", "TrackStats",
    "UpNextSession", "device_listener", "next_batch", "room_listener",
]
