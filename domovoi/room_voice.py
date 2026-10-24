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


# ─── the dashboard's picker (Settings → Rooms, a room's drawer) ──────


class VoiceChangeRefused(Exception):
    """The picker's change was not made: ``status`` is the HTTP answer."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def voice_ready(row: dict) -> str | None:
    """Why the registered voice ``row`` can't speak on this server now (not
    downloaded yet, or a model it can't use), or None when it can. The
    picker only offers a voice that can: a room set to one that can't would
    speak in the fallback voice under its name."""
    if row.get("engine") != "piper":
        return None
    from domovoi.clients.tts import piper_voice_on_disk, piper_voice_problem

    ref = row.get("model_ref") or ""
    if not piper_voice_on_disk(ref):
        return f"{row['name']} isn't downloaded on this server yet"
    problem = piper_voice_problem(ref)
    return f"{row['name']} can't speak here: {problem}" if problem else None


async def _register_shipped(db: Any, name: str) -> dict | None:
    """One of the voices Domovoi ships, named exactly, that the registry
    hasn't got: registered now when its files are here (as the voice
    command does), else a row-shaped stand-in :func:`voice_ready` refuses
    as not downloaded. None for any other name."""
    from domovoi.clients.tts import SHIPPED_VOICES, piper_voice_on_disk
    from domovoi.db.repositories import VoicesRepository

    shipped = next((v for v in SHIPPED_VOICES if v.name.lower() == name.strip().lower()), None)
    if shipped is None:
        return None
    if not piper_voice_on_disk(shipped.model_ref):
        return {"name": shipped.name, "engine": "piper", "model_ref": shipped.model_ref}
    repo = VoicesRepository(db)
    await repo.create(name=shipped.name, engine="piper", model_ref=shipped.model_ref)
    return await repo.get_by_name(shipped.name)


def confirmation(name: str) -> str:
    """What the room says, in the new voice: the voice command's reply."""
    return f"Okay, I'm {name} now."


async def apply(app: Any, room_id: str, name: str | None) -> dict[str, Any]:
    """Set ``room_id``'s voice from the dashboard, as "switch to <name>'s
    voice" said in the room would:

    * **a connected device that keeps its own voice** (a Pi, a PC): the
      room says the confirmation in the new voice, its ``response_end``
      carrying ``pi_action: set_voice`` — the device stores the name and
      reports it back in ``voice_status``. Nothing changes until it does.
      Refused (409) while the room is busy or offline: the choice lives on
      the device.
    * **a device that keeps none** (a phone in room mode): stored in
      ``satellites.voice`` (:func:`choose`), then the confirmation is said
      if the room is connected and free. Works offline too: it takes the
      voice on its next connect. ``name`` None is the server's own voice.

    Returns ``{room_id, voice, kept_on: "device"|"server", said}``. Raises
    :class:`VoiceChangeRefused`."""
    from domovoi.db.repositories import SatellitesRepository, VoicesRepository
    from domovoi.db.session import session_scope
    from domovoi.satellite_abilities import BOARDS_WITHOUT_VOICE, session_abilities

    async with session_scope() as db:
        row = await VoicesRepository(db).get_by_name(name) if name else None
        if name and row is None:
            row = await _register_shipped(db, name)
        default = await VoicesRepository(db).get_default()
        inventory = await SatellitesRepository(db).get(room_id)
        known = await SatellitesRepository(db).room_is_known(room_id)
    if name and row is None:
        raise VoiceChangeRefused(404, f"there is no voice called {name!r}")
    if row is not None:
        why = voice_ready(row)
        if why:
            raise VoiceChangeRefused(409, why)
        name = row["name"]

    session = (getattr(app.state, "active_sessions", None) or {}).get(room_id)
    if session is not None:
        on_server = not session_abilities(session).allows("voice")
    else:
        board = (inventory or {}).get("board")
        on_server = board in BOARDS_WITHOUT_VOICE
        if not on_server:
            if not known:
                raise VoiceChangeRefused(404, f"there is no room {room_id!r}")
            raise VoiceChangeRefused(
                409, f"{room_id} is offline, and its device keeps its own voice: "
                     "change it once the room is back")

    if not on_server:
        if name is None:
            raise VoiceChangeRefused(
                400, f"{room_id}'s device keeps a voice of its own: pick one by name")
        try:
            await session.announce(
                confirmation(name), defer_to_capture=True, voice=name,
                pi_action="set_voice", pi_action_arg=name,
            )
        except Exception as e:  # noqa: BLE001 - AnnounceNotStarted / Interrupted / a dead socket
            reason = getattr(e, "reason", None)
            why = {
                "tts_failed": f"the server couldn't speak in {name} just now",
                "send_failed": "its connection dropped",
            }.get(reason or "", f"{room_id} is busy ({reason})" if reason else f"{room_id} didn't take it")
            raise VoiceChangeRefused(409, f"{why}: nothing changed, try again in a moment") from e
        log.info("room voice: %s asked to speak as %s (dashboard)", room_id, name)
        return {"room_id": room_id, "voice": name, "kept_on": "device", "said": True}

    async with session_scope() as db:
        if session is not None:
            if name is None:
                await SatellitesRepository(db).set_room_voice(room_id, None)
                app.state.satellite_voice.pop(room_id, None)
                await session.notify_sounds_changed()
            else:
                await choose(db, app, room_id, name)
        else:
            await SatellitesRepository(db).set_room_voice(room_id, name)
    log.info("room voice: %s now speaks as %s (dashboard, kept on the server)",
             room_id, name or "the server's voice")
    said = False
    if session is not None:
        spoken = name or (default or {}).get("name")
        if spoken:
            try:
                await session.announce(confirmation(spoken), defer_to_capture=True)
                said = True
            except Exception as e:  # noqa: BLE001 - kept anyway; the room was busy
                log.info("room voice: %s did not say the confirmation: %s", room_id, e)
    return {"room_id": room_id, "voice": name, "kept_on": "server", "said": said}
