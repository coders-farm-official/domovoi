"""Which ALSA device names a satellite will hand to ``aplay -D`` and
``mpg123 -a``, and which mixer cards and controls to ``amixer``.

An ALSA PCM name is a small language, not a name: ``file:"|cmd",raw``
opens a *pipe to a shell command* on every play, and ``plug:'…'`` nests
any other PCM. ``[music] alsa_device`` is settable from the dashboard
(``set_config``), so a device string is checked before it is ever stored
or played (security review of the spare-device work, 2026-10-13: an
unsigned frame could otherwise run a command as the satellite's account).

Accepted, nothing else:

* a bare PCM name: ``default``, ``null``, ``pulse``, ``pipewire``, ``dmix``
  or one defined in the machine's own ALSA config (letters, digits,
  ``_ -``; not the plugin types that write or pipe: ``file``, ``pipe``,
  ``shm``, ``tee``, ``exec``);
* ``<type>:<arguments>`` for the hardware-facing types (:data:`TYPES`), the
  arguments being card/device words only (letters, digits, ``_ = , . -``):
  ``hw:0,0``, ``plughw:CARD=Array,DEV=0``, ``sysdefault:CARD=PCH``,
  ``plug:dmix``;
* ``bluealsa`` with a ``DEV=`` Bluetooth address and an optional profile.

No quotes, no ``|``, no ``/``, no nested ``type:type:``. Stdlib only.
"""

from __future__ import annotations

import re

#: Plugin types that may carry arguments here: they only ever reach sound
#: hardware (or another named device), never a file, a pipe or a program.
TYPES = frozenset({
    "hw", "plughw", "sysdefault", "default", "plug", "dmix", "dsnoop",
    "front", "rear", "center_lfe", "side", "surround21", "surround40",
    "surround41", "surround50", "surround51", "surround71", "iec958",
    "spdif", "hdmi", "usbstream",
})
#: Never, bare or not: they write files, open pipes or run programs.
DENIED = frozenset({"file", "pipe", "shm", "tee", "exec", "ladspa", "lfloat", "share"})

_BARE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_TYPED = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,31}):([A-Za-z0-9_=,.-]{1,64})$")
_BLUEALSA = re.compile(
    r"^bluealsa(:DEV=([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(,PROFILE=(a2dp|sco))?)?$"
)
_CARD = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_CONTROL = re.compile(r"^[A-Za-z0-9 _.,()+-]{1,48}$")


def safe_pcm(name: object) -> bool:
    """Whether ``name`` may be given to ``aplay -D`` / ``mpg123 -a``."""
    if not isinstance(name, str):
        return False
    if _BLUEALSA.fullmatch(name):
        return True
    if _BARE.fullmatch(name):
        return name.lower() not in DENIED
    m = _TYPED.fullmatch(name)
    if not m:
        return False
    kind = m.group(1).lower()
    return kind in TYPES and kind not in DENIED and ":" not in m.group(2)


def safe_card(card: object) -> bool:
    """An ``amixer -c`` card: an index or a card name."""
    return isinstance(card, str) and bool(_CARD.fullmatch(card))


def safe_control(control: object) -> bool:
    """An ``amixer`` simple-control name (``PCM``, ``Master``, ``Speaker,0``)."""
    return isinstance(control, str) and bool(_CONTROL.fullmatch(control))
