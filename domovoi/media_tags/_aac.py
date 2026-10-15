"""Raw AAC in ADTS frames (ISO/IEC 13818-7): the stream's length."""

from __future__ import annotations

from typing import BinaryIO

_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def adts_header(b: bytes) -> tuple[int, int, int] | None:
    """(sample rate, frame length, samples) of the ADTS header in ``b``."""
    if len(b) < 7 or b[0] != 0xFF or (b[1] & 0xF6) != 0xF0:
        return None
    sr_index = (b[2] >> 2) & 0x0F
    if sr_index >= len(_RATES):
        return None
    length = ((b[3] & 0x03) << 11) | (b[4] << 3) | (b[5] >> 5)
    if length < 7:
        return None
    blocks = (b[6] & 0x03) + 1
    return _RATES[sr_index], length, 1024 * blocks


def adts_length(f: BinaryIO, start: int, size: int) -> float | None:
    """Seconds of audio in the ADTS stream at ``start``: every frame
    counted; when the stream breaks off, the rest estimated at the average
    frame size so far."""
    f.seek(start)
    pos = start
    frames = samples = 0
    rate = 0
    while pos + 7 <= size:
        f.seek(pos)
        head = adts_header(f.read(7))
        if head is None:
            break
        rate, length, n = head
        frames += 1
        samples += n
        pos += length
    if not frames or not rate:
        return None
    if pos < size - 128:
        samples = int(samples * (size - start) / (pos - start))
    return samples / rate
