"""Advance widths from the bundled fonts, read by our own small TrueType
reader (``head``, ``hhea``, ``hmtx`` and ``cmap`` formats 4 and 12).

The phone's ``DraftMetrics.kt`` reads the same files the same way, so line
breaking (:mod:`.layout`) measures every character identically on the
server and the phone; no renderer's own text measuring (kerning, hinting,
rounding) is involved. Widths are font units; :data:`Font.units_per_em`
turns them into points.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache

from domovoi.drafts import FONTS_DIR

FONT_STEMS = ("LiberationSans-Regular", "LiberationSans-Bold", "LiberationSerif-Regular", "LiberationSerif-Bold")


@dataclass(frozen=True)
class Font:
    stem: str
    units_per_em: int
    #: code point -> glyph id
    cmap: dict[int, int]
    #: glyph id -> advance width (font units)
    advances: tuple[int, ...]

    def has(self, ch: str) -> bool:
        return self.cmap.get(ord(ch), 0) != 0

    def advance(self, ch: str) -> int:
        gid = self.cmap.get(ord(ch), 0)
        if gid >= len(self.advances):
            return self.advances[-1]
        return self.advances[gid]

    def width_units(self, text: str) -> int:
        return sum(self.advance(c) for c in text)


def _tables(data: bytes) -> dict[str, tuple[int, int]]:
    num = struct.unpack_from(">H", data, 4)[0]
    out = {}
    for i in range(num):
        tag, _check, off, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        out[tag.decode("latin-1")] = (off, length)
    return out


def _cmap(data: bytes, off: int) -> dict[int, int]:
    count = struct.unpack_from(">H", data, off + 2)[0]
    subs = []
    for i in range(count):
        plat, enc, sub_off = struct.unpack_from(">HHI", data, off + 4 + 8 * i)
        subs.append((plat, enc, off + sub_off))
    # Prefer the full Unicode table (3,10 format 12), then the BMP (3,1 format 4).
    chosen = None
    for want in ((3, 10), (0, 4), (3, 1), (0, 3)):
        for plat, enc, at in subs:
            if (plat, enc) == want:
                chosen = at
                break
        if chosen is not None:
            break
    if chosen is None:
        raise ValueError("no Unicode cmap")
    fmt = struct.unpack_from(">H", data, chosen)[0]
    out: dict[int, int] = {}
    if fmt == 4:
        segx2 = struct.unpack_from(">H", data, chosen + 6)[0]
        seg = segx2 // 2
        ends_at = chosen + 14
        starts_at = ends_at + segx2 + 2
        deltas_at = starts_at + segx2
        ranges_at = deltas_at + segx2
        for i in range(seg):
            end = struct.unpack_from(">H", data, ends_at + 2 * i)[0]
            start = struct.unpack_from(">H", data, starts_at + 2 * i)[0]
            delta = struct.unpack_from(">h", data, deltas_at + 2 * i)[0]
            ro = struct.unpack_from(">H", data, ranges_at + 2 * i)[0]
            for c in range(start, end + 1):
                if c == 0xFFFF:
                    continue
                if ro == 0:
                    gid = (c + delta) & 0xFFFF
                else:
                    at = ranges_at + 2 * i + ro + 2 * (c - start)
                    gid = struct.unpack_from(">H", data, at)[0]
                    if gid:
                        gid = (gid + delta) & 0xFFFF
                if gid:
                    out[c] = gid
    elif fmt == 12:
        groups = struct.unpack_from(">I", data, chosen + 12)[0]
        for i in range(groups):
            start, end, gid0 = struct.unpack_from(">III", data, chosen + 16 + 12 * i)
            for c in range(start, end + 1):
                out[c] = gid0 + (c - start)
    else:
        raise ValueError(f"cmap format {fmt} not read")
    return out


def parse(stem: str, data: bytes) -> Font:
    t = _tables(data)
    upem = struct.unpack_from(">H", data, t["head"][0] + 18)[0]
    n_hm = struct.unpack_from(">H", data, t["hhea"][0] + 34)[0]
    hmtx = t["hmtx"][0]
    num_glyphs = struct.unpack_from(">H", data, t["maxp"][0] + 4)[0]
    adv = [struct.unpack_from(">H", data, hmtx + 4 * i)[0] for i in range(n_hm)]
    # Glyphs past the last full metric share its advance.
    adv += [adv[-1]] * max(0, num_glyphs - n_hm)
    return Font(stem=stem, units_per_em=upem, cmap=_cmap(data, t["cmap"][0]), advances=tuple(adv))


@lru_cache(maxsize=None)
def font(stem: str) -> Font:
    if stem not in FONT_STEMS:
        raise KeyError(stem)
    return parse(stem, (FONTS_DIR / f"{stem}.ttf").read_bytes())


def font_path(stem: str) -> str:
    return str(FONTS_DIR / f"{stem}.ttf")
