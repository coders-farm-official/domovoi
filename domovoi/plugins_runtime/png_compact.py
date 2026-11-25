"""The core's compact PNG writer, on the trusted side (D1, SDK 1.19).

Every PNG the core writes for a plugin's images comes from here:
:func:`encode_png` for the pixels a friend's decoder answered (the
``FriendImage`` a plugin gets, lossless at its size), and :func:`mail_png`
for what ``send_image`` puts in an image mail, which has to fit 16,384
bytes with its tag (a plugin's own picture, or a ``FriendImage`` passed on).

The output is a plain PNG that any decoder opens, the friend's decode-only
container included: 8-bit truecolour (RGB or RGBA) or an indexed image (1,
2, 4 or 8 bits a pixel) with an optional ``tRNS``. The core writes every
chunk itself (``IHDR``, ``PLTE``, ``tRNS``, one ``IDAT``, ``IEND``) and
nothing else, so no metadata, colour profile or text can ride along.

**Lossless** (:func:`encode_png`):

* **Exact palette.** A picture of at most :data:`PALETTE_MAX` colours
  (alpha counted) is written as an indexed image of exactly those colours.
  A drawing (strokes on a plain background) usually is one. Up to 16
  colours may also try 4 bits a pixel, up to 4 two, up to 2 one; indexed
  rows are left unfiltered, which packs them best.
* **Opaque alpha dropped.** RGBA whose alpha is 255 everywhere is RGB.
* **Filters and zlib level 9.** Truecolour rows are compressed both
  unfiltered and with PNG's adaptive row filter (libpng's heuristic: per
  row, the one of the five filters with the smallest sum of absolute
  differences); the smaller wins. Flat drawings pack best unfiltered,
  photographs filtered.

**To fit a mail** (:func:`fit_png`), at each long side in turn, the
picture's own and then :data:`SIDES` below it (768, 512, 384, 256, 192,
128; aspect kept), the first of these that fits the budget:

1. the lossless PNG;
2. a picture of more than 256 colours reduced to 256 (lossy: median cut for
   RGB, fast octree for RGBA, no dithering, which packs badly), and one of
   at most :data:`DRAWING_COLOURS_MAX` colours (a drawing whose edges were
   smoothed) to 64 and then 16 as well, before it loses size.

A picture of at most 256 colours keeps exactly those colours when scaled
(resampled, then each pixel snapped to the nearest of them; with
transparency, the nearest pixel), so strokes stay solid. A size that, by
the last one's bytes per pixel, would still be over twice the budget is
skipped; :data:`MIN_SIDE` (128 px on the long side) is always tried, and
past it :class:`TooBig`.

**Bounded work.** The input is at most 768 x 768 (``IMAGE_MAX_SIDE``; a
plugin's own picture is opened only up to :data:`SOURCE_PIXELS_MAX` and
scaled to that first). One encode is one colour count (``numpy.unique``)
and at most two level-9 compressions; :func:`fit_png` makes at most four
encodes at each of six sizes, whose areas add up to under twice the
first. The slowest case measured (768 x 768 RGBA of 16,000 random colours,
every step tried) took about 1.7 s on a desktop CPU; :func:`encode_png`
alone at most about 0.4 s. Memory peaks at about 60 MiB (the filtered rows
as 16-bit integers). Callers run it off the event loop, in
``domovoi.executors``' ``pictures`` pool on the sending side.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: The largest side the core writes (``isolated.IMAGE_MAX_SIDE``; a test
#: keeps them equal).
MAX_SIDE = 768
#: Colours kept exactly in a palette (PNG's palette holds 256).
PALETTE_MAX = 256
#: The long sides :func:`fit_png` steps through, largest first.
SIDES = (768, 512, 384, 256, 192, 128)
#: Below this a picture is too small to be worth sending: :class:`TooBig`.
MIN_SIDE = SIDES[-1]
#: At most this many colours, a picture is taken for a drawing whose edges
#: were smoothed: it may lose colours (down to 16) before it loses size.
DRAWING_COLOURS_MAX = 16384
#: The lossy palettes tried at each size for such a drawing, in order.
LOSSY_COLOURS = (256, 64, 16)
ZLIB_LEVEL = 9
#: A plugin's own picture: the bytes and pixels opened, at most (the decode
#: container's cap; it is scaled to :data:`MAX_SIDE` before anything else).
SOURCE_BYTES_MAX = 8 * 1024 * 1024
SOURCE_PIXELS_MAX = 4_000_000
SOURCE_FORMATS = ("PNG", "JPEG", "WEBP")
#: (adaptive filter, zlib strategy) tried for truecolour rows: flat
#: drawings pack best unfiltered, photographs filtered with Z_FILTERED.
#: Indexed rows are only ever written unfiltered with the default strategy.
_TRUECOLOUR_TRIES = ((False, zlib.Z_DEFAULT_STRATEGY), (True, zlib.Z_FILTERED))


class TooBig(ValueError):
    """Even at :data:`MIN_SIDE` the PNG is over the budget."""


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _deflate(raw: bytes, strategy: int) -> bytes:
    c = zlib.compressobj(ZLIB_LEVEL, zlib.DEFLATED, 15, 9, strategy)
    return c.compress(raw) + c.flush()


def _rows(a: Any, bpp: int, adaptive: bool) -> bytes:
    """The IDAT's uncompressed bytes for rows ``a`` (H x row-bytes, uint8):
    each row behind its filter byte, filter 0 (none) or per row the best of
    the five (minimum sum of absolute differences)."""
    import numpy as np

    h = a.shape[0]
    if not adaptive:
        return np.concatenate([np.zeros((h, 1), np.uint8), a], axis=1).tobytes()
    x = a.astype(np.int16)
    left = np.zeros_like(x)
    left[:, bpp:] = x[:, :-bpp]
    up = np.zeros_like(x)
    up[1:] = x[:-1]
    upleft = np.zeros_like(x)
    upleft[1:, bpp:] = x[:-1, :-bpp]
    p = left + up - upleft
    pa, pb, pc = np.abs(p - left), np.abs(p - up), np.abs(p - upleft)
    paeth = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, up, upleft))
    del p, pa, pb, pc
    best_rows = np.empty_like(a)
    best_sig = np.full(h, np.iinfo(np.int64).max, np.int64)
    choice = np.zeros(h, np.uint8)
    for f, pred in enumerate((None, left, up, (left + up) >> 1, paeth)):
        out = (x if pred is None else x - pred) & 0xFF
        sig = np.minimum(out, 256 - out).sum(axis=1, dtype=np.int64)
        better = sig < best_sig
        best_sig[better] = sig[better]
        choice[better] = f
        best_rows[better] = out[better].astype(np.uint8)
    return np.concatenate([choice[:, None], best_rows], axis=1).tobytes()


def _write(width: int, height: int, color_type: int, bit_depth: int, rows: Any, bpp: int,
           extra: bytes = b"") -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    tries = ((False, zlib.Z_DEFAULT_STRATEGY),) if color_type == 3 else _TRUECOLOUR_TRIES
    best = None
    for adaptive, strategy in tries:
        z = _deflate(_rows(rows, bpp, adaptive), strategy)
        if best is None or len(z) < len(best):
            best = z
    return PNG_MAGIC + _chunk(b"IHDR", ihdr) + extra + _chunk(b"IDAT", best) + _chunk(b"IEND", b"")


def _pack(index: Any, bits: int) -> Any:
    """Indexes (H x W uint8) packed ``bits`` to a pixel, rows padded."""
    import numpy as np

    if bits == 8:
        return index
    per = 8 // bits
    h, w = index.shape
    padded = np.zeros((h, -(-w // per) * per), np.uint8)
    padded[:, :w] = index
    padded = padded.reshape(h, -1, per)
    out = np.zeros(padded.shape[:2], np.uint8)
    for i in range(per):
        out |= padded[:, :, i] << (8 - bits * (i + 1))
    return out


def _indexed(width: int, height: int, palette: Any, index: Any) -> bytes:
    """An indexed PNG: ``palette`` is N x 4 RGBA (N <= 256), ``index`` H x W.
    The smallest of 8 bits a pixel and the fewest bits that hold N."""
    import numpy as np

    n = len(palette)
    extra = _chunk(b"PLTE", palette[:, :3].tobytes())
    alpha = palette[:, 3]
    if (alpha < 255).any():
        last = int(np.nonzero(alpha < 255)[0].max())
        extra += _chunk(b"tRNS", alpha[:last + 1].tobytes())
    index = index.reshape(height, width).astype(np.uint8)
    best = _write(width, height, 3, 8, index, 1, extra)
    bits = 1 if n <= 2 else 2 if n <= 4 else 4 if n <= 16 else 8
    if bits < 8:
        low = _write(width, height, 3, bits, _pack(index, bits), 1, extra)
        if len(low) < len(best):
            best = low
    return best


def _exact(width: int, height: int, channels: int, pixels: bytes) -> tuple[bytes, bool]:
    """The smallest lossless PNG of the rows, and whether it is indexed."""
    import numpy as np

    arr = np.frombuffer(pixels, np.uint8).reshape(-1, channels)
    if channels == 4 and bool((arr[:, 3] == 255).all()):
        arr, channels = arr[:, :3], 3
    key = np.zeros(arr.shape[0], np.uint32)
    for b in range(channels):
        key = (key << 8) | arr[:, b]
    colours, index = np.unique(key, return_inverse=True)
    if len(colours) <= PALETTE_MAX:
        palette = np.full((len(colours), 4), 255, np.uint8)
        for b in range(channels):
            palette[:, b] = (colours >> (8 * (channels - 1 - b))) & 0xFF
        return _indexed(width, height, palette, index), True
    rows = np.ascontiguousarray(arr).reshape(height, width * channels)
    return _write(width, height, 6 if channels == 4 else 2, 8, rows, channels), False


def _check(width: int, height: int, channels: int, pixels: bytes) -> None:
    if channels not in (3, 4) or not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise ValueError("the pixels don't match the size")
    if len(pixels) != width * height * channels:
        raise ValueError("the pixels don't match the size")


def encode_png(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """A compact, lossless PNG of the pixels (RGB or RGBA rows) at their
    size. ValueError when the pixels don't match the size."""
    _check(width, height, channels, pixels)
    return _exact(width, height, channels, bytes(pixels))[0]


def _quantized(im: Any, colours: int) -> bytes:
    """``im`` (Pillow, RGB or RGBA) reduced to ``colours`` colours, no
    dithering, as an indexed PNG (lossy)."""
    import numpy as np
    from PIL import Image

    method = Image.Quantize.FASTOCTREE if im.mode == "RGBA" else Image.Quantize.MEDIANCUT
    q = im.quantize(colours, method=method, dither=Image.Dither.NONE)
    index = np.frombuffer(q.tobytes(), np.uint8)
    used = int(index.max()) + 1
    pal = np.frombuffer(bytes(q.getpalette("RGBA") or b""), np.uint8).reshape(-1, 4)
    palette = np.full((used, 4), 255, np.uint8)
    n = min(used, len(pal))
    palette[:n] = pal[:n]
    if im.mode == "RGB":
        palette[:, 3] = 255
    return _indexed(im.width, im.height, palette, index)


def _scaled(im: Any, size: tuple[int, int], palette_of: Any | None) -> Any:
    """``im`` at ``size``. A picture of few exact colours keeps exactly
    those: an opaque one is resampled (Lanczos) and each pixel snapped to
    the nearest of its colours, so strokes stay solid and smooth; one with
    transparency takes the nearest pixel. Anything else is resampled."""
    from PIL import Image

    if palette_of is None:
        return im.resize(size, Image.Resampling.LANCZOS)
    if im.mode == "RGBA":
        return im.resize(size, Image.Resampling.NEAREST)
    return im.resize(size, Image.Resampling.LANCZOS).quantize(
        palette=palette_of, dither=Image.Dither.NONE).convert("RGB")


def fit_png(im: Any, budget: int) -> bytes:
    """A PNG of the Pillow image ``im`` (RGB or RGBA, at most
    :data:`MAX_SIDE` a side) of at most ``budget`` bytes. At each long side
    in turn (its own, then :data:`SIDES` below it), the first that fits of:
    the lossless PNG; then, for a picture of more than 256 colours, the
    same reduced to 256 colours, and for one of at most
    :data:`DRAWING_COLOURS_MAX` (a drawing with smoothed edges) to 64 and
    16 too. :class:`TooBig` when nothing down to :data:`MIN_SIDE` fits."""
    from PIL import Image

    if im.mode not in ("RGB", "RGBA") or max(im.size) > MAX_SIDE:
        raise ValueError("an RGB or RGBA image of at most 768 px a side")
    if im.mode == "RGBA" and im.getchannel("A").getextrema() == (255, 255):
        im = im.convert("RGB")
    counted = im.getcolors(DRAWING_COLOURS_MAX)          # None past that many
    palette_of = None
    if counted is not None and len(counted) <= PALETTE_MAX and im.mode == "RGB":
        palette_of = Image.new("P", (1, 1))
        palette_of.putpalette(b"".join(bytes(c) for _, c in counted))
    elif counted is not None and len(counted) <= PALETTE_MAX:
        palette_of = True                                # RGBA: nearest pixel
    if counted is None:
        reductions: tuple[int, ...] = (PALETTE_MAX,)
    elif len(counted) > PALETTE_MAX:
        reductions = LOSSY_COLOURS
    else:
        reductions = ()
    long_side = max(im.size)
    sides = [long_side] + [s for s in SIDES if s < long_side]
    smallest = None
    last: tuple[int, int] | None = None          # (long side, bytes) of the last try
    for i, side in enumerate(sides):
        if last is not None and i < len(sides) - 1:
            # Bytes go roughly with the area: skip a size still far over.
            if last[1] * (side / last[0]) ** 2 > 2 * budget:
                continue
        if side == long_side:
            cur = im
        else:
            scale = side / long_side
            cur = _scaled(im, (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                          palette_of)
        png, _ = _exact(cur.width, cur.height, len(cur.mode), cur.tobytes())
        size = len(png)
        if size <= budget:
            return png
        for colours in reductions:
            q = _quantized(cur, colours)
            if len(q) <= budget:
                return q
            size = min(size, len(q))
        smallest = size if smallest is None else min(smallest, size)
        last = (side, size)
    raise TooBig(f"the picture doesn't fit in {budget} bytes even at {sides[-1]} px a side "
                 f"(the smallest try was {smallest} bytes)")


def fit_pixels(width: int, height: int, channels: int, pixels: bytes, budget: int) -> bytes:
    """:func:`fit_png` for raw RGB or RGBA rows."""
    from PIL import Image

    _check(width, height, channels, pixels)
    return fit_png(Image.frombytes("RGBA" if channels == 4 else "RGB", (width, height), bytes(pixels)),
                   budget)


def _opened(data: bytes) -> Any:
    """A plugin's own PNG, JPEG or WebP bytes as a Pillow RGB or RGBA image
    of at most :data:`MAX_SIDE` a side: the first frame, oriented, as the
    friend's decode container would see it. ValueError when it isn't one."""
    import io
    import warnings

    from PIL import Image, ImageFile, ImageOps

    from domovoi import image_safety as safety

    ImageFile.LOAD_TRUNCATED_IMAGES = False
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with safety.open_image(io.BytesIO(data), SOURCE_FORMATS, max_pixels=SOURCE_PIXELS_MAX) as im:
                if im.format not in SOURCE_FORMATS:
                    raise ValueError("not a PNG, JPEG or WebP image")
                if getattr(im, "n_frames", 1) > 1:
                    im.seek(0)
                im.load()
                frame = ImageOps.exif_transpose(im)
                mode = "RGBA" if ("A" in frame.getbands() or "transparency" in frame.info) else "RGB"
                out = frame.convert(mode)
        except safety.PictureRefused as e:
            raise ValueError("the image is too large" if e.too_large else "not a PNG, JPEG or WebP image") from None
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError("the image is too large") from None
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001 — any decoder failure is a refusal
            raise ValueError(f"the image doesn't decode ({type(e).__name__})") from None
    out.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return out


def mail_png(data: bytes, budget: int, *, core_png: bool = False) -> bytes:
    """The PNG an image mail carries, at most ``budget`` bytes. ``data`` is a
    plugin's own PNG, JPEG or WebP, or with ``core_png`` a ``FriendImage``'s
    PNG (one the core wrote), sent unchanged when it already fits. Blocking:
    run it in the ``pictures`` pool. ValueError when the bytes aren't a
    picture this opens; :class:`TooBig` when it can't be made to fit."""
    if core_png and len(data) <= budget:
        return bytes(data)
    if len(data) > SOURCE_BYTES_MAX:
        raise ValueError(f"the image is over {SOURCE_BYTES_MAX // (1024 * 1024)} MiB")
    return fit_png(_opened(bytes(data)), budget)
