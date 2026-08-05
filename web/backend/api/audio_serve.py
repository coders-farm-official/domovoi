"""Shared HTTP Range audio serving for the browser player.

The music router already serves library tracks with Range/206 support
(music.py:_parse_range / _iter_file). The podcasts + audiobooks routers
need the exact same behavior for episode / audiobook files, so the reusable
pieces live here and all three call ``serve_audio_range``. Kept separate so
podcasts/audiobooks don't import the whole music router (MPD clients etc.)
just for byte-range streaming.

Every caller resolves its own containment-checked ``Path`` first (inside
``podcasts_dir`` / ``audiobooks_dir`` / ``music_dir``); this module never
resolves paths, so it can't be turned into an arbitrary-file read.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import StreamingResponse

_AUDIO_CONTENT_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}

# Public alias — routers that enumerate downloadable files (e.g. zipping a
# folder audiobook) filter on the same extensions this module can serve.
AUDIO_EXTENSIONS = frozenset(_AUDIO_CONTENT_TYPES)

# Video containers the Videos tab serves through the same Range machinery.
# .mkv rides as x-matroska: Chromium-family browsers demux Matroska (h264/
# aac plays fine); a browser that can't fires the <video> error event and
# the page offers save-to-device instead. Android's ExoPlayer plays all of
# these natively.
VIDEO_CONTENT_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
VIDEO_EXTENSIONS = frozenset(VIDEO_CONTENT_TYPES)

_STREAM_CHUNK = 256 * 1024

# Characters that are unsafe in a filename on some OS the file may land on
# (Windows reserved set + control chars). The download filename is derived
# from user-editable titles, so scrub rather than trust.
_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_download_name(name: str, fallback: str = "audio") -> str:
    """Scrub an arbitrary title into a filename usable on any client OS."""
    cleaned = _FILENAME_UNSAFE.sub(" ", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:150] or fallback


def attachment_headers(filename: str) -> dict[str, str]:
    """``Content-Disposition: attachment`` with an ASCII fallback plus the
    RFC 5987 ``filename*`` form so non-ASCII titles survive every browser
    and Android's DownloadManager."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }


def parse_range(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Single ``Range: bytes=start-end`` → inclusive (start, end), clamped.
    None for missing/multi-range/unsatisfiable (caller serves 200)."""
    if not range_header:
        return None
    range_header = range_header.strip()
    if not range_header.startswith("bytes=") or "," in range_header:
        return None
    spec = range_header[len("bytes="):].strip()
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            n = int(end_s)
            if n <= 0:
                return None
            start = max(0, file_size - n)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start > end or start >= file_size:
        return None
    return start, min(end, file_size - 1)


async def _iter_file(path: Path, start: int, end: int):
    import anyio

    remaining = end - start + 1
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = await anyio.to_thread.run_sync(f.read, min(_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_audio_range(
    target: Path,
    request: Request,
    download_name: str | None = None,
    content_type: str | None = None,
) -> StreamingResponse:
    """Stream ``target`` with Range/206 support. Caller guarantees ``target``
    is a real file already validated inside its configured dir. With
    ``download_name`` the response is marked ``attachment`` (save-to-device)
    instead of inline playback. ``content_type`` overrides the audio-map
    lookup (the Videos router passes its own video MIME)."""
    file_size = target.stat().st_size
    if content_type is None:
        content_type = _AUDIO_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    rng = parse_range(request.headers.get("range"), file_size)
    base = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
    if download_name:
        base.update(attachment_headers(download_name))

    if rng is None:
        return StreamingResponse(
            _iter_file(target, 0, file_size - 1),
            status_code=200,
            media_type=content_type,
            headers={**base, "Content-Length": str(file_size)},
        )
    start, end = rng
    return StreamingResponse(
        _iter_file(target, start, end),
        status_code=206,
        media_type=content_type,
        headers={
            **base,
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{file_size}",
        },
    )
