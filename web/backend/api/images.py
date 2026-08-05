"""Library-image serving — the dashboard's ``/api/images`` surface.

Two generic endpoints over the Files media-library registry
(:mod:`files_security`):

* ``/thumb`` — Pillow-resized WebP thumbnails in four size buckets,
  cached under ``image_thumbs_dir`` with ``.none`` sentinels (the
  cover-art pattern). Used wherever the dashboard renders an image tile.
* ``/raw`` — the original, served inline: the Files tab's "Open" action
  for image rows opens this in a new tab.

Image *generation* is not a core feature — it lives in the separately
installed Image Generation plugin (Coders Farm), which manages a local
ComfyUI engine and serves its own pages/routes under
``/api/plugins/imagegen``.

Both endpoints are admin-read-gated (the dashboard cookie is enough for
``<img src>``), and every path passes the same containment the Files
surface uses: the client only ever names a ``library_id`` + relative
path.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from domovoi.admin_auth import require_admin_read
from domovoi.config import settings as core_settings
from web.backend.api.documents import _IMAGE_EXTS
from web.backend.api.files_security import MediaLibrary, build_libraries, safe_join

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])

IMAGE_EXTENSIONS: frozenset[str] = frozenset(_IMAGE_EXTS)

# Thumbnail size buckets (max edge, px). The cache is keyed by bucket so
# switching sizes never rescales in the browser.
THUMB_SIZES: dict[str, int] = {"s": 160, "m": 320, "l": 512, "xl": 768}

_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".avif": "image/avif", ".heic": "image/heic",
}


# ─── Registry / path resolution (videos.py pattern) ─────────────────────────
async def _resolve_library(library_id: str) -> MediaLibrary:
    reg = {lib.id: lib for lib in await build_libraries()}
    lib = reg.get(library_id)
    if lib is not None:
        return lib
    if library_id.startswith("removable:"):
        raise HTTPException(status_code=410, detail="drive no longer present")
    raise HTTPException(status_code=404, detail=f"unknown library {library_id!r}")


def _resolve_image(lib: MediaLibrary, path: str) -> Path:
    target = safe_join(lib.root_path, path)
    if target.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="not an image file")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return target


# ─── Raw (inline click-through) ─────────────────────────────────────────────
@router.get("/raw", dependencies=[Depends(require_admin_read)])
async def raw(
    library_id: str = Query(...),
    path: str = Query(...),
):
    """The original image, served inline (the Files tab's Open target)."""
    lib = await _resolve_library(library_id)
    target = _resolve_image(lib, path)
    media_type = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(
        target, media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ─── Thumbnails ─────────────────────────────────────────────────────────────
def _make_thumb(src: Path, dest: Path, max_edge: int) -> bool:
    """Pillow resize → WebP. Sync (worker thread). False when Pillow is
    missing or the file can't be decoded (caller writes the sentinel)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("images: Pillow not installed — thumbnails disabled")
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((max_edge, max_edge))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.save(dest, "WEBP", quality=82)
        return True
    except Exception as e:  # noqa: BLE001 — any decode failure ⇒ sentinel
        log.debug("images: thumb failed for %s: %s", src, e)
        dest.unlink(missing_ok=True)
        return False


@router.get("/thumb", dependencies=[Depends(require_admin_read)])
async def thumb(
    library_id: str = Query(...),
    path: str = Query(...),
    size: str = Query("m"),
):
    """Cached thumbnail in one of the size buckets. 204 when the source
    can't be decoded (client falls back to the raw image / a glyph tile)."""
    if size not in THUMB_SIZES:
        raise HTTPException(status_code=400, detail=f"size must be one of {sorted(THUMB_SIZES)}")
    lib = await _resolve_library(library_id)
    target = _resolve_image(lib, path)
    st = target.stat()

    cache_dir = Path(core_settings.image_thumbs_dir).expanduser() / size
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{library_id}|{path}|{st.st_size}|{st.st_mtime_ns}".encode()
    ).hexdigest()
    webp = cache_dir / f"{key}.webp"
    sentinel = cache_dir / f"{key}.none"

    headers = {"Cache-Control": "public, max-age=604800"}
    if webp.is_file():
        return FileResponse(webp, media_type="image/webp", headers=headers)
    if sentinel.is_file():
        return Response(status_code=204, headers=headers)

    ok = await anyio.to_thread.run_sync(_make_thumb, target, webp, THUMB_SIZES[size])
    if ok:
        return FileResponse(webp, media_type="image/webp", headers=headers)
    sentinel.touch()
    return Response(status_code=204, headers=headers)
