"""Editable-config registry for the per-satellite Settings tab (Phase B).

Mirrors ``config_schema.py`` (reusing its ``FieldSpec`` +
``coerce_and_validate``) but the fields are **satellite** config, keyed by
``section.key`` to match the Pi's ``config.toml`` layout and the flat dict
the Pi reports in its ``config_status`` frame. **The field names here MUST
stay in sync with ``satellite/client.py``'s ``_config_report()``** — the
web joins this schema (metadata + tooltips) with the values that report
caches per-room.

Unlike the Domovoi server gear, every satellite edit applies the same way:
the Pi rewrites its ``config.toml`` and restarts. So ``tier`` is cosmetic
here (kept ``"hot"`` to suppress the per-field "restart" badge); the
Settings tab shows a single "saving restarts the satellite" note instead.

``section`` still matters: ``advanced`` fields (audio devices, LEDs, mixer,
mic-gain) sit behind the folded warning, exactly like the gear.
"""

from __future__ import annotations

import dataclasses
import re

from domovoi.config_schema import FieldSpec
from domovoi.config_schema import coerce_and_validate as _coerce_and_validate

__all__ = ["EDITABLE_FIELDS", "FIELD_BY_NAME", "coerce_and_validate", "spec_for_board", "MAX_STRING"]

#: The longest text value a satellite takes from the dashboard, and no
#: control characters (satellite/remote_config.py MAX_STRING and _scalar; a
#: test holds them equal). Checked here too, so the dashboard shows the
#: error instead of "sent" for a value the satellite would refuse.
MAX_STRING = 120

# ALSA device names, as satellite/alsa_names.py takes them (a test holds the
# two equal on a list of names). A PCM name is a small language: `file`,
# `pipe` and friends make ALSA write files or run a command, so only plain
# device names are accepted.
_ALSA_TYPES = ("hw|plughw|sysdefault|default|plug|dmix|dsnoop|front|rear|center_lfe|side|"
               "surround21|surround40|surround41|surround50|surround51|surround71|iec958|spdif|hdmi|usbstream")
ALSA_PCM_PATTERN = (
    r"(?!(?i:file|pipe|shm|tee|exec|ladspa|lfloat|share)\Z)[A-Za-z][A-Za-z0-9_-]{0,63}"
    rf"|(?i:{_ALSA_TYPES}):[A-Za-z0-9_=,.-]{{1,64}}"
    r"|bluealsa(?::DEV=(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?:,PROFILE=(?:a2dp|sco))?)?"
)
ALSA_CARD_PATTERN = r"[A-Za-z0-9_-]{1,32}"
# A Windows satellite's device names reach PortAudio and its own player, not
# ALSA: "default", a device number, or a plain name, as
# satellite/devices.py valid_windows_device takes them (a test holds the two
# equal). On that board they replace the ALSA rule for these three settings.
WINDOWS_DEVICE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9 ()._,:+\[\]-]{0,79}"
WINDOWS_DEVICE_FIELDS = frozenset({"audio.input_device", "audio.output_device", "music.alsa_device"})
WINDOWS_BOARDS = frozenset({"windows_pc"})
ALSA_CONTROL_PATTERN = r"(?:[A-Za-z0-9 _.,()+-]{1,48})?"


def spec_for_board(spec: FieldSpec, board: str | None) -> FieldSpec:
    """``spec`` as the satellite on ``board`` checks it: a Windows
    satellite's device names by the Windows rule (``Speakers (Realtek(R)
    Audio)`` is one; an ALSA name like ``plughw:CARD=Array`` is not), every
    other board's by the ALSA rule above."""
    if board in WINDOWS_BOARDS and spec.name in WINDOWS_DEVICE_FIELDS:
        return dataclasses.replace(
            spec, pattern=WINDOWS_DEVICE_PATTERN,
            pattern_help="a Windows audio device: default, a device number, or a name as "
                         "`python -m satellite.client --list-devices` prints it (letters, digits, "
                         "spaces and ()._,:+[]-)")
    return spec


def coerce_and_validate(spec: FieldSpec, value: object) -> object:
    """The core's check (type, range, choices, pattern), and the satellite's
    own limits on text: at most MAX_STRING characters, no control
    characters."""
    coerced = _coerce_and_validate(spec, value)
    if isinstance(coerced, str):
        if len(coerced) > MAX_STRING:
            raise ValueError(f"at most {MAX_STRING} characters")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in coerced):
            raise ValueError("no control characters (a line break, a tab, ...)")
    return coerced


EDITABLE_FIELDS: list[FieldSpec] = [
    # ─── Wake word ─────────────────────────────────────────────────────
    FieldSpec(
        "wake.threshold", "Wake sensitivity", "Wake word",
        "How easily the wake word triggers (0–1). HIGHER = fewer false "
        "wakes (the room triggers less); lower = more sensitive. "
        "Default 0.7 — raise it if it wakes on background noise, lower it "
        "if it misses you across the room.",
        "float", min=0.0, max=1.0, step=0.05, widget="slider",
        slider_labels=("more sensitive", "fewer false wakes"),
    ),
    # False wakes, 2026-11-09: a household with the TV on reaches for this
    # one next to the sensitivity, so it is a common slider.
    FieldSpec(
        "wake.patience_frames", "Wake confirmation", "Wake word",
        "How long the wake word must be heard before the room wakes, in "
        "80 ms steps: the score has to stay up that many steps in a row and "
        "reach the wake sensitivity on at least one. Default 2, which "
        "ignores short blips from the TV or a phone. Set 1 if a voice across "
        "the room has to say it twice (the first strong moment then wakes the "
        "room); 3 if blips still get through. A wake "
        "word said to interrupt a reply (barge-in) or during a drop-in call "
        "still counts on the first step.",
        "int", min=1, max=5, step=1, widget="slider",
        slider_labels=("wakes on the first blip", "fewer false wakes"),
    ),
    # Tuned once, if ever: advanced, a number box.
    FieldSpec(
        "wake.cooldown_sec", "Wake pause after a turn", "Wake word",
        "Seconds the wake word is ignored after a turn that went wrong: one "
        "Domovoi ignored as background talk, one cut off at Max command "
        "length, or one that ended in an error or with no reply, so a TV "
        "repeating the word can't start the next one at once. After an "
        "ordinary answered turn there is no pause. Default 2; 0 turns it off.",
        "float", section="advanced", min=0.0, max=10.0, unit="sec", step=0.5,
    ),
    FieldSpec(
        "wake.ack_mode", "Wake acknowledgement", "Wake word",
        "What the room does when it hears the wake word, before it "
        "listens for your command. A spoken greeting (\"Yes?\") or a short "
        "chime plays to the end first, and anything said over it is not "
        "heard — so it cannot answer its own greeting. The greeting adds "
        "its length before listening starts (about 1 s with a Piper "
        "voice); the chime about half a second; "
        "light only, nothing. The greeting is skipped when music was "
        "playing; the chime is not. Works on every board — no echo "
        "cancellation needed.",
        "choice", choices=["greeting", "chime", "none"],
        choice_labels={
            "greeting": "Spoken greeting, then listen",
            "chime": "Short chime, then listen",
            "none": "Light only, listen right away",
        },
    ),

    # ─── Barge-in (interrupting the bot) ───────────────────────────────
    FieldSpec(
        "barge_in.enabled", "Barge-in", "Barge-in",
        "Off by default: nothing interrupts the bot while it is talking. Turn "
        "on to cut it off mid-sentence, with the wake word (the default) or, "
        "with the wake-word requirement below off, by talking over it. Leave "
        "off if speaker echo on this Pi causes false interruptions.",
        "bool",
    ),
    FieldSpec(
        "barge_in.require_wake_word", "Require wake word to interrupt",
        "Barge-in",
        "On by default: only the wake word interrupts playback, not any "
        "speech, so speaker echo and background talk cannot cut the bot off. "
        "Turn off for talk-over interruption. Reliable on the XVF3800; risky "
        "on a HAT whose speaker leaks into the mic.",
        "bool",
    ),
    FieldSpec(
        "barge_in.min_speech_ms", "Barge-in speech threshold", "Barge-in",
        "How much speech (ms) is needed to count as an interruption. Raise "
        "to 500+ if echo trips it; lower to ~150 for snappier interrupts "
        "with headphones.",
        "int", min=100, max=2000, unit="ms", step=50, widget="slider",
        slider_labels=("interrupts on less speech", "needs more speech to interrupt"),
    ),
    FieldSpec(
        "barge_in.vad_aggressiveness_during_tts", "Echo resistance (during TTS)",
        "Barge-in",
        "How strictly speech is detected while the bot is talking (0–3). "
        "Higher resists speaker echo better. The XVF3800's on-chip AEC "
        "means it can run lower than a HAT.",
        "int", min=0, max=3, step=1, widget="slider",
        slider_labels=("easier to interrupt", "resists speaker echo"),
    ),

    # ─── Listening / endpointing ───────────────────────────────────────
    FieldSpec(
        "listen.vad_aggressiveness", "Speech detection strictness", "Listening",
        "How strictly speech is detected while capturing your command "
        "(0–3). Higher ends the utterance sooner on a pause.",
        "int", min=0, max=3, step=1, widget="slider",
        slider_labels=("waits through pauses", "ends sooner on a pause"),
    ),
    FieldSpec(
        "listen.silence_timeout", "End-of-speech silence", "Listening",
        "Seconds of silence that end your command. Lower = snappier "
        "responses; too low cuts you off during a natural pause.",
        "float", min=0.3, max=5.0, unit="sec", step=0.1, widget="slider",
        slider_labels=("answers sooner", "waits through longer pauses"),
    ),
    FieldSpec(
        "listen.max_record_seconds", "Max command length", "Listening",
        "Hard cap on how long a single spoken command can run before the "
        "device gives up and sends what it has.",
        "float", min=5.0, max=60.0, unit="sec", step=1, widget="slider",
        slider_labels=("shorter commands", "longer commands"),
    ),
    FieldSpec(
        "listen.followup_pre_speech_timeout", "Follow-up wait", "Listening",
        "After the bot asks a question ('Did I get that right?'), how long "
        "to wait for your reply — with no wake word needed — before giving "
        "up and returning to wake-word listen.",
        "float", min=2.0, max=20.0, unit="sec", step=1, widget="slider",
        slider_labels=("waits less", "waits longer"),
    ),
    FieldSpec(
        "listen.early_commit", "Stop listening early on a whole command",
        "Listening",
        "On by default: when what the server has heard so far is a whole "
        "command (\"pause the music\", \"set a timer for ten minutes\"), it "
        "stops this room listening after a short pause instead of the "
        "full end-of-speech silence, and answers sooner. Anything said after "
        "that pause is lost — turn off in a room where people pause "
        "mid-command. Needs early commit on for the whole server too.",
        "bool",
    ),
    FieldSpec(
        "listen.speech_pause_ms", "Pause that starts transcribing",
        "Listening",
        "How long a silence after speech (ms) makes the room device tell the "
        "server you have paused, so it starts transcribing while the "
        "end-of-speech silence is still counting down. Default 240. Lower "
        "starts the transcript sooner, but also on the gaps between words, "
        "which costs the server a transcript it throws away. Whole 30 ms "
        "steps.",
        "int", section="advanced", min=90, max=600, unit="ms", step=30,
    ),
    FieldSpec(
        "listen.response_deadline_sec", "Reply deadline", "Listening",
        "How long the room device waits, after you stop talking, for the "
        "server's first answer (what it heard, that it is still working, the "
        "reply, or an error) before it gives up on the request, shows red and "
        "listens for the wake word again. Once the server has answered "
        "anything, this deadline is off and the reply backstop takes over. "
        "Default 75; 0 turns it off. Below about 60 it can cut off the "
        "server's own apology when speech recognition is slow.",
        "float", min=0.0, max=600.0, unit="sec", step=5,
    ),
    FieldSpec(
        "listen.response_backstop_sec", "Reply backstop", "Listening",
        "Once the server has answered something, the longest the room "
        "device waits after the last thing it sent for the request (what it "
        "heard, that it is still working, part of the reply, an error) for "
        "the next, before it gives up, shows red and listens for the wake "
        "word again. Time spent playing a reply or music, or in a call or a "
        "chat, never counts, and the server says it is still working every "
        "30 s, so this only catches a server that has gone completely "
        "silent. Default 240; 0 turns it off.",
        "float", min=0.0, max=900.0, unit="sec", step=5,
    ),

    # ─── Noise gate ────────────────────────────────────────────────────
    FieldSpec(
        "noise_gate.dbfs", "Noise floor", "Noise gate",
        "Loudness floor (dBFS) — audio quieter than this is ignored even if "
        "it looks like speech. Lower = more permissive. With auto-calibrate "
        "on, this is only the boot-time fallback.",
        "float", min=-80.0, max=-10.0, unit="dBFS", step=1, widget="slider",
        slider_labels=("hears quieter sounds", "ignores more background noise"),
    ),
    FieldSpec(
        "noise_gate.auto_calibrate", "Auto-calibrate noise floor", "Noise gate",
        "Derive the floor from real room ambient instead of the fixed value. "
        "On by default on every board; turn off only to hand-tune a room "
        "with the fixed floor above.",
        "bool",
    ),

    # ─── Greeting ──────────────────────────────────────────────────────
    # [greeting] enabled is no longer offered: `wake.ack_mode` replaced it
    # (the Pi reads it only to derive ack_mode for a config that predates
    # it). [greeting] reply_wait went with the greeting played over the
    # capture, which it existed for.
    FieldSpec(
        "greeting.funny_chance", "Funny-greeting chance", "Greeting",
        "When the wake acknowledgement is the spoken greeting: the "
        "probability (0–1) that it is a funny line rather than a plain one.",
        "float", min=0.0, max=1.0, step=0.05, widget="slider",
        slider_labels=("always plain", "always funny"),
    ),

    # ─── Playback ──────────────────────────────────────────────────────
    FieldSpec(
        "playback.gain", "Voice loudness boost", "Playback",
        "Make-up loudness for the bot's voice + greetings (not music). "
        "Raise to 2–4 if Domovoi sounds quiet next to music; ~5 is the "
        "clip ceiling. 1.0 = no change.",
        "float", min=1.0, max=5.0, step=0.1, widget="slider",
        slider_labels=("no boost", "louder (may distort)"),
    ),
    FieldSpec(
        "playback.tts_prebuffer_sec", "TTS pre-buffer", "Playback",
        "Seconds of the bot's voice buffered before playback starts. Higher "
        "resists network jitter (mid-word chop) at the cost of a little "
        "startup latency; 0 = immediate.",
        "float", min=0.0, max=2.0, unit="sec", step=0.1, widget="slider",
        slider_labels=("starts talking sooner", "smoother on weak Wi-Fi"),
    ),
    FieldSpec(
        "music.buffer_sec", "Music buffer", "Playback",
        "Seconds of music held before a song starts. This is the cushion "
        "for Wi-Fi dropouts: raise it if music is choppy in this room. "
        "Songs start, pause and skip that much later.",
        "float", min=0.0, max=10.0, unit="sec", step=0.5, widget="slider",
        slider_labels=("songs start sooner", "fewer dropouts on weak Wi-Fi"),
    ),

    # ─── Voice input / Display (video satellites) ──────────────────────
    FieldSpec(
        "mic.enabled", "Voice input", "Voice input",
        "Run the wake-word/VAD/capture stack. Off on mic-less video builds "
        "— the room still speaks, announces, and plays music. Turning "
        "this ON requires a supported microphone (and [audio] input device "
        "pinned to it) or the room device fails at startup.",
        "bool",
    ),
    FieldSpec(
        "display.idle_mode", "Idle screen", "Display",
        "What a video room's screen shows when nothing is playing: a "
        "dimmed clock, a blank (black) screen, or the last cover art. "
        "Ignored on voice-only rooms.",
        "choice", choices=["clock", "blank", "art"],
    ),
    FieldSpec(
        "display.power_method", "Screen power mechanism", "Display",
        "How the display on/off toggle drives the panel: wlopm (Wayland/"
        "cage), xset (X11 DPMS), backlight (sysfs, DSI panels), auto tries "
        "each in that order, none disables the toggle. Ignored on voice-only "
        "rooms.",
        "choice", section="advanced",
        choices=["auto", "wlopm", "xset", "backlight", "none"],
    ),

    # ─── Sounds / logging ──────────────────────────────────────────────
    FieldSpec(
        "sounds.sync_enabled", "Sync sound clips", "Sounds",
        "Pull rendered greeting + notification clips from the Domovoi server "
        "on connect, instead of only using the bundled clips.",
        "bool",
    ),
    FieldSpec(
        "log.level", "Log level", "Logging",
        "Room device log verbosity. DEBUG is very noisy; INFO is normal.",
        "choice", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),

    # ─── Advanced (folded + warning) ───────────────────────────────────
    FieldSpec(
        "audio.input_device", "Mic device", "Audio devices",
        "Mic device NAME substring — reboot-proof, e.g. 'reSpeaker XVF3800'. "
        "Wrong value = no microphone. Find it with "
        "`python -m satellite.client --list-devices` on the Pi.",
        "str", section="advanced",
    ),
    FieldSpec(
        "audio.output_device", "Speaker device", "Audio devices",
        "Speaker device NAME substring. On the XVF3800 this MUST be the "
        "array, or its on-chip AEC has no reference and barge-in misfires.",
        "str", section="advanced",
    ),
    FieldSpec(
        "audio.output_mixer_card", "Volume mixer card", "Audio devices",
        "ALSA card (amixer -c) that carries the hardware volume control "
        "voice commands drive. XVF3800 default: 'Array'.",
        "str", section="advanced",
        pattern=ALSA_CARD_PATTERN,
        pattern_help="a card number or name (letters, digits, _ and -), e.g. 0 or Array",
    ),
    FieldSpec(
        "audio.output_mixer_control", "Volume mixer control", "Audio devices",
        "ALSA control name for the hardware volume (e.g. 'PCM'). Leave as-is "
        "unless your card uses a different control.",
        "str", section="advanced",
        pattern=ALSA_CONTROL_PATTERN,
        pattern_help="a mixer control name, e.g. PCM, Master or Speaker,0",
    ),
    FieldSpec(
        "music.alsa_device", "Music ALSA device", "Audio devices",
        "ALSA device mpg123 uses for music. Wrong = music routes to the "
        "wrong card or doesn't play. XVF3800: pin to the array. A plain "
        "device name only (default, hw:1,0, plughw:CARD=Array,DEV=0): the "
        "room device refuses one that makes ALSA write a file or run a command.",
        "str", section="advanced",
        pattern=ALSA_PCM_PATTERN,
        pattern_help="a plain ALSA device name, e.g. default, hw:1,0 or plughw:CARD=Array,DEV=0",
    ),
    FieldSpec(
        "leds.enabled", "Status LEDs", "LEDs",
        "The LED state indicators. Off disables the LED subsystem entirely "
        "(e.g. a USB-mic-only rig).",
        "bool", section="advanced",
    ),
    FieldSpec(
        "leds.num_leds", "LED count", "LEDs",
        "Number of LEDs on the board (HAT = 3, XVF3800 ring = 12). Wrong "
        "count looks wrong but won't break anything else.",
        "int", section="advanced", min=1, max=64, step=1,
    ),
    FieldSpec(
        "leds.brightness", "LED brightness", "LEDs",
        "Brightness (HAT 0–31, XVF ring 0–255). Lower if the pulse is too "
        "bright.",
        "int", section="advanced", min=0, max=255,
    ),
    FieldSpec(
        "mic_gain.enabled", "Mic-gain auto-tune", "Mic gain",
        "Boot-time hardware mic-gain auto-tune (HAT only). Leave OFF on the "
        "XVF3800 — it does its own AGC on-chip and there's no ALSA control "
        "to walk.",
        "bool", section="advanced",
    ),
    FieldSpec(
        "mic_gain.target_dbfs", "Mic-gain target", "Mic gain",
        "Ambient level the mic-gain tune aims for (dBFS). Lower = quieter "
        "mic with more headroom; higher = hotter. -55 is a healthy floor.",
        "float", section="advanced", min=-70.0, max=-40.0, unit="dBFS", step=1,
    ),
    FieldSpec(
        "wifi.enabled", "WiFi self-heal", "WiFi",
        "Autonomous watchdog that reassociates the WiFi when the room device "
        "has lost the core AND the core does not answer a TCP connect (the "
        "2026-05-06 wedged-link cause). It never touches a link that is "
        "carrying a live session. Needs the wpa_cli sudoers entry "
        "(PROVISIONING §6.7).",
        "bool", section="advanced",
    ),
    FieldSpec(
        "wifi.min_healthy_mbits", "WiFi slow-link log threshold", "WiFi",
        "Below this rx rate (Mbit/s) the watcher notes the link as slow in "
        "its log. Informational only: an idle link reports the 1 Mbit/s "
        "beacon rate, so the rate no longer decides a reassociate.",
        "float", section="advanced", min=1.0, max=50.0, unit="Mbit/s", step=1,
    ),
]


FIELD_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in EDITABLE_FIELDS}
