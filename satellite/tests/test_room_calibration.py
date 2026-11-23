"""Speaker calibration on the satellite (satellite/room_calibration.py and
the room player's part, docs/ROOM_AUDIO_PROTOCOL.md §11, §5.5).

* finding a chirp in a noisy recording, to a fraction of a sample, and not
  finding one that isn't there;
* a whole step through a fake sound device: the chirp played through the
  room player's output at its stamped time, carried over a simulated room
  with a known acoustic delay into the mic tap, and answered with the
  server time it arrived;
* the server's ``latency``: the room's audio plays that much earlier.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
from scipy.signal import resample_poly

from satellite import room_calibration as C
from satellite import room_player as R

MIC_RATE = 16_000
SWEEP = (1000.0, 6000.0, 80.0)


def converged(p: R.TimedPlayer) -> None:
    """16 pongs: server time = local time."""
    for k in range(16):
        t = 1_000.0 * k
        p.on_text(json.dumps({"type": "pong", "t0": t, "t1": t + 500, "t2": t + 510}), t + 1010)


# ─── finding the chirp ────────────────────────────────────────────────────


def test_the_chirp_is_found_to_a_fraction_of_a_sample_in_noise() -> None:
    rng = np.random.default_rng(3)
    ref = C.chirp(MIC_RATE, *SWEEP)
    for true in (1234.0, 2000.4, 3100.75):
        # The chirp at a fractional position: built at 3x, shifted, decimated.
        hi = np.zeros(6000 * 3)
        start = int(round(true * 3))
        c3 = C.chirp(MIC_RATE * 3, *SWEEP)
        hi[start:start + len(c3)] = 0.05 * c3
        rec = resample_poly(hi, 1, 3) + rng.normal(0, 0.02, 6000)    # about -5 dB SNR per sample
        got, snr = C.find_arrival(rec, ref)
        assert got is not None and snr > C.MIN_SNR_DB
        assert abs(got - start / 3) < 0.35, (true, got)


def test_a_louder_late_reflection_does_not_win_over_the_direct_sound() -> None:
    ref = C.chirp(MIC_RATE, *SWEEP)
    rec = np.zeros(8000)
    rec[1000:1000 + len(ref)] += 0.4 * ref          # direct
    rec[1400:1400 + len(ref)] += 0.6 * ref          # a reflection 25 ms later, louder
    got, _ = C.find_arrival(rec, ref)
    assert abs(got - 1000) < 0.5


def test_noise_alone_is_not_heard() -> None:
    rng = np.random.default_rng(4)
    got, snr = C.find_arrival(rng.normal(0, 0.1, 8000), C.chirp(MIC_RATE, *SWEEP))
    assert got is None and snr < C.MIN_SNR_DB


# ─── a step through a fake sound device ───────────────────────────────────


class FakeStream:
    def __init__(self, kw: dict) -> None:
        self.callback = kw["callback"]

    def start(self) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class FakeSD:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []

    def query_devices(self, device, kind):
        return {"max_output_channels": 2, "default_samplerate": 48_000.0, "hostapi": 0}

    def query_hostapis(self, i=None):
        api = {"name": "fake", "default_output_device": -1}
        return [api] if i is None else api

    def OutputStream(self, **kw):  # noqa: N802 - sounddevice's name
        s = FakeStream(kw)
        self.streams.append(s)
        return s


class FakeTimer:
    made: list["FakeTimer"] = []

    def __init__(self, delay, fn, args=()) -> None:
        self.delay, self.fn, self.args = delay, fn, args
        self.daemon = False
        FakeTimer.made.append(self)

    def start(self) -> None: ...

    def fire(self) -> None:
        self.fn(*self.args)


API_LATENCY_S = 0.02            # what the audio API reports
HIDDEN_OUT_US = 3_000.0         # the path behind it, which calibration measures
MIC_PATH_US = 700.0             # the mic's own hidden path


def run_step(*, at_us: float = 300_000.0, play: bool = True, air_gain: float = 1.0,
             noise: float = 0.002, has_mic: bool = True) -> tuple[list[dict], R.TimedPlayer]:
    """One ``calibrate`` step on one device that hears itself."""
    t = {"us": 0.0}
    clock = lambda: t["us"]  # noqa: E731
    p = R.TimedPlayer()
    converged(p)
    sent: list[dict] = []
    FakeTimer.made = []
    cal = C.Calibrator(p, send=sent.append, has_mic=lambda: has_mic, clock=clock, timer=FakeTimer)
    sd = FakeSD()
    out = R.Output(p, sd=sd, clock=clock)
    assert out.open()
    cal.on_calibrate({"type": "calibrate", "run_id": "r1", "step": 0, "at_us": at_us,
                      "play": play, "listen_ms": 400,
                      "chirp": {"f0_hz": SWEEP[0], "f1_hz": SWEEP[1], "ms": SWEEP[2], "level": 0.3}})
    assert p.wants_output(0.0) is play
    # The speaker: 10 ms buffers; what it plays reaches the air
    # API_LATENCY + HIDDEN_OUT after the callback.
    total = 1_200_000
    air = np.zeros(int(total * 48_000 / 1e6) + 48_000)
    buf = np.zeros((480, 2), dtype=np.int16)
    while t["us"] < total - 100_000:
        stream = sd.streams[0]
        stream.callback(buf, 480, SimpleNamespace(currentTime=t["us"] / 1e6,
                                                  outputBufferDacTime=t["us"] / 1e6 + API_LATENCY_S),
                        None)
        start = int(round((t["us"] + API_LATENCY_S * 1e6 + HIDDEN_OUT_US) * 48_000 / 1e6))
        air[start:start + 480] += buf[:, 0] / 32768.0 * air_gain
        t["us"] += 10_000
    # The microphone: 30 ms blocks at 16 kHz, each heard MIC_PATH after the air.
    rng = np.random.default_rng(7)
    mic = resample_poly(air, 1, 3) + rng.normal(0, noise, len(air) // 3)
    shift = int(round(MIC_PATH_US * MIC_RATE / 1e6))
    mic = np.concatenate([np.zeros(shift), mic])
    n = 480
    for k in range(0, int(total * MIC_RATE / 1e6) - n, n):
        block = np.clip(mic[k:k + n] * 32767, -32768, 32767).astype(np.int16)
        first = k * 1e6 / MIC_RATE
        t["us"] = first + n * 1e6 / MIC_RATE + 1_000        # the callback runs a bit after
        cal.on_mic(block.tobytes(), SimpleNamespace(currentTime=t["us"] / 1e6,
                                                    inputBufferAdcTime=first / 1e6), n, MIC_RATE)
    if FakeTimer.made:
        FakeTimer.made[0].fire()
    return sent, p


def test_a_step_plays_the_chirp_on_time_and_hears_it_where_the_room_put_it() -> None:
    sent, _ = run_step()
    assert len(sent) == 1
    r = sent[0]
    assert r["type"] == "calibrate_result" and (r["run_id"], r["step"]) == ("r1", 0)
    assert r["played"] is True and r["heard"] is True
    # The answer is at_us + the hidden output path + the mic path: what the
    # server's solve takes apart.
    assert abs(r["arrival_us"] - (300_000 + HIDDEN_OUT_US + MIC_PATH_US)) < 80, r


def test_a_listening_step_does_not_play() -> None:
    sent, p = run_step(play=False)
    assert sent[0]["played"] is False and sent[0]["heard"] is False
    assert sent[0]["reason"] == "not_heard"


def test_a_chirp_lost_in_noise_is_reported_not_heard() -> None:
    sent, _ = run_step(air_gain=0.002, noise=0.05)
    assert sent[0]["heard"] is False and sent[0]["reason"] == "not_heard"
    assert "arrival_us" not in sent[0]


def test_no_mic_and_no_clock_answer_at_once() -> None:
    sent, _ = run_step(has_mic=False)
    assert sent == [{"type": "calibrate_result", "run_id": "r1", "step": 0, "played": False,
                     "heard": False, "reason": "no_mic"}]
    p = R.TimedPlayer()                                       # never converged
    out: list[dict] = []
    C.Calibrator(p, send=out.append, clock=lambda: 0.0).on_calibrate(
        {"type": "calibrate", "run_id": "r", "step": 1, "at_us": 1e6, "play": True})
    assert out[0]["reason"] == "no_clock" and not p._sounds


def test_a_sound_whose_time_passed_unplayed_is_dropped() -> None:
    p = R.TimedPlayer()
    converged(p)
    p.schedule_sound(np.zeros((480, 2), dtype=np.int16), 100_000)
    assert p.wants_output(0.0)
    assert not p.wants_output(2_000_000.0) and not p._sounds


# ─── the server's latency ─────────────────────────────────────────────────


def test_the_room_plays_earlier_by_the_calibrated_latency_but_a_chirp_does_not() -> None:
    p = R.TimedPlayer()
    converged(p)
    p.on_text(json.dumps({"type": "timeline", "timeline_id": 1, "t0_us": 0}), 0)
    ramp = (np.arange(48_000) % 30_000).astype("<i2")
    pcm = np.stack([ramp, ramp], axis=1)
    for k in range(50):
        p.on_binary(R.AUDIO_HDR.pack(1, 0, 960, 1, 960 * k, 0) + pcm[960 * k:960 * (k + 1)].tobytes(), 0)
    before = p.render(100_000, 480)[0, 0]
    p.on_text(json.dumps({"type": "latency", "output_latency_us": 10_000}), 0)
    assert p.output_latency_us == 10_000
    p.restart_position()
    after = p.render(100_000, 480)[0, 0]
    assert (before, after) == (4_800, 4_800 + 480)            # 10 ms = 480 samples ahead
    # A local sound at 120 ms lands at its API time, latency or not.
    p2 = R.TimedPlayer()
    converged(p2)
    p2.on_text(json.dumps({"type": "latency", "output_latency_us": 10_000}), 0)
    p2.schedule_sound(np.full((100, 2), 7, dtype=np.int16), 120_000)
    out = p2.render(100_000, 1440)[:, 0]
    assert out[959] == 0 and out[960] == 7 and out[1059] == 7 and out[1060] == 0


def test_the_hello_offers_calibration_and_a_reset_forgets_the_latency() -> None:
    rp = R.RoomPlayer("ws://192.168.1.20:6370/v1/room-audio/kitchen", "kitchen", lambda: "t",
                      sd=FakeSD())
    assert rp._hello()["supports"] == ["calibrate"]
    rp.player.on_text(json.dumps({"type": "latency", "output_latency_us": 5_000}), 0)
    rp.player.reset()
    assert rp.player.output_latency_us == 0
