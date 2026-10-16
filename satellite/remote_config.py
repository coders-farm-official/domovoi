"""What the server may change in a satellite's ``config.toml`` (``set_config``).

The dashboard's per-satellite Settings tab sends ``{"section.key": value}``
edits, and the satellite merges them into its own config file and restarts.
That frame is not signed (the WebSocket is plain on the LAN until TLS), and
the file it edits also holds what the device trusts — the server it is
pinned to, its address, its profile, a laptop's permission — so a frame
must never be able to reach those. The review of the spare-device work
(2026-10-13) showed two ways it could: a key with a trailing space
(``"satellite.server_fingerprint "``) rewrote the pin line, and an audio
device string reached ``aplay -D`` as an ALSA ``file:"|command"`` PCM.

So, on every board:

* **Only canonical keys**, ``section.key`` in lower-case ASCII, nothing
  around them (:data:`KEY_RE`); ``config_writer`` refuses anything else too.
* **Only the settings the dashboard edits** (:data:`SETTABLE`, the same
  list as ``domovoi/satellite_config_schema.py``; a test holds the two
  together). The pin, the server address, the room id, the profile, a
  laptop's ``[pc]`` section and every path to a program are not on it, so
  no frame can change them on any board.
* **A spare computer** (``generic_pc``) takes none of the sections it never
  declared (Wi-Fi, LEDs, mic gain, display).
* **Plain scalar values**: booleans, numbers, and strings of at most
  :data:`MAX_STRING` characters with no control characters. Whether a value
  is *valid* is then the config loader's question (an unsafe ALSA device
  name, an unknown ack mode... fail ``Config.from_toml`` and nothing is
  written).

:func:`describe` gives every accepted change as one line, which the "ask
me" permission shows in full before anything is applied.
"""

from __future__ import annotations

import math
import re
from typing import Any

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}\.[a-z][a-z0-9_]{0,40}$")
MAX_STRING = 120
#: More changes than this in one frame is refused rather than shortened:
#: the person must be able to read all of them before allowing them.
MAX_CHANGES = 40

#: What the dashboard's Settings tab edits — domovoi/satellite_config_schema.py
#: and the satellite's own config report (client._config_report) name the
#: same keys; satellite/tests/test_remote_config.py holds all three equal.
SETTABLE: frozenset[str] = frozenset({
    "wake.threshold", "wake.ack_mode",
    "barge_in.enabled", "barge_in.require_wake_word", "barge_in.min_speech_ms",
    "barge_in.vad_aggressiveness_during_tts",
    "listen.vad_aggressiveness", "listen.silence_timeout", "listen.max_record_seconds",
    "listen.followup_pre_speech_timeout", "listen.early_commit", "listen.speech_pause_ms",
    "noise_gate.dbfs", "noise_gate.auto_calibrate",
    "greeting.funny_chance", "playback.gain", "playback.tts_prebuffer_sec",
    "mic.enabled", "display.idle_mode", "display.power_method",
    "sounds.sync_enabled", "log.level",
    "audio.input_device", "audio.output_device", "audio.output_mixer_card",
    "audio.output_mixer_control", "music.alsa_device",
    "leds.enabled", "leds.num_leds", "leds.brightness",
    "mic_gain.enabled", "mic_gain.target_dbfs",
    "wifi.enabled", "wifi.min_healthy_mbits",
})
#: What a spare computer never takes (it declared none of them).
PC_EXCLUDED_SECTIONS: tuple[str, ...] = ("wifi.", "leds.", "mic_gain.", "display.")


def canonical(key: Any) -> bool:
    return isinstance(key, str) and bool(KEY_RE.fullmatch(key))


#: The device names a Windows satellite hands to PortAudio and its player.
WINDOWS_DEVICE_KEYS = frozenset({"audio.input_device", "audio.output_device", "music.alsa_device"})


def _device_names_ok(key: str, value: Any, windows: bool = False) -> bool:
    """The names that reach aplay -D / mpg123 -a / amixer must be plain
    ALSA names (satellite/alsa_names.py). Refused here, at the frame: a Pi
    that finds such a name already stored starts on its profile's default
    instead (client.Config.from_toml), so the load no longer refuses it.
    On Windows the device names reach PortAudio and the player instead:
    "default", a device number or a plain name
    (devices.valid_windows_device), the rule its loader holds them to."""
    from satellite import alsa_names

    if windows and key in WINDOWS_DEVICE_KEYS:
        from satellite import devices

        return devices.valid_windows_device(value)
    if key == "music.alsa_device":
        return alsa_names.safe_pcm(value)
    if key == "audio.output_mixer_card":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 64 \
            or alsa_names.safe_card(value)
    if key == "audio.output_mixer_control":
        return value in ("", None) or alsa_names.safe_control(value)
    return True


def _scalar(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return value is not None
    if isinstance(value, int):
        return abs(value) < 10**12
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= MAX_STRING and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    return False


def screen(changes: Any, *, pc: bool, windows: bool = False) -> tuple[dict[str, Any], list[str]]:
    """``(accepted, refused)``: the changes a frame may make here, and the
    keys it may not (each refused key once, as received, for the log).
    ``windows``: a Windows satellite, whose device names are Windows ones."""
    accepted: dict[str, Any] = {}
    refused: list[str] = []
    if not isinstance(changes, dict):
        return accepted, ["<not a mapping>"]
    for key, value in changes.items():
        ok = (
            canonical(key)
            and key in SETTABLE
            and not (pc and key.startswith(PC_EXCLUDED_SECTIONS))
            and _scalar(value)
            and _device_names_ok(key, value, windows)
        )
        if ok:
            accepted[key] = value
        else:
            refused.append(repr(key)[:80])
    if len(accepted) > MAX_CHANGES:
        return {}, refused + [f"<{len(accepted)} changes: more than {MAX_CHANGES} at once>"]
    return accepted, refused


def describe(changes: dict[str, Any]) -> list[str]:
    """One line per change, in full — what a person reads before allowing it."""
    return [f"{key} = {value!r}" for key, value in sorted(changes.items())]
