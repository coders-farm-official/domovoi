"""Opening pictures someone else made, with Pillow, safely.

Pillow opens any of forty formats by default, some through plugins that
run other programs (EPS and PostScript call Ghostscript), and only warns
about a "decompression bomb" below about 179 megapixels: a 164 KB PNG of
169 megapixels decoded to 350 MB, and an EPS named ``cat.png`` reached
Ghostscript, in the dashboard's chat (2026-10-16 delta security review,
S7, and round 3's OPS-16). So every decode of a picture from a user or a
file goes through :func:`open_image`:

* only the formats the caller names are tried (:data:`PICTURE_FORMATS`
  for chat pictures, :data:`LIBRARY_FORMATS` for the picture libraries'
  thumbnails) — never EPS, PostScript, PDF or anything that runs a
  program;
* a picture of more than :data:`MAX_IMAGE_PIXELS` is refused
  (:class:`PictureRefused`) from its header, before any pixel is decoded.

:func:`configure_pillow` sets ``Image.MAX_IMAGE_PIXELS`` to the same
number process-wide (Pillow then refuses past twice that on its own,
wherever it is used, a plugin's code included); both processes call it at
startup, and this module calls it on import. Decoding is blocking: callers
run it in ``domovoi.executors``' ``pictures`` pool.
"""

from __future__ import annotations

import os
from typing import IO, Any, Iterable

#: The largest picture decoded: 40 megapixels is a 7300 x 5500 photo, more
#: than any phone takes; RGBA, it is 160 MB in memory.
MAX_IMAGE_PIXELS = 40_000_000

#: What a picture sent in the chat may be (what its upload accepts).
PICTURE_FORMATS: tuple[str, ...] = ("JPEG", "PNG", "WEBP", "GIF", "BMP")
#: What the picture libraries' thumbnails are made from (the Files views'
#: image extensions that Pillow reads).
LIBRARY_FORMATS: tuple[str, ...] = PICTURE_FORMATS + ("ICO", "AVIF")


class PictureRefused(ValueError):
    """Not a picture of an allowed format, or more pixels than allowed."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


def configure_pillow() -> None:
    """``Image.MAX_IMAGE_PIXELS`` set to :data:`MAX_IMAGE_PIXELS` (no-op
    without Pillow). Idempotent; never raises."""
    try:
        from PIL import Image
    except ImportError:
        return
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _available(formats: Iterable[str]) -> list[str]:
    from PIL import Image

    Image.init()
    return [f for f in (x.upper() for x in formats) if f in Image.OPEN]


def open_image(src: str | os.PathLike[str] | IO[bytes], formats: Iterable[str]) -> Any:
    """``PIL.Image.open(src)``, trying only ``formats`` (those this Pillow
    has), refused past :data:`MAX_IMAGE_PIXELS` before decoding. The image
    is lazy, as Pillow's always is: close it (``with``) when done.

    Raises :class:`PictureRefused` for a file that isn't one of
    ``formats`` (whatever its name says), is broken in its header, or is
    too large; OSError when the file itself can't be read."""
    from PIL import Image, UnidentifiedImageError

    configure_pillow()
    names = _available(formats)
    try:
        im = Image.open(src, formats=names)
    except Image.DecompressionBombError as e:
        # Pillow's own refusal, past twice the limit.
        raise PictureRefused(
            f"more than {2 * MAX_IMAGE_PIXELS / 1e6:.0f} megapixels; at most "
            f"{MAX_IMAGE_PIXELS / 1e6:.0f} are decoded",
            too_large=True,
        ) from e
    except UnidentifiedImageError as e:
        raise PictureRefused(f"not a picture of an allowed kind ({', '.join(names)})") from e
    except OSError as e:
        if e.errno is not None:
            raise            # the operating system's: the file itself
        raise PictureRefused(f"a broken picture ({e})") from e
    except (SyntaxError, ValueError, EOFError) as e:
        raise PictureRefused(f"a broken picture ({type(e).__name__})") from e
    width, height = im.size
    if width * height > MAX_IMAGE_PIXELS:
        im.close()
        raise PictureRefused(
            f"{width} x {height} is {width * height / 1e6:.0f} megapixels; at most "
            f"{MAX_IMAGE_PIXELS / 1e6:.0f} are decoded",
            too_large=True,
        )
    return im


configure_pillow()
