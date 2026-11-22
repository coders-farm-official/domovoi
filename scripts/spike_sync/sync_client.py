"""Room-sync spike: the client side (stands in for a Pi/PC satellite player).

PROTOTYPE ONLY. Plays the server's timestamped chunks so each sample leaves
the output at its stamped server time:

* Clock: NTP-style ping/pong (4 timestamps) 4x a second. Keep the lowest-RTT
  10% of the last 60 s of samples and fit offset = a + b*t, so the rate
  error between this device's clock and the server's (ppm) is tracked too.
* Output: sounddevice (PortAudio, MIT; what the Pi client already uses).
  Each callback reports outputBufferDacTime: when the first frame of the
  buffer being filled reaches the DAC, on the stream clock (= this host's
  clock). That converts to server time through the fitted offset, giving
  the sample index that should be playing then.
* Drift: an EMA of (position - due index); past +-1 sample, insert or drop
  one sample in that callback. Past 50 ms: jump (resync).

--skew-ppm / --offset-ms simulate a device whose clock runs fast/slow and
is set wrong. Everything the client "knows" goes through that skewed
clock; the log keeps true times (perf_counter) for the analysis only.
"""
import argparse
import asyncio
from collections import deque
import time

import numpy as np
import sounddevice as sd

from proto import AUDIO_HDR, HELLO, PING, PONG, frame, read_frame, set_low_priority

RING = 48000 * 8


class SkewClock:
    def __init__(self, ppm: float, offset_ms: float):
        self.k = ppm * 1e-6
        self.off = offset_ms / 1000
        self.s0 = time.perf_counter()

    def from_sys(self, s: float) -> float:
        return s + self.off + (s - self.s0) * self.k

    def now(self) -> float:
        return self.from_sys(time.perf_counter())


class OffsetEstimator:
    """server_time ~= local + a + b*(local - tref), from low-RTT samples."""

    def __init__(self):
        self.samples = []  # (local_mid, offset, rtt)
        self.model = None  # (tref, a, b), swapped atomically

    def add(self, t0, t1, t2, t3):
        rtt = (t3 - t0) - (t2 - t1)
        off = ((t1 - t0) + (t2 - t3)) / 2
        mid = (t0 + t3) / 2
        self.samples.append((mid, off, rtt))
        self.samples = [s for s in self.samples if s[0] > mid - 60]
        arr = np.array(self.samples)
        keep = arr[arr[:, 2] <= np.quantile(arr[:, 2], 0.10)]
        if len(keep) < 4:
            keep = arr[np.argsort(arr[:, 2])[:4]]
        tref = mid
        span = keep[:, 0].max() - keep[:, 0].min()
        if len(keep) >= 4 and span > 5:
            b, a = np.polyfit(keep[:, 0] - tref, keep[:, 1], 1)
        else:
            a, b = float(np.median(keep[:, 1])), 0.0
        self.model = (tref, a, b)

    def to_server(self, local):
        tref, a, b = self.model
        return local + a + b * (local - tref)


class Player:
    def __init__(self, clock: SkewClock, est: OffsetEstimator, n_log: int):
        self.clock, self.est = clock, est
        self.ring = np.zeros(RING, dtype=np.int16)
        self.have = 0  # samples received: [0, have)
        self.fs = None
        self.stream_t0 = None
        self.pos = None
        self.ema = 0.0
        self.alpha = 0.02
        self.log = np.zeros((n_log, 5))  # dac_sys, pos, frames, action, raw_err
        self.n = 0
        self.underruns = 0
        # DAC timeline: reported DAC times jitter ~1.5 ms on WASAPI, but the
        # hardware eats frames at a steady rate, so fit dac = f(frames) over
        # the last few seconds and use the fit.
        self.consumed = 0
        self.hist = deque(maxlen=500)
        self.dac_fit = None

    def put(self, first, pcm):
        i = first % RING
        j = i + len(pcm)
        if j <= RING:
            self.ring[i:j] = pcm
        else:
            self.ring[i:] = pcm[: RING - i]
            self.ring[: j - RING] = pcm[RING - i:]
        self.have = first + len(pcm)

    def get(self, start, n):
        out = np.zeros(n, dtype=np.int16)
        lo, hi = max(start, 0, self.have - RING + 4800), min(start + n, self.have)
        if hi > lo:
            idx = np.arange(lo, hi) % RING
            out[lo - start: hi - start] = self.ring[idx]
        if start + n > self.have and start >= 0:
            self.underruns += 1
        return out

    def callback(self, outdata, frames, tinfo, status):
        if self.est.model is None or self.stream_t0 is None:
            outdata[:] = 0
            return
        dac_rep = tinfo.outputBufferDacTime
        self.hist.append((self.consumed, dac_rep))
        if len(self.hist) >= 50 and len(self.hist) and (self.n % 10 == 0 or self.dac_fit is None):
            h = np.array(self.hist)
            self.dac_fit = np.polyfit(h[:, 0] - self.consumed, h[:, 1], 1), self.consumed
        if self.dac_fit is not None:
            (k, c), at = self.dac_fit
            dac_sys = c + k * (self.consumed - at)
        else:
            dac_sys = dac_rep
        self.consumed += frames
        target = self.est.to_server(self.clock.from_sys(dac_sys))
        due = (target - self.stream_t0) * self.fs
        if self.pos is None:
            self.pos = round(due)
            self.ema = 0.0
        err = self.pos - due  # >0: playing ahead of time
        self.ema += self.alpha * (err - self.ema)
        action, n_src = 0, frames
        if abs(err) > 0.05 * self.fs:
            self.pos, self.ema, action = round(due), 0.0, 9
        elif self.ema > 1.0:
            action, n_src = 1, frames - 1  # insert (repeat) one sample
            self.ema -= 1.0
        elif self.ema < -1.0:
            action, n_src = -1, frames + 1  # drop one sample
            self.ema += 1.0
        src = self.get(self.pos, n_src)
        m = n_src // 2
        if action == 1:
            src = np.insert(src, m, src[m])
        elif action == -1:
            src = np.delete(src, m)
        outdata[:] = src[:, None]
        if self.n < len(self.log):
            self.log[self.n] = (dac_rep, self.pos, frames, action, err)
            self.n += 1
        self.pos += n_src


async def run(a):
    clock = SkewClock(a.skew_ppm, a.offset_ms)
    est = OffsetEstimator()
    player = Player(clock, est, n_log=int(a.duration * 400) + 1000)
    est_log = []
    reader, writer = await asyncio.open_connection("127.0.0.1", a.port)
    kind, payload = await read_frame(reader)
    assert kind == b"H"
    fs, _chunk, t0 = HELLO.unpack(payload)
    player.fs = fs

    async def pinger():
        while True:
            writer.write(frame(b"P", PING.pack(clock.now())))
            await asyncio.sleep(0.25)

    async def receiver():
        while True:
            kind, payload = await read_frame(reader)
            if kind == b"A":
                first, _play_time = AUDIO_HDR.unpack_from(payload)
                player.put(first, np.frombuffer(payload[AUDIO_HDR.size:], dtype=np.int16))
                if player.stream_t0 is None:
                    player.stream_t0 = t0
            elif kind == b"Q":
                t3 = clock.now()
                est.add(*PONG.unpack(payload), t3)
                s = time.perf_counter()
                # estimator error vs truth (server clock == perf_counter here)
                est_log.append((s, (est.to_server(clock.from_sys(s)) - s) * 1000,
                                est.model[2] * 1e6))

    tasks = [asyncio.create_task(pinger()), asyncio.create_task(receiver())]
    stream = sd.OutputStream(device=a.device, samplerate=fs, channels=2, dtype="int16",
                             blocksize=a.block, latency=a.latency, callback=player.callback)
    with stream:
        print(f"device={stream.device} latency={stream.latency}", flush=True)
        await asyncio.sleep(a.duration)
    for t in tasks:
        t.cancel()
    np.savez(a.out, log=player.log[: player.n], est=np.array(est_log),
             skew_ppm=a.skew_ppm, offset_ms=a.offset_ms, fs=fs, stream_t0=t0,
             underruns=player.underruns)
    print(f"saved {a.out}: {player.n} callbacks, underruns={player.underruns}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47650)
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--skew-ppm", type=float, default=0.0)
    ap.add_argument("--offset-ms", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--block", type=int, default=480)
    ap.add_argument("--latency", default="low")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    set_low_priority()
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
