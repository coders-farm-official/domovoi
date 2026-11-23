"""Synced room playout, slice 5a (docs/ROOM_AUDIO_PROTOCOL.md).

* the wire format round-trips;
* the clock fit finds offset and rate under skew and jitter, and converges
  within the 20 Hz burst;
* the streamer stamps chunks on time with a fake clock, mixes speech over
  ducked music, skips ahead after a stall, and times its controls;
* end to end: two simulated speakers with skewed clocks, jittery network and
  their own output latency play the same timeline sample for sample;
* the socket: hello, auth, ready/timeline, ping/pong, audio delivery.

No database: auth is injected.
"""

from __future__ import annotations

import asyncio
import heapq
import random
import time

import numpy as np
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from domovoi.room_playout import protocol as P
from domovoi.room_playout.clock import ClockFit
from domovoi.room_playout.ref_client import TimedPlayer
from domovoi.room_playout.service import InMemoryRoomRegistry, RoomPlayout
from domovoi.room_playout.session import RoomAudioSession
from domovoi.room_playout.sources import BufferSource
from domovoi.room_playout.streamer import RoomStreamer


class FakeClock:
    def __init__(self, t: float = 1_000_000_000.0) -> None:
        self.t = t

    def __call__(self) -> int:
        return int(self.t)


class ListSink:
    def __init__(self) -> None:
        self.audio: list[P.AudioFrame] = []
        self.controls: list[dict] = []

    def send_audio(self, frame: bytes) -> None:
        self.audio.append(P.decode_audio(frame))

    def send_control(self, msg: dict) -> None:
        self.controls.append(msg)


def stereo(frames: int, value: int = 1000) -> bytes:
    return np.full((frames, 2), value, dtype="<i2").tobytes()


# ─── protocol ──────────────────────────────────────────────────────────────


def test_audio_frame_round_trips() -> None:
    f = P.AudioFrame(7, 960 * 12345, 9_876_543_210, stereo(960))
    data = P.encode_audio(f)
    assert len(data) == 24 + P.CHUNK_BYTES
    assert P.decode_audio(data) == f


def test_unknown_binary_kind_is_ignored_and_short_frames_refused() -> None:
    assert P.decode_audio(bytes([9]) + bytes(23)) is None
    with pytest.raises(ValueError):
        P.decode_audio(b"\x01\x00")


def test_control_messages_decode_or_return_none() -> None:
    assert P.decode_msg(P.encode_msg(P.flush_msg(5))) == {"type": "flush", "at_us": 5}
    assert P.decode_msg("[1,2]") is None
    assert P.decode_msg("not json") is None
    assert P.error_msg("proto_unsupported")["supported"] == [1]


def test_play_time_is_exact_from_t0_and_index() -> None:
    assert P.play_time_us(1_000_000, 48_000) == 2_000_000
    assert P.play_time_us(0, 960) == 20_000


# ─── clock fit ─────────────────────────────────────────────────────────────


def _exchange(fit: ClockFit, true_t: float, skew: float, off: float,
              d1: float, d2: float) -> None:
    def local(t: float) -> float:
        return t * (1 + skew) + off
    t0 = local(true_t)
    t1 = true_t + d1
    t2 = t1 + 30
    fit.add(t0, t1, t2, local(t2 + d2))


@pytest.mark.parametrize("skew_ppm", [-50.0, 0.0, 50.0])
def test_clock_fit_tracks_offset_and_rate_under_jitter(skew_ppm: float) -> None:
    rng = random.Random(1)
    fit = ClockFit()
    skew, off = skew_ppm * 1e-6, 3_210_987.0
    t = 5_000_000.0
    for _ in range(4 * 60):     # one minute at 4 Hz
        d1 = 800 + rng.expovariate(1 / 3000) + (rng.uniform(3e4, 8e4) if rng.random() < .05 else 0)
        d2 = 800 + rng.expovariate(1 / 3000)
        _exchange(fit, t, skew, off, d1, d2)
        t += 250_000
    probe = t + 1_000_000
    err = fit.to_server(probe * (1 + skew) + off) - probe
    assert abs(err) < 150, err                       # µs
    assert abs(fit.b - (1 / (1 + skew) - 1)) < 10e-6  # within 10 ppm


def test_clock_fit_converges_in_the_burst_and_inverts() -> None:
    fit = ClockFit()
    t = 0.0
    for _ in range(16):
        assert not fit.converged
        _exchange(fit, t, 20e-6, -42_000.0, 1000, 1000)
        t += 50_000
    assert fit.converged
    local = t * (1 + 20e-6) - 42_000.0
    assert abs(fit.to_server(local) - t) < 30
    assert abs(fit.to_local(fit.to_server(local)) - local) < 1e-3


# ─── streamer with a fake clock ────────────────────────────────────────────


def test_chunks_are_stamped_playout_ahead_and_sent_on_time() -> None:
    clk = FakeClock()
    st = RoomStreamer("kitchen", 1, clock=clk, playout_ms=500)
    sink = ListSink()
    st.subscribe(sink)
    assert sink.controls[0] == P.timeline_msg(1, st.t0_us)
    st.play_stream(BufferSource(stereo(960 * 10)))
    first = st.next_index
    assert first % 960 == 0 and st.play_at(first) >= clk.t + 500_000
    assert st.tick() == 1           # the first chunk is due at once
    for _ in range(100):            # 100 ms more: five more chunks
        clk.t += 1000
        st.tick()
    assert [f.first_sample for f in sink.audio] == [first + 960 * k for k in range(6)]
    for f in sink.audio:
        assert f.play_at_us == int(st.play_at(f.first_sample))
        assert f.payload == stereo(960)
    for _ in range(1000):
        clk.t += 1000
        st.tick()
    assert len(sink.audio) == 10 and not st.active   # the source ended: a gap


def test_speech_mixes_over_ducked_music_and_reports_when_it_plays() -> None:
    clk = FakeClock()
    st = RoomStreamer("kitchen", 1, clock=clk)
    sink = ListSink()
    st.subscribe(sink)
    st.play_stream(BufferSource(stereo(960 * 50, 1000)))
    st.tick()
    speech = np.full(4800, 2000, dtype="<i2").tobytes()   # 100 ms mono at 48 kHz
    start, end = st.say(speech, 48_000, 1)
    assert start == st.play_at(sink.audio[-1].first_sample + 960)
    assert end - start == pytest.approx(100_000)
    for _ in range(300):
        clk.t += 1000
        st.tick()
    by_index = {f.first_sample: np.frombuffer(f.payload, "<i2") for f in sink.audio}
    s0 = st.index_at(start)
    assert set(by_index[s0]) == {300 + 2000}               # ducked music + speech
    assert set(by_index[s0 + 4800]) == {1000}              # speech over: music back


def test_speech_resamples_and_queues_after_the_last_sound() -> None:
    clk = FakeClock()
    st = RoomStreamer("hall", 1, clock=clk)
    s1 = st.say(np.zeros(22050, "<i2").tobytes(), 22_050, 1)   # 1 s at 22.05 kHz
    s2 = st.say(np.zeros(1600, "<i2").tobytes(), 16_000, 1)    # 0.1 s
    assert s1[1] - s1[0] == pytest.approx(1_000_000, abs=50)
    assert s2[0] == pytest.approx(s1[1])


def test_a_stall_past_the_cushion_skips_ahead_instead_of_sending_late() -> None:
    clk = FakeClock()
    st = RoomStreamer("den", 1, clock=clk)
    sink = ListSink()
    st.subscribe(sink)
    st.play_stream(BufferSource(stereo(960 * 500)))
    st.tick()
    clk.t += 2_000_000          # the loop was held for 2 s
    st.tick()
    assert st.late_skips == 1
    assert all(f.play_at_us >= clk.t for f in sink.audio[1:])


def test_timed_controls_reach_every_speaker_with_the_same_time() -> None:
    clk = FakeClock()
    st = RoomStreamer("den", 1, clock=clk)
    a, b = ListSink(), ListSink()
    st.subscribe(a)
    st.subscribe(b)
    st.play_stream(BufferSource(stereo(960 * 500)))
    st.tick()
    at = st.duck(0.25)
    assert at == int(clk.t) + 80_000
    stop_at = st.stop(at_us=clk.t + 100_000)
    for s in (a, b):
        assert s.controls[1:] == [P.gain_msg(at, 0.25, 50), P.flush_msg(stop_at)]
    assert not st.active


def test_a_late_joiner_gets_the_cushion_already_sent() -> None:
    clk = FakeClock()
    st = RoomStreamer("den", 1, clock=clk)
    st.play_stream(BufferSource(stereo(960 * 500)))
    for _ in range(1000):
        clk.t += 1000
        st.tick()
    late = ListSink()
    st.subscribe(late)
    assert len(late.audio) >= 20                       # ~400 ms still to play
    assert all(f.play_at_us > clk.t for f in late.audio)


def test_registry_stub_is_todays_house_until_assigned() -> None:
    reg = InMemoryRoomRegistry()
    assert reg.room_of("kitchen") == "kitchen" and reg.devices_in("kitchen") == ["kitchen"]
    reg.assign("kitchen_2", "kitchen")
    assert reg.devices_in("kitchen") == ["kitchen", "kitchen_2"]


# ─── end to end: two speakers, simulated time ─────────────────────────────


class SimSpeaker:
    """A device: its own skewed clock, a network path with jitter, and an
    output that plays 480-frame buffers LAT after it asks for them."""

    LAT_US = 12_000.0
    BLOCK = 480

    def __init__(self, sim: "Sim", skew_ppm: float, off_us: float, seed: int) -> None:
        self.sim = sim
        self.k = 1 + skew_ppm * 1e-6
        self.off = off_us
        self.rng = random.Random(seed)
        self.player = TimedPlayer()
        self.played: list[tuple[float, np.ndarray]] = []   # (true time of frame 0, pcm)

    def local(self, t: float) -> float:
        return t * self.k + self.off

    def delay(self) -> float:
        if not self.sim.jitter:
            return 1500.0
        return 800 + self.rng.expovariate(1 / 3000) + (
            self.rng.uniform(3e4, 8e4) if self.rng.random() < 0.05 else 0)

    # Sink, server side
    def send_audio(self, frame: bytes) -> None:
        self.sim.at(self.sim.t + self.delay(), lambda: self.player.on_binary(frame, self.local(self.sim.t)))

    def send_control(self, msg: dict) -> None:
        text = P.encode_msg(msg)
        self.sim.at(self.sim.t + self.delay(), lambda: self.player.on_text(text, self.local(self.sim.t)))

    def pinger(self) -> None:
        msg = self.player.ping(self.local(self.sim.t))
        if msg is not None:
            def server_side() -> None:
                t1 = self.sim.t
                pong = P.encode_msg({"type": "pong", "t0": msg["t0"], "t1": t1, "t2": t1 + 40})
                self.sim.at(t1 + 40 + self.delay(),
                            lambda: self.player.on_text(pong, self.local(self.sim.t)))
            self.sim.at(self.sim.t + self.delay(), server_side)
        self.sim.at(self.sim.t + 5000, self.pinger)

    def output(self) -> None:
        t_play = self.sim.t + self.LAT_US
        pcm = self.player.render(self.local(t_play), self.BLOCK)
        self.played.append((t_play, pcm))
        self.sim.at(self.sim.t + self.BLOCK / P.RATE * 1e6 / self.k, self.output)

    def index_playing(self, t: float) -> int | None:
        """The timeline index this speaker emits at true time ``t`` (from
        the ramp signal), or None when silent."""
        for t0, pcm in reversed(self.played):
            if t0 <= t:
                j = int((t - t0) * self.k * P.RATE / 1e6)
                if j >= len(pcm):
                    return None
                lo, hi = int(pcm[j, 0]), int(pcm[j, 1])
                if lo == 0 and hi == 0:
                    return None
                return (hi - 1) * 30000 + lo + 15000
        return None


class Sim:
    def __init__(self, jitter: bool) -> None:
        self.t = 1_000_000_000.0
        self.jitter = jitter
        self._q: list[tuple[float, int, object]] = []
        self._n = 0

    def at(self, t: float, fn) -> None:
        self._n += 1
        heapq.heappush(self._q, (t, self._n, fn))

    def run_until(self, t_end: float) -> None:
        while self._q and self._q[0][0] <= t_end:
            self.t, _, fn = heapq.heappop(self._q)
            fn()
        self.t = t_end


def ramp_source(frames: int) -> bytes:
    """Each frame encodes its own position: L = n % 30000 - 15000,
    R = n // 30000 + 1 (never both zero)."""
    n = np.arange(frames)
    return np.stack([n % 30000 - 15000, n // 30000 + 1], axis=1).astype("<i2").tobytes()


# The limits: the drift loop corrects past ±1 sample of smoothed error and
# its smoothing lags a ±50 ppm drift by about a sample, so two speakers
# drifting opposite ways sit a few samples apart, more in the first 10 s
# before the rate is fitted (12 samples = 0.25 ms). The spike's harsh
# Wi-Fi model (3 ms mean jitter each way, 5% stalls of 30-80 ms) adds
# clock-fit error; the spike measured 1.1 ms p95 / 1.6 ms max under it, and
# this allows 1.5 ms (72 samples). Targets at the speakers: 2 ms Pi/PC,
# 5 ms phones.
@pytest.mark.parametrize("jitter,limit_samples", [(False, 12), (True, 72)])
def test_two_speakers_play_the_same_sample_at_the_same_time(jitter: bool, limit_samples: int) -> None:
    sim = Sim(jitter)
    playout = RoomPlayout(InMemoryRoomRegistry({"kitchen": "kitchen", "kitchen_2": "kitchen"}),
                          clock=lambda: int(sim.t), run_tasks=False)
    a = SimSpeaker(sim, +50, 3_000_000.0, 1)
    b = SimSpeaker(sim, -50, -7_500_000.0, 2)
    for sid, spk in (("kitchen", a), ("kitchen_2", b)):
        assert playout.attach(sid, spk) == "kitchen"
        sim.at(sim.t, spk.pinger)
        sim.at(sim.t + 3000 * (spk is b), spk.output)
    st = playout.streamer("kitchen")

    def tick() -> None:
        st.tick()
        sim.at(sim.t + 5000, tick)
    sim.at(sim.t, tick)
    sim.run_until(sim.t + 1_500_000)          # clocks converge in the burst
    asyncio.run(playout.play_stream("kitchen", BufferSource(ramp_source(P.RATE * 40))))
    music_t0 = st.play_at(st.next_index)      # ramp index 0 plays here
    sim.run_until(sim.t + 30_000_000)

    diffs, errs = [], []
    probe = music_t0 + 1_000_000
    while probe < sim.t - 100_000:
        ia, ib = a.index_playing(probe), b.index_playing(probe)
        assert ia is not None and ib is not None, probe - music_t0
        diffs.append(ia - ib)
        due = (probe - music_t0) * P.RATE / 1e6
        errs.append(ia - due)
        probe += 97_003
    assert max(abs(d) for d in diffs) <= limit_samples, diffs
    assert max(abs(e) for e in errs) <= limit_samples, errs
    for spk in (a, b):
        assert spk.player.underruns == 0 and spk.player.late == 0 and spk.player.jumps == 0
        assert spk.player.corrections > 0      # ±50 ppm needs drift correction


def test_reference_player_applies_flush_and_gain_at_their_time() -> None:
    p = TimedPlayer()
    for k in range(16):                       # same clock both sides
        t = 1_000.0 * k
        p.on_text(P.encode_msg({"type": "pong", "t0": t, "t1": t + 500, "t2": t + 510}), t + 1010)
    p.on_text(P.encode_msg(P.timeline_msg(3, 0)), 0)
    for k in range(50):                       # one second of 1000s
        p.on_binary(P.encode_audio(P.AudioFrame(3, 960 * k, 20_000 * k, stereo(960))), 0)
    p.on_text(P.encode_msg(P.gain_msg(100_000, 0.5, 0)), 0)
    p.on_text(P.encode_msg(P.flush_msg(500_000)), 0)
    out = np.concatenate([p.render(10_000 * k, 480) for k in range(80)])[:, 0]
    assert set(out[: 4800]) == {1000}                  # before the gain
    assert set(out[4800 + 10: 24000]) == {500}         # ducked
    assert out[24000] == 500 and out[24000 + 239] < 10  # 5 ms fade from the flush
    assert set(out[24000 + 240:]) == {0}               # then nothing


# ─── the socket ────────────────────────────────────────────────────────────


def _app(playout: RoomPlayout, tokens: dict[str, str]) -> FastAPI:
    app = FastAPI()

    async def auth(sid: str, token: str | None) -> bool:
        return token is not None and tokens.get(sid) == token

    @app.websocket("/v1/room-audio/{satellite_id}")
    async def ra(ws: WebSocket, satellite_id: str) -> None:
        await RoomAudioSession(ws, satellite_id, playout, auth=auth).run()

    return app


def _hello(token: str | None = "tok", **extra) -> str:
    return P.encode_msg({"type": "hello", "proto": 1, "pairing_token": token, **extra})


def test_socket_hello_ready_timeline_pong_and_audio() -> None:
    playout = RoomPlayout(InMemoryRoomRegistry({"kitchen_2": "kitchen"}), run_tasks=False)
    client = TestClient(_app(playout, {"kitchen_2": "tok"}))
    with client.websocket_connect("/v1/room-audio/kitchen_2") as ws:
        ws.send_text(_hello())
        ready = P.decode_msg(ws.receive_text())
        assert ready["type"] == "ready" and ready["room_id"] == "kitchen"
        assert ready["rate"] == 48000 and ready["playout_ms"] == 500
        timeline = P.decode_msg(ws.receive_text())
        assert timeline["type"] == "timeline"
        ws.send_text(P.encode_msg({"type": "ping", "t0": 123}))
        pong = P.decode_msg(ws.receive_text())
        assert pong["type"] == "pong" and pong["t0"] == 123 and pong["t2"] >= pong["t1"]
        ws.send_text(P.encode_msg({"type": "stats", "err_us": 80, "junk": "x"}))
        assert playout.speakers("kitchen") == ["kitchen_2"]
        st = playout.streamers["kitchen"]
        st.say(np.zeros(960, "<i2").tobytes(), 48_000, 1)
        while not st.tick():       # the chunk is due within 20 ms
            time.sleep(0.002)
        # (the tick ran on this thread; the next ping wakes the sender)
        ws.send_text(P.encode_msg({"type": "ping", "t0": 1}))
        got = [ws.receive(), ws.receive()]
        assert [P.decode_msg(m["text"])["type"] for m in got if m.get("text")] == ["pong"]
        f = P.decode_audio(next(m["bytes"] for m in got if m.get("bytes")))
        assert f.timeline_id == timeline["timeline_id"] and f.frames == 960
    assert playout.stats["kitchen_2"] == {"err_us": 80}
    assert playout.speakers("kitchen") == []


@pytest.mark.parametrize("hello,reason", [
    (_hello("wrong"), "pairing_rejected"),
    (_hello(None), "pairing_rejected"),
    (_hello(proto=2), "proto_unsupported"),
    (_hello(satellite_id="other"), "bad_hello"),
    ('{"type":"ping","t0":1}', "bad_hello"),
])
def test_socket_refusals(hello: str, reason: str) -> None:
    playout = RoomPlayout(InMemoryRoomRegistry(), run_tasks=False)
    client = TestClient(_app(playout, {"kitchen": "tok"}))
    with client.websocket_connect("/v1/room-audio/kitchen") as ws:
        ws.send_text(hello)
        err = P.decode_msg(ws.receive_text())
        assert err["type"] == "error" and err["reason"] == reason
    assert playout.speakers("kitchen") == []


def test_core_serves_the_room_audio_route() -> None:
    from domovoi.main import app

    assert any(getattr(r, "path", None) == "/v1/room-audio/{satellite_id}" for r in app.routes)


def test_pairing_auth_refuses_a_missing_token_in_any_pairing_mode(monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.room_playout import session as S

    for strict in (False, True):
        monkeypatch.setattr(settings, "satellite_pairing_strict", strict)
        assert asyncio.run(S.pairing_auth("kitchen", None)) is False
        assert asyncio.run(S.pairing_auth("kitchen", "")) is False


# "kitchen-2" and "2kitchen" are ids the setup portal makes, so the stream
# socket takes them (domovoi/room_ids.py) and this socket does too.
@pytest.mark.parametrize("sid", ["Kitchen", "-kitchen", "kitchen.2", "k" * 33, "kitchen%0A"])
def test_socket_refuses_a_malformed_satellite_id(sid: str) -> None:
    playout = RoomPlayout(InMemoryRoomRegistry(), run_tasks=False)
    client = TestClient(_app(playout, {sid: "tok"}))
    with client.websocket_connect(f"/v1/room-audio/{sid}") as ws:
        ws.send_text(_hello())
        err = P.decode_msg(ws.receive_text())
        assert err["type"] == "error" and err["reason"] == "bad_hello"
    assert playout.streamers == {} and playout.speakers(sid) == []


def test_satellite_id_rule_matches_the_core() -> None:
    # One rule for device and room ids (domovoi/room_ids.py), so the
    # room-audio socket and the stream socket can't drift apart.
    from domovoi import room_ids

    assert P.is_satellite_id is room_ids.is_room_id


def test_idle_streamers_are_dropped_once_no_one_listens() -> None:
    playout = RoomPlayout(InMemoryRoomRegistry(), run_tasks=False)
    a, b = ListSink(), ListSink()
    for n in range(20):
        playout.attach(f"room_{n}", a)
        playout.detach(f"room_{n}")
    assert playout.streamers == {}

    # A room that still plays keeps its streamer until it is silent.
    playout.attach("kitchen", a)
    playout.attach("den", b)
    playout.say("kitchen", np.zeros(960, "<i2").tobytes(), 48_000, 1)
    playout.detach("kitchen")
    assert set(playout.streamers) == {"kitchen", "den"}
    playout.stop("kitchen")
    assert playout.reap_idle() == ["kitchen"]
    assert set(playout.streamers) == {"den"}      # den still has a listener
    playout.detach("den")
    assert playout.streamers == {}


def test_unsent_pongs_are_capped() -> None:
    """Pings keep arriving while nothing is sent (a stuck reader): the
    pongs waiting to go out stay bounded."""
    from domovoi.room_playout import session as S

    pings = 10 * S.MAX_PINGS_PER_S
    clock = iter(range(0, pings * 20_000 + 1, 20_000))   # 50 pings a second

    class FakeWs:
        n = 0

        async def receive(self) -> dict:
            self.n += 1
            if self.n > pings:
                return {"type": "websocket.disconnect"}
            return {"type": "websocket.receive",
                    "text": P.encode_msg({"type": "ping", "t0": self.n})}

    sess = S.RoomAudioSession(FakeWs(), "kitchen", RoomPlayout(run_tasks=False),  # type: ignore[arg-type]
                              clock=lambda: next(clock))
    asyncio.run(sess._recv_loop())
    assert len(sess._pongs) == S.MAX_PONGS
    assert sess._pongs[-1][0] == pings          # the newest are kept
