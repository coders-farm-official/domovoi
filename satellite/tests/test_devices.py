"""Pure-logic tests for the satellite device-profile layer.

No hardware and no DB. The profile-resolution, channel-slice, and resampler
tests run anywhere numpy/scipy are installed. The Config-precedence tests
import the sounddevice-heavy client module and ``importorskip`` if the
satellite audio deps aren't installed on the host (e.g. a server dev
box that has numpy/scipy but not sounddevice).
"""

from __future__ import annotations

import numpy as np
import pytest

from satellite import devices
from satellite._resample import StreamingResampler


# ── Profile resolution ──────────────────────────────────────────────────

def test_resolve_known_profiles():
    hat = devices.resolve("respeaker_2mic_hat")
    xvf = devices.resolve("xvf3800_usb")

    # HAT = the original behavior.
    assert hat.led_backend == "apa102"
    assert hat.capture_dtype == "int16"
    assert hat.capture_select_channel is None
    assert hat.playback_sample_rate is None
    assert hat.mic_gain_enabled is True
    assert hat.noise_gate_auto_calibrate is True
    assert hat.noise_gate_dbfs == -45.0
    assert hat.vad_during_tts == 3

    # XVF = USB array with on-chip DSP.
    assert xvf.led_backend == "ws2812_xvf"
    assert xvf.capture_dtype == "int16"  # firmware enumerates S16_LE
    assert xvf.capture_channels == 2
    assert xvf.capture_select_channel == 1  # ASR beam
    assert xvf.playback_sample_rate == 16_000
    # On-chip AGC + noise suppression → these are off.
    assert xvf.mic_gain_enabled is False
    assert xvf.noise_gate_auto_calibrate is False


def test_default_profile_is_hat():
    assert devices.resolve(devices.DEFAULT_PROFILE).name == "respeaker_2mic_hat"


def test_video_kiosk_profile():
    """The Radxa video-kiosk profile: no mic, no LEDs, no mixer by default."""
    radxa = devices.resolve("radxa_zero3w_video")
    assert radxa.voice_capable is False
    assert radxa.leds_enabled_default is False
    assert radxa.supports_full_duplex is False
    assert radxa.mic_gain_enabled is False
    assert radxa.output_mixer_control is None
    assert radxa.leds_num == 0


def test_existing_profiles_keep_voice_defaults():
    """The new capability fields default so mic boards are untouched."""
    for name in ("respeaker_2mic_hat", "xvf3800_usb"):
        p = devices.resolve(name)
        assert p.voice_capable is True
        assert p.leds_enabled_default is True


def test_resolve_unknown_raises_with_valid_options():
    with pytest.raises(ValueError) as e:
        devices.resolve("respeaker_3mic_hat")  # typo
    msg = str(e.value)
    assert "respeaker_2mic_hat" in msg and "xvf3800_usb" in msg


# ── int32 2ch → int16 ASR-channel slice ─────────────────────────────────

def test_select_channel_int16_slices_not_downmixes():
    # The shipping XVF3800 USB firmware presents S16_LE 2ch. ch0
    # (Conference) loud, ch1 (ASR) a known set; selecting ch1 returns ONLY
    # ch1, no bleed/averaging from ch0, and no bit-shift (already int16).
    ch0 = np.array([10000, -20000, 30000], dtype=np.int16)
    ch1 = np.array([111, 32767, -333], dtype=np.int16)
    interleaved = np.empty(6, dtype=np.int16)
    interleaved[0::2] = ch0
    interleaved[1::2] = ch1

    out = devices.select_channel_int16(interleaved.tobytes(), "int16", channels=2, channel=1)
    got = np.frombuffer(out, dtype=np.int16)

    assert got.tolist() == ch1.tolist()  # ch0 did not bleed in


def test_select_channel_int32_shifts_to_int16():
    # int32 firmware variant: left-justified, arithmetic >>16 to int16.
    ch0 = np.array([1 << 30, -(1 << 30), 0], dtype=np.int32)
    ch1 = np.array([0x0012_0000, 0x7FFF_0000, -0x0008_0000], dtype=np.int32)
    interleaved = np.empty(6, dtype=np.int32)
    interleaved[0::2] = ch0
    interleaved[1::2] = ch1

    out = devices.select_channel_int16(interleaved.tobytes(), "int32", channels=2, channel=1)
    got = np.frombuffer(out, dtype=np.int16)

    assert got.tolist() == (ch1 >> 16).astype(np.int16).tolist()
    assert got.tolist() == [0x0012, 0x7FFF, -0x0008]


# ── Streaming resampler ──────────────────────────────────────────────────

def test_resampler_passthrough_same_rate():
    rs = StreamingResampler(16_000, 16_000)
    assert rs.passthrough
    pcm = np.array([1, 2, 3, 4], dtype=np.int16).tobytes()
    assert rs.process(pcm) == pcm
    assert rs.flush() == b""


def test_resampler_seam_continuity_24k_to_16k():
    """Streaming a sine through small chunks must match a single-shot
    resample — i.e. the FIR keeps continuity across chunk boundaries
    instead of restarting (which would click at every seam)."""
    sr_in, sr_out = 24_000, 16_000
    n = sr_in // 2  # 0.5 s
    t = np.arange(n)
    sig = (0.5 * 32767 * np.sin(2 * np.pi * 440.0 * t / sr_in)).astype(np.int16)

    # Reference: feed the whole signal as one chunk + flush.
    ref_rs = StreamingResampler(sr_in, sr_out)
    ref = np.frombuffer(ref_rs.process(sig.tobytes()) + ref_rs.flush(), dtype=np.int16)

    # Streamed: awkward 1000-sample chunks.
    rs = StreamingResampler(sr_in, sr_out)
    out = b""
    step = 1000
    for i in range(0, len(sig), step):
        out += rs.process(sig[i:i + step].tobytes())
    out += rs.flush()
    streamed = np.frombuffer(out, dtype=np.int16)

    # Drift-free: streamed output is the same length as the single-shot
    # resample and matches it sample-for-sample (within int16 rounding) —
    # no per-chunk filter restart, no boundary sample dropped/duplicated.
    assert len(streamed) == len(ref)
    assert int(np.max(np.abs(streamed.astype(np.int32) - ref.astype(np.int32)))) <= 1


def test_resampler_awkward_ratio_is_bounded_and_clickfree():
    # 22050 → 16000 has an exact ratio of 320/441; the resampler caps the
    # denominator so the FIR stays cheap. Output should be ~the right length
    # and free of large sample-to-sample jumps (a seam click would show one).
    sr_in, sr_out = 22_050, 16_000
    n = sr_in // 2
    sig = (0.3 * 32767 * np.sin(2 * np.pi * 220.0 * np.arange(n) / sr_in)).astype(np.int16)

    rs = StreamingResampler(sr_in, sr_out)
    out = b""
    step = 777
    for i in range(0, len(sig), step):
        out += rs.process(sig[i:i + step].tobytes())
    out += rs.flush()
    streamed = np.frombuffer(out, dtype=np.int16)

    # Roughly the resampled length (within a few %).
    assert abs(len(streamed) - int(n * sr_out / sr_in)) < n * 0.05
    # No click: consecutive-sample deltas stay well under full-scale.
    assert int(np.max(np.abs(np.diff(streamed.astype(np.int32))))) < 4000


# ── Config precedence (needs the satellite audio deps) ──────────────────

def _load_config(tmp_path, body: str):
    pytest.importorskip("sounddevice")
    pytest.importorskip("webrtcvad")
    pytest.importorskip("websockets")
    from satellite.client import Config

    p = tmp_path / "config.toml"
    p.write_text(body)
    return Config.load(p)


def test_config_applies_profile_defaults(tmp_path):
    cfg = _load_config(tmp_path, '[device]\nprofile = "xvf3800_usb"\n')
    assert cfg.device.name == "xvf3800_usb"
    assert cfg.device.led_backend == "ws2812_xvf"
    # Knob defaults sourced from the profile, not the old hard-coded literals.
    assert cfg.mic_gain_enabled is False
    assert cfg.noise_gate_auto_calibrate is False
    assert cfg.noise_gate_dbfs == -60.0
    assert cfg.vad_aggressiveness_during_tts == 2
    assert cfg.leds_num == 12


def test_config_explicit_value_overrides_profile(tmp_path):
    cfg = _load_config(
        tmp_path,
        '[device]\nprofile = "xvf3800_usb"\n'
        "[mic_gain]\nenabled = true\n"
        "[noise_gate]\ndbfs = -33.0\n",
    )
    assert cfg.mic_gain_enabled is True   # explicit TOML wins
    assert cfg.noise_gate_dbfs == -33.0   # explicit TOML wins


def test_config_default_profile_keeps_hat_behavior(tmp_path):
    cfg = _load_config(tmp_path, '[satellite]\nroom_id = "x"\n')
    assert cfg.device.name == "respeaker_2mic_hat"
    assert cfg.mic_gain_enabled is True
    assert cfg.noise_gate_dbfs == -45.0
    assert cfg.vad_aggressiveness_during_tts == 3
    assert cfg.leds_num == 3


def test_config_unknown_profile_raises(tmp_path):
    with pytest.raises(ValueError):
        _load_config(tmp_path, '[device]\nprofile = "bogus"\n')


def test_config_greeting_defaults(tmp_path):
    cfg = _load_config(tmp_path, '[satellite]\nroom_id = "x"\n')
    assert cfg.greeting_enabled is True
    assert cfg.greeting_funny_chance == 0.2


def test_config_greeting_override(tmp_path):
    cfg = _load_config(tmp_path, "[greeting]\nenabled = false\nfunny_chance = 0.5\n")
    assert cfg.greeting_enabled is False
    assert cfg.greeting_funny_chance == 0.5


def test_config_sounds_sync_default_and_override(tmp_path):
    assert _load_config(tmp_path, '[satellite]\nroom_id = "x"\n').sounds_sync_enabled is True
    assert _load_config(tmp_path, "[sounds]\nsync_enabled = false\n").sounds_sync_enabled is False


def test_config_voice_default_and_override(tmp_path):
    assert _load_config(tmp_path, '[satellite]\nroom_id = "x"\n').voice_name is None
    assert _load_config(tmp_path, '[voice]\nname = "Ryan"\n').voice_name == "Ryan"


def test_effective_voice_prefers_sidecar(tmp_path, monkeypatch):
    pytest.importorskip("sounddevice")
    pytest.importorskip("webrtcvad")
    pytest.importorskip("websockets")
    from satellite import client as sat

    cfg = _load_config(tmp_path, '[voice]\nname = "Aria"\n')
    sidecar = tmp_path / "voice"
    monkeypatch.setattr(sat, "VOICE_SIDECAR", sidecar)

    # No sidecar → config value.
    assert sat._effective_voice(cfg) == "Aria"
    # Sidecar present → it wins (a runtime "switch to Ryan").
    sidecar.write_text("Ryan\n")
    assert sat._effective_voice(cfg) == "Ryan"
    # Blank sidecar → falls back to config.
    sidecar.write_text("  \n")
    assert sat._effective_voice(cfg) == "Aria"
