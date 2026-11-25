"""The core's compact PNG writer (``domovoi/plugins_runtime/png_compact.py``):
what ``send_image`` puts in a 16,384-byte image mail, and the lossless PNG
of a friend's picture. Every picture here is drawn in code; every output is
opened by the core's own decoder (the helper a friend's decode-only
container runs) and keeps its size.
"""

from __future__ import annotations

import io
import math
import random
import struct
import sys
import time
import zlib

import pytest

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
from PIL import Image, ImageDraw  # noqa: E402

from domovoi.plugins_runtime import isolated as iso  # noqa: E402
from domovoi.plugins_runtime import isolated_helper as helper  # noqa: E402
from domovoi.plugins_runtime import png_compact as pc  # noqa: E402

#: The image budget of a mail tagged "draw:1": 16,384 - 1 - 6.
BUDGET = 16 * 1024 - 1 - len("draw:1")

#: A real (tiny) picture, for tests that need any valid one.
SMALL_PNG = pc.encode_png(2, 2, 3, bytes([255, 255, 255, 0, 0, 0, 0, 0, 0, 255, 255, 255]))


# ─── pictures, drawn in code ─────────────────────────────────────────────

_INKS = [(20, 20, 20), (200, 40, 40), (40, 90, 200), (40, 150, 70), (240, 190, 20)]


def _sketch(side: int = 768, *, strokes: int = 15, mode: str = "RGB", supersample: int = 1,
            seed: int = 3) -> Image.Image:
    """Smooth pen strokes in a few inks on a plain background (Draw Battle's
    kind of picture). ``supersample`` > 1 draws bigger and scales down, as a
    browser canvas smooths its edges: hundreds of in-between colours."""
    rnd = random.Random(seed)
    s = side * supersample
    im = Image.new(mode, (s, s), (255, 255, 255) if mode == "RGB" else (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    for _ in range(strokes):
        ink = rnd.choice(_INKS) + ((255,) if mode == "RGBA" else ())
        x, y, a = rnd.uniform(.1, .9) * s, rnd.uniform(.1, .9) * s, rnd.uniform(0, 6.3)
        pts = [(x, y)]
        for _ in range(rnd.randint(20, 60)):
            a += rnd.uniform(-.35, .35)
            x = min(s - 1, max(0, x + math.cos(a) * 8 * supersample))
            y = min(s - 1, max(0, y + math.sin(a) * 8 * supersample))
            pts.append((x, y))
        d.line(pts, fill=ink, width=5 * supersample, joint="curve")
    return im.resize((side, side), Image.Resampling.LANCZOS) if supersample > 1 else im


def _busy_drawing(seed: int = 1) -> Image.Image:
    """Forty jagged strokes in six inks: a busy drawing, still 7 colours."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (768, 768), (255, 255, 255))
    d = ImageDraw.Draw(im)
    inks = _INKS + [(130, 60, 160)]
    for _ in range(40):
        pts = [(rnd.randrange(768), rnd.randrange(768))]
        for _ in range(12):
            x, y = pts[-1]
            pts.append((min(767, max(0, x + rnd.randint(-60, 60))), min(767, max(0, y + rnd.randint(-60, 60)))))
        d.line(pts, fill=rnd.choice(inks), width=6, joint="curve")
    return im


def _photo(side: int = 768, noise: int = 24, seed: int = 2) -> Image.Image:
    """Smooth gradients plus sensor-like noise: hundreds of thousands of colours."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:side, 0:side].astype(np.float32) / side
    base = np.stack([xx * 255, yy * 255, (1 - np.hypot(xx - .5, yy - .5)) * 255], axis=-1)
    px = np.clip(base + rng.integers(0, noise, base.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(px, "RGB")


def _noise(side: int = 768, mode: str = "RGB", seed: int = 4) -> Image.Image:
    bands = len(mode)
    px = np.random.default_rng(seed).integers(0, 256, (side, side, bands), dtype=np.uint8)
    return Image.fromarray(px, mode)


#: Strokes in a full drawing: over 16 KiB the old way (about 22 KB RGB,
#: 25 KB RGBA), about 15 KB as the core's PNG now.
FULL = 40


def drawing_pixels() -> bytes:
    """A full 768 px drawing's RGB rows (for the routing tests)."""
    return _sketch(strokes=FULL).tobytes()


def old_style_png(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """The PNG the core wrote before this encoder: unfiltered, zlib level 6."""
    stride = width * channels
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return (pc.PNG_MAGIC + pc._chunk(b"IHDR", ihdr) + pc._chunk(b"IDAT", zlib.compress(raw, 6))
            + pc._chunk(b"IEND", b""))


def decoded(png: bytes) -> tuple[int, int, int, bytes]:
    """The core's own decoder: the helper a friend's decode-only container
    runs (its image_safety under the in-image name)."""
    from domovoi import image_safety

    sys.modules.setdefault("domovoi_image_safety", image_safety)
    return helper.decode_pixels(png)


def rgb(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    return Image.frombytes("RGBA" if channels == 4 else "RGB", (width, height), pixels).convert("RGB").tobytes()


def _chunks(png: bytes) -> list[bytes]:
    pos, out = 8, []
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4])
        out.append(png[pos + 4:pos + 8])
        pos += 12 + n
    return out


def _bytes_of(im: Image.Image, fmt: str = "PNG", **save) -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **save)
    return buf.getvalue()


# ─── the lossless PNG (a friend's picture, encode_png) ───────────────────

def _cases():
    rnd = random.Random(7)
    two = bytes(rnd.choice((0, 255)) for _ in range(7 * 3 * 3))           # 1 bit, odd width
    four = bytes(v for _ in range(9 * 5) for v in rnd.choice([(1, 2, 3), (4, 5, 6), (7, 8, 9), (0, 0, 0)]))
    sixteen = bytes(v for _ in range(13 * 11) for v in (rnd.randrange(16) * 16, 0, 0))
    alpha = bytes(v for _ in range(6 * 4) for v in rnd.choice([(255, 0, 0, 0), (0, 0, 255, 128), (9, 9, 9, 255)]))
    many = bytes(rnd.randrange(256) for _ in range(40 * 30 * 3))
    many_rgba = bytes(rnd.randrange(256) for _ in range(31 * 17 * 4))
    opaque_rgba = bytes(v for _ in range(5 * 5) for v in (rnd.randrange(256), 3, 4, 255))
    return [
        ("1bit", 7, 3, 3, two), ("2bit", 9, 5, 3, four), ("4bit", 13, 11, 3, sixteen),
        ("alpha-palette", 6, 4, 4, alpha), ("truecolour", 40, 30, 3, many),
        ("truecolour-alpha", 31, 17, 4, many_rgba), ("opaque-rgba", 5, 5, 4, opaque_rgba),
        ("sketch", 768, 768, 3, drawing_pixels()),
    ]


@pytest.mark.parametrize("name, w, h, c, pixels", _cases(), ids=[c[0] for c in _cases()])
def test_every_core_png_decodes_to_the_same_pixels(name, w, h, c, pixels) -> None:
    png = iso.encode_png(w, h, c, pixels)
    assert set(_chunks(png)) <= {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
    dw, dh, dc, back = decoded(png)
    assert (dw, dh) == (w, h)
    want = Image.frombytes("RGBA" if c == 4 else "RGB", (w, h), pixels)
    got = Image.frombytes("RGBA" if dc == 4 else "RGB", (w, h), back)
    if c == 4 and dc == 4:
        assert got.tobytes() == want.tobytes()
    else:                                  # opaque RGBA is written as RGB
        assert got.convert("RGBA").tobytes() == want.convert("RGBA").tobytes()


def test_a_drawing_is_an_exact_palette_and_smaller_than_before() -> None:
    pixels = drawing_pixels()
    png = pc.encode_png(768, 768, 3, pixels)
    assert png[25] == 3 and b"PLTE" in _chunks(png)                       # colour type 3: indexed
    assert len(png) < len(old_style_png(768, 768, 3, pixels)) * 0.75
    assert len(png) <= BUDGET                                             # fits a mail as it is


def test_the_caps_match() -> None:
    assert pc.MAX_SIDE == iso.IMAGE_MAX_SIDE == helper.MAX_SIDE
    assert pc.PNG_MAGIC == iso.PNG_MAGIC
    assert pc.MIN_SIDE == 128 and pc.SIDES[0] == iso.IMAGE_MAX_SIDE
    with pytest.raises(ValueError):
        pc.encode_png(2, 2, 3, b"\x00" * 11)
    with pytest.raises(ValueError):
        pc.encode_png(769, 1, 3, b"\x00" * 769 * 3)


# ─── fitting a mail (send_image) ─────────────────────────────────────────

def _fit_and_decode(im: Image.Image) -> tuple[bytes, int, int]:
    png = pc.mail_png(_bytes_of(im), BUDGET)
    assert len(png) <= BUDGET
    assert set(_chunks(png)) <= {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
    w, h, _, _ = decoded(png)
    with Image.open(io.BytesIO(png)) as again:
        assert again.size == (w, h)
    return png, w, h


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_a_768_px_drawing_fits_at_full_size_losslessly(mode) -> None:
    im = _sketch(strokes=FULL, mode=mode)
    assert len(old_style_png(768, 768, len(mode), im.tobytes())) > BUDGET     # it didn't fit before
    png, w, h = _fit_and_decode(im)
    assert (w, h) == (768, 768)
    _, _, c, back = decoded(png)
    want = im.convert("RGBA") if c == 4 else im.convert("RGB")
    assert back == want.tobytes()                                         # every pixel kept


def test_a_busy_drawing_scales_down_keeping_its_exact_colours() -> None:
    im = _busy_drawing()
    colours = {c for _, c in im.getcolors(256)}
    png, w, h = _fit_and_decode(im)
    assert w == h and 384 <= w < 768                                      # 512 today
    with Image.open(io.BytesIO(png)) as out:
        assert {c for _, c in out.convert("RGB").getcolors(256)} <= colours  # snapped, no new shades


def test_a_smoothed_drawing_loses_colours_before_size() -> None:
    im = _sketch(supersample=4)
    assert 256 < len(im.getcolors(pc.DRAWING_COLOURS_MAX)) <= pc.DRAWING_COLOURS_MAX
    png, w, h = _fit_and_decode(im)
    assert w == h and w >= 384                                            # 512 at 64 colours today
    with Image.open(io.BytesIO(png)) as out:
        assert out.mode == "P"


def test_a_photo_scales_down_and_fits() -> None:
    im = _photo()
    _, w, h = _fit_and_decode(im)
    assert w == h and pc.MIN_SIDE <= w < 768


def test_a_wide_picture_keeps_its_aspect() -> None:
    im = _photo().resize((768, 300))
    _, w, h = _fit_and_decode(im)
    assert w < 768 and abs(w / h - 768 / 300) < 0.02


def test_noise_is_refused_cleanly() -> None:
    with pytest.raises(pc.TooBig, match="even at 128 px a side"):
        pc.mail_png(_bytes_of(_noise()), BUDGET)


def test_a_forwarded_friend_image_is_re_encoded_and_fits() -> None:
    """A FriendImage too big for a mail (one written before this encoder)
    goes through the same steps; one that fits goes unchanged."""
    from domovoi.sdk.friend_houses import FriendImage, FriendText

    pixels = drawing_pixels()
    big = FriendImage(old_style_png(768, 768, 3, pixels), 768, 768, FriendText("cat"))
    assert len(big.png) > BUDGET
    png = pc.mail_png(big.png, BUDGET, core_png=True)
    assert len(png) <= BUDGET
    w, h, c, back = decoded(png)
    assert (w, h) == (768, 768) and rgb(w, h, c, back) == pixels
    small = FriendImage(png, 768, 768, FriendText("cat"))
    assert pc.mail_png(small.png, BUDGET, core_png=True) == png           # already the core's: as is
    photo = FriendImage(iso.encode_png(768, 768, 3, _photo().tobytes()), 768, 768, FriendText("p"))
    out = pc.mail_png(photo.png, BUDGET, core_png=True)
    assert len(out) <= BUDGET and decoded(out)[0] < 768


def test_a_plugin_s_big_jpeg_is_opened_like_the_container_does() -> None:
    src = _bytes_of(_sketch(supersample=1).resize((1600, 1200)), "JPEG", quality=90)
    png = pc.mail_png(src, BUDGET)
    w, h, _, _ = decoded(png)
    assert max(w, h) <= 768 and abs(w / h - 4 / 3) < 0.02


@pytest.mark.parametrize("data", [b"", b"GIF89a" + b"\x00" * 40, b"\x89PNG\r\n\x1a\nbroken",
                                  b"<svg xmlns='http://www.w3.org/2000/svg'/>"],
                         ids=["empty", "gif", "broken-png", "svg"])
def test_bytes_that_aren_t_a_picture_are_a_value_error(data) -> None:
    with pytest.raises(ValueError) as e:
        pc.mail_png(data, BUDGET)
    assert not isinstance(e.value, pc.TooBig)


def test_oversized_sources_are_refused_before_decoding() -> None:
    with pytest.raises(ValueError, match="MiB"):
        pc.mail_png(b"\x00" * (pc.SOURCE_BYTES_MAX + 1), BUDGET)
    big = Image.new("RGB", (2001, 2000), (1, 2, 3))                       # over 4 megapixels
    with pytest.raises(ValueError, match="too large"):
        pc.mail_png(_bytes_of(big), BUDGET)


# ─── bounded work ────────────────────────────────────────────────────────

def _worst() -> Image.Image:
    """768 px RGBA of 16,000 random colours: a "drawing" by its colour
    count, so every lossy palette is tried, at every size, and none fits
    until small."""
    rng = np.random.default_rng(1)
    palette = rng.integers(0, 256, (16_000, 4), dtype=np.uint8)
    return Image.fromarray(palette[rng.integers(0, 16_000, 768 * 768)].reshape(768, 768, 4), "RGBA")


def test_the_work_is_bounded(monkeypatch) -> None:
    calls = {"exact": 0, "quantized": 0}
    real_exact, real_quant = pc._exact, pc._quantized

    def exact(*a, **k):
        calls["exact"] += 1
        return real_exact(*a, **k)

    def quant(*a, **k):
        calls["quantized"] += 1
        return real_quant(*a, **k)

    monkeypatch.setattr(pc, "_exact", exact)
    monkeypatch.setattr(pc, "_quantized", quant)
    im = _worst()
    started = time.perf_counter()
    try:
        pc.fit_png(im, BUDGET)
    except pc.TooBig:
        pass
    elapsed = time.perf_counter() - started
    assert calls["exact"] <= len(pc.SIDES)
    assert calls["quantized"] <= len(pc.SIDES) * len(pc.LOSSY_COLOURS)
    # About 1.7 s on a desktop CPU; generous for a slow CI runner.
    assert elapsed < 30, elapsed


def test_the_lossless_encode_of_the_largest_picture_is_quick() -> None:
    for im in (_noise(mode="RGBA"), _worst()):
        started = time.perf_counter()
        png = pc.encode_png(768, 768, 4, im.tobytes())
        assert time.perf_counter() - started < 10
        assert len(png) <= iso.IMAGE_OUT_MAX
