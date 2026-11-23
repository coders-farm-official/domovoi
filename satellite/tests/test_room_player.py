"""The satellite's timed room player (satellite/room_player.py).

Unit tests run it against a fake sound device; the end-to-end test runs two
players against the real server pieces (``RoomPlayout`` and the
``/v1/room-audio`` socket, served by uvicorn on loopback) and checks that
both play the same sample at the same moment.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from satellite import room_player as R

try:  # the end-to-end test's server; module level so FastAPI resolves the annotation
    from fastapi import WebSocket
except ImportError:  # pragma: no cover
    WebSocket = None  # type: ignore[assignment,misc]

# ─── helpers ──────────────────────────────────────────────────────────────


def stereo(frames: int, value: int = 1000) -> bytes:
    return np.full((frames, 2), value, dtype="<i2").tobytes()


def audio(tl: int, first: int, pcm: bytes) -> bytes:
    return R.AUDIO_HDR.pack(1, 0, len(pcm) // 4, tl, first, 0) + pcm


def msg(**m) -> str:
    return json.dumps(m)


def converged(p: R.TimedPlayer, offset_us: float = 0.0) -> None:
    """16 pongs: server = local + offset_us."""
    for k in range(16):
        t = 1_000.0 * k
        p.on_text(msg(type="pong", t0=t, t1=t + 500 + offset_us, t2=t + 510 + offset_us), t + 1010)


class FakeStream:
    def __init__(self, sd: "FakeSD", kw: dict) -> None:
        self.sd, self.kw = sd, kw
        self.callback = kw["callback"]
        self.started = self.closed = False

    def start(self) -> None:
        self.started = True

    def abort(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakeSD:
    """``sounddevice`` as far as the room player uses it."""

    def __init__(self, rates=(48_000,), channels: int = 2, default_rate: int = 48_000) -> None:
        self.rates, self.channels, self.default_rate = rates, channels, default_rate
        self.streams: list[FakeStream] = []

    def query_devices(self, device, kind):
        return {"max_output_channels": self.channels, "default_samplerate": float(self.default_rate),
                "hostapi": 0}

    def query_hostapis(self, i=None):
        api = {"name": "fake", "default_output_device": -1}
        return [api] if i is None else api

    def OutputStream(self, **kw):  # noqa: N802 - sounddevice's name
        if kw["samplerate"] not in self.rates:
            raise RuntimeError("Invalid sample rate")
        s = FakeStream(self, kw)
        self.streams.append(s)
        return s


def tinfo(cur_s: float, dac_s: float):
    return SimpleNamespace(currentTime=cur_s, outputBufferDacTime=dac_s)


# ─── clock and timeline ───────────────────────────────────────────────────


def test_clock_fit_converges_after_16_and_tracks_offset_and_rate() -> None:
    fit = R.ClockFit()
    skew, off = 40e-6, 2_500_000.0
    for k in range(400):                          # 20 s at 20 Hz
        t0 = 1e6 + k * 50_000.0
        srv = t0 * (1 + skew) + off
        fit.add(t0, srv + 700, srv + 740, t0 + 1440)
        if k == 14:
            assert not fit.converged
    assert fit.converged
    t = 1e6 + 400 * 50_000.0
    assert abs(fit.to_server(t) - (t * (1 + skew) + off)) < 5
    assert abs(fit.b - skew) < 1e-6


def test_player_waits_for_the_fit_then_applies_gain_and_flush_at_their_time() -> None:
    p = R.TimedPlayer()
    p.on_text(msg(type="timeline", timeline_id=3, t0_us=0), 0)
    for k in range(50):
        p.on_binary(audio(3, 960 * k, stereo(960)), 0)
    assert not p.render(0, 480).any()              # no fit yet: silence
    converged(p)
    p.on_text(msg(type="gain", at_us=100_000, gain=0.5, ramp_ms=0), 0)
    p.on_text(msg(type="flush", at_us=500_000), 0)
    out = np.concatenate([p.render(10_000 * k, 480) for k in range(80)])[:, 0]
    assert set(out[:4800]) == {1000}
    assert set(out[4810:24000]) == {500}
    assert out[24000] == 500 and out[24000 + 239] < 10   # the 5 ms fade
    assert set(out[24240:]) == {0}


def test_a_new_timeline_drops_the_old_audio_and_late_chunks_count() -> None:
    p = R.TimedPlayer()
    converged(p)
    p.on_text(msg(type="timeline", timeline_id=1, t0_us=0), 0)
    p.on_binary(audio(1, 0, stereo(960)), 0)
    p.on_text(msg(type="timeline", timeline_id=2, t0_us=1_000_000), 0)
    p.on_binary(audio(1, 960, stereo(960)), 0)          # the old timeline: ignored
    assert not p.wants_output(0)
    p.on_binary(audio(2, 0, stereo(960)), 2_000_000)    # already played: late
    assert p.late == 1


def test_drift_is_corrected_one_sample_at_a_time() -> None:
    """A device clock 50 ppm fast: the player drops a sample now and then
    and stays within a few samples of due."""
    p = R.TimedPlayer()
    converged(p)
    p.on_text(msg(type="timeline", timeline_id=1, t0_us=0), 0)
    for k in range(400):                                  # 8 s
        p.on_binary(audio(1, 960 * k, stereo(960)), 0)
    dac = 0.0
    for k in range(780):
        p.render(dac, 480)
        dac += 10_000 * (1 + 50e-6)                      # buffers land later and later
        due = dac * R.RATE / 1e6
    assert p.corrections > 0 and p.jumps == 0
    assert abs(p.pos - due) < 4


# ─── the output ───────────────────────────────────────────────────────────


def _ready_player() -> R.TimedPlayer:
    p = R.TimedPlayer()
    converged(p)
    p.on_text(msg(type="timeline", timeline_id=1, t0_us=0), 0)
    ramp = (np.arange(48_000) % 2000).astype("<i2")
    pcm = np.stack([ramp, -ramp], axis=1).tobytes()
    for k in range(50):
        p.on_binary(audio(1, 960 * k, pcm[k * 3840:(k + 1) * 3840]), 0)
    return p


def test_output_downmixes_to_a_mono_device() -> None:
    sd = FakeSD(channels=1)
    out = R.Output(_ready_player(), sd=sd, clock=lambda: 0.0)
    assert out.open() and out.channels == 1
    buf = np.zeros((480, 1), dtype=np.int16)
    sd.streams[0].callback(buf, 480, tinfo(1.0, 1.0), None)
    assert not buf.any()                                  # (L + R) / 2 of a ramp and its negative


def test_output_decimates_for_a_16k_only_device() -> None:
    """The XVF3800's USB playback takes 16 kHz only: 48 kHz is refused, the
    output opens at 16 kHz and plays a third of the frames for the same time."""
    sd = FakeSD(rates=(16_000,), default_rate=16_000)
    p = _ready_player()
    t = {"us": 0.0}
    out = R.Output(p, sd=sd, clock=lambda: t["us"])
    assert out.open() and out.rate == 16_000 and out.factor == 3
    assert sd.streams[0].kw["blocksize"] == 160
    buf = np.zeros((160, 2), dtype=np.int16)
    for _ in range(10):
        sd.streams[0].callback(buf, 160, tinfo(t["us"] / 1e6, t["us"] / 1e6 + 0.02), None)
        t["us"] += 10_000
    assert buf.any() and out.errors == 0
    assert p.pos == round(0.02 * R.RATE) + 10 * 480       # 480 frames at 48 kHz per 160 played


def test_dac_times_are_smoothed_against_frames_played() -> None:
    rng = np.random.default_rng(1)
    s = R.DacSmoother()
    errs = []
    for k in range(400):
        true = 5e6 + k * 10_000.0
        got = s.add(480 * k, true + rng.normal(0, 1500))
        if k > 100:
            errs.append(abs(got - true))
    assert max(errs) < 600                       # 1.5 ms jitter in, well under in the fit
    for k in range(400, 403):                    # a jump: three in a row start the fit over
        got = s.add(480 * k, 5e6 + k * 10_000.0 + 50_000)
    assert abs(got - (5e6 + 402 * 10_000.0 + 50_000)) < 1


# ─── the room player's output gate ────────────────────────────────────────


def test_output_opens_only_with_audio_and_a_free_speaker_and_yields() -> None:
    sd = FakeSD()
    free = {"v": False}
    rp = R.RoomPlayer("ws://127.0.0.1:9/x", "kitchen", lambda: "tok", sd=sd,
                      device_free=lambda: free["v"], clock=lambda: 0.0)
    loop = threading.Thread(target=rp._output_loop, daemon=True)
    loop.start()
    try:
        time.sleep(0.1)
        assert not sd.streams                         # nothing to play yet
        rp.player.on_text(msg(type="timeline", timeline_id=1, t0_us=0), 0)
        converged(rp.player)
        for k in range(10):
            rp.player.on_binary(audio(1, 960 * k, stereo(960)), 0)
        time.sleep(0.1)
        assert not sd.streams                         # the speaker is busy
        free["v"] = True
        _wait(lambda: rp.playing)
        rp.release_output(hold_s=0.2)
        assert not rp.playing and sd.streams[0].closed
        _wait(lambda: rp.playing)                      # back after the hold
    finally:
        rp.stop_event.set()
        loop.join(2)


def _wait(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


# ─── end to end: two players, the real server pieces ──────────────────────


def ramp_source(frames: int) -> bytes:
    n = np.arange(frames)
    return np.stack([n % 30000 - 15000, n // 30000 + 1], axis=1).astype("<i2").tobytes()


class DrivenSD(FakeSD):
    """A device whose hardware plays 480-frame buffers at exactly 48 kHz of
    true time, LAT after it asks for each; every buffer played is kept with
    the true time its first frame left (perf_counter microseconds)."""

    def __init__(self, lat_s: float) -> None:
        super().__init__()
        self.lat = lat_s
        self.played: list[tuple[float, np.ndarray]] = []
        self.stop = threading.Event()

    def OutputStream(self, **kw):  # noqa: N802
        s = super().OutputStream(**kw)
        threading.Thread(target=self._drive, args=(s,), daemon=True).start()
        return s

    def _drive(self, s: FakeStream) -> None:
        start = time.perf_counter()
        k = 0
        buf = np.zeros((480, 2), dtype=np.int16)
        while not self.stop.is_set() and not s.closed:
            due = start + k * 0.01
            now = time.perf_counter()
            if now < due:
                time.sleep(min(0.002, due - now))
                continue
            dac = due + self.lat
            s.callback(buf, 480, tinfo(now, dac), None)
            self.played.append((dac * 1e6, buf.copy()))
            k += 1

    def index_at(self, t_us: float) -> int | None:
        for t0, pcm in reversed(self.played):
            if t0 <= t_us:
                j = int((t_us - t0) * R.RATE / 1e6)
                if j >= len(pcm):
                    return None
                lo, hi = int(pcm[j, 0]), int(pcm[j, 1])
                return None if lo == hi == 0 else (hi - 1) * 30000 + lo + 15000
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_two_players_against_the_real_server_play_the_same_sample_together() -> None:
    uvicorn = pytest.importorskip("uvicorn")
    from fastapi import FastAPI

    from domovoi.room_playout.clock import now_us as server_now
    from domovoi.room_playout.service import InMemoryRoomRegistry, RoomPlayout
    from domovoi.room_playout.session import RoomAudioSession
    from domovoi.room_playout.sources import BufferSource

    playout = RoomPlayout(InMemoryRoomRegistry({"kitchen": "kitchen", "kitchen_2": "kitchen"}))
    tokens = {"kitchen": "tok-a", "kitchen_2": "tok-b"}
    app = FastAPI()

    async def auth(sid: str, token: str | None) -> bool:
        return token is not None and tokens.get(sid) == token

    @app.websocket("/v1/room-audio/{satellite_id}")
    async def ra(ws: WebSocket, satellite_id: str) -> None:
        await RoomAudioSession(ws, satellite_id, playout, auth=auth).run()

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           ws="websockets"))
    loops: list[asyncio.AbstractEventLoop] = []

    async def serve() -> None:
        loops.append(asyncio.get_running_loop())
        await server.serve()

    srv = threading.Thread(target=asyncio.run, args=(serve(),), daemon=True)
    srv.start()
    _wait(lambda: server.started, 10)

    # Two devices: their own clocks (skewed, set wrong), their own output
    # latencies, the same machine's true time underneath.
    devices = []
    for sid, ppm, off, lat in (("kitchen", +30, 3e6, 0.020), ("kitchen_2", -30, -7.5e6, 0.045)):
        sd = DrivenSD(lat)
        k = 1 + ppm * 1e-6
        clock = (lambda k=k, off=off: R.now_us() * k + off)
        rp = R.RoomPlayer(f"ws://127.0.0.1:{port}/v1/room-audio/{sid}", sid,
                          lambda sid=sid: tokens[sid], sd=sd, clock=clock)
        rp.start()
        devices.append((rp, sd))
    try:
        for rp, _ in devices:
            _wait(lambda rp=rp: rp.connected.is_set() and rp.player.fit.converged, 10)
        fut = asyncio.run_coroutine_threadsafe(
            playout.play_stream("kitchen", BufferSource(ramp_source(R.RATE * 12))), loops[0])
        assert fut.result(5)
        st = playout.streamers["kitchen"]
        music_t0 = st.play_at(st.next_index)          # ramp index 0 plays here (server clock)
        time.sleep(7.0)
        now = server_now()
        diffs, errs = [], []
        probe = music_t0 + 1_500_000
        while probe < now - 300_000:
            ia, ib = (sd.index_at(probe) for _, sd in devices)
            assert ia is not None and ib is not None, (probe - music_t0)
            diffs.append(ia - ib)
            errs.append(ia - (probe - music_t0) * R.RATE / 1e6)
            probe += 97_003
        stats = {rp.satellite_id: rp.player.stats() for rp, _ in devices}
    finally:
        for rp, sd in devices:
            sd.stop.set()
            rp.stop()
        server.should_exit = True
        srv.join(5)
    print(f"\nroom sync, two players on loopback: |a-b| max {max(map(abs, diffs))} samples "
          f"(p50 {float(np.median(np.abs(diffs))):.1f}), |a-due| max {max(abs(e) for e in errs):.1f}; "
          f"{stats}")
    assert len(diffs) > 30
    # Target at the speakers: 2 ms (96 samples) for Pis and PCs. On one
    # machine with no network jitter the players should do far better.
    assert max(abs(d) for d in diffs) <= 24, diffs
    assert max(abs(e) for e in errs) <= 48, errs
    for rp, _ in devices:
        assert rp.player.late == 0 and rp.player.jumps == 0


# ─── spoken sounds on the timeline (slice 5b) ─────────────────────────────


def test_speech_spans_say_when_the_room_is_speaking_and_a_flush_cuts_them() -> None:
    p = R.TimedPlayer()
    p.on_text(msg(type="speech", start_us=50_000, end_us=90_000), 0)
    assert p.speech_playing(10_000) is False          # no clock yet: can't tell
    converged(p)                                      # server = local
    assert p.speech_playing(40_000) is True           # queued: a wake now is a barge-in
    assert p.speech_playing(60_000) is True
    assert p.speech_playing(95_000) is False          # over
    p.on_text(msg(type="speech", start_us=100_000, end_us=200_000), 0)
    p.on_text(msg(type="flush", at_us=120_000), 0)    # a barge-in cut it
    assert p.speech_playing(110_000) is True
    assert p.speech_playing(130_000) is False
    p.on_text(msg(type="speech", start_us=300_000, end_us=400_000), 0)
    p.on_text(msg(type="timeline", timeline_id=2, t0_us=0), 0)
    assert p.speech_playing(310_000) is False         # a new timeline forgets it


def test_seconds_until_a_server_time_needs_the_clock() -> None:
    rp = R.RoomPlayer("ws://192.168.1.20:6370/v1/room-audio/k_sat", "k_sat", lambda: "t",
                      sd=SimpleNamespace(), clock=lambda: 20_000.0)
    assert rp.seconds_until(500_000) is None
    converged(rp.player, offset_us=1_000.0)
    assert rp.seconds_until(521_000) == pytest.approx(0.5, abs=1e-3)
