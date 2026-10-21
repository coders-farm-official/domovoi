"""PWG Raster (PWG 5102.4-2012) and Apple raster / URF encoders. Ours.

Every IPP Everywhere printer must accept PWG Raster; AirPrint-only ones
take URF. Both are a sync word, then per page a header and the bitmap,
each line compressed with a line-repeat byte and PackBits-style runs over
whole pixels.

* PWG Raster: ``RaS2``, then a 1,796-byte big-endian header per page
  (Table 1 of PWG 5102.4), then the bitmap.
* URF, the layout CUPS's Apache-2.0 ``cups/raster-stream.c`` writes in its
  Apple mode: ``UNIRAST\\0`` and a big-endian page count, then a 32-byte
  page header (bits per pixel, colour space, duplex, quality, then width,
  height and dpi at offsets 12, 16 and 20).

Pages are written one at a time (:class:`PwgWriter`, :class:`UrfWriter`),
so a long job never sits in memory whole. The page's pixel size must be
exactly PageSize × dpi / 72: PDFium rounds its bitmap up (6,601 rows for
11 in at 600 dpi), and the drawing code crops (P0, 2026-10-21).

Two-sided back pages follow the printer's ``pwg-raster-document-sheet-back``
(:func:`back_transform`): the bitmap is flipped or rotated as the printer
expects, and the PWG header says so in CrossFeedTransform / FeedTransform.

:func:`validate_pwg` / :func:`validate_urf` are an independent strict reader
(every header field against Table 1, every line decoded, no 0x80, no
overrun); the tests and the Linux harness use them.

Standard library only.
"""

from __future__ import annotations

import hashlib
import itertools
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterable

PWG_HEADER = 1796
CS_SGRAY, CS_SRGB = 18, 19
TYPES = {"srgb_8": (CS_SRGB, 3), "sgray_8": (CS_SGRAY, 1)}
URF_CS = {CS_SGRAY: 0, CS_SRGB: 1}
PAGE_NAMES = {(612, 792): "na_letter_8.5x11in", (595, 842): "iso_a4_210x297mm",
              (612, 1008): "na_legal_8.5x14in"}


@dataclass
class Page:
    w_pt: int
    h_pt: int
    width: int
    height: int
    bpp: int          # bytes per pixel: 1 (grey) or 3 (RGB)
    data: bytes


# ─── Line codec ───────────────────────────────────────────────────────────


def encode_line(line: bytes, bpp: int) -> bytes:
    """Runs in whole pixels: 1-128 repeats -> (count-1, pixel); 2-128
    literals -> (257-count, pixels). Never emits 0x80."""
    n = len(line) // bpp
    out = bytearray()
    if n == 0:
        return b""
    first_px = line[:bpp]
    if line == first_px * n:
        # The commonest line on a page: all one colour (white margins).
        left = n
        while left > 0:
            k = min(left, 128)
            out.append(k - 1)
            out += first_px
            left -= k
        return bytes(out)
    pixels: list = list(line) if bpp == 1 else [line[i:i + bpp] for i in range(0, n * bpp, bpp)]
    as_bytes = (lambda px: bytes((px,))) if bpp == 1 else (lambda px: px)
    lits: list = []

    def flush() -> None:
        while lits:
            chunk = lits[:128]
            del lits[:128]
            out.append(0 if len(chunk) == 1 else 257 - len(chunk))
            out.extend(bytes(chunk) if bpp == 1 else b"".join(chunk))

    for value, grp in itertools.groupby(pixels):
        count = sum(1 for _ in grp)
        if count == 1:
            lits.append(value)
            continue
        flush()
        while count > 0:
            k = min(count, 128)
            if k == 1:
                lits.append(value)
                break
            out.append(k - 1)
            out += as_bytes(value)
            count -= k
    flush()
    return bytes(out)


def encode_bitmap(data: bytes, width: int, height: int, bpp: int, sink: BinaryIO) -> None:
    bpl = width * bpp
    y = 0
    while y < height:
        line = data[y * bpl:(y + 1) * bpl]
        rep = 1
        while rep < 256 and y + rep < height and data[(y + rep) * bpl:(y + rep + 1) * bpl] == line:
            rep += 1
        sink.write(bytes([rep - 1]))
        sink.write(encode_line(line, bpp))
        y += rep


# ─── Back sides ───────────────────────────────────────────────────────────


def back_transform(sheet_back: str | None, sides: str) -> tuple[int, int]:
    """(CrossFeedTransform, FeedTransform) for a BACK side, the way CUPS
    sets them from ``pwg-raster-document-sheet-back``. (1, 1) is no change;
    -1 mirrors that axis (both -1 is a 180° turn)."""
    tumble = sides == "two-sided-short-edge"
    sb = (sheet_back or "normal").lower()
    if sb == "flipped":
        return (-1, 1) if tumble else (1, -1)
    if sb == "manual-tumble":
        return (-1, -1) if tumble else (1, 1)
    if sb == "rotated":
        return (1, 1) if tumble else (-1, -1)
    return (1, 1)


def transform_page(page: Page, cross: int, feed: int) -> Page:
    """The page's bitmap mirrored across the axes marked -1."""
    if (cross, feed) == (1, 1):
        return page
    bpl = page.width * page.bpp
    rows = [page.data[y * bpl:(y + 1) * bpl] for y in range(page.height)]
    if feed == -1:
        rows.reverse()
    if cross == -1:
        b = page.bpp
        rows = [
            b"".join(r[x * b:(x + 1) * b] for x in range(page.width - 1, -1, -1)) if b > 1 else r[::-1]
            for r in rows
        ]
    return Page(page.w_pt, page.h_pt, page.width, page.height, page.bpp, b"".join(rows))


# ─── PWG Raster ───────────────────────────────────────────────────────────


def _cstr(s: str) -> bytes:
    b = s.encode("ascii", "replace")[:63]
    return b + b"\x00" * (64 - len(b))


def pwg_header(
    page: Page, dpi: int, typ: str, total_pages: int, *,
    duplex: bool = False, tumble: bool = False, transform: tuple[int, int] = (1, 1),
    copies: int = 1,
) -> bytes:
    cs, ncolors = TYPES[typ]
    if page.bpp != ncolors:
        raise ValueError("page bytes-per-pixel doesn't match %s" % typ)
    h = bytearray(PWG_HEADER)
    h[0:64] = _cstr("PwgRaster")
    struct.pack_into("!I", h, 272, 1 if duplex else 0)        # Duplex
    struct.pack_into("!II", h, 276, dpi, dpi)                # HWResolution
    struct.pack_into("!I", h, 340, max(1, copies))           # NumCopies
    struct.pack_into("!II", h, 352, page.w_pt, page.h_pt)    # PageSize
    struct.pack_into("!I", h, 368, 1 if tumble else 0)       # Tumble
    struct.pack_into("!II", h, 372, page.width, page.height)
    struct.pack_into("!III", h, 384, 8, 8 * ncolors, page.width * ncolors)
    struct.pack_into("!II", h, 396, 0, cs)                   # ColorOrder chunky, ColorSpace
    struct.pack_into("!I", h, 420, ncolors)                  # NumColors
    struct.pack_into("!I", h, 452, total_pages)              # TotalPageCount
    struct.pack_into("!ii", h, 456, transform[0], transform[1])
    struct.pack_into("!I", h, 480, 0x00FFFFFF)               # AlternatePrimary (white)
    h[1732:1796] = _cstr(PAGE_NAMES.get((page.w_pt, page.h_pt), ""))
    return bytes(h)


class PwgWriter:
    def __init__(self, sink: BinaryIO, *, dpi: int, typ: str, total_pages: int, sides: str = "one-sided",
                 sheet_back: str | None = None, copies: int = 1) -> None:
        self.sink, self.dpi, self.typ, self.total = sink, dpi, typ, total_pages
        self.sides, self.sheet_back, self.copies = sides, sheet_back, copies
        self.index = 0
        # How a back page's pixels are mirrored: pure Python here (the
        # harness container has no Pillow); the render worker swaps in a
        # Pillow transpose, which is hundreds of times faster at 600 dpi.
        self.transformer = transform_page
        sink.write(b"RaS2")

    def write(self, page: Page) -> None:
        duplex = self.sides != "one-sided"
        back = duplex and self.index % 2 == 1
        transform = back_transform(self.sheet_back, self.sides) if back else (1, 1)
        if back:
            page = self.transformer(page, *transform)
        self.sink.write(pwg_header(
            page, self.dpi, self.typ, self.total, duplex=duplex,
            tumble=self.sides == "two-sided-short-edge", transform=transform, copies=self.copies,
        ))
        encode_bitmap(page.data, page.width, page.height, page.bpp, self.sink)
        self.index += 1


# ─── URF ──────────────────────────────────────────────────────────────────

URF_DUPLEX = {"one-sided": 1, "two-sided-long-edge": 2, "two-sided-short-edge": 3}


def urf_header(page: Page, dpi: int, *, sides: str = "one-sided", quality: int = 4) -> bytes:
    h = bytearray(32)
    h[0] = 8 * page.bpp
    h[1] = URF_CS[CS_SRGB if page.bpp == 3 else CS_SGRAY]
    h[2] = URF_DUPLEX.get(sides, 1)
    h[3] = quality
    struct.pack_into("!III", h, 12, page.width, page.height, dpi)
    return bytes(h)


class UrfWriter:
    def __init__(self, sink: BinaryIO, *, dpi: int, total_pages: int, sides: str = "one-sided",
                 quality: int = 4) -> None:
        self.sink, self.dpi, self.sides, self.quality = sink, dpi, sides, quality
        sink.write(b"UNIRAST\x00" + struct.pack("!I", total_pages))

    def write(self, page: Page) -> None:
        self.sink.write(urf_header(page, self.dpi, sides=self.sides, quality=self.quality))
        encode_bitmap(page.data, page.width, page.height, page.bpp, self.sink)


# ─── Validation (an independent strict reader) ───────────────────────────


def decode_bitmap(buf: bytes, off: int, width: int, height: int, bpp: int, problems: list[str]) -> tuple[int, bytes]:
    bpl = width * bpp
    out = bytearray()
    y = 0
    while y < height:
        if off >= len(buf):
            problems.append("truncated at line %d" % y)
            return off, bytes(out)
        rep = buf[off] + 1
        off += 1
        line = bytearray()
        while len(line) < bpl:
            if off >= len(buf):
                problems.append("truncated inside line %d" % y)
                return off, bytes(out)
            c = buf[off]
            off += 1
            if c == 128:
                problems.append("line %d uses 0x80" % y)
                line += b"\xff" * (bpl - len(line))
            elif c < 128:
                line += buf[off:off + bpp] * (c + 1)
                off += bpp
            else:
                n = 257 - c
                line += buf[off:off + n * bpp]
                off += n * bpp
        if len(line) != bpl:
            problems.append("line %d overruns" % y)
            return off, bytes(out)
        if y + rep > height:
            problems.append("line repeat at %d runs past Height" % y)
        out += bytes(line) * min(rep, height - y)
        y += rep
    return off, bytes(out)


def validate_pwg(buf: bytes, *, supported_res: Iterable[str] | None = None,
                 supported_types: Iterable[str] | None = None) -> dict:
    problems: list[str] = []
    pages: list[dict] = []
    if buf[:4] != b"RaS2":
        problems.append("bad sync word")
    off = 4
    while off < len(buf) and not problems[-1:] == ["short header"]:
        h = buf[off:off + PWG_HEADER]
        off += PWG_HEADER
        if len(h) < PWG_HEADER:
            problems.append("short header")
            break
        u = lambda o: struct.unpack_from("!I", h, o)[0]  # noqa: E731
        i32 = lambda o: struct.unpack_from("!i", h, o)[0]  # noqa: E731
        info = {
            "PwgRaster": h[0:64].split(b"\0")[0].decode("ascii", "replace"),
            "Duplex": u(272), "HWResolution": (u(276), u(280)), "PageSize": (u(352), u(356)),
            "Tumble": u(368), "Width": u(372), "Height": u(376), "BitsPerColor": u(384),
            "BitsPerPixel": u(388), "BytesPerLine": u(392), "ColorOrder": u(396),
            "ColorSpace": u(400), "NumColors": u(420), "TotalPageCount": u(452),
            "CrossFeedTransform": i32(456), "FeedTransform": i32(460),
            "PageSizeName": h[1732:1796].split(b"\0")[0].decode("ascii", "replace"),
        }
        if info["PwgRaster"] != "PwgRaster":
            problems.append("PwgRaster field")
        typ = {(19, 24, 3): "srgb_8", (18, 8, 1): "sgray_8"}.get(
            (info["ColorSpace"], info["BitsPerPixel"], info["NumColors"]))
        if typ is None or info["BitsPerColor"] != 8:
            problems.append("unknown colour type")
        elif supported_types is not None and typ not in set(supported_types):
            problems.append("type %s not supported" % typ)
        info["type"] = typ
        res = "%dx%ddpi" % info["HWResolution"]
        if supported_res is not None and res not in set(supported_res):
            problems.append("resolution %s not supported" % res)
        if info["BytesPerLine"] != (info["BitsPerPixel"] * info["Width"] + 7) // 8:
            problems.append("BytesPerLine formula")
        if info["Width"] != round(info["PageSize"][0] * info["HWResolution"][0] / 72) or \
           info["Height"] != round(info["PageSize"][1] * info["HWResolution"][1] / 72):
            problems.append("Width/Height don't match PageSize at HWResolution")
        if info["ColorOrder"] != 0:
            problems.append("ColorOrder must be chunky")
        if info["CrossFeedTransform"] not in (1, -1) or info["FeedTransform"] not in (1, -1):
            problems.append("transforms must be 1 or -1")
        for a, b in [(256, 268), (312, 324), (332, 340), (348, 352), (360, 368), (380, 384),
                     (404, 420), (424, 452), (488, 508), (1604, 1668)]:
            if any(h[a:b]):
                problems.append("reserved bytes %d-%d not zero" % (a, b - 1))
        if info["Width"] == 0 or info["Height"] == 0 or info["Width"] * info["Height"] > 400_000_000:
            problems.append("bad page size")
            break
        off, bitmap = decode_bitmap(buf, off, info["Width"], info["Height"], info["BitsPerPixel"] // 8, problems)
        info["bitmap_sha256"] = hashlib.sha256(bitmap).hexdigest()
        pages.append(info)
    if pages and any(p["TotalPageCount"] not in (0, len(pages)) for p in pages):
        problems.append("TotalPageCount mismatch")
    return {"bytes": len(buf), "pages": pages, "problems": problems, "valid": not problems}


def validate_urf(buf: bytes) -> dict:
    problems: list[str] = []
    pages: list[dict] = []
    if buf[:8] != b"UNIRAST\x00":
        problems.append("bad sync word")
    count = struct.unpack_from("!I", buf, 8)[0] if len(buf) >= 12 else 0
    off = 12
    while off < len(buf):
        h = buf[off:off + 32]
        off += 32
        if len(h) < 32:
            problems.append("short header")
            break
        info = {"bpp": h[0], "colorspace": h[1], "duplex": h[2], "quality": h[3],
                "width": struct.unpack_from("!I", h, 12)[0], "height": struct.unpack_from("!I", h, 16)[0],
                "dpi": struct.unpack_from("!I", h, 20)[0]}
        if any(h[6:12]) or any(h[24:32]):
            problems.append("reserved header bytes not zero")
        if (info["bpp"], info["colorspace"]) not in ((24, 1), (8, 0), (8, 4)):
            problems.append("unknown colour")
            break
        if info["duplex"] not in (1, 2, 3):
            problems.append("duplex byte")
        if info["width"] == 0 or info["height"] == 0 or info["width"] * info["height"] > 400_000_000:
            problems.append("bad page size")
            break
        off, bitmap = decode_bitmap(buf, off, info["width"], info["height"], info["bpp"] // 8, problems)
        info["bitmap_sha256"] = hashlib.sha256(bitmap).hexdigest()
        pages.append(info)
    if count != len(pages):
        problems.append("page count field %d != %d pages" % (count, len(pages)))
    return {"bytes": len(buf), "page_count_field": count, "pages": pages, "problems": problems,
            "valid": not problems}
