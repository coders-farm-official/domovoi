"""Make the iPhone app icon from the repo's own brand art.

    python ios/scripts/make_app_icon.py          # writes the three PNGs
    python ios/scripts/make_app_icon.py --check  # exits 1 if the committed files differ

Input: docs/assets/domovoi-icon.png, the amber cat head every app icon is cut
from (docs/assets/README.md), and #1F1A14, the Android launcher background
(android/app/src/main/res/drawable/ic_launcher_background.xml). The layout is
android/play/make_graphics.py's icon_512 at twice the size, so the phone, Play
and the App Store show the same mark.

Output: ios/Domovoi/Assets.xcassets/AppIcon.appiconset/, one single-size
1024x1024 icon per iOS 18 appearance:

* AppIcon-1024.png: the cat on #1F1A14, a full opaque square, RGB with NO
  alpha channel (App Store Connect refuses an icon with one). iOS rounds the
  corners itself.
* AppIcon-1024-dark.png: the cat alone on a transparent background, which is
  what Apple's dark appearance asks for: iOS draws its own dark background
  behind it.
* AppIcon-1024-tinted.png: the cat in grayscale on opaque black, RGB with no
  alpha. iOS keeps the luminance and paints it in the person's tint colour.

Needs Pillow (a dev tool only; nothing here ships in the app). The output is
byte-stable for one Pillow version; --check is meaningful on the version that
wrote the committed files.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
ICON = ROOT / "docs" / "assets" / "domovoi-icon.png"
OUT = ROOT / "ios" / "Domovoi" / "Assets.xcassets" / "AppIcon.appiconset"

SIZE = 1024
HEAD = 744  # android/play/make_graphics.py's 372 of 512, doubled
DROP = 12  # and its 6 px nudge down, doubled
BACKGROUND = (0x1F, 0x1A, 0x14)


def cat(size: int) -> Image.Image:
    """The cat head, trimmed to its own outline, fitted into size x size."""
    head = Image.open(ICON).convert("RGBA")
    head = head.crop(head.getbbox())
    scale = size / max(head.size)
    return head.resize((max(1, round(head.width * scale)), max(1, round(head.height * scale))), Image.LANCZOS)


def place(canvas: Image.Image, head: Image.Image) -> Image.Image:
    canvas.alpha_composite(head, ((SIZE - head.width) // 2, (SIZE - head.height) // 2 + DROP))
    return canvas


def light() -> Image.Image:
    return place(Image.new("RGBA", (SIZE, SIZE), BACKGROUND + (255,)), cat(HEAD)).convert("RGB")


def dark() -> Image.Image:
    return place(Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)), cat(HEAD))


def tinted() -> Image.Image:
    head = cat(HEAD)
    alpha = head.getchannel("A")
    # Grayscale, stretched so the amber fur reads near white and the face's
    # darks stay dark: the tint is applied by luminance.
    gray = ImageOps.autocontrast(head.convert("L"), cutoff=1)
    mono = Image.merge("RGBA", (gray, gray, gray, alpha))
    return place(Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255)), mono).convert("RGB")


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed files instead of writing")
    args = ap.parse_args()
    made = {
        "AppIcon-1024.png": png(light()),
        "AppIcon-1024-dark.png": png(dark()),
        "AppIcon-1024-tinted.png": png(tinted()),
    }
    OUT.mkdir(parents=True, exist_ok=True)
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
