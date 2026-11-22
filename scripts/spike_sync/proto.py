"""Room-sync spike: wire format shared by sync_server.py and sync_client.py.

PROTOTYPE ONLY (1.5.0 spike, design-notes ROOMS-SEVERAL-SATELLITES.md §4).
Written from scratch under MIT; no Snapcast/Squeezelite code.

Frame: 1-byte type, 4-byte big-endian payload length, payload.

  H  server->client  hello   : fs u32, chunk_frames u32, stream_t0 f64
                               (server-clock time at which sample 0 plays)
  A  server->client  audio   : first_sample u64, play_time f64, int16 PCM mono
  P  client->server  ping    : t0 f64 (client clock)
  Q  server->client  pong    : t0 f64, t1 f64, t2 f64 (t1 receive, t2 send,
                               both server clock)

play_time is redundant with first_sample (play_time = stream_t0 +
first_sample/fs) and is sent so a client could also play a stream with gaps.
"""
import struct
import sys

HDR = struct.Struct(">cI")
HELLO = struct.Struct(">IId")
AUDIO_HDR = struct.Struct(">Qd")
PING = struct.Struct(">d")
PONG = struct.Struct(">ddd")


def frame(kind: bytes, payload: bytes) -> bytes:
    return HDR.pack(kind, len(payload)) + payload


async def read_frame(reader):
    hdr = await reader.readexactly(HDR.size)
    kind, n = HDR.unpack(hdr)
    return kind, await reader.readexactly(n)


def set_low_priority():
    """Below-normal priority, E-cores only on this PC (house rule)."""
    try:
        import psutil

        p = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
        n = psutil.cpu_count()
        if n and n > 16:
            p.cpu_affinity(list(range(16, n)))
    except Exception:
        pass
    if sys.platform == "win32":
        # 1 ms timer resolution, else asyncio sleeps round to ~15.6 ms.
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
