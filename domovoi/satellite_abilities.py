"""What a satellite can do: the hello's ``supports`` list and ``board``.

Spare-device satellites, S0 (2026-10-12). Every satellite in the field is
a Raspberry Pi, and the core has always assumed Pi abilities: restarting
through systemd, mirroring Python code from ``/v1/satellite-code``,
``set_config`` keys for LEDs, mic gain and Wi-Fi, root-run plugin payloads.
A Linux laptop or an Android phone in room mode can do some of that and not
the rest, so a hello may now say which:

* ``supports`` (optional): a list of ability names from :data:`ABILITIES`.
  The core sends a frame, or offers a dashboard action, only when the
  device listed the ability behind it. **A hello without the key is a Pi
  and keeps today's behaviour exactly**: every ability in
  :data:`ASSUMED_WITHOUT_SUPPORTS` is taken for granted, as it always was.
  The key's PRESENCE decides: a device that sends it, even empty or
  malformed, gets only what it named. Unknown names are ignored (logged
  once per connection), never an error, so a newer device can list an
  ability an older core doesn't know.
* ``board`` (optional): a short slug naming the hardware kind, e.g.
  ``"linux_pc"``, ``"windows_pc"`` or ``"android_room"`` (:data:`SPARE_BOARDS`). Persisted to
  ``satellites.board`` on approval and on every accepted hello that carries
  it; a hello without it never clears a stored board.

The older boolean hello keys keep working and mean the same as listing the
ability (:data:`BOOLEAN_KEYS`); the core takes the union of the two. Three
of them (``full_duplex``, ``speech_pause``, ``capture_control``) are
behaviours the core has always negotiated per connection, so they are NOT
assumed for a hello without ``supports``: they come from the booleans, as
before. ``name`` is the name in ``supports``; ``chat_start`` additionally
needs ``full_duplex``, so a phone that declares echo cancellation for
drop-in calls doesn't get chat mode it never said it handles.

No ``PROTOCOL_VERSION`` bump: both keys are optional new fields on an
existing message, which an older core ignores. The table of names, frames
and dashboard actions is in docs/uml/satellite-protocol.md ("Declared
abilities"); the shared fixtures in docs/uml/satellite-fixtures.json give
a Pi, a ``linux_pc`` and an ``android_room`` hello with the core's
expected reaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: Every ability name a hello may list, with what it unlocks. The frames
#: and actions each gates are spelled out in docs/uml/satellite-protocol.md.
ABILITIES: dict[str, str] = {
    "restart": "the `restart` frame and the dashboard's Restart satellite",
    "code_sync": "the `upgrade` frame (satellite code sync) and the dashboard's Upgrade satellite",
    "plugin_payloads": "plugins' satellite payloads applied during a code sync (they run as root on a Pi)",
    "config": "the `set_config` frame and the dashboard's per-satellite Settings tab",
    "wifi": "`pi_action: reassociate_wifi` (\"reconnect your wifi\") and the wifi.* settings",
    "mic_gain": "the mic_gain.* settings",
    "leds": "the leds.* settings",
    "display": "the `set_display` frame (video kiosks) and the display.* settings",
    "logs": "the `get_logs` frame (the dashboard's live log ring)",
    "wake_clips": "`start_wake_recording` / `stop_wake_recording` (recording wake-word clips)",
    "wake_models": "`set_wake_word` and `wake_models_changed` (pushed wake-word models)",
    "sounds": "`sounds_changed` (greeting and offline clips) and the sounds.* settings",
    "voice": "`pi_action: set_voice` (\"switch to voice X\" kept on the device); without it the server keeps the room's voice (domovoi/room_voice.py)",
    "time_sync": "the device sets its clock and zone from GET /v1/time after `ready` (nothing is pushed)",
    "chat_mode": "`chat_start` / `chat_end` (conversational chat mode; needs full_duplex too)",
    "announce_after_session_end": "out-of-turn announcements: timers, reminders, intercom, broadcasts",
    "full_duplex": "echo cancellation: drop-in calls (`dropin_start` / `dropin_end`), and chat mode with chat_mode",
    "speech_pause": "the device reports `speech_pause` / `speech_resume` and its last voiced frame",
    "capture_control": "`end_capture` (early commit; also needs speech_pause)",
}

#: The boolean hello keys from before ``supports``, and the ability each is.
BOOLEAN_KEYS: dict[str, str] = {
    "supports_full_duplex": "full_duplex",
    "speech_pause": "speech_pause",
    "capture_control": "capture_control",
    "announce_after_session_end": "announce_after_session_end",
}

#: Behaviours negotiated per connection by their boolean key, with or
#: without ``supports``: never assumed.
NEGOTIATED: frozenset[str] = frozenset({"full_duplex", "speech_pause", "capture_control"})

#: What a hello WITHOUT ``supports`` (every Pi in the field) is taken to
#: have: everything the core has always pushed to a satellite. Out-of-turn
#: announcements go to such a satellite whether or not its hello carries
#: ``announce_after_session_end`` — today's behaviour, with today's WARNING
#: when it doesn't.
ASSUMED_WITHOUT_SUPPORTS: frozenset[str] = frozenset(ABILITIES) - NEGOTIATED

#: Board slugs for the spare-device kinds the dashboard names. A Pi's board
#: (USB adoption writes e.g. ``raspberry_pi_zero_2_w``) is not listed: the
#: dashboard shows no board badge for it, as before.
SPARE_BOARDS: dict[str, str] = {
    "linux_pc": "Linux PC",
    "windows_pc": "Windows PC",
    "android_room": "Android room",
}

#: The `config` setting sections a narrower ability owns. A section not
#: listed here needs only `config`.
CONFIG_SECTION_ABILITY: dict[str, str] = {
    "wifi": "wifi",
    "mic_gain": "mic_gain",
    "leds": "leds",
    "display": "display",
    "sounds": "sounds",
}

#: `pi_action` values in `response_end`, and the ability each needs.
PI_ACTION_ABILITY: dict[str, str] = {
    "reassociate_wifi": "wifi",
    "set_voice": "voice",
    "restart": "restart",
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_BOARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: A `supports` list longer than this is cut (a hello is one small frame).
MAX_SUPPORTS = 64


def clean_board(raw: Any) -> str | None:
    """A hello's ``board`` as it may be stored and shown, or None: a short
    slug of letters, digits, ``_ . -`` (at most 64), nothing else. It is
    free text from the device, so it is bounded before it reaches the
    database or a page."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value if _BOARD_RE.match(value) else None


@dataclass(frozen=True)
class Abilities:
    """One connection's abilities, decided once from its hello.

    ``declared``: the hello carried ``supports``. ``names``: what it named
    that the core knows, plus what its boolean keys say. ``unknown``:
    names it listed that this core doesn't know (kept for the log)."""

    declared: bool = False
    names: frozenset[str] = frozenset()
    unknown: tuple[str, ...] = field(default_factory=tuple)

    def allows(self, ability: str) -> bool:
        """Whether the core may send what ``ability`` gates."""
        if ability in self.names:
            return True
        return not self.declared and ability in ASSUMED_WITHOUT_SUPPORTS

    def allows_chat(self) -> bool:
        """`chat_start`: chat mode AND echo cancellation. For a hello
        without `supports` that is exactly today's rule: full duplex."""
        return self.allows("chat_mode") and self.allows("full_duplex")

    def allows_config_key(self, key: str) -> bool:
        """A ``section.key`` setting: `config`, plus the section's own
        ability where one owns it (:data:`CONFIG_SECTION_ABILITY`)."""
        if not self.allows("config"):
            return False
        needed = CONFIG_SECTION_ABILITY.get(key.split(".", 1)[0])
        return needed is None or self.allows(needed)

    def declared_list(self) -> list[str] | None:
        """What the dashboard shows: the declared names, sorted, or None
        for a hello without ``supports`` (a Pi: everything, as before)."""
        return sorted(self.names) if self.declared else None


#: A hello without ``supports`` and with none of the boolean keys: what a
#: test double or a session that never said hello stands for.
LEGACY = Abilities()


def from_hello(ctrl: dict[str, Any], *, room_id: str = "?") -> Abilities:
    """The abilities a hello declares (see the module docstring)."""
    booleans = {name for key, name in BOOLEAN_KEYS.items() if bool(ctrl.get(key, False))}
    if "supports" not in ctrl:
        return Abilities(declared=False, names=frozenset(booleans))
    raw = ctrl.get("supports")
    listed: Iterable[Any]
    if isinstance(raw, list):
        listed = raw[:MAX_SUPPORTS]
    else:
        log.warning(
            "hello: room=%s sent a `supports` that is not a list (%s) — "
            "treating it as an empty list: no Pi-only frames",
            room_id, type(raw).__name__,
        )
        listed = ()
    known: set[str] = set()
    unknown: list[str] = []
    for item in listed:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name in ABILITIES:
            known.add(name)
        elif _NAME_RE.match(name) and name not in unknown:
            unknown.append(name)
    if unknown:
        log.info(
            "hello: room=%s lists abilities this core doesn't know (ignored): %s",
            room_id, ", ".join(unknown[:8]),
        )
    return Abilities(declared=True, names=frozenset(known | booleans), unknown=tuple(unknown))


def session_abilities(session: Any) -> Abilities:
    """A live session's abilities; :data:`LEGACY` for anything without them
    (a test double, a phone drop-in leg) — no restriction, as before."""
    found = getattr(session, "abilities", None)
    return found if isinstance(found, Abilities) else LEGACY


def room_allows(app: Any, room_id: str | None, ability: str) -> bool:
    """Whether ``room_id``'s connected satellite may be sent what
    ``ability`` gates. True when nothing narrower is known: no app, no
    room, or no live session for it (a handler answering /v1/intent)."""
    if app is None or room_id is None:
        return True
    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    session = sessions.get(room_id)
    if session is None:
        return True
    return session_abilities(session).allows(ability)


class NotDeclared(RuntimeError):
    """A frame was about to go to a satellite whose hello didn't declare
    the ability behind it. Nothing was sent."""

    def __init__(self, room_id: str, ability: str, frame: str) -> None:
        super().__init__(
            f"room {room_id!r} did not declare {ability!r} in its hello, "
            f"so it is not sent {frame!r}"
        )
        self.room_id = room_id
        self.ability = ability
        self.frame = frame


def unsupported_detail(room_id: str, board: str | None, what: str, ability: str) -> str:
    """The 409 text for a dashboard or API action a device didn't declare."""
    kind = SPARE_BOARDS.get(board or "", board or "this device")
    return (
        f"{room_id} ({kind}) can't take {what}: its hello didn't declare "
        f"{ability!r}"
    )
