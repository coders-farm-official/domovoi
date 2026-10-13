"""Domovoi Setup's artwork, made from the brand's app icon.

    python windows/installer/art/make_art.py           # (re)write the files
    python windows/installer/art/make_art.py --check   # exit 1 if they differ

Source: docs/assets/domovoi-icon.png, the glossy amber cat head (1024x1024,
transparent), the master every app icon is cut from (docs/assets/README.md).
Writes, next to this script:

* domovoi.ico: 16, 20, 24, 32, 40, 48, 64, 96, 128 and 256 px (32-bit
  bitmaps with alpha, 256 as PNG). Setup's and its uninstaller's icon
  (SetupIconFile), the Installed apps entry (UninstallDisplayIcon), and
  DomovoiHost.exe and DomovoiSetupHelper.exe (windows/build.ps1).
* wizard-<W>x<H>.png: WizardImageFile, the left panel of the welcome and
  finish pages: the head on the icon's dark ground (#1F1A14) with a soft
  amber glow, at each image-area size Inno Setup 6.6+ uses from 100% to
  250% (its WizardImageFile topic). Setup picks the closest one.
* small-<N>.png: WizardSmallImageFile, top right of the other pages and of
  the uninstaller: the head alone, transparent, so it sits on the light and
  the dark style alike, at the sizes from the same topic.

No text in the pictures (the product name is in the wizard's own text), so
no font is involved. The output depends only on the source and Pillow's
version (12.3.0, requirements.txt, the version requirements.lock pins);
--check compares decoded pixels, not file bytes, so a different zlib can't
fail it.
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE = REPO / "docs" / "assets" / "domovoi-icon.png"
PILLOW = "12.3.0"

# The icon's dark ground (docs/assets/README.md) and the brand amber,
# oklch(0.78 0.16 75) (docs/design/colors_and_type.css --brand) in sRGB.
GROUND = (0x1F, 0x1A, 0x14)
AMBER = (0xF2, 0xA6, 0x18)

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
# Inno Setup 6.6+ image areas at 100, 125, 150, 175, 200, 225 and 250%.
WIZARD_SIZES = [(202, 386), (269, 515), (336, 643), (403, 772), (430, 824), (498, 953), (534, 1022)]
SMALL_SIZES = [58, 77, 97, 116, 124, 143, 159]

LANCZOS = Image.Resampling.LANCZOS


def head() -> Image.Image:
    """The head cropped to what is drawn, centred on a transparent square."""
    im = Image.open(SOURCE).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    h = im.crop(box)
    side = max(h.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(h, ((side - h.width) // 2, (side - h.height) // 2))
    return sq


def square(src: Image.Image, size: int, margin: float) -> Image.Image:
    inner = max(1, round(size * (1 - 2 * margin)))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(src.resize((inner, inner), LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return out


def wizard(src: Image.Image, w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), GROUND + (255,))
    cx, cy = w / 2, h * 0.40
    # The glow: Pillow's radial gradient is 256x256 with the value 181 at
    # the middle of each edge (distance x 1.414); a circle that fades from a
    # quarter of full amber at the centre to nothing at that radius, so the
    # square it sits in never shows.
    gs = round(w * 1.3)
    glow = Image.radial_gradient("L").point(lambda v: round(64 * max(0.0, 1 - v / 181) ** 2)).resize((gs, gs), Image.Resampling.BICUBIC)
    mask = Image.new("L", (w, h), 0)
    mask.paste(glow, (round(cx - gs / 2), round(cy - gs / 2)))
    layer = Image.new("RGBA", (w, h), AMBER + (0,))
    layer.putalpha(mask)
    img.alpha_composite(layer)
    side = round(w * 0.70)
    img.alpha_composite(src.resize((side, side), LANCZOS), (round(cx - side / 2), round(cy - side / 2)))
    return img.convert("RGB")


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", compress_level=9)
    return b.getvalue()


def bmp_frame(img: Image.Image) -> bytes:
    """An icon frame as Windows stores one: BITMAPINFOHEADER (height doubled
    for the mask), 32-bit BGRA rows bottom-up, then the 1-bit AND mask."""
    w, h = img.size
    px = img.tobytes("raw", "BGRA")
    xor = b"".join(px[y * w * 4:(y + 1) * w * 4] for y in reversed(range(h)))
    stride = (w + 31) // 32 * 4
    alpha = img.getchannel("A").tobytes()
    rows = []
    for y in reversed(range(h)):
        bits = bytearray(stride)
        for x in range(w):
            if alpha[y * w + x] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        rows.append(bytes(bits))
    andmask = b"".join(rows)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(andmask), 0, 0, 0, 0)
    return header + xor + andmask


def ico(src: Image.Image) -> bytes:
    frames = []
    for n in ICO_SIZES:
        # The head fills small icons edge to edge; larger ones keep a hair of margin.
        img = square(src, n, 0.0 if n <= 32 else 0.02)
        frames.append((n, png(img) if n >= 256 else bmp_frame(img)))
    out = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for n, data in frames:
        dim = 0 if n >= 256 else n
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return out + entries + blobs


def outputs() -> dict[str, bytes]:
    src = head()
    files = {"domovoi.ico": ico(src)}
    for w, h in WIZARD_SIZES:
        files[f"wizard-{w}x{h}.png"] = png(wizard(src, w, h))
    for n in SMALL_SIZES:
        files[f"small-{n}.png"] = png(square(src, n, 0.04))
    return files


def _pixels(data: bytes) -> tuple:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.size, im.mode, im.tobytes()


def _ico_frames(data: bytes) -> list:
    _, _, count = struct.unpack_from("<HHH", data, 0)
    frames = []
    for i in range(count):
        w, h, _, _, _, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        blob = data[offset:offset + size]
        frames.append((w, h, bpp, _pixels(blob) if blob[:8] == b"\x89PNG\r\n\x1a\n" else blob))
    return frames


def same(name: str, a: bytes, b: bytes) -> bool:
    if name.endswith(".png"):
        return _pixels(a) == _pixels(b)
    return _ico_frames(a) == _ico_frames(b)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare with the files here; change nothing")
    args = ap.parse_args(argv)
    import PIL

    if PIL.__version__ != PILLOW:
        print(f"make_art: needs Pillow {PILLOW} (requirements.txt), not {PIL.__version__}", file=sys.stderr)
        return 2
    want = outputs()
    have = {p.name for p in HERE.iterdir() if p.suffix in (".ico", ".png")}
    if args.check:
        bad = sorted(n for n, data in want.items() if not (HERE / n).is_file() or not same(n, (HERE / n).read_bytes(), data))
        extra = sorted(have - set(want))
        for n in bad:
            print(f"make_art: {n} is missing or differs from what {SOURCE.name} gives")
        for n in extra:
            print(f"make_art: {n} is not made by this script")
        return 1 if bad or extra else 0
    for n in sorted(have - set(want)):
        (HERE / n).unlink()
    for n, data in want.items():
        (HERE / n).write_bytes(data)
    print(f"make_art: wrote {len(want)} files in {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
