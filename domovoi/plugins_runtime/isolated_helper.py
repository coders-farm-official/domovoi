"""The helper module inside a decode-only container (core 1.5.0, slice 2).

The core copies this file into every decode-only image as
``/opt/domovoi-isolated/domovoi_isolated.py``, so a decoder can
``import domovoi_isolated``. It is the core's code, not the plugin's, and it
uses the standard library plus Pillow from Debian (``[isolated] debian =
["python3-pil"]``), imported only when an image is re-encoded.

A decoder that returns an image returns :class:`Image` (or plain ``bytes``,
meaning ``Image(data)``). The runner (``domovoi_isolated_runner.py``) then
calls :func:`reencode`: the friend's bytes are decoded to pixels here, in
the container, and only freshly encoded pixels leave it:

* the input must start with PNG, JPEG or WebP magic bytes, and Pillow must
  agree on the format; anything else is refused;
* a decompression-bomb guard: the declared size is checked against
  :data:`MAX_PIXELS` before a single pixel is decoded, and Pillow's own
  bomb warning is an error;
* animations (more than one frame) and truncated files are refused;
* the EXIF orientation is applied, then the pixels are copied into a new
  image: EXIF (GPS included), XMP, ICC profiles, text chunks and comments
  are all left behind;
* it is scaled to at most :data:`MAX_SIDE` pixels a side and encoded as PNG
  or WebP; the result must be at most :data:`OUTPUT_MAX` bytes.

The core checks the result again (magic, size, dimensions, no metadata
chunks: ``domovoi/plugins_runtime/isolated.py``, ``check_image``), so a
decoder that bypasses this helper still can't hand it anything else.
"""

from __future__ import annotations

import io
import warnings

MAX_SIDE = 1024
MAX_PIXELS = 12_000_000
OUTPUT_MAX = 512 * 1024
INPUT_FORMATS = ("PNG", "JPEG", "WEBP")
OUTPUT_FORMATS = ("png", "webp")

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
)


class ImageRefused(ValueError):
    """The bytes are not an image this helper will re-encode."""


class Image:
    """What a decoder returns for an image: the (untrusted) bytes, and the
    format the re-encoded image should have."""

    __slots__ = ("data", "format", "lossless")

    def __init__(self, data: bytes, format: str = "png", lossless: bool = True) -> None:  # noqa: A002
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Image data must be bytes")
        if format not in OUTPUT_FORMATS:
            raise ValueError(f"Image format must be one of {OUTPUT_FORMATS}")
        self.data = bytes(data)
        self.format = format
        self.lossless = bool(lossless)


def sniff(data: bytes) -> str | None:
    """The input format by its magic bytes, or None."""
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def reencode(image: Image) -> tuple[str, bytes]:
    """``(format, bytes)``: the image decoded and encoded anew, or
    :class:`ImageRefused`."""
    fmt = sniff(image.data)
    if fmt is None:
        raise ImageRefused("not a PNG, JPEG or WebP image")
    try:
        from PIL import Image as PILImage
        from PIL import ImageFile, ImageOps
    except ImportError:
        raise ImageRefused("this decoder has no image support (isolated.debian needs python3-pil)") from None

    PILImage.MAX_IMAGE_PIXELS = MAX_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    with warnings.catch_warnings():
        warnings.simplefilter("error", PILImage.DecompressionBombWarning)
        try:
            with PILImage.open(io.BytesIO(image.data), formats=[fmt]) as im:
                if im.format != fmt:
                    raise ImageRefused("the image isn't what its first bytes say")
                width, height = im.size
                if width < 1 or height < 1 or width * height > MAX_PIXELS:
                    raise ImageRefused("the image is too large")
                if getattr(im, "n_frames", 1) != 1:
                    raise ImageRefused("animated images aren't accepted")
                im.load()
                im = ImageOps.exif_transpose(im)
                mode = "RGBA" if ("A" in im.getbands() or "transparency" in im.info) else "RGB"
                pixels = im.convert(mode)
                # Only the pixels cross into the new image: no info dict,
                # no EXIF, no ICC profile, no text.
                clean = PILImage.frombytes(mode, pixels.size, pixels.tobytes())
        except ImageRefused:
            raise
        except (PILImage.DecompressionBombError, PILImage.DecompressionBombWarning):
            raise ImageRefused("the image is too large") from None
        except Exception as e:  # noqa: BLE001 — any decoder failure is a refusal
            raise ImageRefused(f"the image doesn't decode ({type(e).__name__})") from None
    clean.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    if image.format == "webp":
        clean.save(out, "WEBP", lossless=image.lossless, quality=85, method=4)
    else:
        clean.save(out, "PNG", optimize=True)
    data = out.getvalue()
    if len(data) > OUTPUT_MAX:
        raise ImageRefused("the re-encoded image is too large")
    return image.format, data
