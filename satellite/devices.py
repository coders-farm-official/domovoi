"""Device profiles — per-board hardware behavior for the satellite client.

The satellite runs on two physically different mic boards that need
genuinely different handling. Rather than scatter `if board == ...`
branches through `client.py`, each board is described once here as a
``DeviceProfile`` and selected by ``[device] profile`` in the config.

    respeaker_2mic_hat  — ReSpeaker 2-Mics Pi HAT (the original target).
                          I2S codec on ALSA card 0, stereo→mono downmix,
                          ALSA-PGA mic-gain tune, software noise gate,
                          3× APA102 LEDs over SPI, music via plughw:0,0.

    xvf3800_usb         — ReSpeaker XVF3800 USB 4-Mic Array. A USB Audio
                          Class board with on-chip AEC / 60 dB AGC /
                          beamforming / noise suppression / VAD. Its
                          2-channel capture firmware presents ch0 =
                          Conference, ch1 = ASR beam (we capture S16_LE
                          stereo and slice the ASR channel for STT). The
                          speaker hangs off the array's own 3.5mm/JST out
                          so the chip has its echo reference; playback
                          goes over USB and is resampled to the device's
                          fixed rate. 12× WS2812 LEDs are driven via the
                          ``xvf_host`` CLI, not SPI.

    radxa_zero3w_video  — Radxa Zero 3W video-kiosk build (screen +
                          speaker, no mic or LEDs by default). Voice is
                          off out of the box (``voice_capable=False`` →
                          ``[mic] enabled`` defaults false) and LED init
                          is skipped; audio goes out HDMI or a USB DAC.

A profile carries two kinds of data:

  * **Hardware switches** the code branches on (``capture_dtype``,
    ``capture_channels``, ``capture_select_channel``, ``led_backend``,
    ``playback_sample_rate``, ``led_xvf_host_path``). These are hardware
    truths, not user knobs — they live only on the profile object
    (``cfg.device``) and are read directly by ``_start_mic`` / the
    playback thread / LED init.

  * **Recommended defaults** for the existing user-tunable knobs
    (mic-gain, noise gate, barge-in, music device, LED brightness/count).
    These flow *through* ``Config.load`` as the fallback for each
    ``.get(key, default)`` so an explicit value in the user's TOML still
    wins, but a fresh config on the right board gets sane defaults.

On-chip AGC + noise suppression make the HAT's ALSA-PGA mic-gain tune a
no-op and the software noise gate counterproductive on the XVF3800, so
that profile disables both; on-chip AEC makes VAD barge-in reliable, so
``require_wake_word`` stays off and the during-TTS VAD doesn't need to be
cranked up to resist speaker echo.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    description: str

    # ── Hardware switches (code branches on these; not user knobs) ──────
    # PortAudio capture sample format. Both current boards present int16
    # (the XVF3800 USB firmware enumerates S16_LE 2ch 16 kHz). Kept as a
    # profile field because the slice/convert path also supports "int32"
    # (left-justified, arithmetic-shifted to int16) for firmware variants
    # that present 32-bit capture.
    capture_dtype: str
    # Channels to open on the input stream. HAT = 1 (PortAudio downmixes
    # the codec's stereo to mono). XVF3800 = 2 (ch0 Conference, ch1 ASR).
    capture_channels: int
    # When not None, the capture callback slices THIS channel index out of
    # a multi-channel frame instead of downmixing — the XVF3800's ASR beam
    # (ch1) is the processed/beamformed stream we want for STT; downmixing
    # it with the Conference channel would corrupt it. None = the stream is
    # already the mono signal we want (HAT).
    capture_select_channel: int | None
    # LED driver backend. "apa102" = per-pixel SPI (HAT); "ws2812_xvf" =
    # whole-ring effects via the xvf_host CLI (XVF3800).
    led_backend: str
    # When set, the playback thread opens the output device at THIS fixed
    # rate and resamples the server's TTS PCM to it (USB UAC devices only
    # accept their native rate). None = open at whatever rate the server
    # declares per response, as the HAT's plug device resamples for free.
    playback_sample_rate: int | None
    # Path to the xvf_host control binary (or "xvf_host" to resolve via
    # PATH). Only consulted by the ws2812_xvf LED backend.
    led_xvf_host_path: str
    # True when the board has on-chip acoustic echo cancellation, i.e. it
    # can capture while playing without the speaker howling into the mic
    # (full duplex). Required for two-way drop-in and open-mic
    # conversational modes. HAT = False (no AEC); XVF3800 = True. Reported
    # to the server in the hello frame so it can gate those modes
    # per room (a HAT room is refused rather than allowed to feed back).
    supports_full_duplex: bool

    # ── Recommended defaults for user-tunable knobs (flow via Config) ───
    mic_gain_enabled: bool
    mic_gain_card: int
    noise_gate_auto_calibrate: bool
    noise_gate_dbfs: float
    barge_require_wake_word: bool
    vad_during_tts: int
    music_alsa_device: str
    leds_num: int
    leds_brightness: int
    # ALSA mixer that the server's volume commands drive — the single
    # hardware output gain both TTS and music pass through. `card` is an
    # amixer `-c` value (index or name); `control` is the simple-control
    # name, or None to disable hardware volume on this board (the satellite
    # then just ignores set_volume frames). amixer accepts a card *name*,
    # so a name is reboot-proof against card-number shuffles.
    output_mixer_card: str
    output_mixer_control: str | None

    # ── Satellite-kind capabilities (defaulted so existing profiles are
    #    untouched) ─────────────────────────────────────────────────────
    # False when the board's default build has no microphone at all (the
    # video-kiosk Radxa profile): ``[mic] enabled`` then defaults off and
    # the client skips the wake-word/VAD/capture stack until the owner adds
    # a supported mic and flips the config key.
    voice_capable: bool = True
    # False when the board's default build has no LEDs wired (the
    # video-kiosk profile has a screen, not a ring) so LED init is skipped
    # without a config edit. An explicit ``[leds] enabled`` still wins.
    leds_enabled_default: bool = True


# The HAT profile reproduces the client's historical hard-coded defaults
# exactly — selecting it (or omitting [device] entirely) is a no-op change
# for every Pi already in the field.
_RESPEAKER_2MIC_HAT = DeviceProfile(
    name="respeaker_2mic_hat",
    description="ReSpeaker 2-Mics Pi HAT (I2S codec, APA102 SPI LEDs)",
    capture_dtype="int16",
    capture_channels=1,
    capture_select_channel=None,
    led_backend="apa102",
    playback_sample_rate=None,
    led_xvf_host_path="xvf_host",
    supports_full_duplex=False,   # no hardware AEC
    mic_gain_enabled=True,
    mic_gain_card=0,
    noise_gate_auto_calibrate=True,
    noise_gate_dbfs=-45.0,
    barge_require_wake_word=False,
    vad_during_tts=3,
    music_alsa_device="plughw:0,0",
    leds_num=3,
    leds_brightness=16,  # APA102 5-bit global brightness (0–31)
    # No hardware-volume control by default: HAT codecs differ (WM8960 vs
    # TLV320) and the control name varies, so a HAT user opts in by setting
    # [audio] output_mixer_control to their codec's playback control.
    output_mixer_card="0",
    output_mixer_control=None,
)

_XVF3800_USB = DeviceProfile(
    name="xvf3800_usb",
    description="ReSpeaker XVF3800 USB 4-Mic Array (on-chip DSP, WS2812 via xvf_host)",
    capture_dtype="int16",  # firmware enumerates S16_LE (verified on hardware)
    capture_channels=2,
    capture_select_channel=1,  # ch1 = ASR beam
    led_backend="ws2812_xvf",
    playback_sample_rate=16_000,
    led_xvf_host_path="xvf_host",
    supports_full_duplex=True,    # on-chip AEC → full duplex OK
    # On-chip 60 dB AGC owns input level — the ALSA-PGA tune has no control
    # to walk and would just no-op; disable it so boot is quiet and fast.
    mic_gain_enabled=False,
    mic_gain_card=0,
    # On-chip noise suppression already delivers clean, leveled audio; a
    # software gate that re-derives a threshold from it fights the chip.
    # Keep a permissive fixed floor so endpointing's VAD still runs but the
    # gate never rejects real speech.
    noise_gate_auto_calibrate=False,
    noise_gate_dbfs=-60.0,
    # On-chip AEC cancels the speaker echo, so VAD barge-in is reliable
    # without the wake-word gate and without cranking the during-TTS VAD.
    barge_require_wake_word=False,
    vad_during_tts=2,
    # USB card index varies per Pi — PROVISIONING pins this explicitly.
    # "default" routes to the ALSA default device as a last resort.
    music_alsa_device="default",
    leds_num=12,
    leds_brightness=200,  # WS2812 0–255 (xvf_host led_brightness; breath/rainbow only)
    # The array's USB DAC exposes a single 'PCM' playback control on card
    # 'Array' (amixer accepts the name). This is the master volume both TTS
    # and music ride through; the server's volume commands drive it.
    output_mixer_card="Array",
    output_mixer_control="PCM",
)


# The video-kiosk build: a Radxa Zero 3W driving a screen (HDMI/DSI) and a
# speaker (HDMI audio or a USB DAC). No mic board in the default build — the
# voice stack stays off until the owner adds a supported mic (e.g. an
# XVF3800 on the USB port, switching profile) or flips [mic] enabled with
# their own capture device pinned.
_RADXA_ZERO3W_VIDEO = DeviceProfile(
    name="radxa_zero3w_video",
    description="Radxa Zero 3W video kiosk (screen + speaker; mic optional)",
    capture_dtype="int16",
    capture_channels=1,
    capture_select_channel=None,
    led_backend="apa102",         # inert — leds_enabled_default=False below
    playback_sample_rate=None,
    led_xvf_host_path="xvf_host",
    supports_full_duplex=False,   # no AEC path on the bare board
    mic_gain_enabled=False,       # no ALSA-PGA to tune without a mic board
    mic_gain_card=0,
    noise_gate_auto_calibrate=False,
    noise_gate_dbfs=-45.0,
    barge_require_wake_word=False,
    vad_during_tts=3,
    # RK3566 HDMI/audio card naming varies by kernel; "default" routes via
    # the ALSA default device. VIDEO_SATELLITE.md documents pinning a
    # specific hw:X,Y or a USB DAC for speaker-quality output.
    music_alsa_device="default",
    leds_num=0,
    leds_brightness=0,
    # HDMI sinks expose no hardware mixer — set_volume is a no-op until a
    # USB-DAC owner opts in via [audio] output_mixer_card/control (the same
    # opt-in the 2-Mic HAT already uses).
    output_mixer_card="0",
    output_mixer_control=None,
    voice_capable=False,
    leds_enabled_default=False,
)


PROFILES: dict[str, DeviceProfile] = {
    _RESPEAKER_2MIC_HAT.name: _RESPEAKER_2MIC_HAT,
    _XVF3800_USB.name: _XVF3800_USB,
    _RADXA_ZERO3W_VIDEO.name: _RADXA_ZERO3W_VIDEO,
}

DEFAULT_PROFILE = _RESPEAKER_2MIC_HAT.name


def select_channel_int16(raw, capture_dtype: str, channels: int, channel: int) -> bytes:
    """Slice one channel out of an interleaved multi-channel capture frame
    and return it as mono int16.

    The XVF3800's 2-channel firmware interleaves ch0 (Conference) and ch1
    (ASR beam); we want only the ASR beam for STT, and downmixing it with
    Conference would corrupt the beamformed signal — so this slices, not
    averages. ``capture_dtype`` picks the conversion:

      * ``"int16"`` — the stream is already int16 (the USB firmware on the
        shipping arrays presents S16_LE, 2ch, 16 kHz): slice as-is.
      * ``"int32"`` — left-justified 32-bit samples: slice, then an
        arithmetic ``>> 16`` (sign-preserving) truncates to int16.

    Output is mono int16 bytes, exactly what the rest of the pipeline
    expects. ``raw`` is any buffer-protocol object (the PortAudio callback
    buffer or plain ``bytes``). Kept as a free function so it's
    unit-testable without importing the sounddevice-heavy client module.
    """
    import numpy as np

    if capture_dtype == "int32":
        arr = np.frombuffer(raw, dtype=np.int32).reshape(-1, channels)
        return (arr[:, channel] >> 16).astype(np.int16).tobytes()
    arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
    return arr[:, channel].tobytes()


def resolve(name: str) -> DeviceProfile:
    """Look up a device profile by name. Raises ValueError on an unknown
    name rather than silently falling back — a typo'd profile on the wrong
    board (HAT logic on the XVF, or vice versa) is a miserable thing to
    debug from symptoms, so fail loud at config-load time instead."""
    try:
        return PROFILES[name]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"unknown device profile {name!r}. Valid [device] profile "
            f"values: {valid}."
        ) from None
