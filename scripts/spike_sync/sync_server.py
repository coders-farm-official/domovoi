"""Room-sync spike: the server side (stands in for a core-side room streamer).

PROTOTYPE ONLY. One asyncio task produces 20 ms PCM chunks from a source
(a synthetic quiet signal, or an audio file decoded with pyminiaudio) and
stamps each with play_time = now + playout. Every connected client gets the
same chunks plus a time-sync (ping/pong) service on the same socket.

The server clock is time.perf_counter(): on one PC that is also the ground
truth the analysis measures against, so any client error shows up.

--jitter adds Wi-Fi-like random delay to the ping path (forward and return
independently), so the min-RTT filter is exercised and not just loopback.
"""
import argparse
import asyncio
import random
import time

import numpy as np

from proto import AUDIO_HDR, HELLO, PING, PONG, frame, read_frame, set_low_priority

FS = 48000
CHUNK = FS // 50  # 20 ms


def source(path: str | None):
    """Yield int16 mono chunks forever."""
    if path:
        import miniaudio

        dec = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16,
                                    nchannels=1, sample_rate=FS)
        pcm = np.frombuffer(dec.samples, dtype=np.int16)
    else:
        # Quiet (about -60 dBFS) tone with a click each second: real samples
        # flow through the path, nobody in the house hears it.
        t = np.arange(FS * 10) / FS
        pcm = (30 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        pcm[:: FS] = 60
    i = 0
    while True:
        if i + CHUNK > len(pcm):
            i = 0
        yield pcm[i:i + CHUNK]
        i += CHUNK


def net_delay(jitter: bool) -> float:
    if not jitter:
        return 0.0
    d = random.expovariate(1 / 0.003)  # mean 3 ms
    if random.random() < 0.05:  # occasional Wi-Fi stall
        d += random.uniform(0.03, 0.08)
    return d


class Server:
    def __init__(self, playout: float, jitter: bool, path: str | None):
        self.playout = playout
        self.jitter = jitter
        self.src = source(path)
        self.clients: set[asyncio.StreamWriter] = set()
        self.stream_t0 = None

    async def producer(self):
        # stream_t0: when sample 0 plays (server clock). Chunk k is sent at
        # stream_t0 + k*20ms - playout, i.e. stamped now + playout.
        self.stream_t0 = time.perf_counter() + self.playout + 0.5
        k = 0
        while True:
            send_at = self.stream_t0 + k * CHUNK / FS - self.playout
            delay = send_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            pcm = next(self.src)
            play_time = self.stream_t0 + k * CHUNK / FS
            msg = frame(b"A", AUDIO_HDR.pack(k * CHUNK, play_time) + pcm.tobytes())
            for w in list(self.clients):
                w.write(msg)
            k += 1

    async def pong(self, w, t0):
        await asyncio.sleep(net_delay(self.jitter))  # forward path
        t1 = time.perf_counter()
        t2 = time.perf_counter()
        await asyncio.sleep(net_delay(self.jitter))  # return path
        w.write(frame(b"Q", PONG.pack(t0, t1, t2)))

    async def handle(self, reader, writer):
        while self.stream_t0 is None:
            await asyncio.sleep(0.01)
        writer.write(frame(b"H", HELLO.pack(FS, CHUNK, self.stream_t0)))
        self.clients.add(writer)
        try:
            while True:
                kind, payload = await read_frame(reader)
                if kind == b"P":
                    (t0,) = PING.unpack(payload)
                    asyncio.create_task(self.pong(writer, t0))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(writer)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47650)
    ap.add_argument("--playout", type=float, default=0.5)
    ap.add_argument("--jitter", action="store_true")
    ap.add_argument("--file")
    a = ap.parse_args()
    set_low_priority()
    s = Server(a.playout, a.jitter, a.file)
    asyncio.create_task(s.producer())
    srv = await asyncio.start_server(s.handle, "127.0.0.1", a.port)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
