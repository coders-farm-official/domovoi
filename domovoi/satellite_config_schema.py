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

from domovoi.config_schema import FieldSpec, coerce_and_validate

__all__ = ["EDITABLE_FIELDS", "FIELD_BY_NAME", "coerce_and_validate"]


EDITABLE_FIELDS: list[FieldSpec] = [
    # ─── Wake word ─────────────────────────────────────────────────────
    FieldSpec(
        "wake.threshold", "Wake sensitivity", "Wake word",
        "How easily the wake word triggers (0–1). HIGHER = fewer false "
        "wakes (the satellite triggers less); lower = more sensitive. "
        "Default 0.5 — raise toward 0.6–0.7 if it wakes on background noise.",
        "float", min=0.0, max=1.0,
    ),

    # ─── Barge-in (interrupting the bot) ───────────────────────────────
    FieldSpec(
        "barge_in.enabled", "Barge-in", "Barge-in",
        "Let you interrupt the bot mid-sentence by talking over it. Turn "
        "off if speaker echo on this Pi causes false interruptions.",
        "bool",
    ),
    FieldSpec(
        "barge_in.require_wake_word", "Require wake word to interrupt",
        "Barge-in",
        "Only the wake word interrupts playback, not any speech. Immune to "
        "speaker echo and background talk, but you must say the wake word "
        "to cut the bot off. Best on Pis whose speaker leaks into the mic.",
        "bool",
    ),
    FieldSpec(
        "barge_in.min_speech_ms", "Barge-in speech threshold", "Barge-in",
        "How much speech (ms) is needed to count as an interruption. Raise "
        "to 500+ if echo trips it; lower to ~150 for snappier interrupts "
        "with headphones.",
        "int", min=100, max=2000, unit="ms",
    ),
    FieldSpec(
        "barge_in.vad_aggressiveness_during_tts", "Echo resistance (during TTS)",
        "Barge-in",
        "How strictly speech is detected while the bot is talking (0–3). "
        "Higher resists speaker echo better. The XVF3800's on-chip AEC "
        "means it can run lower than a HAT.",
        "int", min=0, max=3,
    ),

    # ─── Listening / endpointing ───────────────────────────────────────
    FieldSpec(
        "listen.vad_aggressiveness", "Speech detection strictness", "Listening",
        "How strictly speech is detected while capturing your command "
        "(0–3). Higher ends the utterance sooner on a pause.",
        "int", min=0, max=3,
    ),
    FieldSpec(
        "listen.silence_timeout", "End-of-speech silence", "Listening",
        "Seconds of silence that end your command. Lower = snappier "
        "responses; too low cuts you off during a natural pause.",
        "float", min=0.3, max=5.0, unit="sec",
    ),
    FieldSpec(
        "listen.max_record_seconds", "Max command length", "Listening",
        "Hard cap on how long a single spoken command can run before the "
        "satellite gives up and sends what it has.",
        "float", min=5.0, max=60.0, unit="sec",
    ),
    FieldSpec(
        "listen.followup_pre_speech_timeout", "Follow-up wait", "Listening",
        "After the bot asks a question ('Did I get that right?'), how long "
        "to wait for your reply — with no wake word needed — before giving "
        "up and returning to wake-word listen.",
        "float", min=2.0, max=20.0, unit="sec",
    ),

    # ─── Noise gate ────────────────────────────────────────────────────
    FieldSpec(
        "noise_gate.dbfs", "Noise floor", "Noise gate",
        "Loudness floor (dBFS) — audio quieter than this is ignored even if "
        "it looks like speech. Lower = more permissive. With auto-calibrate "
        "on, this is only the boot-time fallback.",
        "float", min=-80.0, max=-10.0, unit="dBFS",
    ),
    FieldSpec(
        "noise_gate.auto_calibrate", "Auto-calibrate noise floor", "Noise gate",
        "Derive the floor from real room ambient instead of the fixed value. "
        "On for the HAT; off for the XVF3800 (its chip already levels the "
        "audio, so re-deriving a software gate fights its AGC).",
        "bool",
    ),

    # ─── Greeting ──────────────────────────────────────────────────────
    FieldSpec(
        "greeting.enabled", "Wake greeting", "Greeting",
        "Play a short spoken acknowledgment the instant the wake word fires. "
        "Needs hardware AEC — on by default for the XVF3800; turn OFF on a "
        "HAT or it bleeds into the capture and garbles the transcript.",
        "bool",
    ),
    FieldSpec(
        "greeting.funny_chance", "Funny-greeting chance", "Greeting",
        "Probability (0–1) that a wake greeting is a funny line rather than "
        "a plain one.",
        "float", min=0.0, max=1.0,
    ),

    # ─── Playback ──────────────────────────────────────────────────────
    FieldSpec(
        "playback.gain", "Voice loudness boost", "Playback",
        "Make-up loudness for the bot's voice + greetings (not music). "
        "Raise to 2–4 if Domovoi sounds quiet next to music; ~5 is the "
        "clip ceiling. 1.0 = no change.",
        "float", min=1.0, max=5.0,
    ),
    FieldSpec(
        "playback.tts_prebuffer_sec", "TTS pre-buffer", "Playback",
        "Seconds of the bot's voice buffered before playback starts. Higher "
        "resists network jitter (mid-word chop) at the cost of a little "
        "startup latency; 0 = immediate.",
        "float", min=0.0, max=2.0, unit="sec",
    ),

    # ─── Voice input / Display (video satellites) ──────────────────────
    FieldSpec(
        "mic.enabled", "Voice input", "Voice input",
        "Run the wake-word/VAD/capture stack. Off on mic-less video builds "
        "— the satellite still speaks, announces, and plays music. Turning "
        "this ON requires a supported microphone (and [audio] input device "
        "pinned to it) or the satellite fails at startup.",
        "bool",
    ),
    FieldSpec(
        "display.idle_mode", "Idle screen", "Display",
        "What a video satellite's screen shows when nothing is playing: a "
        "dimmed clock, a blank (black) screen, or the last cover art. "
        "Ignored on voice satellites.",
        "choice", choices=["clock", "blank", "art"],
    ),
    FieldSpec(
        "display.power_method", "Screen power mechanism", "Display",
        "How the display on/off toggle drives the panel: wlopm (Wayland/"
        "cage), xset (X11 DPMS), backlight (sysfs, DSI panels), auto tries "
        "each in that order, none disables the toggle. Ignored on voice "
        "satellites.",
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
        "Satellite log verbosity. DEBUG is very noisy; INFO is normal.",
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
    ),
    FieldSpec(
        "audio.output_mixer_control", "Volume mixer control", "Audio devices",
        "ALSA control name for the hardware volume (e.g. 'PCM'). Leave as-is "
        "unless your card uses a different control.",
        "str", section="advanced",
    ),
    FieldSpec(
        "music.alsa_device", "Music ALSA device", "Audio devices",
        "ALSA device mpg123 uses for music. Wrong = music routes to the "
        "wrong card or doesn't play. XVF3800: pin to the array.",
        "str", section="advanced",
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
        "int", section="advanced", min=1, max=64,
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
        "float", section="advanced", min=-70.0, max=-40.0, unit="dBFS",
    ),
    FieldSpec(
        "wifi.enabled", "WiFi self-heal", "WiFi",
        "Autonomous watchdog that reassociates the WiFi when the link wedges "
        "at a pathologically low rate (the 2026-05-06 TTS-chop cause). Needs "
        "the wpa_cli sudoers entry (PROVISIONING §6.7).",
        "bool", section="advanced",
    ),
    FieldSpec(
        "wifi.min_healthy_mbits", "WiFi reassociate threshold", "WiFi",
        "Reassociate when the rx rate drops below this (Mbit/s). 5 is well "
        "above what TTS needs but below healthy operation. Raise to catch "
        "borderline cases earlier; lower to avoid false positives.",
        "float", section="advanced", min=1.0, max=50.0, unit="Mbit/s",
    ),
]


FIELD_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in EDITABLE_FIELDS}
