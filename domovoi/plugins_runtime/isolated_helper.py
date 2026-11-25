"""The image helper inside a decode-only container (core 1.5.0).

The core copies this file into every decode-only image as
``/opt/domovoi-isolated/domovoi_isolated.py``, next to the core's own
``domovoi/image_safety.py`` (as ``domovoi_image_safety.py``). It is the
core's code, not the plugin's: the plugin's decoder never calls it. The
runner (``domovoi_isolated_runner.py``) calls :func:`decode_pixels` for an
**image request**, one the core sends itself with bytes it reassembled
(``domovoi/plugins_runtime/isolated.py``, ``ImageChunks``). It uses the
standard library plus Pillow from Debian (``[isolated] debian =
["python3-pil"]``), imported only for an image request.

The container **only decodes**. Its answer is the raw pixels, never an
encoded file, so nothing a friend crafted can ride out in a file format:
the core checks ``len == width * height * channels`` and the caps and
encodes the PNG itself, on the trusted side (security review 2026-11-23,
D1). Here:

* the bytes are opened with the core's ``open_image``, trying only PNG,
  JPEG and WebP (never by a name or a guess), and refused past
  :data:`MAX_PIXELS` by their header, before a pixel is decoded;
* only the first frame of an animation is used;
* the EXIF orientation is applied, then the pixels are converted to RGB or
  RGBA: no EXIF, GPS, XMP, ICC profile or text can leave, because only
  pixels do;
* the image is scaled to at most :data:`MAX_SIDE` pixels a side.
"""

from __future__ import annotations

import io
import warnings

# Sized to the container (128 MB): 4 megapixels is 16 MB as RGBA.
MAX_PIXELS = 4_000_000
MAX_SIDE = 768
INPUT_FORMATS = ("PNG", "JPEG", "WEBP")


class ImageRefused(ValueError):
    """The bytes are not an image this helper will decode."""


def decode_pixels(data: bytes) -> tuple[int, int, int, bytes]:
    """``(width, height, channels, pixels)``: the first frame, oriented,
    scaled to :data:`MAX_SIDE`, as 8-bit RGB (3) or RGBA (4), row by row.
    :class:`ImageRefused` for anything else."""
    try:
        from PIL import Image as PILImage
        from PIL import ImageFile, ImageOps

        import domovoi_image_safety as safety
    except ImportError:
        raise ImageRefused("this decoder has no image support (isolated.debian needs python3-pil)") from None

    ImageFile.LOAD_TRUNCATED_IMAGES = False
    with warnings.catch_warnings():
        warnings.simplefilter("error", PILImage.DecompressionBombWarning)
        try:
            with safety.open_image(io.BytesIO(data), INPUT_FORMATS, max_pixels=MAX_PIXELS) as im:
                if im.format not in INPUT_FORMATS:
                    raise ImageRefused("not a PNG, JPEG or WebP image")
                if getattr(im, "n_frames", 1) > 1:
                    im.seek(0)
                im.load()
                frame = ImageOps.exif_transpose(im)
                mode = "RGBA" if ("A" in frame.getbands() or "transparency" in frame.info) else "RGB"
                pixels = frame.convert(mode)
        except ImageRefused:
            raise
        except safety.PictureRefused as e:
            raise ImageRefused("the image is too large" if e.too_large else "not a PNG, JPEG or WebP image") from None
        except (PILImage.DecompressionBombError, PILImage.DecompressionBombWarning):
            raise ImageRefused("the image is too large") from None
        except Exception as e:  # noqa: BLE001 — any decoder failure is a refusal
            raise ImageRefused(f"the image doesn't decode ({type(e).__name__})") from None
    pixels.thumbnail((MAX_SIDE, MAX_SIDE))
    width, height = pixels.size
    return width, height, len(mode), pixels.tobytes()
