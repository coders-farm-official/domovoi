"""Make the Play Store listing graphics from the repo's own brand sources.

    python android/play/make_graphics.py          # writes into android/play/graphics/
    python android/play/make_graphics.py --check  # exits 1 if the committed files differ

Inputs (all in the repo, all our own or permissively licensed):

* docs/assets/domovoi-icon.png: the amber cat head, the master every app
  icon is cut from (docs/assets/README.md).
* #1F1A14: the launcher icon's background
  (android/app/src/main/res/drawable/ic_launcher_background.xml).
* #F9AD26 / #F5F3F0 / #A7A49F: the dark theme's brand, text and muted text
  (android/app/src/main/java/com/domovoi/app/ui/theme/Color.kt).
* web/static/vendor/fonts/inter-latin-wght-normal.woff2: Inter (SIL Open
  Font License 1.1), the dashboard's own font.

Outputs (android/play/graphics/):

* icon-512.png: 512x512, 32-bit PNG: the cat on #1F1A14, a full square (Play
  rounds the corners itself), the same layout as
  android/app/src/main/ic_launcher-playstore.png.
* feature-graphic-1024x500.png: 1024x500, 24-bit PNG (no alpha), as Play
  asks: the cat, the name and one line. No other product or brand appears.

Needs Pillow (a dev tool only; nothing here ships in the app).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "graphics"
ICON = ROOT / "docs" / "assets" / "domovoi-icon.png"
FONT = ROOT / "web" / "static" / "vendor" / "fonts" / "inter-latin-wght-normal.woff2"

BACKGROUND = (0x1F, 0x1A, 0x14)
BRAND = (0xF9, 0xAD, 0x26)
TEXT = (0xF5, 0xF3, 0xF0)
MUTED = (0xA7, 0xA4, 0x9F)

TAGLINE = "Your home assistant, on your own hardware"
SUBLINE = "Music, rooms, timers and an assistant that runs on your phone"


def cat(size: int) -> Image.Image:
    """The cat head, trimmed to its own outline, fitted into size x size."""
    head = Image.open(ICON).convert("RGBA")
    head = head.crop(head.getbbox())
    scale = size / max(head.size)
    return head.resize((max(1, round(head.width * scale)), max(1, round(head.height * scale))), Image.LANCZOS)


def font(px: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), px)
    f.set_variation_by_axes([weight])
    return f


def icon_512() -> Image.Image:
    img = Image.new("RGBA", (512, 512), BACKGROUND + (255,))
    head = cat(372)  # inside the central ~73%: clear of Play's rounded corners
    img.alpha_composite(head, ((512 - head.width) // 2, (512 - head.height) // 2 + 6))
    return img


def feature_graphic() -> Image.Image:
    w, h = 1024, 500
    img = Image.new("RGBA", (w, h), BACKGROUND + (255,))
    # A soft amber glow behind the cat: a radial mask, blurred.
    cx, cy, radius = 230, 250, 230
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for r in range(radius, 0, -2):
        md.ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(70 * (1 - r / radius) ** 1.6))
    mask = mask.filter(ImageFilter.GaussianBlur(18))
    img.paste(Image.new("RGBA", (w, h), BRAND + (255,)), (0, 0), mask)
    head = cat(290)
    img.alpha_composite(head, (cx - head.width // 2, cy - head.height // 2))
    # The name and the line, left-aligned on the right.
    x = 430
    name = font(112, 700)
    tag = font(34, 600)
    sub = font(26, 400)
    draw = ImageDraw.Draw(img)
    draw.text((x, 96), "Domovoi", font=name, fill=TEXT)
    draw.rectangle((x + 6, 242, x + 96, 248), fill=BRAND)
    y = 272
    for line in wrap(TAGLINE, tag, w - x - 40):
        draw.text((x, y), line, font=tag, fill=TEXT)
        y += 44
    y += 10
    for line in wrap(SUBLINE, sub, w - x - 40):
        draw.text((x, y), line, font=sub, fill=MUTED)
        y += 34
    return img.convert("RGB")


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed files instead of writing")
    args = ap.parse_args()
    made = {"icon-512.png": png(icon_512()), "feature-graphic-1024x500.png": png(feature_graphic())}
    OUT.mkdir(exist_ok=True)
    stale = []
    for name, data in made.items():
        path = OUT / name
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(name)
        else:
            path.write_bytes(data)
        print(f"{name}: {hashlib.sha256(data).hexdigest()}")
    if stale:
        print("differs from what the script makes: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
