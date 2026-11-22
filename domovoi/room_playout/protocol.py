"""Room audio wire format, version 1 (docs/ROOM_AUDIO_PROTOCOL.md).

Text frames are JSON control messages; binary frames are audio chunks with
a 24-byte little-endian header. Everything here is pure encode/decode.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Any

PROTO = 1
SUPPORTED = (PROTO,)

#: A satellite id as the core takes it elsewhere (main.py ``_ROOM_ID_RE``).
SATELLITE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")

RATE = 48_000
CHANNELS = 2
CHUNK_FRAMES = 960          # 20 ms
BYTES_PER_FRAME = 2 * CHANNELS
CHUNK_BYTES = CHUNK_FRAMES * BYTES_PER_FRAME

KIND_AUDIO = 1
CODEC_PCM = 0
CODEC_OPUS = 1              # reserved, not sent in v1
CODEC_NAMES = {CODEC_PCM: "pcm_s16le", CODEC_OPUS: "opus"}

AUDIO_HDR = struct.Struct("<BBHIqq")
assert AUDIO_HDR.size == 24


@dataclass(frozen=True)
class AudioFrame:
    timeline_id: int
    first_sample: int
    play_at_us: int
    payload: bytes
    frames: int = CHUNK_FRAMES
    codec: int = CODEC_PCM


def encode_audio(f: AudioFrame) -> bytes:
    return AUDIO_HDR.pack(KIND_AUDIO, f.codec, f.frames, f.timeline_id,
                          f.first_sample, f.play_at_us) + f.payload


def decode_audio(data: bytes) -> AudioFrame | None:
    """None for a binary frame of another (future) kind; ValueError when
    it is too short to be one."""
    if len(data) < AUDIO_HDR.size:
        raise ValueError("binary frame shorter than its header")
    kind, codec, frames, tl, first, play_at = AUDIO_HDR.unpack_from(data)
    if kind != KIND_AUDIO:
        return None
    return AudioFrame(tl, first, play_at, bytes(data[AUDIO_HDR.size:]), frames, codec)


def play_time_us(t0_us: int, index: int, rate: int = RATE) -> float:
    """Server time sample ``index`` of a timeline plays at (exact)."""
    return t0_us + index * 1_000_000 / rate


def encode_msg(msg: dict[str, Any]) -> str:
    return json.dumps(msg, separators=(",", ":"))


def decode_msg(text: str) -> dict[str, Any] | None:
    """The message dict, or None when it is not a JSON object with a
    string ``type``."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    return msg


def timeline_msg(timeline_id: int, t0_us: int) -> dict[str, Any]:
    return {"type": "timeline", "timeline_id": timeline_id, "t0_us": t0_us,
            "rate": RATE, "channels": CHANNELS, "codec": CODEC_NAMES[CODEC_PCM],
            "chunk_frames": CHUNK_FRAMES}


def ready_msg(satellite_id: str, room_id: str, playout_ms: int, now_us: int) -> dict[str, Any]:
    return {"type": "ready", "proto": PROTO, "satellite_id": satellite_id,
            "room_id": room_id, "codec": CODEC_NAMES[CODEC_PCM], "rate": RATE,
            "channels": CHANNELS, "chunk_frames": CHUNK_FRAMES,
            "playout_ms": playout_ms, "server_now_us": now_us}


def error_msg(reason: str) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "error", "reason": reason}
    if reason == "proto_unsupported":
        msg["supported"] = list(SUPPORTED)
    return msg


def flush_msg(at_us: int) -> dict[str, Any]:
    return {"type": "flush", "at_us": int(at_us)}


def gain_msg(at_us: int, gain: float, ramp_ms: int) -> dict[str, Any]:
    return {"type": "gain", "at_us": int(at_us), "gain": float(gain), "ramp_ms": int(ramp_ms)}
