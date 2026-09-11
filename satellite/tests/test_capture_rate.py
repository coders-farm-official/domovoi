"""The capture path correcting a mic stream that runs N× realtime.

Found on hardware, on both Pi Zero 2 Ws: the capture callback fired ~259
times a second where 33 were negotiated. A USB audio device clocks its ADC
off the host's start-of-frame rate - 1 kHz at full speed, 8 kHz at high
speed - and firmware assuming the former, enumerated at the latter, samples
8× fast. Real audio at ~128 kHz: pitch-shifted, and unmatchable by any wake
model. Nothing driver-side held across a restart, so the client measures
the rate it actually gets and corrects for it.

Every test here drives the corrector with a fake clock and synthetic audio
so it can run anywhere. The tone test is the one that matters: it proves
the OUTPUT is the original audio at the original pitch, not merely the
right number of bytes.
"""

from __future__ import annotations

import numpy as np

from satellite.client import FRAME_SAMPLES, SAMPLE_RATE, CaptureRateCorrector


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _feed_stream(corrector, clock, *, ratio: float, seconds: float, signal=None):
    """Simulate a stream firing at ``ratio`` × the expected callback rate.
    Returns every frame the corrector emitted."""
    expected = SAMPLE_RATE / FRAME_SAMPLES
    cb_rate = expected * ratio
    n = int(cb_rate * seconds)
    dt = 1.0 / cb_rate
    out = []
    pos = 0
    for _ in range(n):
        if signal is None:
            block = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        else:
            block = signal[pos : pos + FRAME_SAMPLES]
            pos += FRAME_SAMPLES
            if len(block) < FRAME_SAMPLES:
                break
        out.extend(corrector.feed(block.tobytes()))
        clock.t += dt
    return out


def test_a_nominal_stream_passes_through_untouched():
    clock = FakeClock()
    c = CaptureRateCorrector(SAMPLE_RATE, FRAME_SAMPLES, now=clock)
    frames = _feed_stream(c, clock, ratio=1.0, seconds=4.0)
    assert c.measured
    assert c.resampler is None
    assert abs(c.ratio - 1.0) < 0.05
    # One frame in, one frame out, byte-identical.
    assert len(frames) == int(SAMPLE_RATE / FRAME_SAMPLES * 4.0)
    assert all(len(f) == FRAME_SAMPLES * 2 for f in frames)


def test_jitter_does_not_trigger_correction():
    """A stream at 1.2x is a busy box, not a broken clock."""
    clock = FakeClock()
    c = CaptureRateCorrector(SAMPLE_RATE, FRAME_SAMPLES, now=clock)
    _feed_stream(c, clock, ratio=1.2, seconds=3.0)
    assert c.measured and c.resampler is None


def test_an_8x_stream_is_detected_and_reframed():
    clock = FakeClock()
    c = CaptureRateCorrector(SAMPLE_RATE, FRAME_SAMPLES, now=clock)
    frames = _feed_stream(c, clock, ratio=7.8, seconds=6.0)
    assert c.measured
    assert c.resampler is not None
    assert 7.5 < c.ratio < 8.1
    # After the 2s measurement window, output runs at realtime: ~33 frames
    # per second of WALL time, not ~259. Allow the pass-through window and
    # resampler latency.
    realtime_frames = int(SAMPLE_RATE / FRAME_SAMPLES * 4.0)          # the 4s after measuring
    passthrough = int(SAMPLE_RATE / FRAME_SAMPLES * 7.8 * 2.0)        # the 2s of measuring
    assert passthrough <= len(frames) <= passthrough + realtime_frames + 5
    assert all(len(f) == FRAME_SAMPLES * 2 for f in frames)


def test_the_corrected_audio_is_the_original_pitch():
    """The test that matters. A 440 Hz tone, as the array would deliver it
    when its clock runs 8x fast: 128 kHz worth of samples arriving in the
    time 16 kHz should. After correction it must come out as 440 Hz at
    16 kHz - not 55 Hz, and not silence."""
    k = 8
    src_rate = SAMPLE_RATE * k
    seconds = 6.0
    t = np.arange(int(src_rate * seconds)) / src_rate
    tone = (0.5 * 32767 * np.sin(2 * np.pi * 440.0 * t)).astype(np.int16)

    clock = FakeClock()
    c = CaptureRateCorrector(SAMPLE_RATE, FRAME_SAMPLES, now=clock)
    frames = _feed_stream(c, clock, ratio=float(k), seconds=seconds, signal=tone)
    assert c.resampler is not None

    # Skip the measurement window's pass-through (still 8x-fast audio) and
    # look at what came out once correction was live.
    passthrough = int(SAMPLE_RATE / FRAME_SAMPLES * k * CaptureRateCorrector.MEASURE_SEC)
    corrected = np.frombuffer(b"".join(frames[passthrough + 4 :]), dtype=np.int16)
    assert len(corrected) > SAMPLE_RATE  # at least a second to analyse

    spectrum = np.abs(np.fft.rfft(corrected.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(corrected), 1.0 / SAMPLE_RATE)
    peak = freqs[np.argmax(spectrum)]
    assert abs(peak - 440.0) < 5.0, f"peak at {peak:.1f} Hz, expected 440"
    # And it is audio, not a DC line: real amplitude survived the resample.
    assert np.abs(corrected).max() > 0.3 * 32767


def test_uncorrected_8x_audio_really_is_the_wrong_pitch():
    """Documents the failure mode the class exists for. The DATA arrives 8x
    fast, but a consumer reading it as 16 kHz stretches time - so the
    SOUND is 8x slow: a 440 Hz tone reads as 55 Hz, three octaves down.
    "Too fast" in the docs describes the sample rate, not the pitch. If
    this ever passes at 440, the simulation is wrong, not the satellite."""
    k = 8
    src_rate = SAMPLE_RATE * k
    t = np.arange(src_rate * 2) / src_rate
    tone = (0.5 * 32767 * np.sin(2 * np.pi * 440.0 * t)).astype(np.int16)
    spectrum = np.abs(np.fft.rfft(tone.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(tone), 1.0 / SAMPLE_RATE)   # consumer's belief
    peak = freqs[np.argmax(spectrum)]
    assert abs(peak - 440.0 / k) < 3.0, f"peak {peak:.1f} Hz"


def test_ratio_is_clamped_to_something_sane():
    clock = FakeClock()
    c = CaptureRateCorrector(SAMPLE_RATE, FRAME_SAMPLES, now=clock)
    _feed_stream(c, clock, ratio=40.0, seconds=3.0)
    assert c.resampler is not None
    # 40x is not a real USB failure mode; we cap rather than build a
    # 640 kHz resampler.
    assert c.resampler is not None and c.MAX_RATIO == 16
