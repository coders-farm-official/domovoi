"""MPEG-1/2/2.5 audio (Layers I-III): finding the first frame and the
stream's length.

From ISO/IEC 11172-3 / 13818-3 frame headers, with the Xing/Info header
or the Fraunhofer VBRI header giving a variable-bitrate stream's frame
count; a constant bitrate stream's length comes from its size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from domovoi.media_tags._util import u32be

_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


@dataclass(frozen=True)
class FrameHeader:
    version: int        # 1, 2 or 25 (MPEG-2.5)
    layer: int          # 1, 2 or 3
    bitrate: int        # bits per second
    sample_rate: int
    padding: int
    channels: int
    protected: bool

    @property
    def samples(self) -> int:
        if self.layer == 1:
            return 384
        if self.layer == 2 or self.version == 1:
            return 1152
        return 576

    @property
    def length(self) -> int:
        if self.layer == 1:
            return (12 * self.bitrate // self.sample_rate + self.padding) * 4
        if self.layer == 3 and self.version != 1:
            return 72 * self.bitrate // self.sample_rate + self.padding
        return 144 * self.bitrate // self.sample_rate + self.padding


def parse_header(b: bytes) -> FrameHeader | None:
    """The frame header in the first four bytes of ``b``, or None."""
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    vbits = (b[1] >> 3) & 3
    lbits = (b[1] >> 1) & 3
    if vbits == 1 or lbits == 0:
        return None
    version = {0: 25, 2: 2, 3: 1}[vbits]
    layer = 4 - lbits
    br_index = b[2] >> 4
    sr_index = (b[2] >> 2) & 3
    if br_index in (0, 15) or sr_index == 3:
        return None
    table = (1 if version == 1 else 2, layer)
    bitrate = _BITRATES[table][br_index] * 1000
    rate = _RATES[version][sr_index]
    channels = 1 if (b[3] >> 6) == 3 else 2
    return FrameHeader(version, layer, bitrate, rate, (b[2] >> 1) & 1, channels, not (b[1] & 1))


def _consistent(a: FrameHeader, b: FrameHeader) -> bool:
    return (a.version, a.layer, a.sample_rate) == (b.version, b.layer, b.sample_rate)


def find_first_frame(f: BinaryIO, start: int, size: int, limit: int = 256 * 1024) -> tuple[int, FrameHeader] | None:
    """The first frame at or after ``start`` that the next frame confirms
    (or that ends the file), searching at most ``limit`` bytes."""
    f.seek(start)
    window = f.read(min(limit, max(0, size - start)) + 4)
    i = 0
    while True:
        i = window.find(b"\xff", i)
        if i < 0 or i + 4 > len(window):
            return None
        head = parse_header(window[i:i + 4])
        if head is not None and head.length > 4:
            nxt_at = start + i + head.length
            if nxt_at + 4 > size:
                return start + i, head
            f.seek(nxt_at)
            nxt = parse_header(f.read(4))
            if nxt is not None and _consistent(head, nxt):
                return start + i, head
        i += 1


def _side_info(head: FrameHeader) -> int:
    if head.version == 1:
        return 17 if head.channels == 1 else 32
    return 9 if head.channels == 1 else 17


def stream_length(f: BinaryIO, start: int, head: FrameHeader, audio_end: int) -> float | None:
    """The stream's length in seconds from its first frame at ``start``."""
    f.seek(start)
    frame = f.read(max(head.length, 200))
    # Xing / Info (LAME and most VBR encoders), where the side info ends.
    for off in (4 + _side_info(head), 4 + _side_info(head) + 2):
        tag = frame[off:off + 4]
        if tag in (b"Xing", b"Info") and len(frame) >= off + 8:
            flags = u32be(frame, off + 4)
            pos = off + 8
            frames = None
            if flags & 1:
                frames = u32be(frame, pos)
                pos += 4
            if flags & 2:
                pos += 4
            if flags & 4:
                pos += 100
            if flags & 8:
                pos += 4
            if frames is None:
                break
            # The whole frames' length, encoder delay and padding included
            # (the LAME extension's gapless trim is not applied): what the
            # library has always stored, so a re-index changes no row.
            return frames * head.samples / head.sample_rate
    # VBRI (Fraunhofer), 32 bytes after the header.
    if frame[36:40] == b"VBRI" and len(frame) >= 54:
        frames = u32be(frame, 50)
        return frames * head.samples / head.sample_rate
    # Constant bitrate.
    if head.bitrate <= 0:
        return None
    return max(0, audio_end - start) * 8 / head.bitrate
