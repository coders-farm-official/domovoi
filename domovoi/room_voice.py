"""The voice a room speaks in, when the server keeps the choice.

The server renders every room's speech: replies, announcements, the
interim lines and the greeting clips. Which registered voice it renders a
room's speech in is ``app.state.satellite_voice[room]`` (None: the server's
own voice, Settings → Voices). Two kinds of device fill that in:

* **A device that keeps its own voice**, every Pi and a PC satellite (no
  ``supports`` list, or one naming ``"voice"``): "switch to Ryan" is sent
  to it as ``pi_action: set_voice``, it writes the name to its
  ``~/.domovoi/voice`` sidecar and reports it in ``voice_status`` on every
  connect. Unchanged.
* **A device that keeps none**, an Android phone in room mode (its hello
  declares ``supports`` without ``"voice"``): the server keeps the choice
  in ``satellites.voice`` (V031), puts it in ``satellite_voice`` when the
  device connects, and says it in ``ready`` and ``sounds_changed`` as
  ``room_voice`` so the device can mirror that voice's greeting clips. Such
  a device's ``voice_status`` is ignored: the server's choice stands. The
  phone needs nothing new to be rendered in the chosen voice; an app from
  before 2026-10-17 keeps playing the default voice's greeting clips.

The phone's own voice (the app's Settings → Voice: the chat mic's replies
and the on-device assistant) is a different setting on the phone itself;
nothing here touches it.
"""

from __future__ import annotations

import logging
from typing import Any

from domovoi.satellite_abilities import room_allows

log = logging.getLogger(__name__)


def kept_by_server(app: Any, room_id: str | None) -> bool:
    """Whether ``room_id``'s connected device keeps no voice of its own, so
    the server keeps the room's choice. False for a Pi, and for a room with
    no live device (a handler answering /v1/intent)."""
    return not room_allows(app, room_id, "voice")


async def choose(db: Any, app: Any, room_id: str, name: str) -> None:
    """Keep ``name`` as the voice of ``room_id`` (a device that keeps none):
    store it, render the room's speech in it from now on, warm it up, and
    tell the device its greeting clips changed (``sounds_changed`` with
    ``room_voice``). ``db`` is the turn's database session: the choice is
    committed with the turn."""
    from domovoi.db.repositories import SatellitesRepository
    from domovoi.speech_warmup import schedule_room_voice_warm_up

    await SatellitesRepository(db).set_room_voice(room_id, name)
    app.state.satellite_voice[room_id] = name
    schedule_room_voice_warm_up(name)
    session = (getattr(app.state, "active_sessions", None) or {}).get(room_id)
    if session is not None:
        try:
            await session.notify_sounds_changed()
        except Exception as e:  # noqa: BLE001 - the clips catch up on reconnect
            log.warning("room voice: could not tell %s its clips changed: %s", room_id, e)
